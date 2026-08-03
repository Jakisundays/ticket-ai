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
    if fecha > datetime.date.today():
        return "La fecha de emisión de esta factura es futura. Verificala antes de continuar."
    return None


def validar_numero_comprobante(numero_comprobante: Optional[str]) -> Optional[str]:
    """Mismo parseo que _extraer_prefijo_numero_comprobante_externo -- acá
    solo para RECHAZAR el caso en que ese parseo caería en el fallback
    numero_externo=0 (que rompe la deduplicación real contra BAS)."""
    numero_completo = (numero_comprobante or "").replace(" ", "")
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
    if not items:
        return "No se detectaron ítems válidos en la factura para registrar. Revisá el documento antes de continuar."
    for item in items:
        if item.get("precio_total") is None:
            return "Algunos ítems de la factura tienen el precio incompleto. Completalos antes de registrar la factura."
        cantidad = item.get("cantidad")
        precio_unitario = item.get("precio_unitario")
        precio_total = item.get("precio_total")
        if cantidad is not None and precio_unitario is not None and precio_total is not None:
            esperado = round(float(cantidad) * float(precio_unitario), 2)
            real = round(float(precio_total), 2)
            tolerancia = max(0.02, abs(esperado) * 0.02)
            if abs(esperado - real) > tolerancia:
                return (
                    "Algunos ítems de la factura tienen la cantidad o el precio "
                    "inconsistentes entre sí. Revisalos antes de continuar."
                )
    return None


def validar_monto_vs_total(monto: Optional[float], total_factura: Optional[float]) -> Optional[str]:
    if monto is None:
        return None
    if monto <= 0:
        return "El monto a pagar no es válido. Verificalo antes de generar la orden de pago."
    if total_factura is not None and monto > float(total_factura) + 0.01:
        return (
            f"El monto a pagar (${monto}) supera el total de la factura (${total_factura}). "
            "Verificá el monto antes de continuar."
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
    )
    return [mensaje for mensaje in validaciones if mensaje]
