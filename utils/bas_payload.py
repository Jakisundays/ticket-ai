"""
Construcción del payload real de BAS (ComprobanteCompra) anclada en un
único total confiable -- NUNCA en la suma de ítems.

Antes de este módulo, routes/process_invoice_google_2.py tenía 3 copias
independientes de esta lógica (InvoiceOrchestrator.procesar_factura_en_bas,
crear_orden_pago, registrar_comprobante), cada una sumando ImporteGravado/
ImporteIva de cada línea de invoice_items/detalles para armar
Total/TotalGravado/TotalIva de cabecera. Eso significaba que un descuento,
una bonificación, un precio_total en cero/negativo/null, o un error de OCR
en un solo ítem podían alterar directamente lo que se registraba en BAS --
exactamente el patrón que bloqueaba facturas reales (factura Telefónica
Móviles, "Bonificacion Movistar" con precio_total negativo, 2026-08-18) y,
en el incidente de agosto, dejó una Orden de Pago huérfana real en
producción (ver docs/incidente-2026-08-04-pagos-solo-neto.md).

Arquitectura nueva (decidida 2026-08-19): el ÚNICO monto que importa es
`total_bruto`, provisto siempre por el caller (viene de invoice.total, o de
un valor explícito como monto_override en pruebas de importación masiva --
nunca de sumar ítems). Los ítems se usan EXCLUSIVAMENTE para elegir un
CodigoItem de clasificación contable -- una sola línea por comprobante, no
un reparto proporcional (se evaluó y se descartó: con ítems que casi se
cancelan entre sí -- ej. un ítem y su descuento -- el reparto proporcional
puede repartir porcentajes sin ningún sentido de negocio, como 360%/-260%
del total real). El precio_total de un ítem, como mucho, desempata cuál
categoría gana cuando hay más de un candidato válido -- nunca determina un
monto registrado en BAS.
"""

from typing import Optional

from utils.bas_config import (
    BAS_CENTRO_APROPIACION_SD,
    BAS_TIPO_ENTREGA_SIN_STOCK,
)
from utils.bas_item_resolver import resolver_codigo_item

# Único CodigoItem con elegible_compras=true Y confirmado=true en el
# catálogo real sincronizado (ver utils/bas_items_sync.py) al día de esta
# implementación (2026-08-19) -- el único candidato de todo bas_items con
# un 201 real ya demostrado contra BAS. Hardcodeado a propósito, NO
# resuelto vía bas_config.CATEGORIA_CATCH_ALL ("Gastos Generales") +
# alícuota: ese camino puede fallar igual si la variante de alícuota
# específica no existe en el catálogo real -- confirmado real: "Gastos
# Generales" a 10,5% ("Gs Gs 10, 5") NO existe en bas_items pese a estar
# hardcodeado en bas_config.py:resolver_item_bas. Este fallback tiene que
# funcionar SIEMPRE, sin depender de ninguna resolución por categoría+
# alícuota -- por eso el código va fijo acá, y aun así se revalida contra
# bas_items en cada llamada (nunca se asume válido a ciegas). Si este ítem
# se desactiva alguna vez en BAS, construir_comprobante_totales_e_items
# levanta ValueError explícito en vez de inventar un código -- revisar el
# catálogo real (bas_items) si eso llega a pasar.
CODIGO_ITEM_CATCH_ALL = "Gs Gs 21%"


