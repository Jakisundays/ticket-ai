"""
Test OFFLINE (sin red, sin BAS, sin PocketBase real) para P0-C: el endpoint
POST /invoices/{process_id}/recheck-provider (routes/process_invoice_google_2.py:
recheck_provider).

Mismo criterio que scripts/test_offline_registrar_comprobante_endpoint.py:
se extrae el código REAL vía AST (tanto el endpoint como
InvoiceOrchestrator._buscar_proveedor_bas, ya que el endpoint depende de él)
y se ejecuta contra dobles de prueba -- no una reimplementación paralela.

El caso que más le importa al usuario (y el que más vale la pena probar
mecánicamente, no solo leyendo el código) es el escenario D: un proveedor
que no existía queda cacheado como None EN MEMORIA (mismo objeto
`orchestrator._proveedores_bas_cache` que usaría el proceso real, sin
reinstanciar nada) -- se "da de alta" en el FakeBasClient (simula que
alguien lo creó a mano en BAS) -- y se llama a recheck_provider. Si el
endpoint no invalida el cache negativo antes de consultar, este test falla
(seguiría devolviendo None indefinidamente, exactamente el bug que P0-C
tiene que evitar).

Cubre:
  A. process_id inexistente -> 404.
  B. bas_registration_status ya "registered" -> corta ahí, CERO llamadas a
     BAS (ni siquiera invalida el cache).
  C. Proveedor no existe (primera consulta) -> encontrado=False,
     bas_registration_status sigue "awaiting_provider_match", CERO
     escrituras en PocketBase. Confirma que _buscar_proveedor_bas cacheó el
     None en memoria (post-condición necesaria para el escenario D).
  D. EL CASO CRÍTICO: cache ya tiene None para este CUIT (de una consulta
     previa, sin reiniciar nada) -- el proveedor "aparece" en BAS -- el
     recheck lo tiene que encontrar. Confirma bas_provider +
     bas_registration_status="awaiting_service_selection" persistidos, y
     que el cache en memoria quedó actualizado con el valor positivo.
  E. bas_registration_status ya "ready_to_register" -> el recheck encuentra
     el proveedor pero NO downgradea el estado (regla 8: no perder trabajo
     de selección de ítems ya hecho) -- sigue en "ready_to_register".
  F. CUIT vacío/faltante -> encontrado=False, sin explotar, sin llamar a BAS.
  G. Prueba mecánica: el doble de BasClient SOLO implementa
     buscar_proveedor_por_cuit (lectura) -- cualquier intento de crear/
     modificar un proveedor tira AttributeError. Se verifica en todos los
     escenarios de arriba.
  H. Prueba mecánica: el doble de PocketBaseClient NO implementa ningún
     método de invoice_items -- si el endpoint intentara tocar los ítems de
     la factura (violando la regla 8), este test lo pescaría con un
     AttributeError.

Uso: python3 scripts/test_offline_recheck_provider.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import ast
import asyncio
import sys
import textwrap
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Header, HTTPException  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE = REPO_ROOT / "routes" / "process_invoice_google_2.py"

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


# ============================================================================
# Extracción AST del código real
# ============================================================================
codigo_fuente = TARGET_FILE.read_text(encoding="utf-8")
arbol = ast.parse(codigo_fuente, filename=str(TARGET_FILE))


def _extraer_funcion_top_level(nombre):
    nodo = next(
        (n for n in arbol.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nombre),
        None,
    )
    return ast.get_source_segment(codigo_fuente, nodo) if nodo else None


def _extraer_metodo_de_clase(clase, metodo):
    nodo_clase = next((n for n in arbol.body if isinstance(n, ast.ClassDef) and n.name == clase), None)
    if nodo_clase is None:
        return None
    nodo_metodo = next(
        (n for n in ast.walk(nodo_clase) if isinstance(n, ast.FunctionDef) and n.name == metodo), None
    )
    return ast.get_source_segment(codigo_fuente, nodo_metodo) if nodo_metodo else None


fuente_buscar_proveedor = _extraer_metodo_de_clase("InvoiceOrchestrator", "_buscar_proveedor_bas")
fuente_endpoint = _extraer_funcion_top_level("recheck_provider")

check("Se extrajo 'InvoiceOrchestrator._buscar_proveedor_bas' del código fuente real", bool(fuente_buscar_proveedor))
check("Se extrajo 'recheck_provider' del código fuente real", bool(fuente_endpoint))

for token_prohibido in ("crear_proveedor(", "actualizar_proveedor(", "asegurar_cuenta_corriente_proveedor(", "verificar_o_dar_de_alta_proveedor(", "bulk_create_invoice_items", "get_invoice_items"):
    check(
        f"El endpoint NO contiene '{token_prohibido}' (nunca toca proveedores/ítems más allá de lo permitido)",
        fuente_endpoint is not None and token_prohibido not in fuente_endpoint,
    )
check(
    "El endpoint invalida el cache en memoria (_proveedores_bas_cache.pop) ANTES de re-buscar",
    fuente_endpoint is not None and "_proveedores_bas_cache.pop" in fuente_endpoint,
)

if FALLOS:
    print("\nNo se pudo extraer el código real -- abortando.")
    sys.exit(1)


# ============================================================================
# Namespace + dobles de prueba
# ============================================================================
class _FakeAppLogger:
    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


namespace = {
    "Header": Header,
    "Optional": Optional,
    "HTTPException": HTTPException,
    "app_logger": _FakeAppLogger(),
    "_verificar_secreto_invoicy": lambda secret: None,
}

exec(compile(textwrap.dedent(fuente_buscar_proveedor), "<_buscar_proveedor_bas real>", "exec"), namespace)
_buscar_proveedor_bas_real = namespace["_buscar_proveedor_bas"]

exec(compile(textwrap.dedent(fuente_endpoint), "<recheck_provider real>", "exec"), namespace)
recheck_provider = namespace["recheck_provider"]
check("La función extraída (código real) se pudo compilar y ejecutar vía exec", callable(recheck_provider))


class FakeBasClient:
    """SOLO implementa buscar_proveedor_por_cuit (lectura). `proveedores`
    es un dict mutable -- simula "alguien lo dio de alta en BAS a mano"
    cambiándolo A MITAD del test, sin tocar el resto del estado."""

    def __init__(self, proveedores=None):
        self.proveedores = proveedores or {}
        self.metodos_llamados = []

    def buscar_proveedor_por_cuit(self, cuit):
        self.metodos_llamados.append("buscar_proveedor_por_cuit")
        return self.proveedores.get(cuit)


class FakePbClient:
    """NO implementa get_invoice_items/bulk_create_invoice_items a
    propósito -- si el endpoint los llamara, AttributeError (prueba
    mecánica de la regla 8)."""

    def __init__(self, invoice, cache_providers=None):
        self._invoice = dict(invoice)
        self._cache = dict(cache_providers or {})
        self.upserts_invoice = []
        self.llamadas_get_cache = []
        self.llamadas_set_cache = []

    def get_invoice_by_process_id(self, pid):
        return dict(self._invoice) if self._invoice.get("process_id") == pid else None

    def get_provider_cache(self, cuit):
        self.llamadas_get_cache.append(cuit)
        return self._cache.get(cuit)

    def set_provider_cache(self, cuit, proveedor):
        self.llamadas_set_cache.append(cuit)
        registro = dict(proveedor)
        registro["id"] = f"pb_{cuit}"
        self._cache[cuit] = registro
        return registro

    def upsert_invoice(self, data):
        self.upserts_invoice.append(dict(data))
        self._invoice.update({k: v for k, v in data.items() if k != "process_id"})
        return dict(self._invoice)


class OrchestratorReal:
    """`_proveedores_bas_cache` es un dict real y PERSISTENTE -- el mismo
    objeto se reusa entre llamadas dentro de un mismo test, tal como pasa
    con el singleton real de producción. Es justamente ESTE objeto el que
    recheck_provider tiene que invalidar."""

    def __init__(self, pb_client, bas_client):
        self._pb_client = pb_client
        self._bas_client = bas_client
        self._proveedores_bas_cache = {}

    def _buscar_proveedor_bas(self, cuit, razon_social=""):
        return _buscar_proveedor_bas_real(self, cuit, razon_social)


def correr(orchestrator, process_id):
    namespace["orchestrator"] = orchestrator
    return asyncio.run(recheck_provider(process_id=process_id, x_invoicy_secret="test"))


INVOICE_BASE = {
    "id": "pb-invoice-1",
    "process_id": "proc-recheck-1",
    "emisor_cuit": "20111111111",
    "emisor_nombre": "Proveedor Nuevo SA",
    "bas_registration_status": "awaiting_provider_match",
}


print("=" * 78)
print("A -- process_id inexistente -> 404")
print("=" * 78)

pb_a = FakePbClient({"process_id": "otro-distinto"})
orch_a = OrchestratorReal(pb_a, FakeBasClient())
try:
    correr(orch_a, "proc-recheck-1")
    check("process_id inexistente -> debería haber lanzado HTTPException", False)
except HTTPException as e:
    check("process_id inexistente -> 404", e.status_code == 404)


print()
print("=" * 78)
print("B -- ya 'registered' -> corta ahí, cero llamadas a BAS")
print("=" * 78)

pb_b = FakePbClient(dict(INVOICE_BASE, bas_registration_status="registered"))
bas_b = FakeBasClient(proveedores={"20111111111": {"Codigo": "PROVNEW"}})
orch_b = OrchestratorReal(pb_b, bas_b)
resultado_b = correr(orch_b, "proc-recheck-1")
check("Ya registered -> already_resolved=True", resultado_b.get("already_resolved") is True)
check("Ya registered -> NUNCA llamó a buscar_proveedor_por_cuit", len(bas_b.metodos_llamados) == 0)
check("Ya registered -> NUNCA tocó upsert_invoice", len(pb_b.upserts_invoice) == 0)


print()
print("=" * 78)
print("C -- proveedor no existe (primera consulta) -> awaiting_provider_match, cachea None")
print("=" * 78)

pb_c = FakePbClient(dict(INVOICE_BASE))
bas_c = FakeBasClient(proveedores={})  # no existe
orch_c = OrchestratorReal(pb_c, bas_c)
resultado_c = correr(orch_c, "proc-recheck-1")
check("Proveedor no existe -> encontrado=False", resultado_c.get("encontrado") is False)
check("Proveedor no existe -> bas_registration_status sigue 'awaiting_provider_match'", resultado_c.get("bas_registration_status") == "awaiting_provider_match")
check("Proveedor no existe -> CERO escrituras en PocketBase (invoices)", len(pb_c.upserts_invoice) == 0)
check(
    "Proveedor no existe -> quedó cacheado como None EN MEMORIA (post-condición para el escenario D)",
    "20111111111" in orch_c._proveedores_bas_cache and orch_c._proveedores_bas_cache["20111111111"] is None,
)


print()
print("=" * 78)
print("D -- EL CASO CRÍTICO: cache negativo previo + proveedor recién dado de alta en BAS")
print("=" * 78)

pb_d = FakePbClient(dict(INVOICE_BASE))
bas_d = FakeBasClient(proveedores={})  # todavía no existe
orch_d = OrchestratorReal(pb_d, bas_d)

# Paso 1: una consulta previa (ej. la ingesta automática) ya dejó el CUIT
# cacheado como None -- MISMO objeto orchestrator, sin reiniciar nada.
resultado_previo = orch_d._buscar_proveedor_bas("20111111111", "Proveedor Nuevo SA")
check("Paso 1 -- consulta previa (simula la ingesta automática) -> no lo encuentra", resultado_previo is None)
check("Paso 1 -- cache en memoria tiene None para este CUIT", orch_d._proveedores_bas_cache.get("20111111111") is None and "20111111111" in orch_d._proveedores_bas_cache)

# Paso 2: "alguien lo da de alta a mano en BAS" -- se muta el FakeBasClient,
# NADA MÁS. El objeto orchestrator (y su cache) sigue siendo el mismo.
bas_d.proveedores["20111111111"] = {"Codigo": "PROVNEW", "RazonSocial": "Proveedor Nuevo SA"}

# Paso 3: el usuario presiona "Volver a buscar".
resultado_d = correr(orch_d, "proc-recheck-1")

check("EL CASO CRÍTICO -> encontrado=True (sin reiniciar el proceso)", resultado_d.get("encontrado") is True)
check("EL CASO CRÍTICO -> bas_registration_status='awaiting_service_selection'", resultado_d.get("bas_registration_status") == "awaiting_service_selection")
check("EL CASO CRÍTICO -> se persistió bas_provider", any(u.get("bas_provider") for u in pb_d.upserts_invoice))
check(
    "EL CASO CRÍTICO -> el cache EN MEMORIA quedó actualizado con el valor positivo (ya no None)",
    orch_d._proveedores_bas_cache.get("20111111111") is not None,
)
check(
    "EL CASO CRÍTICO -> se persistió también en el cache L2 de PocketBase (bas_providers)",
    "20111111111" in pb_d.llamadas_set_cache,
)


print()
print("=" * 78)
print("E -- ya 'ready_to_register' -> encuentra el proveedor pero NO downgradea el estado")
print("=" * 78)

pb_e = FakePbClient(dict(INVOICE_BASE, bas_registration_status="ready_to_register"))
bas_e = FakeBasClient(proveedores={"20111111111": {"Codigo": "PROVNEW"}})
orch_e = OrchestratorReal(pb_e, bas_e)
resultado_e = correr(orch_e, "proc-recheck-1")
check("ready_to_register -> encontrado=True", resultado_e.get("encontrado") is True)
check(
    "ready_to_register -> NO se downgradea a awaiting_service_selection (regla 8: no perder trabajo ya hecho)",
    resultado_e.get("bas_registration_status") == "ready_to_register",
)
check(
    "ready_to_register -> bas_provider SÍ se (re)confirma igual",
    any(u.get("bas_provider") for u in pb_e.upserts_invoice),
)
check(
    "ready_to_register -> el upsert NO incluyó bas_registration_status (no se tocó ese campo)",
    all("bas_registration_status" not in u for u in pb_e.upserts_invoice),
)


print()
print("=" * 78)
print("F -- CUIT vacío/faltante -> encontrado=False, sin explotar")
print("=" * 78)

pb_f = FakePbClient(dict(INVOICE_BASE, emisor_cuit=""))
bas_f = FakeBasClient(proveedores={})
orch_f = OrchestratorReal(pb_f, bas_f)
resultado_f = correr(orch_f, "proc-recheck-1")
check("CUIT vacío -> encontrado=False, no explota", resultado_f.get("encontrado") is False)
check("CUIT vacío -> nunca llamó a BAS", len(bas_f.metodos_llamados) == 0)


print()
print("=" * 78)
print("G -- prueba mecánica: solo se llamó buscar_proveedor_por_cuit en todos los escenarios con BAS")
print("=" * 78)

for nombre, bas in (("C", bas_c), ("D", bas_d), ("E", bas_e)):
    check(
        f"Escenario {nombre} -- únicos métodos llamados sobre BasClient: {sorted(set(bas.metodos_llamados))}",
        set(bas.metodos_llamados) <= {"buscar_proveedor_por_cuit"},
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
