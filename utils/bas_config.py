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

import logging
import time

app_logger = logging.getLogger("app_logger")

# Fallback de último recurso -- confirmado contra el catálogo real de BAS.
CATEGORIA_A_CODIGO_ITEM = {
    "Limpieza": "Limp 21%",
    "Economato Alimentos": "Ec. Alim 21%",
    "Vajilla y Cocina": "Vaj. 21%",
    "Farmacia": "Gs.Farm. 21%",
    "Combustible": "Comb 21%",
    "Mantenimiento": "Mant21%",
    "Seguros": "Seg. 21%",
    "Gastos Generales": "Gs Gs 21%",  # catch-all, verificado con un 201 real
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


def codigo_item_de_categoria(categoria: str) -> str:
    """CodigoItem de BAS para una categoría (elegida por el LLM). Cae al
    catch-all -- de PocketBase si está, si no del fallback hardcodeado -- si
    la categoría no matchea ninguna fila real."""
    mapa = _categoria_map_vigente()
    if categoria in mapa:
        return mapa[categoria]
    return mapa.get(CATEGORIA_CATCH_ALL) or CATEGORIA_A_CODIGO_ITEM[CATEGORIA_CATCH_ALL]


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
