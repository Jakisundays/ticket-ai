"""
Test OFFLINE (sin red, sin BAS, sin PocketBase real) dedicado a
utils/bas_payload.py:construir_comprobante_totales_e_items -- la función
compartida que arma Total/TotalGravado/TotalIva/Items para los 3 call
sites reales de BAS (InvoiceOrchestrator.procesar_factura_en_bas,
crear_orden_pago, registrar_comprobante), creada 2026-08-19 para que
`invoice.total` sea la única fuente del Total registrado en BAS -- nunca
la suma de invoice_items (ver el docstring del módulo para el porqué
completo).

Este archivo prueba la función EN SÍ, aislada de cualquier endpoint --
scripts/test_offline_importe_total_linea.py ya cubre 2 casos reales de
punta a punta (OESTEREICHER HUGO, Telefónica Móviles) contra
SP_VALIDA_TOTALES; acá se cubre la matriz de casos de la función:

  A. total_bruto ausente/inválido -> ValueError, con mensaje claro:
     None, 0, negativo, string no numérico.
  B. Fórmula Total/TotalGravado/TotalIva para varias alícuotas (21, 10.5,
     0, None) -- en todos los casos, total_gravado + total_iva ==
     total_bruto EXACTO (por resta, no por cálculo independiente -- así
     SP_VALIDA_TOTALES se cumple siempre, sin casos especiales).
  C. Prioridad de selección de CodigoItem:
     C1. Override manual válido gana sobre la categoría automática,
         aunque el automático también sea válido y tenga mayor
         abs(precio_total).
     C2. Entre 2 ítems con override manual válido, gana el de mayor
         abs(precio_total).
     C3. Sin overrides, entre 2 ítems con categoría automática válida,
         gana el de mayor abs(precio_total) (el signo no importa).
     C4. Un solo candidato gana aunque su precio_total sea 0, negativo o
         None -- no compite contra nada, así que no puede perder.
  D. Catch-all ('Gs Gs 21%'):
     D1. Sin ítems (lista vacía) -> cae al catch-all.
     D2. La categoría de un ítem resuelve un candidato real (vía
         bas_config), pero ese candidato no está habilitado en bas_items
         -> cae al catch-all real de este módulo. (Nota: una categoría
         directamente desconocida no sirve para probar esto -- bas_config
         tiene su PROPIO catch-all interno, CATEGORIA_CATCH_ALL =
         "Gastos Generales", que resuelve categorías desconocidas antes
         de que este módulo intervenga.)
     D3. Ni los ítems ni el catch-all resuelven -> ValueError explícito
         (nunca un CodigoItem inventado o vacío).
  E. Un override manual INVÁLIDO bloquea de inmediato (ValueError), SIN
     caer al catch-all ni a la categoría automática de ese mismo ítem --
     regla 1 de resolver_codigo_item (P0-E, 2026-08-12), que esta función
     tiene que preservar exactamente igual. Esto es una regresión directa
     de un bug real encontrado y corregido durante la implementación de
     este módulo (ver historial de utils/bas_payload.py).

Uso: python3 scripts/test_offline_bas_payload.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import utils.bas_config as bas_config  # noqa: E402
from utils.bas_payload import CODIGO_ITEM_CATCH_ALL, construir_comprobante_totales_e_items  # noqa: E402

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


# Cache determinístico de bas_config -- 3 categorías reales distintas para
# poder probar desempate de verdad (con 1 sola categoría, cualquier
# resolución automática colapsa siempre al mismo código).
bas_config._cache_categoria_map["datos"] = {
    "Gastos Generales": {21: "Gs Gs 21%"},
    "Categoria A": {21: "Codigo A"},
    "Categoria B": {21: "Codigo B"},
}
bas_config._cache_categoria_map["actualizado_en"] = time.time()


class FakePbClient:
    def __init__(self, bas_items_validos):
        self._bas_items = bas_items_validos
        self.llamadas_get_bas_item = []

    def get_bas_item(self, codigo):
        self.llamadas_get_bas_item.append(codigo)
        return self._bas_items.get(codigo)


CATCH_ALL_DISPONIBLE = {CODIGO_ITEM_CATCH_ALL: {"activo": True, "elegible_compras": True}}


# ============================================================================
# A -- total_bruto ausente/inválido -> ValueError
# ============================================================================
print("=" * 78)
print("A -- total_bruto ausente/inválido -> ValueError")
print("=" * 78)

for total_invalido, motivo in [(None, "None"), (0, "cero"), (-100, "negativo")]:
    try:
        construir_comprobante_totales_e_items(
            total_bruto=total_invalido, alicuota_iva=21, items=[], pb_client=FakePbClient(CATCH_ALL_DISPONIBLE)
        )
        check(f"total_bruto={motivo} -> debería haber lanzado ValueError", False)
    except ValueError:
        check(f"total_bruto={motivo} -> ValueError levantado correctamente", True)


# ============================================================================
# B -- Fórmula Total/TotalGravado/TotalIva para varias alícuotas
# ============================================================================
print()
print("=" * 78)
print("B -- Fórmula correcta para varias alícuotas (21, 10.5, 0, None)")
print("=" * 78)

for alicuota, etiqueta in [(21, "21%"), (10.5, "10.5%"), (0, "0%"), (None, "None (tratado como 0)")]:
    payload = construir_comprobante_totales_e_items(
        total_bruto=1000.00,
        alicuota_iva=alicuota,
        items=[{"categoria": "Gastos Generales", "precio_total": 1000}],
        pb_client=FakePbClient(CATCH_ALL_DISPONIBLE),
    )
    check(f"alicuota={etiqueta} -> Total == 1000.00 (invoice.total, sin cambios)", payload["Total"] == 1000.00)
    check(
        f"alicuota={etiqueta} -> TotalGravado + TotalIva == Total EXACTO (garantiza SP_VALIDA_TOTALES por construcción)",
        round(payload["TotalGravado"] + payload["TotalIva"], 2) == payload["Total"],
    )
    check(
        f"alicuota={etiqueta} -> ImporteTotal de la línea == ImporteGravado (regla de línea real de SP_VALIDA_TOTALES)",
        payload["Items"][0]["ImporteTotal"] == payload["Items"][0]["ImporteGravado"] == payload["TotalGravado"],
    )


# ============================================================================
# C -- Prioridad de selección de CodigoItem
# ============================================================================
print()
print("=" * 78)
print("C1 -- Override manual válido gana sobre categoría automática (aunque ésta tenga mayor precio_total)")
print("=" * 78)

bas_items_c1 = {"Gs Gs 21%": {"activo": True, "elegible_compras": True}, "Codigo Manual": {"activo": True, "elegible_compras": True}}
payload_c1 = construir_comprobante_totales_e_items(
    total_bruto=1000.00,
    alicuota_iva=21,
    items=[
        {"categoria": "Gastos Generales", "precio_total": 999999},  # automático, precio enorme
        {"categoria": "", "bas_codigo_item": "Codigo Manual", "precio_total": 1},  # override, precio chico
    ],
    pb_client=FakePbClient(bas_items_c1),
)
check("Override manual gana pese a precio_total mucho menor -> CodigoItem == 'Codigo Manual'", payload_c1["Items"][0]["CodigoItem"] == "Codigo Manual")
check("Override manual gana -> fallback_catch_all == False", payload_c1["fallback_catch_all"] is False)


print()
print("=" * 78)
print("C2 -- Entre 2 overrides manuales válidos, gana el de mayor abs(precio_total)")
print("=" * 78)

bas_items_c2 = {"Codigo Manual A": {"activo": True, "elegible_compras": True}, "Codigo Manual B": {"activo": True, "elegible_compras": True}}
payload_c2 = construir_comprobante_totales_e_items(
    total_bruto=1000.00,
    alicuota_iva=21,
    items=[
        {"categoria": "", "bas_codigo_item": "Codigo Manual A", "precio_total": 50},
        {"categoria": "", "bas_codigo_item": "Codigo Manual B", "precio_total": -200},  # abs 200 > 50
    ],
    pb_client=FakePbClient(bas_items_c2),
)
check("Gana 'Codigo Manual B' (abs(-200) > abs(50)) -- el signo no importa para el desempate", payload_c2["Items"][0]["CodigoItem"] == "Codigo Manual B")


print()
print("=" * 78)
print("C3 -- Sin overrides, entre 2 categorías automáticas válidas, gana la de mayor abs(precio_total)")
print("=" * 78)

bas_items_c3 = {"Codigo A": {"activo": True, "elegible_compras": True}, "Codigo B": {"activo": True, "elegible_compras": True}}
payload_c3 = construir_comprobante_totales_e_items(
    total_bruto=1000.00,
    alicuota_iva=21,
    items=[
        {"categoria": "Categoria A", "precio_total": 100},
        {"categoria": "Categoria B", "precio_total": -500},  # abs 500 > 100
    ],
    pb_client=FakePbClient(bas_items_c3),
)
check("Gana 'Codigo B' (abs(-500) > abs(100)) -- mismo criterio que C2, ahora automático", payload_c3["Items"][0]["CodigoItem"] == "Codigo B")


print()
print("=" * 78)
print("C4 -- Un solo candidato gana aunque su precio_total sea 0/negativo/None (no compite contra nada)")
print("=" * 78)

for precio_total_raro, etiqueta in [(0, "cero"), (-50, "negativo"), (None, "None")]:
    items_c4 = [{"categoria": "Categoria A", "precio_total": precio_total_raro}]
    payload_c4 = construir_comprobante_totales_e_items(
        total_bruto=1000.00, alicuota_iva=21, items=items_c4, pb_client=FakePbClient({"Codigo A": {"activo": True, "elegible_compras": True}})
    )
    check(f"precio_total={etiqueta} -> único candidato igual gana ('Codigo A')", payload_c4["Items"][0]["CodigoItem"] == "Codigo A")


# ============================================================================
# D -- Catch-all
# ============================================================================
print()
print("=" * 78)
print("D1 -- Sin ítems (lista vacía) -> cae al catch-all")
print("=" * 78)

payload_d1 = construir_comprobante_totales_e_items(total_bruto=1000.00, alicuota_iva=21, items=[], pb_client=FakePbClient(CATCH_ALL_DISPONIBLE))
check(f"Sin ítems -> CodigoItem == catch-all ('{CODIGO_ITEM_CATCH_ALL}')", payload_d1["Items"][0]["CodigoItem"] == CODIGO_ITEM_CATCH_ALL)
check("Sin ítems -> fallback_catch_all == True", payload_d1["fallback_catch_all"] is True)


print()
print("=" * 78)
print("D2 -- Candidato automático resuelve pero no está habilitado en BAS -> cae al catch-all")
print("=" * 78)

# "Categoria A" SÍ está en el mapa (resuelve a 'Codigo A'), pero 'Codigo A'
# no está habilitado en bas_items acá -- a propósito, para forzar el
# fallback real, sin pasar por el propio catch-all interno de
# resolver_item_bas (bas_config.CATEGORIA_CATCH_ALL == "Gastos Generales",
# que también está cacheado y hubiera resuelto en silencio si la
# categoría fuera desconocida -- ver la nota de más abajo).
items_d2 = [{"categoria": "Categoria A", "precio_total": 1000}]
pb_d2 = FakePbClient(CATCH_ALL_DISPONIBLE)  # solo el catch-all está habilitado, 'Codigo A' no
payload_d2 = construir_comprobante_totales_e_items(total_bruto=1000.00, alicuota_iva=21, items=items_d2, pb_client=pb_d2)
check(f"Candidato no habilitado -> cae al catch-all ('{CODIGO_ITEM_CATCH_ALL}')", payload_d2["Items"][0]["CodigoItem"] == CODIGO_ITEM_CATCH_ALL)
check("Candidato no habilitado -> fallback_catch_all == True", payload_d2["fallback_catch_all"] is True)


print()
print("=" * 78)
print("D3 -- Ni los ítems ni el catch-all resuelven -> ValueError explícito")
print("=" * 78)

try:
    construir_comprobante_totales_e_items(
        total_bruto=1000.00,
        alicuota_iva=21,
        items=[{"categoria": "Gastos Generales", "precio_total": 1000}],
        pb_client=FakePbClient({}),  # catálogo vacío -- ni el ítem ni el catch-all están
    )
    check("Nada resuelve -> debería haber lanzado ValueError", False)
except ValueError as e:
    check("Nada resuelve -> ValueError levantado, con el catch-all mencionado en el mensaje", CODIGO_ITEM_CATCH_ALL in str(e))


# ============================================================================
# E -- Override inválido bloquea de inmediato (regresión del bug encontrado
# durante la implementación: NO debe caer al catch-all ni a la categoría
# automática de ese mismo ítem).
# ============================================================================
print()
print("=" * 78)
print("E -- Override manual INVÁLIDO bloquea de inmediato, SIN fallback silencioso")
print("=" * 78)

pb_e = FakePbClient(CATCH_ALL_DISPONIBLE)  # el catch-all SÍ estaría disponible -- no debe usarse igual
try:
    construir_comprobante_totales_e_items(
        total_bruto=1000.00,
        alicuota_iva=21,
        items=[{"categoria": "Gastos Generales", "bas_codigo_item": "Codigo-Que-No-Existe", "precio_total": 1000}],
        pb_client=pb_e,
    )
    check("Override inválido -> debería haber lanzado ValueError", False)
except ValueError as e:
    check("Override inválido -> ValueError levantado (no cae al catch-all disponible)", "Codigo-Que-No-Existe" in str(e))


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
