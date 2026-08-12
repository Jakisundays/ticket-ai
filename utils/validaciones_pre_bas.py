"""
Validaciones de datos de factura antes de escribir en BAS.

Ver docs/plan-validaciones-pre-bas.md para el análisis completo (Etapa 0 del
plan). Estas funciones se llaman como gate ANTES del único lugar donde se
mueve dinero real (routes/process_invoice_google_2.py: crear_orden_pago) --
NO se llaman desde el flujo automático (InvoiceOrchestrator.worker() /
procesar_factura_en_bas), que siempre corre con dry_run=True y nunca escribe
nada real en BAS. Bloquear ahí solo agregaría fricción sin proteger dinero
real; el gate que importa es el de crear_orden_pago.

Cada función devuelve `None` si la validación pasa, o un mensaje amigable
(listo para mostrarle al usuario final, sin jerga técnica) si falla.
"""

import re
import datetime
from typing import Optional

from utils.bas_config import fecha_hoy_bas

FORMATOS_FECHA_ACEPTADOS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")

# BAS_EMITIDO_POR_CAE está hardcodeado a "2" (factura electrónica) para TODA
# factura de este pipeline, sin importar el tipo real de comprobante (ver
# utils/bas_config.py) -- por lo tanto el CAE es un requisito incondicional
# hoy, no algo que dependa de si la factura "es electrónica". Si en el
# futuro EmitidoPor se deriva dinámicamente, esta validación debe volverse
# condicional también.
CAE_REGEX = re.compile(r"^\d{14}$")

MONEDAS_PERMITIDAS = {"ARS", "$", "PESOS", "PESO ARGENTINO"}


def _parsear_fecha(valor: Optional[str]) -> Optional[datetime.date]:
    if not valor:
        return None
    valor = str(valor).strip()
    for formato in FORMATOS_FECHA_ACEPTADOS:
        try:
            return datetime.datetime.strptime(valor, formato).date()
        except ValueError:
            continue
    return None


def validar_cuit(cuit: Optional[str]) -> Optional[str]:
    """CUIT/RUC del emisor: 11 dígitos (no valida el dígito verificador --
    solo descarta el caso más común de OCR mal leído: longitud incorrecta)."""
    solo_digitos = re.sub(r"\D", "", str(cuit or ""))
    if len(solo_digitos) != 11:
        return (
            "El número de identificación fiscal del proveedor no parece válido. "
            "Revisá la factura y corregilo antes de continuar."
        )
    return None


def validar_moneda(moneda: Optional[str]) -> Optional[str]:
    """Ausencia de moneda no bloquea (se asume ARS, comportamiento histórico
    del pipeline) -- pero un valor EXPLÍCITO que no sea pesos sí, porque el
    payload hacia BAS no tiene ningún campo de moneda y registraría el monto
    nominal como si fueran pesos."""
    if not moneda:
        return None
    if moneda.strip().upper() not in MONEDAS_PERMITIDAS:
        return (
            "Esta factura parece estar en una moneda distinta a pesos. Por ahora "
            "no se puede registrar el pago automáticamente — contactá al equipo "
            "para procesarla manualmente."
        )
    return None


def validar_fecha_emision(fecha_emision: Optional[str]) -> Optional[str]:
    fecha = _parsear_fecha(fecha_emision)
    if fecha is None:
        return (
            "La fecha de emisión de la factura no es válida o no pudo leerse "
            "correctamente. Verificala antes de continuar."
        )
    # Huso ARGENTINO, no datetime.date.today() -- ver
    # utils/bas_config.py:ZONA_HORARIA_BAS (mismo motivo que las fechas que
    # se le mandan a BAS: "hoy" tiene que ser el de Argentina).
    if fecha > fecha_hoy_bas():
        return "La fecha de emisión de esta factura es futura. Verificala antes de continuar."
    return None


_LETRAS_COMPROBANTE_AFIP = {"A", "B", "C", "E", "M", "T"}


def normalizar_numero_comprobante(numero_comprobante: Optional[str]) -> Optional[str]:
    """Gemini a veces extrae el número con la letra de tipo de comprobante
    AFIP pegada adelante como un tercer segmento separado por guión (ej.
    "A-0064-00671710" en vez de "0064-00671710" -- confirmado real,
    2026-08-07, el prompt de extracción nunca le pide incluirla ni
    excluirla). La letra ya se guarda aparte en tipo_comprobante/
    subtipo_comprobante (texto descriptivo derivado, no la letra cruda), así
    que no se pierde información al descartarla acá -- se recupera el
    formato "prefijo-numero" que BAS espera sin bloquear la factura.

    Cualquier otro caso (2 partes ya bien formadas, más de 3 partes, una
    letra no reconocida, vacío/None) se devuelve INTACTO -- no se inventan
    más patrones sin haberlos visto reales; que sigan cayendo en el rechazo
    de validar_numero_comprobante como hasta ahora."""
    if not numero_comprobante:
        return numero_comprobante
    partes = numero_comprobante.replace(" ", "").split("-")
    if len(partes) == 3 and len(partes[0]) == 1 and partes[0].upper() in _LETRAS_COMPROBANTE_AFIP:
        return f"{partes[1]}-{partes[2]}"
    return numero_comprobante


