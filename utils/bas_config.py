"""
Config de negocio para la integración BAS (instalación PLATINUM_TEST, Empresa 1).

Separado de utils/bas.py (el cliente HTTP, agnóstico a qué categorías o qué
empresa se usan) porque esto es dato de negocio de esta instalación puntual,
no lógica de transporte.

Mapeo categoría-de-ítem (la que elige el LLM al extraer la factura, ver
tools_standard.py) -> código de ítem real del catálogo de BAS. Las categorías
son un subconjunto curado de los ítems reales de BAS que SÍ tienen posición
contable con concepto COM (Compras) configurada -- ver
docs/bas-orden-de-pago-research.md (193 de 255 ítems tienen COM).

A DIFERENCIA de una versión anterior de este archivo, este mapeo YA NO vive
hardcodeado acá -- la fuente de verdad es la colección PocketBase
"bas_category_map" (editable desde /category-map en el dashboard, filas con
confirmado=true), para que agregar/sacar una categoría sea un cambio de datos,
no un deploy de código, y para que el LLM elija en cada extracción sobre datos
reales y vigentes. Ver PocketBaseClient.obtener_categoria_map().

Las constantes de abajo (CATEGORIA_A_CODIGO_ITEM / CATEGORIAS_ITEM_BAS) quedan
SOLO como fallback de último recurso si PocketBase no responde -- confirmadas
contra el catálogo real de BAS (docs/bas-orden-de-pago-research.md +
GET /api/Servicios, /api/Bienes, /api/PosicionesContables en vivo), no
placeholders. "Bebidas y Bar" e "Insumos" (de una iteración anterior) se
sacaron: no existe ninguna posición contable de Compras para ese rubro en
esta instalación de BAS (PLATINUM HOMES es una residencia/centro de cuidado,
no tiene bar) -- usarlas habría ensuciado la contabilidad real.
"""

import datetime
import logging
import time
from typing import Optional
from zoneinfo import ZoneInfo

app_logger = logging.getLogger("app_logger")

# El servidor de BAS valida ciertas reglas de negocio (ej. "la fecha de la
# aplicación debe ser igual o superior a la de los comprobantes que se están
# aplicando") contra SU PROPIO reloj de servidor -- no contra lo que le
# mandemos en el campo "Fecha" del payload. Confirmado real, 2026-08-04: un
# ComprobanteCompra registrado con `datetime.date.today()` calculado en el
# contenedor (que corre en UTC) quedó con Fecha "de mañana" para BAS durante
# la ventana diaria 00:00-03:00 UTC (21:00-23:59 en Argentina, donde corre
# el servidor de BAS) -- la aplicación del pago se rechazó con 409
# "SP_VALIDA_APLICACIONES" porque, en ese momento, el propio reloj de BAS
# todavía estaba en el día anterior. Reproducido y resuelto con una prueba
# real: mandando la Fecha en huso argentino en vez de UTC, el mismo flujo
# (registrar + crear OP + aplicar) funcionó sin error.
#
# CUALQUIER fecha que se le mande a BAS (Fecha de ComprobanteCompra, de la
# Orden de Pago, de medios de pago, etc.) tiene que salir de acá
# (fecha_hoy_bas()), NUNCA de datetime.date.today()/datetime.datetime.utcnow()
# "pelado" -- esos toman la zona horaria del sistema del contenedor (hoy
# UTC), no la de Argentina. NO reemplazar por conveniencia sin volver a leer
# este comentario: el bug es intermitente (~3hs por día) y fácil de no
# reproducir en una prueba manual que no pegue justo en esa ventana.
ZONA_HORARIA_BAS = ZoneInfo("America/Argentina/Buenos_Aires")


def fecha_hoy_bas() -> datetime.date:
    """"Hoy" en la fecha de Argentina (no la del sistema/contenedor). Ver
    ZONA_HORARIA_BAS. Devuelve un date -- para el string que espera BAS,
    usar fecha_hoy_bas().isoformat()."""
    return datetime.datetime.now(ZONA_HORARIA_BAS).date()

