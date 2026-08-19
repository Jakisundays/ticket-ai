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


def combinar_numero_comprobante(
    numero: Optional[str], punto_de_venta: Optional[str] = None
) -> Optional[str]:
    """Arma el "prefijo-numero" que BAS espera cuando el documento imprime
    Punto de Venta y Número de Comprobante como dos campos separados (el
    formato AFIP estándar -- "Punto de Venta: 00001" / "Comp. Nro:
    00000066" -- presente en la enorme mayoría de comprobantes electrónicos
    argentinos, no algo específico de un layout puntual). El schema de
    extracción (tools_standard.py) ya le pide a Gemini estos dos valores
    POR SEPARADO ("numero" y "punto_de_venta" son campos propios) -- lo que
    faltaba era combinarlos acá. Bug real confirmado 2026-08-14 (factura
    LEON LUGO BLANCA ELENA, comp. 00000066 / P.V. 00001): sin esto,
    `numero_comprobante` quedaba guardado como "00000066" a secas, y las
    tres copias de la validación (Invoicy, dashboard, hook de PocketBase)
    lo rechazan por no tener el separador "-" -- factura bloqueada pidiendo
    corrección manual por un dato que la IA ya había extraído bien, solo
    que en dos pedazos.

    No es determinístico confiar en que Gemini combine los dos números por
    su cuenta -- confirmado real: la MISMA factura, en corridas distintas,
    a veces devuelve "numero" ya combinado ("00001-00000066") y a veces
    solo el comprobante ("00000066"). Por eso esta función NUNCA asume cuál
    de los dos casos pasó -- siempre revisa la forma real de "numero" antes
    de decidir si hace falta anteponer punto_de_venta.

    Reglas, en orden (case-insensitive con normalizar_numero_comprobante,
    que corre primero para no confundir un "A-00001-00000066" de 3 partes
    con un caso ya combinado de 2):
      1. Letra AFIP pegada adelante (3 partes) -> se saca primero, vía
         normalizar_numero_comprobante (ej. "A-00001-00000066" ->
         "00001-00000066").
      2. Si el resultado YA tiene 2 partes (prefijo-numero) -> se devuelve
         TAL CUAL, sin tocar. Nunca se antepone punto_de_venta acá aunque
         venga presente -- haría "00001-00001-00000066" (doble prefijo) si
         Gemini ya lo había combinado él mismo en esta corrida.
      3. Si tiene 1 sola parte (sin guión) Y punto_de_venta viene no vacío
         -> se combinan: f"{punto_de_venta}-{numero}". Concatenación de
         strings, nunca se convierte a int en el camino -- los ceros a la
         izquierda de ambos números quedan intactos tal como los extrajo
         Gemini.
      4. Cualquier otro caso (sin punto_de_venta, o una forma que no
         reconocemos) -> se devuelve tal cual, sin inventar un prefijo --
         mismo comportamiento de hoy, que dejaba bloquear aguas abajo y
         pedir corrección manual en vez de adivinar.

    Idempotente por construcción: el único caso que modifica el valor (la
    regla 3) siempre produce un resultado de 2 partes, y un resultado de 2
    partes SIEMPRE cae en la regla 2 (paso directo) si se le vuelve a pasar
    -- nunca se le vuelve a anteponer punto_de_venta una segunda vez."""
    numero_normalizado = normalizar_numero_comprobante(numero)
    if not numero_normalizado:
        return numero_normalizado
    partes = numero_normalizado.replace(" ", "").split("-")
    if len(partes) == 2:
        return numero_normalizado
    punto_de_venta = (punto_de_venta or "").strip()
    if len(partes) == 1 and punto_de_venta:
        return f"{punto_de_venta}-{numero_normalizado}"
    return numero_normalizado


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


def validar_total(total: Optional[float]) -> Optional[str]:
    """`invoices.total` es, desde 2026-08-19, la ÚNICA fuente de Total/
    TotalGravado/TotalIva que se manda a BAS -- ver
    utils/bas_payload.py:construir_comprobante_totales_e_items. Antes de
    ese cambio de arquitectura este campo nunca se validaba en ningún
    lado (no hacía falta: el Total salía de sumar invoice_items). Ahora es
    el dato más crítico de todo el flujo -- si falta o es inválido, no hay
    ningún ítem de respaldo del cual reconstruirlo, así que corta acá con
    un mensaje claro en vez de dejar que construir_comprobante_totales_e_items
    levante un ValueError más abajo (defensa en profundidad, no el camino
    esperado)."""
    if total is None:
        return "Falta el total de la factura. Verificalo antes de continuar."
    try:
        total = float(total)
    except (TypeError, ValueError):
        return "El total de la factura no es un número válido. Verificalo antes de continuar."
    if total <= 0:
        return f"El total de la factura (${total}) tiene que ser mayor a cero. Verificalo antes de continuar."
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


def validar_factura_antes_de_pago_real(invoice: dict) -> list:
    """
    Corre las validaciones críticas de datos de la factura (Etapa 0 del plan
    de validaciones) antes de crear/registrar un comprobante REAL en BAS.
    Devuelve una lista de mensajes amigables (vacía si todo está OK).

    Ya NO recibe `items` (hasta 2026-08-19 sí, para una validar_items() que
    exigía "hay ítems" + "cada uno tiene precio_total"): desde la
    arquitectura invoice.total-como-ancla
    (utils/bas_payload.py:construir_comprobante_totales_e_items), el Total
    que se registra en BAS nunca depende de los ítems -- ni de que existan,
    ni de sus precios -- así que no queda nada de ellos que validar acá.
    """
    validaciones = (
        validar_cuit(invoice.get("emisor_cuit")),
        validar_moneda(invoice.get("moneda")),
        validar_fecha_emision(invoice.get("fecha_emision")),
        validar_numero_comprobante(invoice.get("numero_comprobante")),
        validar_cae(invoice.get("cae"), invoice.get("cae_vencimiento")),
        validar_total(invoice.get("total")),
        validar_alicuota_iva(invoice.get("iva_alicuota")),
    )
    return [mensaje for mensaje in validaciones if mensaje]