def construir_comprobante_totales_e_items(
    total_bruto: Optional[float],
    alicuota_iva: Optional[float],
    items: list,
    pb_client,
) -> dict:
    """
    Arma los campos Total/TotalGravado/TotalIva/Items de un
    ComprobanteCompra, anclados siempre a `total_bruto` -- nunca a la suma
    de `items`. `items` es una lista de dicts (invoice_items de PocketBase,
    o `detalles` crudos de Gemini -- ambos exponen "categoria"/
    "precio_total", y "bas_codigo_item" si existe) usada EXCLUSIVAMENTE
    para elegir el CodigoItem de una única línea; su contenido nunca
    afecta Total/TotalGravado/TotalIva.

    Devuelve:
        {
            "Total": float,          # == total_bruto, redondeado
            "TotalGravado": float,
            "TotalIva": float,
            "Items": [<una sola línea>],
            "fallback_catch_all": bool,  # True si se usó CODIGO_ITEM_CATCH_ALL
        }

    Levanta ValueError si:
      - `total_bruto` es None o <= 0 -- es el único dato realmente
        indispensable acá; nunca se inventa un valor ni se cae a sumar
        ítems como respaldo.
      - Ningún ítem resuelve una categoría válida Y el catch-all tampoco
        está disponible en BAS (bas_items real) -- caso extremo, pero
        preferible a mandar un CodigoItem inventado o vacío (mismo
        criterio ya establecido en resolver_codigo_item).
    """
    if total_bruto is None:
        raise ValueError("total_bruto ausente -- no se puede construir el payload de BAS.")
    total_bruto = round(float(total_bruto), 2)
    if total_bruto <= 0:
        raise ValueError(f"total_bruto debe ser mayor a 0 (viene {total_bruto}).")

    try:
        alicuota = float(alicuota_iva) if alicuota_iva is not None else 0.0
    except (TypeError, ValueError):
        alicuota = 0.0

    total_gravado = round(total_bruto / (1 + alicuota / 100), 2)
    # Por resta, no por cálculo independiente -- garantiza
    # total_gravado + total_iva == total_bruto exacto, siempre, sin
    # importar el redondeo del paso anterior. Esto es lo que hace que
    # SP_VALIDA_TOTALES (Total == TotalGravado + TotalIva) se cumpla por
    # construcción, sin ningún caso especial que verificar.
    total_iva = round(total_bruto - total_gravado, 2)

    codigo_item, fallback_catch_all = _elegir_codigo_item_dominante(items, alicuota, pb_client)

    linea = {
        "CodigoItem": codigo_item,
        "TipoEntrega": BAS_TIPO_ENTREGA_SIN_STOCK,
        "NumeroUnidadMedida": "1",
        "CantidadPrimeraUnidad": 1,
        "PrecioUnitario": total_gravado,
        "ImporteGravado": total_gravado,
        "ImporteIva": total_iva,
        # = ImporteGravado, no gravado+iva -- regla de línea real de
        # SP_VALIDA_TOTALES (tolerancia $1), confirmada con datos reales
        # de producción. Ver el comentario largo equivalente que existía
        # en los 3 call sites viejos antes de este refactor.
        "ImporteTotal": total_gravado,
        "TasaIva": alicuota,
        "CentroApropiacionA": BAS_CENTRO_APROPIACION_SD,
        "CentroApropiacionB": BAS_CENTRO_APROPIACION_SD,
    }

    return {
        "Total": total_bruto,
        "TotalGravado": total_gravado,
        "TotalIva": total_iva,
        "Items": [linea],
        "fallback_catch_all": fallback_catch_all,
    }


def _elegir_codigo_item_dominante(items: list, alicuota: float, pb_client) -> tuple:
    """Devuelve (codigo_item, fallback_catch_all).

    Prioridad: ítems con `bas_codigo_item` elegido a mano por un humano
    que resuelven válido, después cualquier ítem con `categoria` que
    resuelve válido automáticamente -- en ambos casos, el de mayor
    abs(precio_total) gana el desempate (un precio_total None/0/negativo
    simplemente no puede ganar contra uno con magnitud real, salvo que
    sea el único candidato). Ningún ítem resuelve (o no hay ítems) -> cae
    al catch-all, revalidado contra bas_items -- nunca bloquea por esto.

    Excepción a "nunca bloquea": un `bas_codigo_item` elegido a mano que
    resuelve INVÁLIDO bloquea de inmediato (levanta ValueError), sin caer
    ni al catch-all ni a la categoría automática de ese ítem -- regla 1 de
    resolver_codigo_item (P0-E, 2026-08-12), que esta función tiene que
    preservar exactamente igual: un humano que corrigió mal un ítem tiene
    que enterarse y corregirlo, no que el sistema le pise la elección con
    un fallback silencioso."""
    manuales = []
    automaticas = []
    for it in items or []:
        override = (it.get("bas_codigo_item") or "").strip()
        r = resolver_codigo_item(
            categoria=it.get("categoria", ""),
            alicuota=alicuota,
            override_codigo_item=override or None,
            pb_client=pb_client,
        )
        if override:
            if not r["valido"]:
                raise ValueError(r["motivo_bloqueo"])
            manuales.append((it, r))
        elif r["valido"]:
            automaticas.append((it, r))

    pool = manuales or automaticas
    if pool:
        _, r_dominante = max(pool, key=lambda par: abs(float(par[0].get("precio_total", 0) or 0)))
        return r_dominante["codigo_item"], False

    r_catch_all = resolver_codigo_item(override_codigo_item=CODIGO_ITEM_CATCH_ALL, pb_client=pb_client)
    if not r_catch_all["valido"]:
        raise ValueError(
            f"Ningún ítem resolvió una categoría válida y el catch-all "
            f"({CODIGO_ITEM_CATCH_ALL!r}) tampoco está disponible en BAS: "
            f"{r_catch_all['motivo_bloqueo']}"
        )
    return r_catch_all["codigo_item"], True
