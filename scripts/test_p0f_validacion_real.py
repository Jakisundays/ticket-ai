"""
Validación REAL y controlada de P0-F contra BAS + PocketBase.

Ejecuta el CÓDIGO REAL del endpoint POST /invoices/{process_id}/register-comprobante
(routes/process_invoice_google_2.py:registrar_comprobante), extraído vía AST
(mismo criterio que scripts/test_offline_registrar_comprobante_endpoint.py)
para probar exactamente el código que corre en producción -- pero acá, en vez
de dobles de prueba, se conecta con BasClient/PocketBaseClient REALES.

BAS: instalación REAL de BAS (no hay sandbox) -- Total=1 en todo, proveedor
de prueba ya existente (SUPERCOOP), mismo criterio de seguridad que el resto
del proyecto (ver docs/bas-orden-de-pago-research.md).

PocketBase: instancia LOCAL de Docker (ticket-ai-infra-pocketbase-1,
localhost:8090) -- NO la de producción. No había credenciales de
service_accounts locales (.env de Invoicy no tiene POCKETBASE_*) -- se creó
un superuser temporal + un record en "service_accounts" a mano vía la Admin
API para poder correr esta validación (ver resumen entregado al usuario).
No se toca el .env compartido -- las credenciales se pasan por variables de
entorno de este proceso únicamente.

Prerequisito: bas_items debe estar sincronizado en esta PocketBase local
(scripts/sync_bas_items.py) -- si no, resolver_codigo_item bloquea todo por
diseño (P0-E). Se verifica al arrancar y se corta con instrucciones si falta.

Escenarios (en este orden, cada uno depende del anterior salvo el de error):
  1. Registro real exitoso -- factura de prueba nueva, Total=1, SUPERCOOP,
     categoría "Gastos Generales" (-> CodigoItem "Gs Gs 21%", confirmado
     activo+elegible). Verifica con GET independiente. Confirma
     bas_id_transaccion/Prefijo/Numero/Total que devuelve BAS, y que
     PocketBase queda con bas_registration_status=registered +
     bas_id_transaccion + comprobante_registrado_at +
     comprobante_total_registrado + bas_last_error vacío.
  2. Segunda llamada AL MISMO endpoint, mismo process_id -- confirma
     already_resolved=True y que BAS NO tiene un segundo comprobante para el
     mismo número externo (GET real, cuenta cuántos hay).
  3. Camino de error real de BAS -- mismo proveedor/ítem válidos, pero un
     NumeroComprobanteExterno de 9 dígitos (fuera del rango que BAS acepta,
     confirmado real y documentado en scripts/test_crear_comprobante_compra.py:
     "between 0 and 99999999") -- pasa la validación LOCAL (formato
     prefijo-numero) pero BAS lo rechaza de verdad. Confirma que el endpoint
     responde 200 con success=False + error poblado (nunca una excepción
     HTTP), y que PocketBase queda con bas_registration_status=register_failed
     + bas_last_error poblado + comprobante_registrado_at vacío.

NOTA (hallazgo real de esta misma validación, 2026-08-12): BAS no expone el
Total del comprobante en NINGÚN endpoint de consulta (confirmado contra el
swagger real -- RespuestaConsultaComprobante, additionalProperties:false,
sin Total/TotalGravado/TotalIva). comprobante_total_registrado se persiste
del payload enviado, no de la respuesta de BAS -- fix aplicado en
routes/process_invoice_google_2.py tras encontrar esto en el primer intento
de esta validación. Ver también el fix del mismo hallazgo en
scripts/test_offline_registrar_comprobante_endpoint.py.

Uso (una sola vez, hace POSTs reales a BAS -- Total=1 cada uno):
    POCKETBASE_URL=http://localhost:8090 \\
    POCKETBASE_SERVICE_EMAIL=invoicy-backend@invoicy.local \\
    POCKETBASE_SERVICE_PASSWORD=<ver resumen> \\
    venv/bin/python scripts/test_p0f_validacion_real.py

Sale con código 0 si todo pasa, 1 si algo falla.
"""

import ast
import asyncio
import contextlib
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()  # BAS_* del .env compartido -- POCKETBASE_* se pasan por env del proceso, no se tocan acá.

