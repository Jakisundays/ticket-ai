"""
Resolución FINAL y validada del CodigoItem que se manda a BAS al registrar
un ComprobanteCompra -- P0-E del nuevo alcance (confirmado 2026-08-12).

Por qué existe este módulo separado de utils/bas_config.py: bas_config.py
tiene `resolver_item_bas`/`codigo_item_de_categoria`, que dan un CANDIDATO
de CodigoItem a partir de categoria+alicuota (con sus propios fallbacks de
negocio: catch-all de categoría "Gastos Generales", fallback de alícuota al
21%) -- pero ese candidato nunca se confirma contra el catálogo real
sincronizado en PocketBase ("bas_items", ver utils/bas_items_sync.py). Este
módulo agrega esa validación como gate obligatorio antes de que CUALQUIER
CodigoItem llegue a un payload real, sea cual sea el camino por el que se
generó el candidato.

Reglas del alcance (dadas explícitamente por el cliente/usuario, P0-E,
2026-08-12):
  1. Un CodigoItem elegido a mano por un humano (override) se valida contra
     bas_items -- activo=true Y elegible_compras=true -- antes de usarse. Si
     es inválido, BLOQUEA -- nunca cae silenciosamente a la resolución
     automática por categoría (un humano que eligió mal un ítem tiene que
     enterarse, no que el sistema le pise la elección con un guess).
  2. La IA (categoria elegida por Gemini, ver tools_standard.py -- ya es un
     enum cerrado de categorías, nunca texto libre) nunca es la fuente
     final de un CodigoItem: la categoría siempre pasa por este resolver: la
     salida de la IA es un INSUMO de la resolución, nunca el CodigoItem en
     sí.
  3. La resolución automática por categoría+alícuota (bas_config.py:
     resolver_item_bas) da un CANDIDATO -- ese candidato tiene que existir
     en bas_items (activo=true Y elegible_compras=true) para poder usarse.
  4. Si no hay ningún CodigoItem válido (ni override, ni auto), se bloquea:
     `valido=False`/`codigo_item=None`/`motivo_bloqueo` -- el caller debe
     dejar la factura en bas_registration_status="awaiting_service_selection"
     (valor ya reservado para esto, ver comentario de la migración
     1784500000_add_bas_traceability_and_state.js en ticket-ai-infra) y
     NUNCA construir un payload con un CodigoItem inventado o vacío.
  5. Ningún candidato -- venga de un override, un match exacto de
     categoría+alícuota, el catch-all de categoría, o el fallback de
     alícuota de resolver_item_bas -- se usa sin pasar por el gate de
     bas_items: así ninguno de esos caminos puede terminar mandando un
     código inexistente o no elegible, sea cual sea el motivo por el que se
     generó el candidato.
  6. `PocketBaseClient.get_bas_item` devuelve None tanto si el ítem no
     existe como si falló la consulta a PocketBase -- en ambos casos se
     trata acá como "no validado" (fail-safe: bloquear, nunca asumir
     válido ante una respuesta ambigua -- mismo criterio ya usado en
     utils/bas_items_sync.py:_tiene_concepto_compras).

Toma `pb_client` por parámetro (no lo instancia acá) -- mismo criterio de
testabilidad ya establecido en utils/bas_items_sync.py: poder testear
offline con dobles de prueba, ver scripts/test_offline_resolver_codigo_item.py.
"""

import logging
from typing import Optional

from utils.bas_config import resolver_item_bas

app_logger = logging.getLogger("app_logger")


def _item_activo_y_elegible(pb_client, codigo_item: str) -> bool:
    """True solo si `codigo_item` existe en bas_items, activo=true Y
    elegible_compras=true. Cualquier otra situación (no existe, inactivo,
    no elegible, o error de PocketBase) -> False. No distingue esos casos a
    propósito: para la decisión de "¿se puede usar?" da lo mismo por qué no
    se puede confirmar -- el fail-safe es el mismo (bloquear)."""
    if not codigo_item:
        return False
    try:
        item = pb_client.get_bas_item(codigo_item)
    except Exception as e:
        app_logger.warning(f"bas_item_resolver: error consultando get_bas_item({codigo_item!r}): {e}")
        return False
    if not item:
        return False
    return bool(item.get("activo")) and bool(item.get("elegible_compras"))


def resolver_codigo_item(
    *,
    categoria: str = "",
    alicuota: Optional[float] = None,
    override_codigo_item: Optional[str] = None,
    pb_client,
) -> dict:
    """
    Resuelve el CodigoItem final para una línea de factura, con la
    precedencia del alcance (reglas 1-6 arriba). Devuelve SIEMPRE un dict
    con esta forma (nunca lanza por un caso de negocio -- solo por un
    argumento mal tipado):

        {
            "codigo_item": str | None,  # listo para el payload, o None si hay que bloquear
            "valido": bool,
            "fuente": "override" | "categoria_alicuota" | "categoria_alicuota_fallback" | None,
            "motivo_bloqueo": str | None,  # mensaje humano si valido=False
        }

    Precedencia: un `override_codigo_item` no vacío manda siempre -- si es
    válido se usa, si NO es válido se bloquea ahí mismo (regla 1: nunca cae
    a la resolución automática como si el override no hubiera existido).
    Sin override, se resuelve por categoría+alícuota y el candidato se
    valida contra bas_items antes de aceptarse (reglas 2-3-5). Sin
    categoría y sin override, no hay nada que resolver (regla 4).
    """
    override_codigo_item = (override_codigo_item or "").strip()
    if override_codigo_item:
        if _item_activo_y_elegible(pb_client, override_codigo_item):
            return {
                "codigo_item": override_codigo_item,
                "valido": True,
                "fuente": "override",
                "motivo_bloqueo": None,
            }
        return {
            "codigo_item": None,
            "valido": False,
            "fuente": None,
            "motivo_bloqueo": (
                f"El ítem seleccionado manualmente ('{override_codigo_item}') no existe "
                "o no está habilitado para compras en el catálogo de BAS."
            ),
        }

    categoria = (categoria or "").strip()
    if not categoria:
        return {
            "codigo_item": None,
            "valido": False,
            "fuente": None,
            "motivo_bloqueo": "Falta la categoría del ítem -- no se puede resolver un CodigoItem.",
        }

    candidato, alicuota_real = resolver_item_bas(categoria, alicuota)
    if not _item_activo_y_elegible(pb_client, candidato):
        return {
            "codigo_item": None,
            "valido": False,
            "fuente": None,
            "motivo_bloqueo": (
                f"El ítem resuelto automáticamente ('{candidato}') para la categoría "
                f"'{categoria}' no existe o no está habilitado para compras en el "
                "catálogo de BAS."
            ),
        }

    fuente = "categoria_alicuota"
    if alicuota is not None and round(float(alicuota), 2) != round(float(alicuota_real), 2):
        fuente = "categoria_alicuota_fallback"

    return {
        "codigo_item": candidato,
        "valido": True,
        "fuente": fuente,
        "motivo_bloqueo": None,
    }
