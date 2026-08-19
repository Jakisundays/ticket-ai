"""
Test OFFLINE (sin red, sin BAS, sin PocketBase real) del mismo hallazgo
crítico que scripts/test_offline_registrar_comprobante_endpoint.py, pero
para el endpoint LEGADO POST /payment-orders/{process_id}/create
(routes/process_invoice_google_2.py: crear_orden_pago).

Por qué existe un archivo aparte en vez de agregar escenarios acá al de
registrar_comprobante: crear_orden_pago es el endpoint del alcance VIEJO
(Orden de Pago real, fuera de alcance desde 2026-08-10, pero código
deprecado que la instrucción explícita del usuario prohíbe borrar) -- no
tenía ningún test offline propio a nivel de endpoint (solo
test_crear_orden_pago_desde_factura.py, que prueba el método de más bajo
nivel BasClient.crear_orden_de_pago_desde_factura, no esta función). Este
test es DELIBERADAMENTE angosto: solo cubre el mismo hallazgo (override de
CodigoItem ignorado) para que no exista una discrepancia entre el flujo
nuevo y el legado -- no reintenta cubrir toda la lógica de pago/OP de este
endpoint, que es un eje distinto y no es lo que cambió acá.

Mismo criterio de extracción AST + namespace con dobles que el test de
registrar_comprobante (ver ese docstring para el detalle): reales cuando
son puros y ya probados (resolver_codigo_item, _extraer_prefijo...),
dobles para todo lo que toca red/estado.

Actualizado 2026-08-19: el respeto del override de CodigoItem ya no vive
en el source de crear_orden_pago -- se relocalizó a
utils/bas_payload.py:_elegir_codigo_item_dominante (la misma función
compartida que usan los 3 call sites de BAS, ver
utils/bas_payload.py). El check estructural de abajo ahora verifica que
crear_orden_pago llame a construir_comprobante_totales_e_items en vez de
buscar el patrón viejo inline -- los escenarios I/J/K/L siguen probando
el comportamiento de punta a punta contra el código real.

Escenarios (mismos 4 del hallazgo, ver test_offline_registrar_comprobante_endpoint.py
I/J/K/L para el detalle completo de cada uno):
  I. Override válido distinto del automático -> BAS recibe el override.
  J. Automático inválido + override válido -> ya NO bloquea.
  K. Override inválido (aunque el automático sea válido) -> bloquea, SIN
     fallback silencioso.
  L. Override vacío -> cae al comportamiento automático de siempre.

Uso: python3 scripts/test_offline_crear_orden_pago_codigo_item.py
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
from pydantic import BaseModel, Field  # noqa: E402 -- real, puro (CrearOrdenPagoBody)

import utils.bas_config as bas_config  # noqa: E402
from utils.bas_config import (  # noqa: E402 -- reales, constantes puras
    BAS_CAJA,
    BAS_CENTRO_APROPIACION_SD,
    BAS_DEPOSITO,
    BAS_EMITIDO_POR_CAE,
    BAS_EMPRESA,
    BAS_IMPUTACION_CONTABLE_PROVEEDORES,
    BAS_METODO_PAGO_CTA_CTE,
    BAS_PREFIJO_TALONARIO_MA,
    BAS_PREFIJO_TALONARIO_OP,
    BAS_SUCURSAL,
    BAS_TIPO_ENTREGA_SIN_STOCK,
    METODO_PAGO_ARRAY_BAS,
)
from utils.bas_item_resolver import resolver_codigo_item  # noqa: E402 -- real
from utils.bas_payload import construir_comprobante_totales_e_items  # noqa: E402 -- real
from utils.validaciones_pre_bas import combinar_numero_comprobante  # noqa: E402 -- real

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE = REPO_ROOT / "routes" / "process_invoice_google_2.py"

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


# Cache determinístico de bas_config -- mismo criterio que el test de
# registrar_comprobante: resolver_codigo_item real no debe intentar red.
bas_config._cache_categoria_map["datos"] = {
    "Gastos Generales": {21: "Gs Gs 21%"},
}
import time as _time  # noqa: E402

bas_config._cache_categoria_map["actualizado_en"] = _time.time()


# ============================================================================
# Extracción AST: CrearOrdenPagoBody (la función la necesita como type hint
# del parámetro `body`, evaluado eager -- este archivo no usa
# `from __future__ import annotations`), _extraer_prefijo... y la función
# real crear_orden_pago.
# ============================================================================
codigo_fuente = TARGET_FILE.read_text(encoding="utf-8")
arbol = ast.parse(codigo_fuente, filename=str(TARGET_FILE))


def _extraer_top_level(nombre, tipos):
    nodo = next((n for n in arbol.body if isinstance(n, tipos) and n.name == nombre), None)
    if nodo is None:
        return None
    return ast.get_source_segment(codigo_fuente, nodo)


fuente_body_model = _extraer_top_level("CrearOrdenPagoBody", (ast.ClassDef,))
fuente_extraer_prefijo = _extraer_top_level(
    "_extraer_prefijo_numero_comprobante_externo", (ast.FunctionDef, ast.AsyncFunctionDef)
)
fuente_endpoint = _extraer_top_level("crear_orden_pago", (ast.FunctionDef, ast.AsyncFunctionDef))

check("Se extrajo 'CrearOrdenPagoBody' del AST", bool(fuente_body_model))
check("Se extrajo '_extraer_prefijo_numero_comprobante_externo' del AST", bool(fuente_extraer_prefijo))
check("Se extrajo 'crear_orden_pago' del AST", bool(fuente_endpoint))

# Regresión estructural directa: el hallazgo corregido acá era que este
# call site ignorara el override -- ahora se corrige delegando SIEMPRE a la
# función compartida (construir_comprobante_totales_e_items), nunca
# reimplementando su propia resolución de CodigoItem inline. Si alguien
# reintroduce una copia local de esa lógica, este check lo pesca sin
# necesitar que ningún escenario de abajo falle primero.
check(
    "El endpoint delega la resolución de CodigoItem a construir_comprobante_totales_e_items (no la reimplementa)",
    fuente_endpoint is not None and "construir_comprobante_totales_e_items(" in fuente_endpoint,
)


# ============================================================================
# Namespace de ejecución -- reales cuando son puros, dobles cuando tocan red/estado.
# ============================================================================
class _FakeAppLogger:
    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


class FakePbClient:
    def __init__(self, invoice, items, bas_items_validos=None):
        self._invoice = dict(invoice)
        self._items = items
        self._bas_items = bas_items_validos or {}
        self.llamadas_get_bas_item = []

    def get_invoice_by_process_id(self, pid):
        return dict(self._invoice)

    def get_invoice_items(self, invoice_id):
        return self._items

    def get_payment_order(self, pid):
        return None  # sin intento previo -- camino normal, no el de idempotencia

    def get_bas_processing_status(self, pid):
        return {}

    def get_payment_method(self, metodo_pago):
        return {"bas_medio_pago_codigo": "EF"}

    def get_bas_item(self, codigo):
        self.llamadas_get_bas_item.append(codigo)
        return self._bas_items.get(codigo)

    def upsert_payment_order(self, process_id, **campos):
        return dict(campos)


class _ExplotaSiSeLlama:
    def __getattr__(self, nombre):
        def _explota(*a, **kw):
            raise AssertionError(f"No debería haberse llamado a '{nombre}' -- el gate no cortó el flujo")

        return _explota


class FakeOrchestrator:
    def __init__(self, pb_client, proveedor, bas_client):
        self._pb_client = pb_client
        self._proveedor = proveedor
        self._bas_client = bas_client

    def _buscar_proveedor_bas(self, cuit, razon_social):
        return self._proveedor


class FakeBasClient:
    """OP exitosa y aplicada por default -- alcanza para probar el
    resolver de CodigoItem, que es lo único que cambió."""

    def __init__(self):
        self.llamadas = []

    def crear_orden_de_pago_desde_factura(self, **kw):
        self.llamadas.append(kw)
        return {
            "orden_pago": {
                "Comprobantes": [{"Prefijo": "00010", "Numero": 555}],
            }
        }


namespace = {
    "router": types.SimpleNamespace(post=lambda *a, **kw: (lambda f: f)),
    "Header": Header,
    "Optional": Optional,
    "HTTPException": HTTPException,
    "BaseModel": BaseModel,
    "Field": Field,
    "METODO_PAGO_ARRAY_BAS": METODO_PAGO_ARRAY_BAS,
    "BAS_TIPO_ENTREGA_SIN_STOCK": BAS_TIPO_ENTREGA_SIN_STOCK,
    "BAS_CENTRO_APROPIACION_SD": BAS_CENTRO_APROPIACION_SD,
    "BAS_PREFIJO_TALONARIO_MA": BAS_PREFIJO_TALONARIO_MA,
    "BAS_PREFIJO_TALONARIO_OP": BAS_PREFIJO_TALONARIO_OP,
    "BAS_IMPUTACION_CONTABLE_PROVEEDORES": BAS_IMPUTACION_CONTABLE_PROVEEDORES,
    "BAS_EMITIDO_POR_CAE": BAS_EMITIDO_POR_CAE,
    "BAS_EMPRESA": BAS_EMPRESA,
    "BAS_SUCURSAL": BAS_SUCURSAL,
    "BAS_DEPOSITO": BAS_DEPOSITO,
    "BAS_CAJA": BAS_CAJA,
    "BAS_METODO_PAGO_CTA_CTE": BAS_METODO_PAGO_CTA_CTE,
    "resolver_codigo_item": resolver_codigo_item,
    "construir_comprobante_totales_e_items": construir_comprobante_totales_e_items,
    "combinar_numero_comprobante": combinar_numero_comprobante,
    "datetime": datetime,
    "fecha_hoy_bas": lambda: datetime.date(2026, 8, 12),
    "app_logger": _FakeAppLogger(),
    "_lock_comprobante": lambda *a, **kw: contextlib.nullcontext(),
    "_verificar_secreto_invoicy": lambda secret: None,
}

exec(compile(fuente_body_model, filename="<CrearOrdenPagoBody extraído>", mode="exec"), namespace)
exec(compile(fuente_extraer_prefijo, filename="<_extraer_prefijo extraído>", mode="exec"), namespace)

namespace["validar_factura_antes_de_pago_real"] = lambda invoice: []

exec(compile(fuente_endpoint, filename="<crear_orden_pago extraído>", mode="exec"), namespace)
crear_orden_pago = namespace.get("crear_orden_pago")
CrearOrdenPagoBody = namespace.get("CrearOrdenPagoBody")
check("La función extraída (código real) se pudo compilar y ejecutar vía exec", callable(crear_orden_pago))


def correr(orchestrator):
    namespace["orchestrator"] = orchestrator
    body = CrearOrdenPagoBody(metodo_pago="efectivo", requested_by="tester-p0g-fix")
    return asyncio.run(crear_orden_pago(process_id="proc-test-op-1", body=body, x_invoicy_secret="lo-que-sea"))


INVOICE_BASE = {
    "id": "pb-invoice-op-1",
    "review_status": "confirmed",
    "emisor_cuit": "20111111111",
    "emisor_nombre": "Proveedor Test SA",
    "numero_comprobante": "00010-00001854",
    "fecha_emision": "2026-08-01",
    "cae": "12345678901234",
    "cae_vencimiento": "2026-09-01",
    "iva_alicuota": None,
    "moneda": "ARS",
    "total": 1000,
}
ITEM_BASE = {"categoria": "Gastos Generales", "precio_total": 1000, "cantidad": 1, "precio_unitario": 1000}
BAS_ITEMS_VALIDOS = {"Gs Gs 21%": {"activo": True, "elegible_compras": True}}


print("=" * 78)
print("I -- override válido DISTINTO del automático (ambos válidos) -> BAS recibe el override")
print("=" * 78)

if crear_orden_pago is not None:
    items_i = [dict(ITEM_BASE, bas_codigo_item="Gs Gs Manual")]
    bas_items_i = dict(BAS_ITEMS_VALIDOS, **{"Gs Gs Manual": {"activo": True, "elegible_compras": True}})
    pb_i = FakePbClient(dict(INVOICE_BASE), items_i, bas_items_i)
    bas_client_i = FakeBasClient()
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

if crear_orden_pago is not None:
    items_j = [dict(ITEM_BASE, bas_codigo_item="Gs Gs Manual")]
    bas_items_j = {"Gs Gs Manual": {"activo": True, "elegible_compras": True}}  # "Gs Gs 21%" NO está
    pb_j = FakePbClient(dict(INVOICE_BASE), items_j, bas_items_j)
    bas_client_j = FakeBasClient()
    orch_j = FakeOrchestrator(pb_j, proveedor={"Codigo": "PROV001"}, bas_client=bas_client_j)
    resultado_j = correr(orch_j)

    check(
        "Automático inválido + override válido -> success=True (el bug bloqueaba esto)",
        resultado_j.get("success") is True,
    )
    check("Automático inválido + override válido -> SÍ llegó a llamar a BAS", len(bas_client_j.llamadas) == 1)


print()
print("=" * 78)
print("K -- override inválido (aunque el automático SÍ sea válido) -> bloquea, SIN fallback")
print("=" * 78)

if crear_orden_pago is not None:
    items_k = [dict(ITEM_BASE, bas_codigo_item="Codigo-Que-No-Existe-En-BAS")]
    pb_k = FakePbClient(dict(INVOICE_BASE), items_k, BAS_ITEMS_VALIDOS)  # "Gs Gs 21%" SÍ sería válido
    bas_client_k = FakeBasClient()
    orch_k = FakeOrchestrator(pb_k, proveedor={"Codigo": "PROV001"}, bas_client=bas_client_k)
    try:
        correr(orch_k)
        check("Override inválido -> debería haber lanzado HTTPException", False)
    except HTTPException as e:
        check("Override inválido -> 422", e.status_code == 422)
    check("Override inválido -> NUNCA llegó a llamar a BAS (sin fallback silencioso al automático)", len(bas_client_k.llamadas) == 0)


print()
print("=" * 78)
print("L -- override vacío ('') -> cae al comportamiento automático por categoría de siempre")
print("=" * 78)

if crear_orden_pago is not None:
    items_l = [dict(ITEM_BASE, bas_codigo_item="")]
    pb_l = FakePbClient(dict(INVOICE_BASE), items_l, BAS_ITEMS_VALIDOS)
    bas_client_l = FakeBasClient()
    orch_l = FakeOrchestrator(pb_l, proveedor={"Codigo": "PROV001"}, bas_client=bas_client_l)
    resultado_l = correr(orch_l)

    check("Override vacío -> success=True (resuelve por categoría, como siempre)", resultado_l.get("success") is True)
    codigo_enviado_l = bas_client_l.llamadas[0]["comprobante_compra_payload"]["Items"][0]["CodigoItem"]
    check("Override vacío -> BAS recibió 'Gs Gs 21%' (el automático por categoría)", codigo_enviado_l == "Gs Gs 21%")


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
