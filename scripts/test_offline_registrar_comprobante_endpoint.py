"""
Test OFFLINE (sin red, sin BAS, sin PocketBase real) para P0-F: el endpoint
POST /invoices/{process_id}/register-comprobante (routes/process_invoice_google_2.py:
registrar_comprobante) -- el registro REAL de ComprobanteCompra sin Orden de
Pago.

Por qué no se importa el módulo directo: mismo problema ya documentado en
scripts/test_offline_importe_total_linea.py (InvoiceOrchestrator.__init__
hace asyncio.create_task a nivel de módulo). Acá el problema es mayor
todavía: el endpoint depende de ~15 nombres de módulo (orchestrator,
constantes de BAS_*, helpers). Se extrae la función real vía AST (mismo
criterio que test_offline_buscar_proveedor_bas.py) y se ejecuta en un
namespace con:
  - Dependencias REALES cuando son puras y ya están probadas por su cuenta
    (resolver_codigo_item, normalizar_numero_comprobante, constantes de
    utils/bas_config.py) -- así el test ejercita la integración real, no
    una reimplementación paralela.
  - Dobles de prueba (Fake*) para todo lo que toca red/estado (orchestrator,
    PocketBase, BasClient) -- algunos configurados para EXPLOTAR si se
    llaman cuando no deberían (prueba mecánica de que un gate cortó el flujo
    antes de tiempo, no solo que el resultado final "se ve bien").

Cubre, en orden de precedencia real del endpoint:
  A. review_status != "confirmed" -> 409, CERO llamadas a proveedor/items/BAS.
  B. bas_registration_status == "registered" -> idempotente, CERO llamadas
     a proveedor/items/BAS (ni siquiera se re-valida).
  C. Proveedor no resuelto -> 422 + bas_registration_status="awaiting_provider_match",
     CERO llamadas a registrar_comprobante_compra_idempotente.
  D. CodigoItem inválido en algún ítem -> 422 + bas_registration_status=
     "awaiting_service_selection", CERO llamadas a
     registrar_comprobante_compra_idempotente (ningún payload con un
     CodigoItem sin validar llega nunca a intentar escribirse).
  E. Falla validar_factura_antes_de_pago_real -> 422, bas_registration_status
     SIN TOCAR (mismo criterio que crear_orden_pago), CERO llamadas a BAS.
  F. Camino feliz -> se llama a registrar_comprobante_compra_idempotente
     con dry_run=False EXPLÍCITO (el default del método es True -- un olvido
     acá degradaría en silencio a "no hace nada real" sin ningún error, el
     peor tipo de bug posible para este endpoint) y comprobante="MA";
     persiste bas_registration_status="registered" +
     bas_id_transaccion/comprobante_registrado_at/comprobante_total_registrado
     -- este último SIEMPRE del payload enviado, nunca de la respuesta de
     BAS (confirmada real, 2026-08-12: ningún endpoint de consulta de BAS
     devuelve el Total del comprobante -- ver docstring del endpoint real).
  F2. Cuando el comprobante YA existía (encontrado por ConsultaComprobantesExternos,
      sin POST esta vez), comprobante_total_registrado se deja SIN TOCAR --
      no hay forma de saber con qué Total quedó registrado en un intento
      anterior, así que no se asume que coincide con el de ahora.
  G. registrar_comprobante_compra_idempotente lanza BasApiError -> NO se
     relanza como HTTP error: responde 200-shape con success=False +
     bas_registration_status="register_failed" + bas_last_error poblado
     (mismo patrón que crear_orden_pago, para no perder la persistencia de
     auditoría aunque BAS haya fallado).

Agregado en la revisión final de P0-G (2026-08-12) -- hallazgo crítico:
`resolver_codigo_item` soporta `override_codigo_item` desde P0-E, pero
ningún call site real lo pasaba, así que un ítem elegido a mano en el
selector del dashboard (`invoice_items.bas_codigo_item`) no tenía ningún
efecto sobre lo que se registraba en BAS. Escenarios I-M cubren el fix:
  I. Override válido DISTINTO del candidato automático (también válido) ->
     BAS recibe el override, no el automático (el humano manda).
  J. Candidato automático inválido + override válido -> ya NO bloquea
     (antes del fix, esto era exactamente el bug: D de arriba bloqueaba
     este caso aunque el humano ya lo hubiera corregido).
  K. Override inválido, aunque el automático SÍ sea válido -> bloquea
     igual, SIN fallback silencioso al automático (regla 1 del resolver).
  L. Override vacío ("") -> cae al comportamiento automático de siempre
     (mismo camino que F, con el campo explícito para dejar la regla
     documentada en el test).
  M. Arranca en bas_registration_status="awaiting_service_selection" (no
     "ready_to_register") + override válido -> llega a "registered" igual:
     el endpoint no gatea sobre el estado previo (salvo el atajo B de
     "registered"), revalida todo en fresco en cada llamada.

Uso: python3 scripts/test_offline_registrar_comprobante_endpoint.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import ast
import asyncio
import contextlib
import datetime
import sys
import types
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Header, HTTPException  # noqa: E402 -- reales, puros

import utils.bas_config as bas_config  # noqa: E402
from utils.bas import BasApiError  # noqa: E402 -- real, puro
from utils.bas_config import (  # noqa: E402 -- reales, constantes puras
    BAS_CAJA,
    BAS_CENTRO_APROPIACION_SD,
    BAS_DEPOSITO,
    BAS_EMITIDO_POR_CAE,
    BAS_EMPRESA,
    BAS_METODO_PAGO_CTA_CTE,
    BAS_PREFIJO_TALONARIO_MA,
    BAS_SUCURSAL,
    BAS_TIPO_ENTREGA_SIN_STOCK,
)
from utils.bas_item_resolver import resolver_codigo_item  # noqa: E402 -- real
from utils.validaciones_pre_bas import normalizar_numero_comprobante  # noqa: E402 -- real

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE = REPO_ROOT / "routes" / "process_invoice_google_2.py"

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


# Cache determinístico de bas_config (mismo criterio que
# test_offline_resolver_codigo_item.py) -- resolver_codigo_item real no debe
# intentar red.
bas_config._cache_categoria_map["datos"] = {
    "Gastos Generales": {21: "Gs Gs 21%"},
}
import time as _time  # noqa: E402

bas_config._cache_categoria_map["actualizado_en"] = _time.time()


# ============================================================================
# Extracción AST de las 2 funciones reales necesarias (top-level, sin decorador
# relevante para _extraer_prefijo..., con decorador @router.post para el
# endpoint -- ver namespace["router"] abajo, que lo neutraliza sea cual sea
# la forma exacta del segmento extraído).
# ============================================================================
codigo_fuente = TARGET_FILE.read_text(encoding="utf-8")
arbol = ast.parse(codigo_fuente, filename=str(TARGET_FILE))


def _extraer_funcion_top_level(nombre):
    nodo = next(
        (
            n
            for n in arbol.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nombre
        ),
        None,
    )
    if nodo is None:
        return None
    return ast.get_source_segment(codigo_fuente, nodo)


fuente_extraer_prefijo = _extraer_funcion_top_level("_extraer_prefijo_numero_comprobante_externo")
fuente_endpoint = _extraer_funcion_top_level("registrar_comprobante")

check("Se extrajo '_extraer_prefijo_numero_comprobante_externo' del AST", bool(fuente_extraer_prefijo))
check("Se extrajo 'registrar_comprobante' del AST", bool(fuente_endpoint))

# Regresión estructural directa sobre el source real (además de la prueba
# mecánica de más abajo): el literal dry_run=False tiene que estar en la
# ÚNICA llamada a registrar_comprobante_compra_idempotente de este endpoint
# -- el default del método es True.
check(
    "El endpoint pasa dry_run=False EXPLÍCITO a registrar_comprobante_compra_idempotente",
    fuente_endpoint is not None and "dry_run=False" in fuente_endpoint,
)
# Nota: "metodo_pago" SÍ aparece en el docstring (prosa que explica qué se
# excluyó a propósito) -- lo que no debe aparecer es una referencia de
# CÓDIGO real (body.metodo_pago, la clave "pagos" del payload de BAS, o una
# llamada a las funciones de OP).
for token_prohibido in ("body.metodo_pago", "\"pagos\"", "crear_orden_de_pago(", "aplicar_comprobantes("):
    check(
        f"El endpoint NO contiene '{token_prohibido}' (sin OP/pago embebido)",
        fuente_endpoint is not None and token_prohibido not in fuente_endpoint,
    )


# ============================================================================
# Namespace de ejecución -- reales cuando son puros, dobles cuando tocan red/estado.
# ============================================================================
class _FakeAppLogger:
    def __init__(self):
        self.mensajes = []

    def info(self, msg):
        self.mensajes.append(("info", msg))

    def warning(self, msg):
        self.mensajes.append(("warning", msg))

    def error(self, msg):
        self.mensajes.append(("error", msg))


class FakePbClient:
    """Doble de PocketBaseClient. `bas_items_validos` alimenta get_bas_item
    (usado por el resolver_codigo_item REAL) -- así el test ejercita la
    integración real de principio a fin, no una reimplementación."""

    def __init__(self, invoice, items, bas_items_validos=None, status_previo="existe"):
        self._invoice = dict(invoice)
        self._items = items
        self._bas_items = bas_items_validos or {}
        self._status_previo = status_previo  # "existe" | None -- simula si ya había bas_processing_status
        self.upserts_invoice = []
        self.upserts_status = []
        self.llamadas_get_bas_item = []
        self.llamadas_get_items = 0

    def get_invoice_by_process_id(self, pid):
        return dict(self._invoice)

    def get_invoice_items(self, invoice_id):
        self.llamadas_get_items += 1
        return self._items

    def get_bas_processing_status(self, pid):
        if self._status_previo is None:
            return None
        return {"process_id": pid, "_nota": "estado previo simulado"}

    def get_bas_item(self, codigo):
        self.llamadas_get_bas_item.append(codigo)
        return self._bas_items.get(codigo)

    def upsert_invoice(self, data):
        self.upserts_invoice.append(dict(data))
        self._invoice.update({k: v for k, v in data.items() if k != "process_id"})
        return dict(self._invoice)

    def upsert_bas_processing_status(self, process_id, **campos):
        self.upserts_status.append(dict(campos))
        return dict(campos)


class _ExplotaSiSeLlama:
    """Doble que tira AssertionError ante CUALQUIER llamada -- prueba
    mecánica de que un gate cortó el flujo antes de llegar acá."""

    def __getattr__(self, nombre):
        def _explota(*a, **kw):
            raise AssertionError(f"No debería haberse llamado a '{nombre}' -- el gate no cortó el flujo")

        return _explota


class FakeOrchestrator:
    def __init__(self, pb_client, proveedor=None, bas_client=None):
        self._pb_client = pb_client
        self._proveedor = proveedor
        self._bas_client = bas_client if bas_client is not None else _ExplotaSiSeLlama()

    def _buscar_proveedor_bas(self, cuit, razon_social):
        if isinstance(self._proveedor, Exception):
            raise self._proveedor
        return self._proveedor


class FakeBasClient:
    def __init__(self, respuesta=None, excepcion=None):
        self._respuesta = respuesta
        self._excepcion = excepcion
        self.llamadas = []

    def registrar_comprobante_compra_idempotente(self, **kw):
        self.llamadas.append(kw)
        if self._excepcion is not None:
            raise self._excepcion
        return self._respuesta


namespace = {
    "router": types.SimpleNamespace(post=lambda *a, **kw: (lambda f: f)),
    "Header": Header,
    "Optional": Optional,
    "HTTPException": HTTPException,
    "BasApiError": BasApiError,
    "BAS_TIPO_ENTREGA_SIN_STOCK": BAS_TIPO_ENTREGA_SIN_STOCK,
    "BAS_CENTRO_APROPIACION_SD": BAS_CENTRO_APROPIACION_SD,
    "BAS_PREFIJO_TALONARIO_MA": BAS_PREFIJO_TALONARIO_MA,
    "BAS_EMITIDO_POR_CAE": BAS_EMITIDO_POR_CAE,
    "BAS_EMPRESA": BAS_EMPRESA,
    "BAS_SUCURSAL": BAS_SUCURSAL,
    "BAS_DEPOSITO": BAS_DEPOSITO,
    "BAS_CAJA": BAS_CAJA,
    "BAS_METODO_PAGO_CTA_CTE": BAS_METODO_PAGO_CTA_CTE,
    "resolver_codigo_item": resolver_codigo_item,
    "normalizar_numero_comprobante": normalizar_numero_comprobante,
    "datetime": datetime,
    "fecha_hoy_bas": lambda: datetime.date(2026, 8, 12),
    "app_logger": _FakeAppLogger(),
    "_lock_comprobante": lambda *a, **kw: contextlib.nullcontext(),
    "_verificar_secreto_invoicy": lambda secret: None,
}

exec(compile(fuente_extraer_prefijo, filename="<_extraer_prefijo extraído>", mode="exec"), namespace)

# validar_factura_antes_de_pago_real -- controlable por escenario vía esta
# lista mutable capturada por closure (namespace["validar_factura_antes_de_pago_real"]
# se reasigna directo entre escenarios, más simple).
namespace["validar_factura_antes_de_pago_real"] = lambda invoice, items: []

exec(compile(fuente_endpoint, filename="<registrar_comprobante extraído>", mode="exec"), namespace)
registrar_comprobante = namespace.get("registrar_comprobante")
check("La función extraída (código real) se pudo compilar y ejecutar vía exec", callable(registrar_comprobante))


def correr(orchestrator, validar_stub=None):
    namespace["orchestrator"] = orchestrator
    namespace["validar_factura_antes_de_pago_real"] = validar_stub or (lambda invoice, items: [])
    return asyncio.run(registrar_comprobante(process_id="proc-test-1", x_invoicy_secret="lo-que-sea"))


INVOICE_BASE = {
    "id": "pb-invoice-1",
    "review_status": "confirmed",
    "bas_registration_status": "ready_to_register",
    "emisor_cuit": "20111111111",
    "emisor_nombre": "Proveedor Test SA",
    "numero_comprobante": "00010-00001854",
    "fecha_emision": "2026-08-01",
    "cae": "12345678901234",
    "cae_vencimiento": "2026-09-01",
    "iva_alicuota": None,
    "moneda": "ARS",
}
ITEMS_BASE = [{"categoria": "Gastos Generales", "precio_total": 1000, "cantidad": 1, "precio_unitario": 1000}]
BAS_ITEMS_VALIDOS = {"Gs Gs 21%": {"activo": True, "elegible_compras": True}}


print("=" * 78)
print("A -- review_status != 'confirmed' -> 409, cero llamadas downstream")
print("=" * 78)

if registrar_comprobante is not None:
    invoice_a = dict(INVOICE_BASE, review_status="needs_review")
    pb_a = FakePbClient(invoice_a, ITEMS_BASE, BAS_ITEMS_VALIDOS)
    orch_a = FakeOrchestrator(pb_a, proveedor=_ExplotaSiSeLlama())
    try:
        correr(orch_a)
        check("review_status no confirmado -> debería haber lanzado HTTPException", False)
    except HTTPException as e:
        check("review_status no confirmado -> 409", e.status_code == 409)
    check("review_status no confirmado -> NUNCA llamó a get_invoice_items", pb_a.llamadas_get_items == 0)
    check("review_status no confirmado -> NUNCA tocó upsert_invoice", len(pb_a.upserts_invoice) == 0)


print()
print("=" * 78)
print("B -- bas_registration_status == 'registered' -> idempotente, cero llamadas downstream")
print("=" * 78)

if registrar_comprobante is not None:
    invoice_b = dict(INVOICE_BASE, bas_registration_status="registered")
    pb_b = FakePbClient(invoice_b, ITEMS_BASE, BAS_ITEMS_VALIDOS)
    orch_b = FakeOrchestrator(pb_b, proveedor=_ExplotaSiSeLlama())
    resultado_b = correr(orch_b)
    check("Ya registrado -> already_resolved=True", resultado_b.get("already_resolved") is True)
    check("Ya registrado -> NUNCA llamó a get_invoice_items (ni re-valida)", pb_b.llamadas_get_items == 0)
    check("Ya registrado -> NUNCA tocó upsert_invoice (no reescribe nada)", len(pb_b.upserts_invoice) == 0)


print()
print("=" * 78)
print("C -- proveedor no resuelto -> 422 + awaiting_provider_match")
print("=" * 78)

if registrar_comprobante is not None:
    pb_c = FakePbClient(dict(INVOICE_BASE), ITEMS_BASE, BAS_ITEMS_VALIDOS)
    bas_c = FakeBasClient()  # explota si se llama, vía assert de abajo
    orch_c = FakeOrchestrator(pb_c, proveedor=None, bas_client=bas_c)
    try:
        correr(orch_c)
        check("Proveedor no resuelto -> debería haber lanzado HTTPException", False)
    except HTTPException as e:
        check("Proveedor no resuelto -> 422", e.status_code == 422)
    check(
        "Proveedor no resuelto -> se persistió bas_registration_status='awaiting_provider_match'",
        any(u.get("bas_registration_status") == "awaiting_provider_match" for u in pb_c.upserts_invoice),
    )
    check("Proveedor no resuelto -> NUNCA llegó a llamar a registrar_comprobante_compra_idempotente", len(bas_c.llamadas) == 0)


print()
print("=" * 78)
print("D -- CodigoItem inválido -> 422 + awaiting_service_selection, sin llegar a BAS")
print("=" * 78)

if registrar_comprobante is not None:
    pb_d = FakePbClient(dict(INVOICE_BASE), ITEMS_BASE, bas_items_validos={})  # "Gs Gs 21%" NO está -> inválido
    bas_d = FakeBasClient()
    orch_d = FakeOrchestrator(pb_d, proveedor={"Codigo": "PROV001"}, bas_client=bas_d)
    try:
        correr(orch_d)
        check("CodigoItem inválido -> debería haber lanzado HTTPException", False)
    except HTTPException as e:
        check("CodigoItem inválido -> 422", e.status_code == 422)
    check(
        "CodigoItem inválido -> se persistió bas_registration_status='awaiting_service_selection'",
        any(u.get("bas_registration_status") == "awaiting_service_selection" for u in pb_d.upserts_invoice),
    )
    check("CodigoItem inválido -> NUNCA llegó a llamar a registrar_comprobante_compra_idempotente", len(bas_d.llamadas) == 0)
    check("CodigoItem inválido -> SÍ se consultó bas_items (el resolver real corrió de verdad)", len(pb_d.llamadas_get_bas_item) > 0)


print()
print("=" * 78)
print("E -- validar_factura_antes_de_pago_real falla -> 422, sin tocar bas_registration_status")
print("=" * 78)

if registrar_comprobante is not None:
    pb_e = FakePbClient(dict(INVOICE_BASE), ITEMS_BASE, BAS_ITEMS_VALIDOS)
    bas_e = FakeBasClient()
    orch_e = FakeOrchestrator(pb_e, proveedor={"Codigo": "PROV001"}, bas_client=bas_e)
    try:
        correr(orch_e, validar_stub=lambda invoice, items: ["El CAE no es válido."])
        check("Validación de datos falla -> debería haber lanzado HTTPException", False)
    except HTTPException as e:
        check("Validación de datos falla -> 422", e.status_code == 422)
    check("Validación de datos falla -> NO tocó bas_registration_status (mismo criterio que crear_orden_pago)", len(pb_e.upserts_invoice) == 0)
    check("Validación de datos falla -> NUNCA llegó a llamar a registrar_comprobante_compra_idempotente", len(bas_e.llamadas) == 0)


print()
print("=" * 78)
print("F -- camino feliz: dry_run=False explícito, persiste 'registered' + auditoría")
print("=" * 78)

if registrar_comprobante is not None:
    pb_f = FakePbClient(dict(INVOICE_BASE), ITEMS_BASE, BAS_ITEMS_VALIDOS)
    # Sin "Total" -- confirmado real (validación P0-F, 2026-08-12) contra el
    # swagger real de BAS: RespuestaConsultaComprobante NUNCA trae Total,
    # ni el 201 del POST ni ningún GET posterior. Si el código todavía
    # leyera comprobante_total_registrado desde acá, este test lo pescaría
    # (quedaría None en vez de 1000.0).
    respuesta_bas = {
        "comprobante": {"Prefijo": "00010", "Numero": 999, "Anulado": False},
        "ya_existia": False,
        "id_transaccion": 55555,
    }
    bas_f = FakeBasClient(respuesta=respuesta_bas)
    orch_f = FakeOrchestrator(pb_f, proveedor={"Codigo": "PROV001"}, bas_client=bas_f)
    resultado_f = correr(orch_f)

    check("Camino feliz -> success=True", resultado_f.get("success") is True)
    check("Camino feliz -> se llamó exactamente 1 vez a registrar_comprobante_compra_idempotente", len(bas_f.llamadas) == 1)
    check("Camino feliz -> dry_run=False EXPLÍCITO en la llamada real (el default del método es True)", bas_f.llamadas[0].get("dry_run") is False)
    check("Camino feliz -> comprobante='MA'", bas_f.llamadas[0].get("comprobante") == "MA")
    check(
        "Camino feliz -> se persistió bas_registration_status='registered'",
        any(u.get("bas_registration_status") == "registered" for u in pb_f.upserts_invoice),
    )
    status_persistido = pb_f.upserts_status[-1] if pb_f.upserts_status else {}
    check("Camino feliz -> bas_id_transaccion persistido = 55555", status_persistido.get("bas_id_transaccion") == 55555)
    check("Camino feliz -> comprobante_registrado_at persistido (no None)", status_persistido.get("comprobante_registrado_at") is not None)
    check(
        "Camino feliz -> comprobante_total_registrado = 1000.0 (del PAYLOAD enviado -- BAS no expone Total en su respuesta, confirmado real)",
        status_persistido.get("comprobante_total_registrado") == 1000.0,
    )
    check("Camino feliz -> comprobante_registrado=True", status_persistido.get("comprobante_registrado") is True)
    check("Camino feliz -> bas_last_error=None", status_persistido.get("bas_last_error") is None)
    check(
        "Camino feliz -> YA había bas_processing_status -> NO se pisó 'orden_pago_status' (campo legado)",
        "orden_pago_status" not in status_persistido,
    )


print()
print("=" * 78)
print("F2 -- ya_existia=True -> comprobante_total_registrado se deja SIN TOCAR (no se inventa)")
print("=" * 78)

if registrar_comprobante is not None:
    pb_f2 = FakePbClient(dict(INVOICE_BASE), ITEMS_BASE, BAS_ITEMS_VALIDOS)
    respuesta_bas_existente = {
        "comprobante": {"Prefijo": "00010", "Numero": 888, "Anulado": False},
        "ya_existia": True,
        "id_transaccion": None,
    }
    bas_f2 = FakeBasClient(respuesta=respuesta_bas_existente)
    orch_f2 = FakeOrchestrator(pb_f2, proveedor={"Codigo": "PROV001"}, bas_client=bas_f2)
    resultado_f2 = correr(orch_f2)

    check("ya_existia=True -> success=True igual (encontrarlo ya registrado también es éxito)", resultado_f2.get("success") is True)
    status_persistido_f2 = pb_f2.upserts_status[-1] if pb_f2.upserts_status else {}
    check(
        "ya_existia=True -> comprobante_total_registrado queda None (no hay forma de saber con qué Total quedó esa vez)",
        status_persistido_f2.get("comprobante_total_registrado") is None,
    )
    check("ya_existia=True -> bas_id_transaccion=None (no hubo POST esta vez)", status_persistido_f2.get("bas_id_transaccion") is None)


print()
print("=" * 78)
print("G -- BAS falla (BasApiError) -> success=False, 'register_failed', sin excepción HTTP")
print("=" * 78)

if registrar_comprobante is not None:
    pb_g = FakePbClient(dict(INVOICE_BASE), ITEMS_BASE, BAS_ITEMS_VALIDOS)
    bas_g = FakeBasClient(excepcion=BasApiError(409, "no coincide con la suma de los totales parciales", "/api/ComprobantesCompra"))
    orch_g = FakeOrchestrator(pb_g, proveedor={"Codigo": "PROV001"}, bas_client=bas_g)
    resultado_g = correr(orch_g)

    check("BAS falla -> NO lanza HTTPException (responde 200-shape, como crear_orden_pago)", resultado_g.get("success") is False)
    check("BAS falla -> error contiene el detalle de BasApiError", "no coincide" in (resultado_g.get("error") or ""))
    check(
        "BAS falla -> se persistió bas_registration_status='register_failed'",
        any(u.get("bas_registration_status") == "register_failed" for u in pb_g.upserts_invoice),
    )
    status_persistido_g = pb_g.upserts_status[-1] if pb_g.upserts_status else {}
    check("BAS falla -> comprobante_registrado=False", status_persistido_g.get("comprobante_registrado") is False)
    check("BAS falla -> bas_last_error poblado", bool(status_persistido_g.get("bas_last_error")))
    check("BAS falla -> comprobante_registrado_at=None (no hubo éxito real)", status_persistido_g.get("comprobante_registrado_at") is None)


print()
print("=" * 78)
print("H -- caso raro: sin bas_processing_status previo -> create defensivo")
print("=" * 78)

if registrar_comprobante is not None:
    pb_h = FakePbClient(dict(INVOICE_BASE), ITEMS_BASE, BAS_ITEMS_VALIDOS, status_previo=None)
    bas_h = FakeBasClient(respuesta={
        "comprobante": {"Prefijo": "00010", "Numero": 1000, "Total": 1000.0, "Anulado": False},
        "ya_existia": False,
        "id_transaccion": 66666,
    })
    orch_h = FakeOrchestrator(pb_h, proveedor={"Codigo": "PROV001"}, bas_client=bas_h)
    resultado_h = correr(orch_h)
    check("Sin bas_processing_status previo -> success=True igual (no rompe el registro real)", resultado_h.get("success") is True)
    status_persistido_h = pb_h.upserts_status[-1] if pb_h.upserts_status else {}
    check(
        "Sin bas_processing_status previo -> SÍ incluye orden_pago_status='pending' (satisface el required en el create)",
        status_persistido_h.get("orden_pago_status") == "pending",
    )


print()
print("=" * 78)
print("I -- override válido DISTINTO del automático (ambos válidos) -> BAS recibe el override")
print("=" * 78)

if registrar_comprobante is not None:
    items_i = [dict(ITEMS_BASE[0], bas_codigo_item="Gs Gs Manual")]
    bas_items_i = dict(BAS_ITEMS_VALIDOS, **{"Gs Gs Manual": {"activo": True, "elegible_compras": True}})
    pb_i = FakePbClient(dict(INVOICE_BASE), items_i, bas_items_i)
    bas_client_i = FakeBasClient(respuesta={
        "comprobante": {"Prefijo": "00010", "Numero": 111, "Anulado": False},
        "ya_existia": False,
        "id_transaccion": 11111,
    })
    orch_i = FakeOrchestrator(pb_i, proveedor={"Codigo": "PROV001"}, bas_client=bas_client_i)
    resultado_i = correr(orch_i)

    check("Override distinto del automático -> success=True", resultado_i.get("success") is True)
    codigo_enviado_i = bas_client_i.llamadas[0]["comprobante_compra_payload"]["Items"][0]["CodigoItem"]
    check(
        "Override distinto del automático -> BAS recibió 'Gs Gs Manual' (el override), NO 'Gs Gs 21%' (el automático)",
        codigo_enviado_i == "Gs Gs Manual",
    )


print()
print("=" * 78)
print("J -- candidato automático inválido + override válido -> YA NO bloquea (era el bug)")
print("=" * 78)

if registrar_comprobante is not None:
    items_j = [dict(ITEMS_BASE[0], bas_codigo_item="Gs Gs Manual")]
    # "Gs Gs 21%" (lo que resolvería la categoría sola) NO está acá -- solo
    # el código elegido a mano. Antes del fix esto bloqueaba igual (D),
    # aunque el humano ya hubiera corregido el ítem.
    bas_items_j = {"Gs Gs Manual": {"activo": True, "elegible_compras": True}}
    pb_j = FakePbClient(dict(INVOICE_BASE), items_j, bas_items_j)
    bas_client_j = FakeBasClient(respuesta={
        "comprobante": {"Prefijo": "00010", "Numero": 222, "Anulado": False},
        "ya_existia": False,
        "id_transaccion": 22222,
    })
    orch_j = FakeOrchestrator(pb_j, proveedor={"Codigo": "PROV001"}, bas_client=bas_client_j)
    resultado_j = correr(orch_j)

    check(
        "Automático inválido + override válido -> success=True (el bug bloqueaba esto)",
        resultado_j.get("success") is True,
    )
    check("Automático inválido + override válido -> SÍ llegó a llamar a BAS", len(bas_client_j.llamadas) == 1)
    codigo_enviado_j = bas_client_j.llamadas[0]["comprobante_compra_payload"]["Items"][0]["CodigoItem"] if bas_client_j.llamadas else None
    check("Automático inválido + override válido -> BAS recibió el override 'Gs Gs Manual'", codigo_enviado_j == "Gs Gs Manual")
    check(
        "Automático inválido + override válido -> se persistió bas_registration_status='registered'",
        any(u.get("bas_registration_status") == "registered" for u in pb_j.upserts_invoice),
    )


print()
print("=" * 78)
print("K -- override inválido (aunque el automático SÍ sea válido) -> bloquea, SIN fallback")
print("=" * 78)

if registrar_comprobante is not None:
    items_k = [dict(ITEMS_BASE[0], bas_codigo_item="Codigo-Que-No-Existe-En-BAS")]
    # "Gs Gs 21%" (el automático) SÍ está acá y sería válido -- pero un
    # override inválido nunca debe caer silenciosamente a esta resolución
    # (regla 1 de resolver_codigo_item, ver utils/bas_item_resolver.py).
    pb_k = FakePbClient(dict(INVOICE_BASE), items_k, BAS_ITEMS_VALIDOS)
    bas_client_k = FakeBasClient()
    orch_k = FakeOrchestrator(pb_k, proveedor={"Codigo": "PROV001"}, bas_client=bas_client_k)
    try:
        correr(orch_k)
        check("Override inválido -> debería haber lanzado HTTPException", False)
    except HTTPException as e:
        check("Override inválido -> 422", e.status_code == 422)
    check(
        "Override inválido -> se persistió bas_registration_status='awaiting_service_selection' (no 'registered')",
        any(u.get("bas_registration_status") == "awaiting_service_selection" for u in pb_k.upserts_invoice),
    )
    check("Override inválido -> NUNCA llegó a llamar a BAS (sin fallback silencioso al automático)", len(bas_client_k.llamadas) == 0)


print()
print("=" * 78)
print("L -- override vacío ('') -> cae al comportamiento automático por categoría de siempre")
print("=" * 78)

if registrar_comprobante is not None:
    items_l = [dict(ITEMS_BASE[0], bas_codigo_item="")]
    pb_l = FakePbClient(dict(INVOICE_BASE), items_l, BAS_ITEMS_VALIDOS)
    bas_client_l = FakeBasClient(respuesta={
        "comprobante": {"Prefijo": "00010", "Numero": 333, "Anulado": False},
        "ya_existia": False,
        "id_transaccion": 33333,
    })
    orch_l = FakeOrchestrator(pb_l, proveedor={"Codigo": "PROV001"}, bas_client=bas_client_l)
    resultado_l = correr(orch_l)

    check("Override vacío -> success=True (resuelve por categoría, como siempre)", resultado_l.get("success") is True)
    codigo_enviado_l = bas_client_l.llamadas[0]["comprobante_compra_payload"]["Items"][0]["CodigoItem"]
    check("Override vacío -> BAS recibió 'Gs Gs 21%' (el automático por categoría)", codigo_enviado_l == "Gs Gs 21%")


print()
print("=" * 78)
print("M -- arranca en 'awaiting_service_selection' (no 'ready_to_register') -> llega a 'registered' igual")
print("=" * 78)

if registrar_comprobante is not None:
    invoice_m = dict(INVOICE_BASE, bas_registration_status="awaiting_service_selection")
    items_m = [dict(ITEMS_BASE[0], bas_codigo_item="Gs Gs Manual")]
    bas_items_m = dict(BAS_ITEMS_VALIDOS, **{"Gs Gs Manual": {"activo": True, "elegible_compras": True}})
    pb_m = FakePbClient(invoice_m, items_m, bas_items_m)
    bas_client_m = FakeBasClient(respuesta={
        "comprobante": {"Prefijo": "00010", "Numero": 444, "Anulado": False},
        "ya_existia": False,
        "id_transaccion": 44444,
    })
    orch_m = FakeOrchestrator(pb_m, proveedor={"Codigo": "PROV001"}, bas_client=bas_client_m)
    resultado_m = correr(orch_m)

    check(
        "Arranca en awaiting_service_selection -> success=True de todos modos (el endpoint no gatea sobre el estado previo)",
        resultado_m.get("success") is True,
    )
    check(
        "Arranca en awaiting_service_selection -> termina en bas_registration_status='registered'",
        any(u.get("bas_registration_status") == "registered" for u in pb_m.upserts_invoice),
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