from fastapi import Header, HTTPException  # noqa: E402

from utils.bas import BasApiError, BasClient  # noqa: E402
from utils.bas_config import (  # noqa: E402
    BAS_CAJA,
    BAS_CENTRO_APROPIACION_SD,
    BAS_DEPOSITO,
    BAS_EMITIDO_POR_CAE,
    BAS_EMPRESA,
    BAS_METODO_PAGO_CTA_CTE,
    BAS_PREFIJO_TALONARIO_MA,
    BAS_SUCURSAL,
    BAS_TIPO_ENTREGA_SIN_STOCK,
    fecha_hoy_bas,
)
from utils.bas_item_resolver import resolver_codigo_item  # noqa: E402
from utils.pocketbase_client import PocketBaseClient  # noqa: E402
from utils.validaciones_pre_bas import combinar_numero_comprobante  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE = REPO_ROOT / "routes" / "process_invoice_google_2.py"

FALLOS = []
PROVEEDOR_CODIGO_TEST = "SUPERCOO"
ITEM_CODIGO_TEST = "Gs Gs 21%"
CATEGORIA_TEST = "Gastos Generales"


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


def _pretty(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


# ============================================================================
# Pre-requisitos
# ============================================================================
print("=" * 78)
print("PASO 0 -- Pre-requisitos")
print("=" * 78)

if not os.getenv("POCKETBASE_URL") or not os.getenv("POCKETBASE_SERVICE_EMAIL"):
    print("FALTA POCKETBASE_URL/POCKETBASE_SERVICE_EMAIL/POCKETBASE_SERVICE_PASSWORD en el entorno del proceso.")
    print("Ver docstring de este archivo para cómo se bootstrapeó el service account local.")
    sys.exit(1)

bas_cliente_real = BasClient()
pb_cliente_real = PocketBaseClient()

print(f"BAS base_url = {bas_cliente_real.base_url}")
print(f"PocketBase base_url = {pb_cliente_real.base_url}")

proveedor_real = bas_cliente_real.obtener_proveedor(PROVEEDOR_CODIGO_TEST)
check(f"Proveedor de prueba '{PROVEEDOR_CODIGO_TEST}' existe en BAS", proveedor_real is not None)
check(
    f"'{PROVEEDOR_CODIGO_TEST}' tiene CuentasCorrientes configurada",
    bool(proveedor_real and proveedor_real.get("CuentasCorrientes")),
)
cuit_proveedor = (proveedor_real or {}).get("NumeroImpositivo1", "")
check("CUIT del proveedor de prueba resuelto", bool(cuit_proveedor))
print(f"  Proveedor: Codigo={proveedor_real.get('Codigo') if proveedor_real else None} "
      f"RazonSocial={proveedor_real.get('RazonSocial') if proveedor_real else None} CUIT={cuit_proveedor}")

item_real = pb_cliente_real.get_bas_item(ITEM_CODIGO_TEST)
if item_real is None:
    print(
        f"\n'{ITEM_CODIGO_TEST}' no está en bas_items (PocketBase local) -- "
        "corré primero: POCKETBASE_URL=... POCKETBASE_SERVICE_EMAIL=... POCKETBASE_SERVICE_PASSWORD=... "
        "venv/bin/python scripts/sync_bas_items.py"
    )
    sys.exit(1)
check(
    f"'{ITEM_CODIGO_TEST}' está en bas_items con activo=true + elegible_compras=true",
    bool(item_real.get("activo")) and bool(item_real.get("elegible_compras")),
)

if FALLOS:
    print("\nPre-requisitos no cumplidos -- no se puede continuar con seguridad. Abortando.")
    sys.exit(1)


# ============================================================================
# Extracción AST del código REAL (mismo criterio que los tests offline)
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


import textwrap  # noqa: E402

fuente_extraer_prefijo = _extraer_funcion_top_level("_extraer_prefijo_numero_comprobante_externo")
fuente_buscar_proveedor = _extraer_metodo_de_clase("InvoiceOrchestrator", "_buscar_proveedor_bas")
fuente_endpoint = _extraer_funcion_top_level("registrar_comprobante")

for nombre, fuente in (
    ("_extraer_prefijo_numero_comprobante_externo", fuente_extraer_prefijo),
    ("InvoiceOrchestrator._buscar_proveedor_bas", fuente_buscar_proveedor),
    ("registrar_comprobante", fuente_endpoint),
):
    check(f"Se extrajo '{nombre}' del código fuente real", bool(fuente))

if FALLOS:
    print("\nNo se pudo extraer el código real -- abortando.")
    sys.exit(1)


# ============================================================================
# Namespace: TODO real salvo `router`/`_verificar_secreto_invoicy`/`_lock_comprobante`
# (infraestructura de FastAPI/threading que no aporta nada a esta validación).
# ============================================================================
import types  # noqa: E402

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
    "combinar_numero_comprobante": combinar_numero_comprobante,
    "datetime": datetime,
    "fecha_hoy_bas": fecha_hoy_bas,  # REAL -- huso horario argentino real, no un stub.
    "_lock_comprobante": lambda *a, **kw: contextlib.nullcontext(),
    "_verificar_secreto_invoicy": lambda secret: None,
}