# Fallback de último recurso -- confirmado contra el catálogo real de BAS
# (GET /api/Servicios + /api/Bienes + /api/Impuestos/1 + /api/PosicionesContables,
# 2026-08-03). {categoria: {alicuota: codigo_item}} -- antes era 1 código fijo
# por categoria, siempre a 21%, lo cual rompía cualquier factura con otra
# alícuota real (ver docstring de resolver_item_bas). Mismo criterio que la
# migración 1783483945_add_alicuota_to_bas_category_map.js: solo se agregan acá
# variantes de tasa CONFIRMADAS contra el catálogo real, no inventadas -- las
# categorías/tasas sin variante confirmada caen al código de 21% (comportamiento
# histórico) vía el fallback de resolver_item_bas.
CATEGORIA_A_CODIGO_ITEM = {
    "Limpieza": {21: "Limp 21%", 0: "Limp Ex."},
    "Economato Alimentos": {
        21: "Ec. Alim 21%",
        10.5: "Ec. Alim 10.5%",
        5: "Ec. Alim 5%",
        0: "Ec. Alim Exe",
    },
    "Vajilla y Cocina": {21: "Vaj. 21%", 10.5: "Vaj. 10.5%", 0: "Vaj. Exe"},
    "Farmacia": {21: "Gs.Farm. 21%", 0: "Gs.Farm. Exe."},
    "Combustible": {21: "Comb 21%"},
    "Mantenimiento": {21: "Mant21%", 10.5: "Mant10,5%", 0: "Mant Ex"},
    "Seguros": {21: "Seg. 21%", 0: "Segu Ex"},
    "Gastos Generales": {21: "Gs Gs 21%", 10.5: "Gs Gs 10, 5"},  # catch-all
}

CATEGORIA_CATCH_ALL = "Gastos Generales"

CATEGORIAS_ITEM_BAS = list(CATEGORIA_A_CODIGO_ITEM.keys())

# --- Cache en memoria del mapeo real (60s) -- evita pegarle a PocketBase por
# cada ítem de cada factura; suficientemente "tiempo real" para un dato que
# alguien edita a mano en el dashboard de vez en cuando, no por segundo. ---
_CACHE_TTL_SEGUNDOS = 60
_cache_categoria_map = {"datos": None, "actualizado_en": 0.0}


def _categoria_map_vigente() -> dict:
    ahora = time.time()
    if (
        _cache_categoria_map["datos"] is not None
        and (ahora - _cache_categoria_map["actualizado_en"]) < _CACHE_TTL_SEGUNDOS
    ):
        return _cache_categoria_map["datos"]
    try:
        # Import perezoso: bas_config.py no debe depender de pocketbase_client.py
        # a nivel de módulo (evita cualquier riesgo de import circular).
        from utils.pocketbase_client import PocketBaseClient

        mapa = PocketBaseClient().obtener_categoria_map()
        if mapa:
            _cache_categoria_map["datos"] = mapa
            _cache_categoria_map["actualizado_en"] = ahora
            return mapa
    except Exception as e:
        app_logger.warning(f"bas_config: no se pudo refrescar categoria_map desde PocketBase: {e}")
    # PocketBase no respondió (o la tabla está vacía): reusar el último valor
    # cacheado si hay uno, o el fallback hardcodeado como último recurso.
    return _cache_categoria_map["datos"] or CATEGORIA_A_CODIGO_ITEM


def categorias_disponibles() -> list:
    """Categorías para el enum del LLM (tools_standard.py) -- leídas en vivo
    de PocketBase (con cache de 60s), no hardcodeadas."""
    return list(_categoria_map_vigente().keys())


def resolver_item_bas(categoria: str, alicuota: Optional[float] = None) -> tuple:
    """NO USAR DIRECTO para armar un payload real hacia BAS (P0-E,
    2026-08-12): esto da un CANDIDATO de CodigoItem, sin validar contra el
    catálogo real sincronizado ("bas_items", ver utils/bas_items_sync.py) --
    puede devolver un código que ya no existe o dejó de estar habilitado
    para compras. Para el CodigoItem final y validado, usar
    utils.bas_item_resolver.resolver_codigo_item (que llama a esta función
    internamente como generador de candidatos y agrega el gate contra
    bas_items). Esta función y `codigo_item_de_categoria` quedan como
    piezas internas de resolución de candidatos, no como fuente final.

    (codigo_item, alicuota_real_del_codigo) para una categoría + la
    alícuota real de IVA de la factura (ver invoices.iva_alicuota,
    extraída por Gemini pero antes descartada).

    Devuelve la alícuota REAL del código elegido, no necesariamente la
    pedida: si no hay una variante confirmada en el catálogo de BAS para esa
    alícuota exacta (ver CATEGORIA_A_CODIGO_ITEM / bas_category_map), cae al
    código de 21% de esa categoría (comportamiento histórico) y devuelve 21,
    no la alícuota pedida -- así el caller nunca arma un ImporteTotal
    "bruto" que no coincide con lo que BAS calcula internamente para el
    código que realmente se está usando (eso dispara 409 "no son
    consistentes", ver comentario largo en items_bas de
    process_invoice_google_2.py).

    Si la categoría en sí no matchea ninguna fila real, cae al catch-all
    ("Gastos Generales") con el mismo criterio de alícuota."""
    mapa = _categoria_map_vigente()
    variantes = mapa.get(categoria) or mapa.get(CATEGORIA_CATCH_ALL) or CATEGORIA_A_CODIGO_ITEM[CATEGORIA_CATCH_ALL]

    if alicuota is not None:
        clave = round(float(alicuota), 2)
        if clave in variantes:
            return variantes[clave], clave

    # Fallback: la variante de 21% de esa categoría (o la que exista, si por
    # algún motivo ni siquiera esa está cargada) -- comportamiento histórico.
    if 21 in variantes:
        return variantes[21], 21.0
    alicuota_disponible = next(iter(variantes))
    return variantes[alicuota_disponible], float(alicuota_disponible)