def validar_numero_comprobante(numero_comprobante: Optional[str]) -> Optional[str]:
    """Mismo parseo que _extraer_prefijo_numero_comprobante_externo -- acá
    solo para RECHAZAR el caso en que ese parseo caería en el fallback
    numero_externo=0 (que rompe la deduplicación real contra BAS)."""
    numero_completo = (normalizar_numero_comprobante(numero_comprobante) or "").replace(" ", "")
    prefijo, separador, numero_str = numero_completo.partition("-")
    if not separador or not prefijo or not numero_str.isdigit():
        return (
            "No pudimos identificar el número de comprobante de forma confiable. "
            "Por favor verificá y corregí el número antes de registrar la factura."
        )
    return None


def validar_cae(cae: Optional[str], cae_vencimiento: Optional[str]) -> Optional[str]:
    if not cae or not CAE_REGEX.match(str(cae).strip()):
        return "Falta el CAE de esta factura o no tiene un formato válido. Verificalo antes de continuar."
    if _parsear_fecha(cae_vencimiento) is None:
        return "Falta la fecha de vencimiento del CAE de esta factura. Verificala antes de continuar."
    return None


def validar_items(items: list) -> Optional[str]:
    """Solo completitud (hay ítems, cada uno tiene precio_total) -- lo único
    que items_bas necesita para armar ImporteGravado por línea (ver
    process_invoice_google_2.py). Antes también rechazaba acá si
    cantidad * precio_unitario no cerraba contra precio_total (tolerancia
    2%) -- se sacó (2026-08-05, mismo criterio que la baja de
    validar_monto_aplicable_vs_neto, docs/incidente-2026-08-04-pagos-solo-
    neto.md): BAS nunca recibe cantidad/precio_unitario como restricción,
    solo ImporteGravado ya calculado a partir de precio_total -- ese cruce
    era un invento de Invoicy sin correspondencia real en lo que BAS valida,
    y el ruido normal de OCR (descuentos, redondeos) lo hacía bloquear
    facturas reales que BAS habría aceptado sin problema."""
    if not items:
        return "No se detectaron ítems válidos en la factura para registrar. Revisá el documento antes de continuar."
    for item in items:
        if item.get("precio_total") is None:
            return "Algunos ítems de la factura tienen el precio incompleto. Completalos antes de registrar la factura."
    return None


def validar_alicuota_iva(alicuota: Optional[float]) -> Optional[str]:
    """`invoices.iva_alicuota` se escribe directo en el registro contable
    real de BAS (ImporteIva/TotalIva/Total del ComprobanteCompra, ver
    process_invoice_google_2.py) -- a diferencia de `monto` (lo que
    efectivamente se paga, ya acotado por invoice.total), nada más limita
    este valor. Un typo del revisor (ej. "215" en vez de "21.5") produciría
    un comprobante internamente consistente para BAS (ImporteGravado +
    ImporteIva == ImporteTotal siempre cierra, sea cual sea la tasa) pero
    con un Total que no corresponde a la factura real -- BAS no lo
    rechazaría, así que hay que frenarlo acá.

    None (alícuota no determinada) NO es un error -- ese caso ya cae al
    neto puro (ver _extraer_alicuota_iva). Rango 0-27: cubre todas las
    alícuotas reales de IVA en Argentina (0/2,5/5/10,5/21/27, confirmadas
    contra el catálogo real de BAS, GET /api/Impuestos/1) con margen. No
    asume que `alicuota` ya viene convertida a número -- PocketBase puede
    devolver lo que sea que haya quedado guardado tras una edición manual."""
    if alicuota is None:
        return None
    try:
        alicuota = float(alicuota)
    except (TypeError, ValueError):
        return "La alícuota de IVA de esta factura no es un número válido. Verificala antes de continuar."
    if alicuota < 0 or alicuota > 27:
        return (
            f"La alícuota de IVA de esta factura ({alicuota}%) no parece válida. "
            "Verificala antes de continuar."
        )
    return None


def validar_factura_antes_de_pago_real(invoice: dict, items: list) -> list:
    """
    Corre las validaciones críticas de datos de la factura (Etapa 0 del plan
    de validaciones) antes de crear una Orden de Pago REAL en BAS. Devuelve
    una lista de mensajes amigables (vacía si todo está OK).
    """
    validaciones = (
        validar_cuit(invoice.get("emisor_cuit")),
        validar_moneda(invoice.get("moneda")),
        validar_fecha_emision(invoice.get("fecha_emision")),
        validar_numero_comprobante(invoice.get("numero_comprobante")),
        validar_cae(invoice.get("cae"), invoice.get("cae_vencimiento")),
        validar_items(items),
        validar_alicuota_iva(invoice.get("iva_alicuota")),
    )
    return [mensaje for mensaje in validaciones if mensaje]
