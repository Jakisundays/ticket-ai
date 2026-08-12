"""
Test OFFLINE (sin red, sin BAS, sin PocketBase real) para P0-B: eliminar la
creación/reparación automática de proveedores en BAS.

Por qué no se importa routes/process_invoice_google_2.py directo: ese módulo
instancia `orchestrator = InvoiceOrchestrator(...)` a nivel de módulo, cuyo
__init__ llama `asyncio.create_task(self.worker())` -- requiere un event loop
corriendo solo para poder importarlo, y además intenta hablar con
PocketBase/BAS reales al arrancar (mismo problema de testabilidad ya
documentado en scripts/test_offline_importe_total_linea.py).

A diferencia de ese test anterior (que reconstruye la lógica a mano), acá se
extrae el método REAL `InvoiceOrchestrator._buscar_proveedor_bas` del AST del
archivo fuente y se ejecuta tal cual está hoy en el repo (vía exec de su
propio source segment) -- no es una reimplementación paralela. Esto es
importante porque la garantía que pide el alcance ("un proveedor inexistente
jamás dispara un POST/PUT de creación o modificación") tiene que probarse
sobre el código que realmente corre, no sobre una copia que podría divergir.

Cubre:
  1. Regresión estructural: el método viejo `_obtener_o_verificar_proveedor_bas`
     ya no existe; el nuevo `_buscar_proveedor_bas` sí, y su cuerpo fuente no
     menciona textualmente ninguna de las 4 funciones de escritura de BAS.
  2. Prueba mecánica (la real garantía de rule 8): se ejecuta la función
     extraída contra dobles (Fake*) que SOLO implementan los métodos de
     lectura legítimos -- cualquier intento de llamar crear_proveedor/
     actualizar_proveedor/asegurar_cuenta_corriente_proveedor/
     verificar_o_dar_de_alta_proveedor tira AttributeError (no hay forma de
     que pase en silencio). Se prueban los 4 escenarios reales: CUIT
     inexistente (+ reintento), CUIT vacío, CUIT existente (+ reintento),
     y hit de cache de PocketBase (2do nivel).
  3. Regresión sobre utils/bas.py: siguen existiendo exactamente los mismos
     2 call sites de escritura a /api/Proveedores (POST en crear_proveedor,
     PUT en actualizar_proveedor) -- si aparece un tercero, este test no lo
     cubre y hay que extenderlo. Las 4 funciones de alta/reparación de
     proveedor tienen su docstring marcado DEPRECATED.
  4. Regresión sobre el flujo de estados: `procesar_factura_en_bas` deja
     bas_registration_status="awaiting_provider_match" cuando no encuentra
     el proveedor (nunca "listo para registrar"), y
     "awaiting_service_selection" cuando sí lo encuentra.

Uso: python3 scripts/test_offline_buscar_proveedor_bas.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import ast
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR_FILE = REPO_ROOT / "routes" / "process_invoice_google_2.py"
BAS_FILE = REPO_ROOT / "utils" / "bas.py"

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


FUNCIONES_ESCRITURA_PROVEEDOR = (
    "crear_proveedor(",
    "actualizar_proveedor(",
    "asegurar_cuenta_corriente_proveedor(",
    "verificar_o_dar_de_alta_proveedor(",
)

# ============================================================================
# Parte 1 -- Extracción AST de InvoiceOrchestrator._buscar_proveedor_bas
# ============================================================================
print("=" * 78)
print("Parte 1 -- Extracción AST de InvoiceOrchestrator._buscar_proveedor_bas")
print("=" * 78)

codigo_orchestrator = ORCHESTRATOR_FILE.read_text(encoding="utf-8")
arbol = ast.parse(codigo_orchestrator, filename=str(ORCHESTRATOR_FILE))

clase_orchestrator = next(
    (n for n in ast.walk(arbol) if isinstance(n, ast.ClassDef) and n.name == "InvoiceOrchestrator"),
    None,
)
check("Clase InvoiceOrchestrator encontrada en el AST", clase_orchestrator is not None)

metodo_nuevo_nodo = next(
    (
        n
        for n in ast.walk(clase_orchestrator)
        if isinstance(n, ast.FunctionDef) and n.name == "_buscar_proveedor_bas"
    ),
    None,
) if clase_orchestrator else None
check("Método '_buscar_proveedor_bas' existe dentro de InvoiceOrchestrator", metodo_nuevo_nodo is not None)

metodo_viejo_nodo = next(
    (
        n
        for n in ast.walk(clase_orchestrator)
        if isinstance(n, ast.FunctionDef) and n.name == "_obtener_o_verificar_proveedor_bas"
    ),
    None,
) if clase_orchestrator else None
check(
    "Método viejo '_obtener_o_verificar_proveedor_bas' ya NO existe (reemplazado, no coexiste)",
    metodo_viejo_nodo is None,
)

fuente_metodo = ast.get_source_segment(codigo_orchestrator, metodo_nuevo_nodo) if metodo_nuevo_nodo else None
check("Se extrajo el source segment real del método", bool(fuente_metodo))

for funcion_prohibida in FUNCIONES_ESCRITURA_PROVEEDOR:
    check(
        f"El cuerpo real de _buscar_proveedor_bas NO invoca '{funcion_prohibida}'",
        fuente_metodo is not None and funcion_prohibida not in fuente_metodo,
    )
check(
    "El cuerpo real de _buscar_proveedor_bas SÍ invoca 'buscar_proveedor_por_cuit(' (solo lectura)",
    fuente_metodo is not None and "buscar_proveedor_por_cuit(" in fuente_metodo,
)


class _FakeAppLogger:
    """El método solo usa app_logger.warning -- alcanza con un doble mudo."""

    def __init__(self):
        self.warnings = []

    def warning(self, msg):
        self.warnings.append(msg)


_buscar_proveedor_bas_real = None
if fuente_metodo:
    namespace = {"app_logger": _FakeAppLogger()}
    exec(compile(textwrap.dedent(fuente_metodo), filename="<_buscar_proveedor_bas extraído>", mode="exec"), namespace)
    _buscar_proveedor_bas_real = namespace.get("_buscar_proveedor_bas")
check("La función extraída (código real) se pudo compilar y ejecutar vía exec", callable(_buscar_proveedor_bas_real))


# ============================================================================
# Parte 2 -- Prueba mecánica: un CUIT inexistente jamás dispara escritura
# ============================================================================
print()
print("=" * 78)
print("Parte 2 -- Prueba mecánica contra la función real extraída")
print("=" * 78)

METODOS_ESCRITURA_BAS_PROHIBIDOS = {
    "crear_proveedor",
    "actualizar_proveedor",
    "asegurar_cuenta_corriente_proveedor",
    "verificar_o_dar_de_alta_proveedor",
    "crear_comprobante_compra",
    "crear_orden_de_pago",
}


class FakeBasClient:
    """Doble de BasClient: SOLO implementa buscar_proveedor_por_cuit
    (lectura). Cualquier otro método -- en particular los 4 de escritura --
    no existe acá, así que llamarlo tira AttributeError: no hay forma de que
    una creación/modificación pase en silencio."""

    def __init__(self, proveedores_por_cuit):
        self._proveedores = proveedores_por_cuit
        self.metodos_llamados = []
        self.llamadas_busqueda = 0

    def buscar_proveedor_por_cuit(self, cuit):
        self.metodos_llamados.append("buscar_proveedor_por_cuit")
        self.llamadas_busqueda += 1
        return self._proveedores.get(cuit)


class FakePbClient:
    """Doble del cache propio de Invoicy en PocketBase (colección
    bas_providers) -- distinto del maestro de BAS. get_provider_cache /
    set_provider_cache son legítimos acá."""

    def __init__(self, cache_previo=None):
        self._cache = dict(cache_previo or {})
        self.llamadas_get = 0
        self.llamadas_set = 0

    def get_provider_cache(self, cuit):
        self.llamadas_get += 1
        return self._cache.get(cuit)

    def set_provider_cache(self, cuit, proveedor):
        self.llamadas_set += 1
        registro = dict(proveedor)
        registro["id"] = f"pb_{cuit}"
        self._cache[cuit] = registro
        return registro


class FakeSelf:
    def __init__(self, bas_client, pb_client):
        self._bas_client = bas_client
        self._pb_client = pb_client
        self._proveedores_bas_cache = {}


if _buscar_proveedor_bas_real is not None:
    # --- Escenario A: CUIT que NO existe en BAS -----------------------------
    bas_a = FakeBasClient(proveedores_por_cuit={})
    pb_a = FakePbClient()
    self_a = FakeSelf(bas_a, pb_a)

    resultado_a1 = _buscar_proveedor_bas_real(self_a, "20111111111", "Proveedor Fantasma SA")
    check("Escenario A (CUIT inexistente) -> devuelve None", resultado_a1 is None)
    check("Escenario A -> buscar_proveedor_por_cuit se llamó exactamente 1 vez", bas_a.llamadas_busqueda == 1)

    resultado_a2 = _buscar_proveedor_bas_real(self_a, "20111111111", "Proveedor Fantasma SA")
    check("Escenario A, reintento (misma factura reprocesada) -> sigue None", resultado_a2 is None)
    check(
        f"Escenario A, reintento -> NO volvió a llamar a BAS (cache negativo en memoria; total: {bas_a.llamadas_busqueda})",
        bas_a.llamadas_busqueda == 1,
    )
    check(
        f"Escenario A -- únicos métodos llamados sobre BasClient: {sorted(set(bas_a.metodos_llamados))}",
        set(bas_a.metodos_llamados) <= {"buscar_proveedor_por_cuit"},
    )
    check(
        "Escenario A -- CERO métodos de escritura prohibidos invocados sobre BasClient",
        set(bas_a.metodos_llamados).isdisjoint(METODOS_ESCRITURA_BAS_PROHIBIDOS),
    )
    check("Escenario A -- set_provider_cache nunca se llamó (nada real que cachear)", pb_a.llamadas_set == 0)

    # --- Escenario B: CUIT vacío --------------------------------------------
    bas_b = FakeBasClient(proveedores_por_cuit={})
    pb_b = FakePbClient()
    self_b = FakeSelf(bas_b, pb_b)
    resultado_b = _buscar_proveedor_bas_real(self_b, "", "")
    check(
        "Escenario B (CUIT vacío) -> None sin tocar BAS ni PocketBase",
        resultado_b is None and bas_b.llamadas_busqueda == 0 and pb_b.llamadas_get == 0,
    )

    # --- Escenario C: CUIT que SÍ existe en BAS ------------------------------
    proveedor_real = {"Codigo": "PROV001", "RazonSocial": "Proveedor Real SA"}
    bas_c = FakeBasClient(proveedores_por_cuit={"20222222222": proveedor_real})
    pb_c = FakePbClient()
    self_c = FakeSelf(bas_c, pb_c)

    resultado_c = _buscar_proveedor_bas_real(self_c, "20222222222", "Proveedor Real SA")
    check(
        "Escenario C (CUIT existente) -> devuelve el proveedor real de BAS",
        resultado_c is not None and resultado_c.get("Codigo") == "PROV001",
    )
    check("Escenario C -> '_nuevo' es False (nunca hay alta automática)", resultado_c is not None and resultado_c.get("_nuevo") is False)
    check("Escenario C -> quedó '_pb_id' del cache de PocketBase", resultado_c is not None and resultado_c.get("_pb_id") == "pb_20222222222")
    check(
        f"Escenario C -- únicos métodos llamados sobre BasClient: {sorted(set(bas_c.metodos_llamados))}",
        set(bas_c.metodos_llamados) <= {"buscar_proveedor_por_cuit"},
    )
    check(
        "Escenario C -- CERO métodos de escritura prohibidos invocados sobre BasClient",
        set(bas_c.metodos_llamados).isdisjoint(METODOS_ESCRITURA_BAS_PROHIBIDOS),
    )

    llamadas_antes_c = bas_c.llamadas_busqueda
    _buscar_proveedor_bas_real(self_c, "20222222222", "Proveedor Real SA")
    check(
        f"Escenario C, reintento -> cache en memoria evita 2da llamada a BAS ({llamadas_antes_c} llamadas totales)",
        bas_c.llamadas_busqueda == llamadas_antes_c,
    )

    # --- Escenario D: cache de PocketBase (2do nivel) ya tiene el proveedor --
    bas_d = FakeBasClient(proveedores_por_cuit={"20333333333": {"Codigo": "PROV002"}})
    pb_d = FakePbClient(cache_previo={"20333333333": {"Codigo": "PROV002", "_nuevo": False, "_pb_id": "pb_existente"}})
    self_d = FakeSelf(bas_d, pb_d)

    resultado_d = _buscar_proveedor_bas_real(self_d, "20333333333", "")
    check(
        "Escenario D (hit de cache de PocketBase) -> devuelve el dict cacheado",
        resultado_d is not None and resultado_d.get("Codigo") == "PROV002",
    )
    check("Escenario D -> NUNCA llegó a llamar a BAS (ni siquiera de lectura)", bas_d.llamadas_busqueda == 0)
else:
    check("Parte 2 completa saltada -- no se pudo extraer la función real (ver Parte 1)", False)


# ============================================================================
# Parte 3 -- Regresión sobre utils/bas.py: superficie de escritura sin cambios
# ============================================================================
print()
print("=" * 78)
print("Parte 3 -- utils/bas.py: superficie de escritura a /api/Proveedores")
print("=" * 78)

codigo_bas = BAS_FILE.read_text(encoding="utf-8")

import re  # noqa: E402

llamadas_post_proveedores = re.findall(r'_request\(\s*"POST",\s*"/api/Proveedores"', codigo_bas)
llamadas_put_proveedores = re.findall(r'_request\(\s*"PUT",\s*f?"/api/Proveedores', codigo_bas)
check(
    f"Sigue existiendo exactamente 1 POST a /api/Proveedores (crear_proveedor) -- encontrados: {len(llamadas_post_proveedores)}",
    len(llamadas_post_proveedores) == 1,
)
check(
    f"Sigue existiendo exactamente 1 PUT a /api/Proveedores/... (actualizar_proveedor) -- encontrados: {len(llamadas_put_proveedores)}",
    len(llamadas_put_proveedores) == 1,
)

for nombre_funcion in (
    "crear_proveedor",
    "actualizar_proveedor",
    "asegurar_cuenta_corriente_proveedor",
    "verificar_o_dar_de_alta_proveedor",
):
    m = re.search(rf'def {nombre_funcion}\(.{{0,400}}?"""(.*?)"""', codigo_bas, re.S)
    check(
        f"'{nombre_funcion}': docstring contiene 'DEPRECATED'",
        m is not None and "DEPRECATED" in m.group(1),
    )


# ============================================================================
# Parte 4 -- Regresión sobre los estados del flujo (procesar_factura_en_bas)
# ============================================================================
print()
print("=" * 78)
print("Parte 4 -- Estados bas_registration_status en procesar_factura_en_bas")
print("=" * 78)

check(
    "'awaiting_provider_match' se setea cuando NO se encuentra el proveedor",
    bool(re.search(r'if proveedor is None:.*?bas_registration_status"\]\s*=\s*"awaiting_provider_match"', codigo_orchestrator, re.S)),
)
check(
    "'awaiting_service_selection' se setea cuando SÍ se encuentra el proveedor",
    '"bas_registration_status"] = "awaiting_service_selection"' in codigo_orchestrator,
)
check(
    "El dict 'resultado' inicializa bas_registration_status (no queda undefined si se corta antes)",
    '"bas_registration_status": None' in codigo_orchestrator,
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