class _AppLogger:
    def info(self, msg):
        print(f"  [log] {msg}")

    def warning(self, msg):
        print(f"  [WARN] {msg}")

    def error(self, msg):
        print(f"  [ERROR] {msg}")


namespace["app_logger"] = _AppLogger()

exec(compile(textwrap.dedent(fuente_extraer_prefijo), "<_extraer_prefijo real>", "exec"), namespace)
exec(compile(textwrap.dedent(fuente_buscar_proveedor), "<_buscar_proveedor_bas real>", "exec"), namespace)
_buscar_proveedor_bas_real = namespace["_buscar_proveedor_bas"]

namespace["validar_factura_antes_de_pago_real"] = None  # se asigna real más abajo, tras importarlo
from utils.validaciones_pre_bas import validar_factura_antes_de_pago_real as _validar_real  # noqa: E402

namespace["validar_factura_antes_de_pago_real"] = _validar_real

exec(compile(textwrap.dedent(fuente_endpoint), "<registrar_comprobante real>", "exec"), namespace)
registrar_comprobante = namespace["registrar_comprobante"]


class OrchestratorReal:
    """Envoltorio liviano con BasClient/PocketBaseClient REALES -- evita
    instanciar InvoiceOrchestrator completo (asyncio.create_task a nivel de
    __init__, credenciales de Drive/webhook que esta validación no necesita).
    `_buscar_proveedor_bas` es el método REAL extraído arriba, con
    `self._proveedores_bas_cache` propio (mismo campo que usa el código real)."""

    def __init__(self, pb_client, bas_client):
        self._pb_client = pb_client
        self._bas_client = bas_client
        self._proveedores_bas_cache = {}

    def _buscar_proveedor_bas(self, cuit, razon_social=""):
        return _buscar_proveedor_bas_real(self, cuit, razon_social)


orchestrator = OrchestratorReal(pb_cliente_real, bas_cliente_real)
namespace["orchestrator"] = orchestrator


def correr(process_id):
    return asyncio.run(registrar_comprobante(process_id=process_id, x_invoicy_secret="validacion-real"))


def _numero_externo_de_prueba():
    ahora = datetime.datetime.now(datetime.timezone.utc)
    return "00090", int(ahora.strftime("%j%H%M"))  # <= 7 dígitos, dentro del rango real de BAS


def crear_factura_de_prueba(*, numero_comprobante, categoria=CATEGORIA_TEST):
    fecha_hoy_str = fecha_hoy_bas().isoformat()
    process_id = f"p0f-validacion-{numero_comprobante.replace('-', '')}"
    invoice = pb_cliente_real.upsert_invoice(
        {
            "process_id": process_id,
            "status": "completed",
            "review_status": "confirmed",
            "emisor_cuit": cuit_proveedor,
            "emisor_nombre": proveedor_real.get("RazonSocial"),
            "numero_comprobante": numero_comprobante,
            "fecha_emision": fecha_hoy_str,
            "cae": "12345678901234",
            "cae_vencimiento": fecha_hoy_str,
            "moneda": "ARS",
            "subtotal": 1,
            "total": 1,
        }
    )
    check(f"Factura de prueba creada en PocketBase local (process_id={process_id})", invoice is not None and invoice.get("id"))
    pb_cliente_real.bulk_create_invoice_items(
        invoice["id"],
        [{"process_id": process_id, "linea": 1, "descripcion": "Validación real P0-F", "cantidad": 1, "precio_unitario": 1, "precio_total": 1, "categoria": categoria}],
    )
    return process_id, invoice