def codigo_item_de_categoria(categoria: str, alicuota: Optional[float] = None) -> str:
    """Compat: solo el CodigoItem, para los sitios que solo lo guardan a
    título informativo y no necesitan saber qué alícuota terminó aplicando
    (ver resolver_item_bas para eso)."""
    codigo, _ = resolver_item_bas(categoria, alicuota)
    return codigo


# --- Config de negocio fija de esta instalación (Empresa 1 = PLATINUM HOMES) ---
# Documentado con evidencia real en docs/bas-orden-de-pago-research.md (201 logrado).
BAS_EMPRESA = 1
BAS_SUCURSAL = 1
BAS_DEPOSITO = 1
BAS_CAJA = "1"
BAS_METODO_PAGO_CTA_CTE = "C"  # cuenta corriente (no contado)
BAS_TIPO_ENTREGA_SIN_STOCK = "E"  # entrega pendiente, no mueve mercadería
BAS_CENTRO_APROPIACION_SD = "SD"  # "sin definir", genérico
BAS_EMITIDO_POR_CAE = "2"  # factura con CAE (electrónica)
BAS_TRAT_IMPOSITIVO_RI = "2"  # Responsable Inscripto (catálogo real de BAS)
BAS_TRAT_IMPOSITIVO_PROV_RI = "1"  # NO RET NO PERCEP (catálogo real de BAS)
BAS_NUMERO_IMPOSITIVO_TIPO_CUIT = "80"
# TODO: resolver dinámico con buscar_prefijo_talonario(empresa, "MA"/"OP") en vez
# de hardcodear -- por ahora, valores reales confirmados con un 201 real.
BAS_PREFIJO_TALONARIO_MA = "00001"  # talonario de Factura de Compra A
BAS_PREFIJO_TALONARIO_OP = "00001"  # talonario de Orden de Pago (distinto talonario, mismo prefijo)

# Cuenta contable genérica "Proveedores" (plan de cuentas, GET /api/Cuentas),
# NO específica de ningún proveedor puntual -- confirmado 2026-07-18
# muestreando 25 proveedores reales y activos del maestro (GET /api/Proveedores):
# 211001 aparece en el 100% (25/25). Se usa como CuentasCorrientes por defecto
# al dar de alta un proveedor nuevo (ver BasClient.verificar_o_dar_de_alta_proveedor),
# porque sin ella BAS no puede resolver la moneda de la cuenta corriente del
# proveedor al registrar un ComprobanteCompra con MetodoPago="C" (cuenta
# corriente) -- cae a una cuenta "0" inexistente y responde 409
# (SP_ICR_VALIDA_CODTAB). Ver docs/bas-comprobante-compra-cuenta-0-diagnostico.md.
BAS_IMPUTACION_CONTABLE_PROVEEDORES = 211001

# --- Métodos de pago (Orden de Pago humana, ver /payment-orders/{id}/create) ---
# Qué ARRAY de "pagos" de OrdenDePago usar por método es forma de payload
# (estructura de código), no dato de negocio descubrible con el tiempo, así
# que vive hardcodeado acá -- a diferencia del CÓDIGO de MedioPago y, para
# transferencia/tarjeta, la CuentaBancaria/Plan/CodigoTarjeta, que SÍ son
# datos de negocio del ERP y viven en bas_payment_methods (ver
# PocketBaseClient.get_payment_method).
#
# Confirmado en vivo contra la API real de BAS (2026-07-21, probando
# MedioPago 1 a 20 con Total=1, ver docs/bas-orden-de-pago-research.md):
# - "cheque" -> Cheques (cheque RECIBIDO/de terceros, endosado para pagar --
#   NO ChequesPropios). La Caja actual no tiene ningún medio de pago tipo
#   "cheque propio" habilitado (existen los códigos 3/5 en el maestro de BAS,
#   pero ninguno configurado en Caja 1) -- si en el futuro se necesita emitir
#   cheques propios, hay que pedirle a un admin de BAS que habilite uno en la
#   Caja primero, esto no es algo que Invoicy pueda resolver solo.
# - "tarjeta" (nuevo): confirmado que MedioPago=9 es tipo tarjeta. A
#   diferencia de los demás, Tarjetas[] exige también Plan/CodigoTarjeta
#   (códigos del maestro de tarjetas de BAS) además de NumeroTarjeta -- ver
#   bas_payment_methods.bas_plan_tarjeta/bas_codigo_tarjeta.
METODO_PAGO_ARRAY_BAS = {
    "efectivo": "Efectivos",
    "cheque": "Cheques",
    "transferencia": "PagosPorBanco",
    "tarjeta": "Tarjetas",
}
