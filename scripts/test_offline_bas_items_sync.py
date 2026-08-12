"""
Test OFFLINE (sin red, sin BAS, sin PocketBase real) para el sync del
catálogo de ítems (utils/bas_items_sync.py) -- P0-D del nuevo alcance.

Usa dobles de prueba (Fake*) en vez de mocks de librería para que el test
sea legible sin depender de unittest.mock -- mismo criterio que
scripts/test_offline_importe_total_linea.py (no hay pytest instalado en
este proyecto, ver requirements.txt).

Cubre:
  1. _tiene_concepto_compras -- las 3 formas de respuesta soportadas +
     shape desconocido (fail-safe a False).
  2. _tasa_iva_compras -- resolución + coerción numérica + cache (no debe
     pegarle a BAS dos veces por el mismo código de impuesto).
  3. _elegible_compras -- cache (no debe pegarle a BAS dos veces por la
     misma posición contable, aunque varios ítems la compartan).
  4. sincronizar_bas_items end-to-end -- nuevos/actualizados/inactivos,
     ítems sin 'Codigo' se saltean sin romper el resto, soft-delete de lo
     que ya no viene en el catálogo, y (constraint explícito del alcance)
     CERO llamadas de escritura contra BasClient -- solo se llaman los 4
     métodos de lectura documentados.

Uso: python3 scripts/test_offline_bas_items_sync.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.bas_items_sync import (  # noqa: E402
    _elegible_compras,
    _tasa_iva_compras,
    _tiene_concepto_compras,
    sincronizar_bas_items,
)

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


# Métodos de ESCRITURA que bas_items_sync NUNCA debe llamar sobre BasClient
# -- el sync es solo-lectura contra BAS por diseño (regla explícita del
# alcance: "no creamos ni modificamos nada en BAS").
METODOS_ESCRITURA_BAS_PROHIBIDOS = {
    "crear_proveedor",
    "actualizar_proveedor",
    "asegurar_cuenta_corriente_proveedor",
    "verificar_o_dar_de_alta_proveedor",
    "crear_comprobante_compra",
    "crear_orden_de_pago",
    "aplicar_comprobantes",
    "crear_orden_de_pago_desde_factura",
}


class FakeBasClient:
    """Doble de BasClient: solo implementa los 4 métodos de lectura que
    bas_items_sync realmente usa. Si el código bajo prueba intentara llamar
    cualquier otro método (en particular uno de escritura), Python tira
    AttributeError -- el test lo captura como falla explícita."""

    def __init__(self, servicios, bienes, impuestos, posiciones):
        self._servicios = servicios
        self._bienes = bienes
        self._impuestos = impuestos
        self._posiciones = posiciones
        self.llamadas_impuesto = 0
        self.llamadas_posicion = 0
        self.metodos_llamados = []

    def listar_servicios(self):
        self.metodos_llamados.append("listar_servicios")
        return [dict(s) for s in self._servicios]

    def listar_bienes(self):
        self.metodos_llamados.append("listar_bienes")
        return [dict(b) for b in self._bienes]

    def obtener_impuesto(self, empresa, codigo):
        self.metodos_llamados.append("obtener_impuesto")
        self.llamadas_impuesto += 1
        return self._impuestos.get(codigo)

    def obtener_posicion_contable(self, codigo):
        self.metodos_llamados.append("obtener_posicion_contable")
        self.llamadas_posicion += 1
        return self._posiciones.get(codigo)


class FakePbClient:
    """Doble de PocketBaseClient, en memoria."""

    def __init__(self, filas_previas=None):
        self._filas = {f["codigo"]: dict(f) for f in (filas_previas or [])}
        self.upserts = []

    def get_bas_item(self, codigo):
        return self._filas.get(codigo)

    def list_bas_items(self, solo_elegibles=True):
        filas = list(self._filas.values())
        if solo_elegibles:
            filas = [f for f in filas if f.get("activo") and f.get("elegible_compras")]
        return filas

    def upsert_bas_item(self, codigo, **campos):
        self.upserts.append((codigo, dict(campos)))
        existente = self._filas.get(codigo, {"codigo": codigo})
        existente.update(campos)
        self._filas[codigo] = existente
        return existente


print("=" * 78)
print("1 -- _tiene_concepto_compras: las 3 formas soportadas + fail-safe")
print("=" * 78)

check(
    "(a) Conceptos = lista de dicts, con COM -> elegible",
    _tiene_concepto_compras({"Conceptos": [{"Codigo": "VEN"}, {"Codigo": "COM"}]}) is True,
)
check(
    "(a) Conceptos = lista de dicts, sin COM -> NO elegible",
    _tiene_concepto_compras({"Conceptos": [{"Codigo": "VEN"}]}) is False,
)
check(
    "(b) Conceptos = lista de strings, con COM -> elegible",
    _tiene_concepto_compras({"Conceptos": ["VEN", "com"]}) is True,  # case-insensitive
)
check(
    "(c) Concepto singular = 'COM' -> elegible",
    _tiene_concepto_compras({"Concepto": "COM"}) is True,
)
check(
    "(c) CodigoConcepto singular = 'VEN' -> NO elegible",
    _tiene_concepto_compras({"CodigoConcepto": "VEN"}) is False,
)
check(
    "Shape desconocido (dict vacío) -> fail-safe False, no explota",
    _tiene_concepto_compras({}) is False,
)
check(
    "Input no-dict (None) -> fail-safe False, no explota",
    _tiene_concepto_compras(None) is False,
)

print()
print("=" * 78)
print("2 -- _tasa_iva_compras: resolución + coerción + cache")
print("=" * 78)

bas = FakeBasClient(
    servicios=[], bienes=[],
    impuestos={"3": {"TasaIvaCompras": 21.0}, "9": {"TasaIvaCompras": "10.5"}, "sin_tasa": {}},
    posiciones={},
)
cache = {}
check("Resuelve tasa numérica real (21.0)", _tasa_iva_compras(bas, "3", 1, cache) == 21.0)
check("Coerciona string a float ('10.5' -> 10.5)", _tasa_iva_compras(bas, "9", 1, cache) == 10.5)
check("Sin campo TasaIvaCompras -> None, no explota", _tasa_iva_compras(bas, "sin_tasa", 1, cache) is None)
check("codigo_impuesto vacío -> None sin llamar a BAS", _tasa_iva_compras(bas, "", 1, cache) is None)
llamadas_antes = bas.llamadas_impuesto
_tasa_iva_compras(bas, "3", 1, cache)  # mismo código de nuevo
check(
    f"Cache evita una 2da llamada real a BAS para el mismo código ({llamadas_antes} llamadas, sigue en {bas.llamadas_impuesto})",
    bas.llamadas_impuesto == llamadas_antes,
)

print()
print("=" * 78)
print("3 -- _elegible_compras: cache entre ítems que comparten posición")
print("=" * 78)

bas2 = FakeBasClient(
    servicios=[], bienes=[],
    impuestos={},
    posiciones={"8": {"Conceptos": [{"Codigo": "COM"}]}, "5": {"Conceptos": [{"Codigo": "VEN"}]}},
)
cache2 = {}
check("Posición 8 (con COM) -> elegible", _elegible_compras(bas2, "8", cache2) is True)
check("Posición 5 (sin COM) -> NO elegible", _elegible_compras(bas2, "5", cache2) is False)
llamadas_antes2 = bas2.llamadas_posicion
_elegible_compras(bas2, "8", cache2)
_elegible_compras(bas2, "8", cache2)
check(
    f"3 ítems comparten posición 8 -> 1 sola llamada real a BAS ({llamadas_antes2} llamadas totales)",
    bas2.llamadas_posicion == llamadas_antes2,
)

print()
print("=" * 78)
print("4 -- sincronizar_bas_items: end-to-end con datos representativos")
print("=" * 78)

servicios = [
    {"Codigo": "Gs Gs 21%", "Descripcion": "Gastos Generales 21%", "Impuesto": "3", "PosicionContable": "8"},
    {"Codigo": "Vaj. 21%", "Descripcion": "Vajilla y Cocina 21%", "Impuesto": "1", "PosicionContable": "8"},
    {"Codigo": "VENTAS-Insumos", "Descripcion": "Solo ventas, sin COM", "Impuesto": "1", "PosicionContable": "5"},
    {"Descripcion": "Ítem corrupto sin Codigo"},  # debe saltearse, no romper el resto
]
bienes = [
    {"Codigo": "Bien001", "Descripcion": "Un bien elegible", "Impuesto": "1", "PosicionContable": "8"},
]
impuestos = {"3": {"TasaIvaCompras": 21.0}, "1": {"TasaIvaCompras": 21.0}}
posiciones = {
    "8": {"Conceptos": [{"Codigo": "COM"}, {"Codigo": "VEN"}]},
    "5": {"Conceptos": [{"Codigo": "VEN"}]},
}

bas3 = FakeBasClient(servicios, bienes, impuestos, posiciones)
# Fila previa en PocketBase de un ítem que YA NO viene en el catálogo de
# esta corrida -- debe quedar marcado activo=false (soft-delete).
pb3 = FakePbClient(filas_previas=[
    {"codigo": "Descontinuado 21%", "activo": True, "elegible_compras": True},
    {"codigo": "Gs Gs 21%", "activo": True, "elegible_compras": True},  # este SÍ sigue viniendo -> "actualizado"
])

resumen = sincronizar_bas_items(bas3, pb3, empresa=1)
print(f"  resumen: {resumen}")

check("total_bas cuenta los 4 servicios + 1 bien (incluye el corrupto, se filtra después)", resumen["total_bas"] == 5)
check("El ítem sin 'Codigo' se salteó (no se cuenta como nuevo/actualizado)", resumen["nuevos"] + resumen["actualizados"] == 4)
check("'Gs Gs 21%' ya existía en PocketBase -> contó como actualizado, no nuevo", resumen["actualizados"] >= 1)
check("3 de los 4 ítems válidos son elegibles (posición 8), 1 no (posición 5)", resumen["elegibles"] == 3)
check("'Descontinuado 21%' (no vino en esta corrida) quedó marcado inactivo", resumen["inactivos"] == 1)

fila_descontinuado = pb3.get_bas_item("Descontinuado 21%")
check("Verificado en PocketBase: activo=False, NO se borró la fila", fila_descontinuado is not None and fila_descontinuado["activo"] is False)

fila_gsgs = pb3.get_bas_item("Gs Gs 21%")
check("'Gs Gs 21%': elegible_compras=True, tasa_iva_compras=21.0, tipo=servicio", (
    fila_gsgs["elegible_compras"] is True
    and fila_gsgs["tasa_iva_compras"] == 21.0
    and fila_gsgs["tipo"] == "servicio"
))

fila_bien = pb3.get_bas_item("Bien001")
check("'Bien001' quedó con tipo=bien (no 'servicio')", fila_bien is not None and fila_bien["tipo"] == "bien")

fila_ventas = pb3.get_bas_item("VENTAS-Insumos")
check("'VENTAS-Insumos' (posición sin COM) quedó elegible_compras=False", fila_ventas is not None and fila_ventas["elegible_compras"] is False)

# Constraint explícito del alcance: el sync es 100% solo-lectura contra BAS.
metodos_usados = set(bas3.metodos_llamados)
check(
    f"Solo se llamaron los 4 métodos de lectura esperados (llamados: {sorted(metodos_usados)})",
    metodos_usados <= {"listar_servicios", "listar_bienes", "obtener_impuesto", "obtener_posicion_contable"},
)
check(
    "Ninguno de los métodos de escritura prohibidos fue invocado",
    metodos_usados.isdisjoint(METODOS_ESCRITURA_BAS_PROHIBIDOS),
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