# ============================================================================
# 1 -- Registro real exitoso
# ============================================================================
print()
print("=" * 78)
print("1 -- Registro real exitoso (Total=1, SUPERCOOP, Gs Gs 21%)")
print("=" * 78)

prefijo_ok, numero_ok = _numero_externo_de_prueba()
numero_comprobante_ok = f"{prefijo_ok}-{numero_ok}"
print(f"  Número de comprobante externo de prueba: {numero_comprobante_ok}")

process_id_ok, invoice_ok = crear_factura_de_prueba(numero_comprobante=numero_comprobante_ok)

resultado_1 = correr(process_id_ok)
print("\n  Respuesta del endpoint:")
print(textwrap.indent(_pretty(resultado_1), "    "))

check("Registro real -> success=True", resultado_1.get("success") is True)
check("Registro real -> sin 'error'", resultado_1.get("error") is None)
comprobante_bas = resultado_1.get("comprobante") or {}
check("Registro real -> BAS devolvió Prefijo/Numero internos", bool(comprobante_bas.get("Prefijo")) and comprobante_bas.get("Numero") is not None)
# Confirmado real (esta misma validación, 2026-08-12): NINGÚN endpoint de
# consulta de BAS devuelve el Total del comprobante -- verificado contra el
# swagger real (RespuestaConsultaComprobante, additionalProperties:false,
# sin campo Total) y confirmado empíricamente acá con el GET real de abajo.
# El endpoint persiste el Total del PAYLOAD enviado, no de la respuesta --
# ver el fix aplicado en routes/process_invoice_google_2.py.
check("Registro real -> BAS NO devuelve 'Total' en su respuesta (confirmado real, no un bug de este script)", "Total" not in comprobante_bas)

status_pb = resultado_1.get("bas_processing_status") or {}
print("\n  bas_processing_status persistido:")
print(textwrap.indent(_pretty(status_pb), "    "))
check("bas_id_transaccion persistido (no None)", status_pb.get("bas_id_transaccion") is not None)
check("comprobante_registrado_at persistido (no vacío)", bool(status_pb.get("comprobante_registrado_at")))
check(
    "comprobante_total_registrado == 1.0 (del payload enviado -- BAS no expone Total en su respuesta)",
    float(status_pb.get("comprobante_total_registrado", -1)) == 1.0,
)
check("bas_last_error vacío/None", not status_pb.get("bas_last_error"))
check("comprobante_registrado == True", status_pb.get("comprobante_registrado") is True)

invoice_actualizada = pb_cliente_real.get_invoice_by_process_id(process_id_ok)
check("invoices.bas_registration_status == 'registered' (leído fresco de PocketBase)", (invoice_actualizada or {}).get("bas_registration_status") == "registered")

# Verificación independiente con GET real (no confiar en la respuesta del POST).
verificacion_get = bas_cliente_real.consultar_comprobante_externo(
    BAS_EMPRESA, BAS_SUCURSAL, "MA",
    prefijo_externo=prefijo_ok, numero_externo=numero_ok, fecha_externo=fecha_hoy_bas().isoformat(),
)
check("Verificación independiente (GET ConsultaComprobantesExternos) encuentra el comprobante", verificacion_get is not None)
if verificacion_get:
    print("\n  GET de verificación independiente:")
    print(textwrap.indent(_pretty(verificacion_get), "    "))
    check("GET de verificación -- mismo Prefijo/Numero que el POST", verificacion_get.get("Prefijo") == comprobante_bas.get("Prefijo") and verificacion_get.get("Numero") == comprobante_bas.get("Numero"))


