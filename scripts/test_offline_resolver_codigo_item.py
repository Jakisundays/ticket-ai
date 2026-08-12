"""
Test OFFLINE (sin red, sin BAS, sin PocketBase real) para P0-E: resolución
final y validada del CodigoItem (utils/bas_item_resolver.py).

A diferencia de utils/bas_items_sync.py, `resolver_item_bas`/
`codigo_item_de_categoria` (utils/bas_config.py) SÍ pueden importarse
directo -- no instancian nada a nivel de módulo ni requieren un event loop
(a diferencia de routes/process_invoice_google_2.py, ver docstring de
scripts/test_offline_importe_total_linea.py). Pero `resolver_item_bas`
internamente intenta refrescar "bas_category_map" desde PocketBase real
(utils.bas_config._categoria_map_vigente) -- para que este test sea
determinístico y 100% offline (sin depender de que haya red, credenciales
válidas, o una instancia de PocketBase alcanzable), se pre-carga el cache en
memoria del módulo (`bas_config._cache_categoria_map`) directo, así
`_categoria_map_vigente()` toma la rama "cache vigente" y nunca intenta la
llamada de red real.

Cubre las 6 combinaciones pedidas explícitamente para P0-E:
  1. override válido
  2. override inexistente
  3. override no elegible (activo=false Y, por separado, elegible_compras=false)
  4. resolución por categoría + alícuota (match exacto)
  5. alícuota sin match (cae al fallback de 21% de resolver_item_bas) --
     con un sub-caso que prueba que ESE fallback también se bloquea si no
     está validado en bas_items (regla 5/6 del alcance: ningún camino,
     ni siquiera el histórico, se usa sin validar).
  6. ausencia total de CodigoItem (sin override, sin categoría)

Más: precedencia override > categoría (incluso con una categoría que
resolvería distinto), fail-safe ante get_bas_item que tira excepción o
devuelve una fila sin las claves activo/elegible_compras, y que la
precedencia de "override inválido" NUNCA cae silenciosamente a la
resolución automática.

Uso: python3 scripts/test_offline_resolver_codigo_item.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import utils.bas_config as bas_config  # noqa: E402
from utils.bas_item_resolver import resolver_codigo_item  # noqa: E402

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


# Precarga determinística del cache de bas_config -- ver docstring arriba.
# Con esto, resolver_item_bas() NUNCA intenta la llamada de red real.
bas_config._cache_categoria_map["datos"] = {
    "Vajilla y Cocina": {21: "Vaj. 21%", 10.5: "Vaj. 10.5%"},
    "Gastos Generales": {21: "Gs Gs 21%", 10.5: "Gs Gs 10, 5"},
}
bas_config._cache_categoria_map["actualizado_en"] = time.time()


class FakePbClient:
    """Doble de PocketBaseClient: SOLO implementa get_bas_item -- el único
    método que resolver_codigo_item debería necesitar. Si el código bajo
    prueba intentara llamar cualquier otro método (en particular alguno de
    escritura), Python tira AttributeError -- falla explícita del test."""

    def __init__(self, items):
        self._items = items  # {codigo: {"activo":..., "elegible_compras":...}}
        self.llamadas = []

    def get_bas_item(self, codigo):
        self.llamadas.append(codigo)
        return self._items.get(codigo)


class FakePbClientQueRompe:
    """get_bas_item siempre tira -- prueba el fail-safe de
    _item_activo_y_elegible ante un error de PocketBase."""

    def get_bas_item(self, codigo):
        raise ConnectionError("PocketBase inalcanzable (simulado)")


BAS_ITEMS = {
    "Vaj. 21%": {"activo": True, "elegible_compras": True},
    "Vaj. 10.5%": {"activo": True, "elegible_compras": True},
    "Gs Gs 21%": {"activo": True, "elegible_compras": True},
    "Item Inactivo": {"activo": False, "elegible_compras": True},
    "Item No Elegible": {"activo": True, "elegible_compras": False},
    "Item Sin Claves": {},  # fila corrupta/incompleta -- ver check fail-safe
    # "Item Fantasma 21%" y "Gs Gs 21%ANTIGUO" NO están acá -- simulan
    # "no existe en el catálogo".
}

pb = FakePbClient(BAS_ITEMS)


print("=" * 78)
print("1 -- override válido")
print("=" * 78)

r = resolver_codigo_item(
    categoria="Gastos Generales",  # a propósito: resolvería a "Gs Gs 21%" si se usara
    alicuota=21,
    override_codigo_item="Vaj. 21%",
    pb_client=pb,
)
check("Override válido -> valido=True", r["valido"] is True)
check("Override válido -> codigo_item es el override, NO el de la categoría", r["codigo_item"] == "Vaj. 21%")
check("Override válido -> fuente='override'", r["fuente"] == "override")
check("Override válido -> motivo_bloqueo es None", r["motivo_bloqueo"] is None)
check("Override con espacios (' Vaj. 21% ') se normaliza igual", resolver_codigo_item(override_codigo_item="  Vaj. 21%  ", pb_client=pb)["codigo_item"] == "Vaj. 21%")


print()
print("=" * 78)
print("2 -- override inexistente")
print("=" * 78)

pb2 = FakePbClient(BAS_ITEMS)
r = resolver_codigo_item(
    categoria="Vajilla y Cocina",  # categoría válida, a propósito -- no debe rescatar el resultado
    alicuota=21,
    override_codigo_item="Item Fantasma 21%",
    pb_client=pb2,
)
check("Override inexistente -> valido=False", r["valido"] is False)
check("Override inexistente -> codigo_item=None (nunca se inventa nada)", r["codigo_item"] is None)
check("Override inexistente -> motivo_bloqueo menciona el código", "Item Fantasma 21%" in (r["motivo_bloqueo"] or ""))
check(
    "Override inexistente -- NUNCA cae silenciosamente a la categoría (regla 1): "
    "no se llamó a get_bas_item con el candidato de categoría ('Vaj. 21%')",
    "Vaj. 21%" not in pb2.llamadas,
)


print()
print("=" * 78)
print("3 -- override no elegible (activo=false / elegible_compras=false)")
print("=" * 78)

pb3 = FakePbClient(BAS_ITEMS)
r_inactivo = resolver_codigo_item(override_codigo_item="Item Inactivo", pb_client=pb3)
check("Override con activo=False -> valido=False", r_inactivo["valido"] is False)
check("Override con activo=False -> codigo_item=None", r_inactivo["codigo_item"] is None)
check("Override con activo=False -> motivo_bloqueo lo explica", "no está habilitado" in (r_inactivo["motivo_bloqueo"] or "") or "no existe" in (r_inactivo["motivo_bloqueo"] or ""))

r_no_elegible = resolver_codigo_item(override_codigo_item="Item No Elegible", pb_client=pb3)
check("Override con elegible_compras=False -> valido=False", r_no_elegible["valido"] is False)
check("Override con elegible_compras=False -> codigo_item=None", r_no_elegible["codigo_item"] is None)

r_sin_claves = resolver_codigo_item(override_codigo_item="Item Sin Claves", pb_client=pb3)
check("Override con fila sin 'activo'/'elegible_compras' -> fail-safe False (no asume válido)", r_sin_claves["valido"] is False)


print()
print("=" * 78)
print("4 -- resolución por categoría + alícuota (match exacto)")
print("=" * 78)

pb4 = FakePbClient(BAS_ITEMS)
r = resolver_codigo_item(categoria="Vajilla y Cocina", alicuota=10.5, pb_client=pb4)
check("Categoría+alícuota con match exacto -> valido=True", r["valido"] is True)
check("Categoría+alícuota con match exacto -> codigo_item='Vaj. 10.5%'", r["codigo_item"] == "Vaj. 10.5%")
check("Categoría+alícuota con match exacto -> fuente='categoria_alicuota' (no fallback)", r["fuente"] == "categoria_alicuota")


print()
print("=" * 78)
print("5 -- alícuota sin match (fallback al 21% de resolver_item_bas)")
print("=" * 78)

pb5 = FakePbClient(BAS_ITEMS)
# "Vajilla y Cocina" solo tiene variantes a 21 y 10.5 -- pedir 5% no matchea
# ninguna, resolver_item_bas cae al 21% de esa categoría (comportamiento
# histórico, ver bas_config.py:resolver_item_bas).
r = resolver_codigo_item(categoria="Vajilla y Cocina", alicuota=5, pb_client=pb5)
check("Alícuota sin match -> igual resuelve (vía fallback a 21%) -> valido=True", r["valido"] is True)
check("Alícuota sin match -> codigo_item='Vaj. 21%' (el fallback de la categoría)", r["codigo_item"] == "Vaj. 21%")
check("Alícuota sin match -> fuente='categoria_alicuota_fallback' (marca que NO fue match exacto)", r["fuente"] == "categoria_alicuota_fallback")

# Sub-caso -- el MISMO fallback, pero el candidato del 21% no está validado
# en bas_items (ej. se desactivó en BAS después de confirmarse en
# bas_category_map). Regla 5/6: ni siquiera el fallback histórico se usa
# sin validar -- tiene que bloquear, no mandar igual el código viejo.
bas_items_sin_fallback_valido = dict(BAS_ITEMS)
del bas_items_sin_fallback_valido["Vaj. 21%"]
pb5b = FakePbClient(bas_items_sin_fallback_valido)
r_bloqueado = resolver_codigo_item(categoria="Vajilla y Cocina", alicuota=5, pb_client=pb5b)
check(
    "Alícuota sin match + el candidato de fallback NO está en bas_items -> BLOQUEA (no manda el código histórico sin validar)",
    r_bloqueado["valido"] is False and r_bloqueado["codigo_item"] is None,
)


print()
print("=" * 78)
print("6 -- ausencia total de CodigoItem (sin override, sin categoría)")
print("=" * 78)

pb6 = FakePbClient(BAS_ITEMS)
r = resolver_codigo_item(categoria="", alicuota=21, pb_client=pb6)
check("Sin override y sin categoría -> valido=False", r["valido"] is False)
check("Sin override y sin categoría -> codigo_item=None", r["codigo_item"] is None)
check("Sin override y sin categoría -> motivo_bloqueo menciona la categoría faltante", "categoría" in (r["motivo_bloqueo"] or "").lower())
check(
    "Sin override y sin categoría -> CERO llamadas a get_bas_item (bloquea sin intentar validar nada)",
    len(pb6.llamadas) == 0,
)

# Mismo caso con categoría solo espacios -- debe tratarse igual que vacía.
r_espacios = resolver_codigo_item(categoria="   ", alicuota=21, pb_client=FakePbClient(BAS_ITEMS))
check("Categoría solo-espacios se trata como ausente -> valido=False", r_espacios["valido"] is False)


print()
print("=" * 78)
print("Extra -- fail-safe ante error de PocketBase (get_bas_item tira excepción)")
print("=" * 78)

r_error = resolver_codigo_item(override_codigo_item="Vaj. 21%", pb_client=FakePbClientQueRompe())
check(
    "get_bas_item que tira excepción -> fail-safe: valido=False, NUNCA asume válido",
    r_error["valido"] is False and r_error["codigo_item"] is None,
)
r_error_cat = resolver_codigo_item(categoria="Vajilla y Cocina", alicuota=21, pb_client=FakePbClientQueRompe())
check(
    "Mismo fail-safe también en la rama de categoría+alícuota",
    r_error_cat["valido"] is False and r_error_cat["codigo_item"] is None,
)


print()
print("=" * 78)
if FALLOS:
    print(f"RESULTADO: {len(FALLOS)} chequeo(s) fallaron:")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("RESULTADO: todos los chequeos pasaron.")
    sys.exit(0)