# ============================================================================
# 2 -- Idempotencia: segunda llamada, mismo process_id
# ============================================================================
print()
print("=" * 78)
print("2 -- Idempotencia: segunda llamada al mismo endpoint")
print("=" * 78)

resultado_2 = correr(process_id_ok)
print("\n  Respuesta de la 2da llamada:")
print(textwrap.indent(_pretty(resultado_2), "    "))
check("2da llamada -> already_resolved=True (no reintenta)", resultado_2.get("already_resolved") is True)

# Confirmar con BAS real que sigue habiendo UN solo comprobante para este
# número externo (no se duplicó).
verificacion_get_2 = bas_cliente_real.consultar_comprobante_externo(
    BAS_EMPRESA, BAS_SUCURSAL, "MA",
    prefijo_externo=prefijo_ok, numero_externo=numero_ok, fecha_externo=fecha_hoy_bas().isoformat(),
)
check(
    "BAS real sigue devolviendo el MISMO Prefijo/Numero tras la 2da llamada (sin duplicado)",
    verificacion_get_2 is not None
    and verificacion_get_2.get("Prefijo") == comprobante_bas.get("Prefijo")
    and verificacion_get_2.get("Numero") == comprobante_bas.get("Numero"),
)


# ============================================================================
# 3 -- Camino de error real de BAS
# ============================================================================
print()
print("=" * 78)
print("3 -- Camino de error real de BAS (NumeroComprobanteExterno fuera de rango)")
print("=" * 78)

numero_comprobante_error = "00091-999999999"  # 9 dígitos -- BAS exige <= 99999999 (8 dígitos), confirmado real
print(f"  Número de comprobante externo (deliberadamente inválido para BAS): {numero_comprobante_error}")

process_id_error, invoice_error = crear_factura_de_prueba(numero_comprobante=numero_comprobante_error)

resultado_3 = correr(process_id_error)
print("\n  Respuesta del endpoint (HTTP 200 en todos los casos -- no lanza excepción):")
print(textwrap.indent(_pretty(resultado_3), "    "))

check("BAS rechaza -> el endpoint NO lanza excepción (responde 200-shape)", True)  # si esto no fuera cierto, correr() ya habría tirado
check("BAS rechaza -> success=False", resultado_3.get("success") is False)
check("BAS rechaza -> 'error' poblado con el detalle real de BAS", bool(resultado_3.get("error")))
check(
    "El frontend puede distinguir claramente éxito de error con solo mirar 'success' (sin depender del status HTTP)",
    resultado_1.get("success") is True and resultado_3.get("success") is False,
)

status_pb_error = resultado_3.get("bas_processing_status") or {}
print("\n  bas_processing_status persistido (caso de error):")
print(textwrap.indent(_pretty(status_pb_error), "    "))
check("Caso de error -> bas_last_error poblado", bool(status_pb_error.get("bas_last_error")))
check("Caso de error -> comprobante_registrado == False", status_pb_error.get("comprobante_registrado") is False)
# PocketBase serializa un campo "date" sin valor como "" (string vacío), no
# como null -- confirmado real acá (mismo criterio ya documentado para
# campos "number", ver docs/incidente-2026-08-04-pagos-solo-neto.md sección
# 10). El endpoint SÍ pasa None correctamente (ver test_offline_registrar_comprobante_endpoint.py
# escenario G, que prueba la lógica Python antes del round-trip real) -- acá
# se valida el resultado tal cual lo devuelve PocketBase de verdad.
check("Caso de error -> comprobante_registrado_at vacío (no hubo éxito real)", not status_pb_error.get("comprobante_registrado_at"))

invoice_error_actualizada = pb_cliente_real.get_invoice_by_process_id(process_id_error)
check(
    "invoices.bas_registration_status == 'register_failed' (leído fresco de PocketBase)",
    (invoice_error_actualizada or {}).get("bas_registration_status") == "register_failed",
)


print()
print("=" * 78)
if FALLOS:
    print(f"RESULTADO: {len(FALLOS)} chequeo(s) fallaron:")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("RESULTADO: todos los chequeos pasaron -- P0-F validado contra BAS real.")
    sys.exit(0)
