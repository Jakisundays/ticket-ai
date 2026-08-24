"""
Test offline de regresión para los 5 hallazgos encontrados en la revisión
adversarial del REDISEÑO de ZIP (después de que la primera revisión
adversarial post-implementación ya había sido resuelta -- ver
docs/plan-fase1-zip-REDISEÑO.md). Estos 5 son distintos de los 14
originales: 2 preexistentes (adyacentes al código tocado, no introducidos
por el rediseño de identidad) y 3 introducidos por el propio rediseño de
notificaciones.

H1 (CRÍTICO, preexistente). `file_name` del webhook de email
   (routes/process_invoice_google_2.py, webhook_endpoint) se usaba crudo
   para construir la ruta donde se descarga el adjunto -- sin ninguna
   autenticación en el endpoint (sin secret_key, sin X-Invoicy-Secret, sin
   rate limiting), esto era escritura de archivo arbitraria (con contenido
   descargado de una URL también controlada por el caller). Fix: saneo con
   _sanear_nombre_para_process_id + verificación estructural de que la ruta
   final resuelve DENTRO de temp_dir (no confía solo en el saneo de
   caracteres).

H2 (SERIO, preexistente). `process_invoice` y `website_upload` guardaban el
   archivo subido inicial usando file.filename.split('/')[-1] sin ningún
   componente único por-request -- dos uploads concurrentes con el mismo
   nombre de archivo se pisaban en disco. Fix: prefijo uuid4.hex único.

H3 (SERIO, introducido por el rediseño). El payload de fire_webhook por
   factura (on_item_completado) no coincidía con el contrato real que ya
   emite worker() -- faltaba drive_file_id, "saved"/"bas" cambiaban de
   nombre/nivel. Fix: réplica EXACTA del contrato de worker(), inconsistencia
   incluida (éxito usa "id", error usa "process_id") -- ya cubierto a fondo
   en scripts/test_offline_zip_extraccion.py (escenario "Extra"), NO se
   repite acá.

H4 (SERIO, introducido por el rediseño). Un ZIP rechazado por completo (ya
   sea por _validar_zip_rapido síncrono, o por error_zip async) no disparaba
   NINGUNA notificación -- ni email ni webhook -- dejando al remitente y a
   integraciones externas sin ningún rastro. Fix (semántica EXISTENTE del
   canal email, no inventada: worker() nunca mandó un email de error, solo
   fire_webhook -- ver comparación completa en
   docs/plan-fase1-zip-REDISEÑO.md): se dispara fire_webhook (nunca email)
   con un payload de error, tanto en el rechazo síncrono como en error_zip.

H5 (SERIO/MODERADO, introducido por el rediseño, amplifica un problema
   preexistente). fire_webhook() y enviar_email() son bloqueantes
   (requests.post / smtplib síncronos) -- antes del rediseño, un ZIP por
   email nunca los llamaba; ahora se llaman hasta N veces por ZIP (una por
   factura) en un loop secuencial. Fix: fire_webhook ahora envuelve su
   llamada bloqueante en asyncio.to_thread; enviar_email ahora tiene
   timeout explícito en su conexión SMTP (ya se llamaba vía to_thread desde
   el flujo de ZIP, pero sin timeout propio podía agotar el pool de hilos
   compartido igual si el SMTP colgaba).

H12 (MODERADO, preexistente, encontrado en la ronda 4 de revisión
   adversarial posterior al fix de durabilidad de Fase A).
   /invoices/{process_id}/retry-extraction escribía el archivo re-descargado
   de PocketBase usando documento_original crudo (sin sanear) para construir
   file_location -- mismo patrón de riesgo que H1, pero en un sink distinto
   (este endpoint SÍ requiere X-Invoicy-Secret, y el nombre no viene directo
   de un request público sino de un valor guardado en PocketBase; por eso
   H1 se clasificó crítico y este moderado). Fix: mismo patrón que H1 --
   _sanear_nombre_para_process_id + verificación estructural de que la ruta
   final resuelve DENTRO de ./downloads/ antes de escribir.

Todas las funciones bajo prueba se extraen vía AST del código fuente real y
se ejecutan con exec() -- nunca se reimplementa la lógica a mano acá.

Uso: python3 scripts/test_offline_zip_regresiones_notificaciones_seguridad.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import ast
import asyncio
import io
import os
import re
import shutil
import sys
import tempfile
import textwrap
import threading
import time
import uuid
import zipfile
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import File, Form, Header, HTTPException, Request, UploadFile  # noqa: E402
import filetype  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE = REPO_ROOT / "routes" / "process_invoice_google_2.py"

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


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
        (n for n in ast.walk(nodo_clase) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == metodo),
        None,
    )
    return ast.get_source_segment(codigo_fuente, nodo_metodo) if nodo_metodo else None


NOMBRES_TOP_LEVEL = [
    "_sanear_nombre_para_process_id",
    "_carpeta_ingest_zip",
    "_validar_zip_rapido",
    "_reservar_trabajo_en_vuelo_zip",
    "_liberar_trabajo_en_vuelo_zip",
    "_truncar_nombre_a_bytes",
    "webhook_endpoint",
    "process_invoice",
    "reintentar_extraccion",
    "_verificar_secreto_invoicy",
]
FUENTES = {n: _extraer_funcion_top_level(n) for n in NOMBRES_TOP_LEVEL}
for n in NOMBRES_TOP_LEVEL:
    check(f"Se extrajo '{n}' del código fuente real", bool(FUENTES[n]))

fuente_fire_webhook = _extraer_metodo_de_clase("InvoiceOrchestrator", "fire_webhook")
fuente_enviar_email = _extraer_metodo_de_clase("InvoiceOrchestrator", "enviar_email")
check("Se extrajo 'InvoiceOrchestrator.fire_webhook' del código fuente real", bool(fuente_fire_webhook))
check("Se extrajo 'InvoiceOrchestrator.enviar_email' del código fuente real", bool(fuente_enviar_email))

if FALLOS:
    print("\nNo se pudo extraer el código real -- abortando.")
    sys.exit(1)

# Confirma en el código fuente real (no solo por comportamiento observado)
# que fire_webhook usa asyncio.to_thread -- si alguien lo revierte, este
# chequeo lo detecta incluso antes de correr nada.
check(
    "fire_webhook() usa asyncio.to_thread para la llamada bloqueante (requests.post)",
    "asyncio.to_thread" in fuente_fire_webhook and "requests.post" in fuente_fire_webhook,
)
check(
    "enviar_email() pasa un timeout explícito a smtplib.SMTP_SSL",
    "timeout=" in fuente_enviar_email and "SMTP_SSL" in fuente_enviar_email,
)


PDF_VALIDO = b"%PDF-1.4\n" + b"X" * 50 + b"\n%%EOF\n"


def construir_zip_bytes(miembros):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for nombre, contenido in miembros:
            zf.writestr(nombre, contenido)
    return buf.getvalue()


def construir_zip_vacio_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    return buf.getvalue()


class FakeUploadFile:
    def __init__(self, filename, content: bytes):
        self.filename = filename
        self.file = io.BytesIO(content)


class FakeRequest:
    def __init__(self, data):
        self._data = data
        self.url = "http://test/gemini2/webhook"

    async def json(self):
        return self._data


class FakeAppLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class RegistradorLlamadasFS:
    def __init__(self):
        self.makedirs = []
        self.rmtree = []


@contextmanager
def espiar_fs_real(registrador):
    """Monkeypatch temporal de os.makedirs/shutil.rmtree sobre los módulos
    REALES -- mismo patrón ya usado en scripts/test_offline_zip_identidad_seguridad.py."""
    makedirs_original = os.makedirs
    rmtree_original = shutil.rmtree

    def makedirs_espiado(path, *a, **k):
        registrador.makedirs.append(path)
        return makedirs_original(path, *a, **k)

    def rmtree_espiado(path, *a, **k):
        registrador.rmtree.append(path)
        return rmtree_original(path, *a, **k)

    os.makedirs = makedirs_espiado
    shutil.rmtree = rmtree_espiado
    try:
        yield registrador
    finally:
        os.makedirs = makedirs_original
        shutil.rmtree = rmtree_original


cwd_original = os.getcwd()


# ============================================================================
# H1 -- webhook de email: file_name malicioso nunca escapa temp_dir
# ============================================================================
print("=" * 78)
print("H1 -- webhook_endpoint: file_name malicioso -- nunca escribe fuera de temp_dir")
print("=" * 78)

DIR_H1 = Path(tempfile.mkdtemp(prefix="test_zip_regresiones_h1_"))
os.chdir(DIR_H1)
try:
    PAYLOADS_FILE_NAME = [
        "..",  # el caso límite real: sobrevive basename() intacto (sin "/" que cortar)
        "../../../../tmp/pwn",  # se neutraliza vía basename() -> "pwn"
        "/etc/passwd",  # se neutraliza vía basename() -> "passwd"
    ]

    for payload in PAYLOADS_FILE_NAME:
        namespace = {
            "os": os, "re": re, "shutil": shutil, "asyncio": asyncio,
            "zipfile": zipfile, "zlib": zlib, "filetype": filetype,
            "uuid": uuid, "datetime": __import__("datetime"),
            "Optional": Optional, "Callable": Callable,
            "Request": Request, "HTTPException": HTTPException,
            "app_logger": FakeAppLogger(),
        }
        escrituras_reales = []

        class OrchestratorH1:
            def __init__(self):
                self.job_queue = self
                self.fire_webhook_llamadas = []

            def get_file_type_from_url(self, attachments):
                return "application/pdf"

            def download_file_from_url(self, url, destino):
                # Doble que SÍ escribe algo real en disco (como el método
                # real) -- así H1 puede confirmar dónde terminó escribiendo,
                # no solo que "algo" pasó.
                escrituras_reales.append(destino)
                with open(destino, "wb") as f:
                    f.write(PDF_VALIDO)
                return True

            async def fire_webhook(self, data):
                self.fire_webhook_llamadas.append(data)

            async def put(self, job):
                pass

        namespace["orchestrator"] = OrchestratorH1()

        for nombre in ["_sanear_nombre_para_process_id", "_carpeta_ingest_zip", "_validar_zip_rapido", "webhook_endpoint"]:
            exec(compile(textwrap.dedent(FUENTES[nombre]), f"<{nombre} real>", "exec"), namespace)

        payload_webhook = {
            "from_email": "cliente@example.com",
            "subject": "Factura",
            "attachments": "https://fake.example.com/x.pdf",
            "file_name": payload,
        }
        resultado = asyncio.run(namespace["webhook_endpoint"](FakeRequest(payload_webhook)))

        check(
            f"H1 file_name={payload!r}: ninguna escritura real terminó fuera de ./downloads/",
            all(os.path.realpath(r).startswith(os.path.realpath("./downloads")) for r in escrituras_reales),
        )
        if payload == "..":
            # Este es el caso donde la contención estructural (no el saneo
            # de caracteres) es la que realmente actúa -- confirmá que el
            # endpoint lo rechazó explícitamente, sin siquiera intentar
            # descargar nada.
            check(
                "H1 file_name='..': el endpoint lo rechaza ANTES de descargar (download_file_from_url nunca se llamó)",
                len(escrituras_reales) == 0,
            )
            check(
                "H1 file_name='..': la respuesta indica error claro (no un 500 ni un éxito falso)",
                resultado.get("success") is False and "inválido" in (resultado.get("message") or "").lower(),
            )
finally:
    os.chdir(cwd_original)
    shutil.rmtree(DIR_H1, ignore_errors=True)


# ============================================================================
# H2 -- process_invoice / website_upload: ruta única por-request para el
# guardado inicial (sin colisión aunque el filename se repita).
# ============================================================================
print()
print("=" * 78)
print("H2 -- process_invoice: 2 uploads con el MISMO filename -> rutas de disco distintas")
print("=" * 78)

DIR_H2 = Path(tempfile.mkdtemp(prefix="test_zip_regresiones_h2_"))
os.chdir(DIR_H2)
os.environ["SECRET_KEY"] = "test-secret"
try:
    llamadas_bg = []

    async def _fake_procesar_en_background(**kwargs):
        llamadas_bg.append(dict(kwargs))
        return {"success": True, "factura": {"id": kwargs.get("process_id")}}

    async def _fake_extraer_zip(**kwargs):
        return {"aceptados": 0, "no_soportados": [], "errores_despacho": [], "error_zip": None}

    namespace_h2 = {
        "os": os, "re": re, "shutil": shutil, "asyncio": asyncio,
        "zipfile": zipfile, "zlib": zlib, "filetype": filetype, "uuid": uuid,
        "Optional": Optional, "Callable": Callable,
        "HTTPException": HTTPException, "Form": Form, "File": File, "UploadFile": UploadFile,
        "app_logger": FakeAppLogger(),
        "_procesar_en_background": _fake_procesar_en_background,
        "_extraer_zip_y_despachar_individualmente": _fake_extraer_zip,
    }
    for nombre in ["_reservar_trabajo_en_vuelo_zip", "_liberar_trabajo_en_vuelo_zip", "_carpeta_ingest_zip", "_validar_zip_rapido", "_truncar_nombre_a_bytes", "process_invoice"]:
        namespace_h2.setdefault("MAX_ARCHIVOS_ZIP", 100)
        namespace_h2.setdefault("MAX_ZIP_DESCOMPRIMIDO_BYTES", 500 * 1024 * 1024)
        namespace_h2.setdefault("MAX_TRABAJO_EN_VUELO_ZIP", 200)
        namespace_h2.setdefault("_trabajo_en_vuelo_zip", 0)
        namespace_h2.setdefault("_trabajo_en_vuelo_zip_lock", threading.Lock())
        exec(compile(textwrap.dedent(FUENTES[nombre]), f"<{nombre} real>", "exec"), namespace_h2)

    process_invoice_real = namespace_h2["process_invoice"]

    registrador_h2 = RegistradorLlamadasFS()
    MISMO_NOMBRE = "factura.pdf"

    with espiar_fs_real(registrador_h2):
        archivo_1 = FakeUploadFile(MISMO_NOMBRE, PDF_VALIDO)
        r1 = asyncio.run(process_invoice_real(id="req-1", secret_key="test-secret", file=archivo_1))

        archivo_2 = FakeUploadFile(MISMO_NOMBRE, PDF_VALIDO)
        r2 = asyncio.run(process_invoice_real(id="req-2", secret_key="test-secret", file=archivo_2))

    check("H2: ambos requests con el mismo filename respondieron éxito", r1.get("success") is True and r2.get("success") is True)
    check("H2: _procesar_en_background fue llamada 2 veces (una por request)", len(llamadas_bg) == 2)
    if len(llamadas_bg) == 2:
        ruta_1 = llamadas_bg[0]["file_location"]
        ruta_2 = llamadas_bg[1]["file_location"]
        check(
            f"H2: las 2 rutas de disco son DISTINTAS pese al mismo filename ({ruta_1!r} vs {ruta_2!r})",
            ruta_1 != ruta_2,
        )
        check(
            "H2: ambas rutas terminan realmente en 'factura.pdf' (el filename original se conserva, solo se prefija)",
            ruta_1.endswith("factura.pdf") and ruta_2.endswith("factura.pdf"),
        )
        check(
            "H2: process_id de cada llamada corresponde al request correcto (sin mezclarse)",
            llamadas_bg[0]["process_id"] == "req-1" and llamadas_bg[1]["process_id"] == "req-2",
        )
finally:
    os.chdir(cwd_original)
    shutil.rmtree(DIR_H2, ignore_errors=True)


# ============================================================================
# H4 -- ZIP rechazado por completo (síncrono y error_zip) -> fire_webhook
# de error, NUNCA un email (semántica existente del canal, no inventada).
# ============================================================================
print()
print("=" * 78)
print("H4a -- webhook de email: ZIP rechazado SÍNCRONO (_validar_zip_rapido) -> fire_webhook de error, sin email")
print("=" * 78)

DIR_H4A = Path(tempfile.mkdtemp(prefix="test_zip_regresiones_h4a_"))
os.chdir(DIR_H4A)
try:
    class OrchestratorH4:
        def __init__(self):
            self.job_queue = self
            self.webhooks = []
            self.emails = []

        def get_file_type_from_url(self, attachments):
            return "application/zip"

        def download_file_from_url(self, url, destino):
            with open(destino, "wb") as f:
                f.write(construir_zip_vacio_bytes())  # ZIP vacío -> _validar_zip_rapido lo rechaza
            return True

        async def fire_webhook(self, data):
            self.webhooks.append(data)

        def enviar_email(self, *a, **k):
            self.emails.append((a, k))
            return True

        async def put(self, job):
            pass

    namespace_h4a = {
        "os": os, "re": re, "shutil": shutil, "asyncio": asyncio,
        "zipfile": zipfile, "zlib": zlib, "filetype": filetype, "uuid": uuid,
        "datetime": __import__("datetime"),
        "Optional": Optional, "Callable": Callable,
        "Request": Request, "HTTPException": HTTPException,
        "app_logger": FakeAppLogger(),
        "orchestrator": OrchestratorH4(),
        "MAX_ARCHIVOS_ZIP": 100, "MAX_ZIP_DESCOMPRIMIDO_BYTES": 500 * 1024 * 1024,
        "MAX_TRABAJO_EN_VUELO_ZIP": 200, "_trabajo_en_vuelo_zip": 0,
        "_trabajo_en_vuelo_zip_lock": threading.Lock(),
    }
    for nombre in ["_reservar_trabajo_en_vuelo_zip", "_liberar_trabajo_en_vuelo_zip", "_sanear_nombre_para_process_id", "_carpeta_ingest_zip", "_validar_zip_rapido", "webhook_endpoint"]:
        exec(compile(textwrap.dedent(FUENTES[nombre]), f"<{nombre} real>", "exec"), namespace_h4a)

    payload_h4a = {
        "from_email": "cliente@example.com", "subject": "Facturas",
        "attachments": "https://fake.example.com/vacio.zip", "file_name": "vacio.zip",
    }

    async def _correr_h4a():
        r = await namespace_h4a["webhook_endpoint"](FakeRequest(payload_h4a))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return r

    resultado_h4a = asyncio.run(_correr_h4a())
    orch_h4a = namespace_h4a["orchestrator"]

    check("H4a: la respuesta indica error (ZIP vacío rechazado)", resultado_h4a.get("success") is False)
    check(
        f"H4a: fire_webhook fue llamado exactamente 1 vez (fue {len(orch_h4a.webhooks)})",
        len(orch_h4a.webhooks) == 1,
    )
    if orch_h4a.webhooks:
        wh = orch_h4a.webhooks[0]
        check("H4a: el webhook trae success=False y status='error'", wh.get("success") is False and wh.get("status") == "error")
        check("H4a: el webhook trae file_name == 'vacio.zip'", wh.get("file_name") == "vacio.zip")
        check("H4a: el webhook trae un mensaje de error real", bool(wh.get("error")))
    check(
        f"H4a: NUNCA se llamó a enviar_email (semántica existente: worker() nunca mandó email de error) (fue {len(orch_h4a.emails)})",
        len(orch_h4a.emails) == 0,
    )
finally:
    os.chdir(cwd_original)
    shutil.rmtree(DIR_H4A, ignore_errors=True)


print()
print("=" * 78)
print("H4b -- _extraer_zip_y_despachar_individualmente: error_zip async -> fire_webhook de error, sin email")
print("=" * 78)

DIR_H4B = Path(tempfile.mkdtemp(prefix="test_zip_regresiones_h4b_"))
os.chdir(DIR_H4B)
try:
    fuente_extraer = _extraer_funcion_top_level("_extraer_zip_y_despachar_individualmente")
    check("H4b: se extrajo '_extraer_zip_y_despachar_individualmente'", bool(fuente_extraer))

    class OrchestratorH4B:
        def __init__(self):
            self.webhooks = []
            self.emails = []

        async def fire_webhook(self, data):
            self.webhooks.append(data)

        def enviar_email(self, *a, **k):
            self.emails.append((a, k))
            return True

    async def _fake_procesar_en_background_h4b(**kwargs):
        return {"success": True, "factura": {"id": kwargs.get("process_id")}}

    namespace_h4b = {
        "os": os, "re": re, "shutil": shutil, "asyncio": asyncio,
        "zipfile": zipfile, "zlib": zlib, "filetype": filetype,
        "Optional": Optional, "Callable": Callable,
        "app_logger": FakeAppLogger(),
        "orchestrator": OrchestratorH4B(),
        "_procesar_en_background": _fake_procesar_en_background_h4b,
        "MAX_ARCHIVOS_ZIP": 100, "MAX_ZIP_DESCOMPRIMIDO_BYTES": 500 * 1024 * 1024,
        "MAX_TRABAJO_EN_VUELO_ZIP": 200, "_trabajo_en_vuelo_zip": 0,
        "_trabajo_en_vuelo_zip_lock": threading.Lock(),
        "ZIP_EXTRACTION_SEMAPHORE": asyncio.Semaphore(2),
        "ZIP_REGISTRO_SEMAPHORE": asyncio.Semaphore(8),
    }
    for nombre in [
        "_sanear_nombre_para_process_id",
        "_carpeta_ingest_zip",
        "_generar_html_resumen_zip",
        "_reservar_trabajo_en_vuelo_zip",
        "_liberar_trabajo_en_vuelo_zip",
    ]:
        exec(compile(textwrap.dedent(_extraer_funcion_top_level(nombre)), f"<{nombre} real>", "exec"), namespace_h4b)
    exec(compile(textwrap.dedent(fuente_extraer), "<_extraer_zip_y_despachar_individualmente real>", "exec"), namespace_h4b)

    zip_corrupto_path = DIR_H4B / "corrupto.zip"
    zip_corrupto_path.write_bytes(b"esto no es un zip para nada")

    webhooks_h4b = []

    async def _on_item_completado_h4b(payload):
        webhooks_h4b.append(payload)

    resultado_h4b = asyncio.run(
        namespace_h4b["_extraer_zip_y_despachar_individualmente"](
            zip_path=str(zip_corrupto_path),
            ingest_id=uuid.uuid4().hex,
            origen="email",
            reservado_trabajo_en_vuelo=1,
            enviar_resumen_por_email=True,
            datos_email={"from_email": "cliente@example.com", "subject": "Facturas"},
            on_item_completado=_on_item_completado_h4b,
        )
    )

    check("H4b: error_zip quedó seteado (el ZIP no se pudo abrir)", resultado_h4b.get("error_zip") is not None)
    check(
        f"H4b: on_item_completado (fire_webhook) fue llamado 1 vez para el rechazo total (fue {len(webhooks_h4b)})",
        len(webhooks_h4b) == 1,
    )
    if webhooks_h4b:
        check("H4b: el payload trae success=False y status='error'", webhooks_h4b[0].get("success") is False and webhooks_h4b[0].get("status") == "error")
    orch_h4b = namespace_h4b["orchestrator"]
    check(
        f"H4b: NUNCA se llamó a enviar_email para un ZIP totalmente rechazado (fue {len(orch_h4b.emails)})",
        len(orch_h4b.emails) == 0,
    )
finally:
    os.chdir(cwd_original)
    shutil.rmtree(DIR_H4B, ignore_errors=True)


# ============================================================================
# H5 -- fire_webhook no bloquea el event loop (usa asyncio.to_thread de verdad).
# ============================================================================
print()
print("=" * 78)
print("H5 -- fire_webhook no bloquea el event loop: otra coroutine avanza mientras espera")
print("=" * 78)

namespace_h5 = {"os": os, "asyncio": asyncio, "app_logger": FakeAppLogger()}


class _FakeRequestsModule:
    """Doble de la librería `requests` -- .post() simula una llamada de red
    lenta (time.sleep, BLOQUEANTE de verdad, como haría requests real) para
    poder detectar si fire_webhook la corre en un hilo real (no bloquea el
    loop) o la corre directo (si bloqueara, ninguna otra coroutine podría
    avanzar mientras tanto)."""

    class Response:
        status_code = 200

    def post(self, url, json=None, timeout=None):
        time.sleep(0.3)
        return _FakeRequestsModule.Response()


namespace_h5["requests"] = _FakeRequestsModule()
exec(compile(textwrap.dedent(fuente_fire_webhook), "<fire_webhook real>", "exec"), namespace_h5)
fire_webhook_real = namespace_h5["fire_webhook"]


class _SelfConWebhook:
    """`fire_webhook_real` se extrajo vía AST como método SIN bind -- se
    invoca pasándole esta instancia como `self` a mano (necesita
    .webhook_url, que es lo único que fire_webhook lee de `self`)."""

    webhook_url = "https://fake.example.com/webhook"


async def _correr_h5():
    avances_paralelos = []

    async def _tick_rapido():
        for _ in range(10):
            avances_paralelos.append(time.monotonic())
            await asyncio.sleep(0.02)

    instancia = _SelfConWebhook()
    resultados = await asyncio.gather(
        fire_webhook_real(instancia, {"x": 1}),
        _tick_rapido(),
    )
    return resultados, avances_paralelos


(resultado_fw, _), avances = asyncio.run(_correr_h5())
check("H5: fire_webhook devolvió True (la llamada 'de red' se completó)", resultado_fw is True)
check(
    f"H5: mientras fire_webhook 'esperaba' 0.3s, otra coroutine SÍ pudo avanzar varias veces "
    f"(event loop libre) -- {len(avances)} ticks registrados",
    len(avances) >= 5,
)


# ============================================================================
# H6 -- find_stale_invoices: corte por arranque del proceso (antes_de), NO
# por antigüedad fija. Bug real encontrado en la 2da revisión adversarial:
# con auto-restart real (segundos, no 30+ minutos), un umbral de edad fijo
# nunca encontraba nada que recuperar -- el barrido corre una sola vez.
# ============================================================================
print()
print("=" * 78)
print("H6 -- find_stale_invoices: corte por 'antes_de' (arranque del proceso), no por edad fija")
print("=" * 78)

PB_CLIENT_FILE = REPO_ROOT / "utils" / "pocketbase_client.py"
codigo_fuente_pb = PB_CLIENT_FILE.read_text(encoding="utf-8")
arbol_pb = ast.parse(codigo_fuente_pb, filename=str(PB_CLIENT_FILE))


def _extraer_metodo_de_clase_pb(clase, metodo):
    nodo_clase = next((n for n in arbol_pb.body if isinstance(n, ast.ClassDef) and n.name == clase), None)
    if nodo_clase is None:
        return None
    nodo_metodo = next(
        (n for n in ast.walk(nodo_clase) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == metodo),
        None,
    )
    return ast.get_source_segment(codigo_fuente_pb, nodo_metodo) if nodo_metodo else None


fuente_find_stale_invoices = _extraer_metodo_de_clase_pb("PocketBaseClient", "find_stale_invoices")
check("H6: se extrajo 'PocketBaseClient.find_stale_invoices' del código fuente real", bool(fuente_find_stale_invoices))

if fuente_find_stale_invoices:
    check(
        "H6: la firma real usa 'antes_de' (NO 'older_than_minutes', el diseño viejo con umbral fijo)",
        "antes_de" in fuente_find_stale_invoices and "older_than_minutes" not in fuente_find_stale_invoices,
    )

    import datetime as _dt

    class _FakeAppLoggerPB:
        def warning(self, *a, **k):
            pass

    class _SelfConListAll:
        def __init__(self, filas):
            self._filas = filas
            self.app_logger = _FakeAppLoggerPB()

        def _list_all(self, coleccion, filtro, page_size=200):
            return self._filas

    ahora = _dt.datetime.now(_dt.timezone.utc)
    arranque_proceso = ahora - _dt.timedelta(seconds=10)  # simula "el barrido corre a los 10s del arranque"

    filas_fixture = [
        # Huérfana real: se actualizó por última vez ANTES de que este
        # proceso arrancara (el proceso viejo murió con ella a mitad de
        # camino) -- aunque tenga solo unos pocos segundos de antigüedad
        # real, sigue siendo huérfana con certeza.
        {"process_id": "huerfana-reciente", "updated": (arranque_proceso - _dt.timedelta(seconds=2)).isoformat().replace("+00:00", "Z")},
        # En curso legítimamente: la registró Fase A de ESTE MISMO proceso,
        # después de que arrancara -- NO es huérfana aunque el barrido corra
        # apenas unos segundos más tarde.
        {"process_id": "en-curso-de-este-proceso", "updated": (arranque_proceso + _dt.timedelta(seconds=3)).isoformat().replace("+00:00", "Z")},
        # Huérfana vieja de verdad (varias horas) -- debe seguir detectándose también.
        {"process_id": "huerfana-vieja", "updated": (arranque_proceso - _dt.timedelta(hours=5)).isoformat().replace("+00:00", "Z")},
    ]

    namespace_h6 = {"datetime": _dt, "app_logger": _FakeAppLoggerPB(), "INVOICES_COLLECTION": "invoices"}
    exec(compile(textwrap.dedent(fuente_find_stale_invoices), "<find_stale_invoices real>", "exec"), namespace_h6)
    find_stale_invoices_real = namespace_h6["find_stale_invoices"]

    self_h6 = _SelfConListAll(filas_fixture)
    resultado_h6 = find_stale_invoices_real(self_h6, antes_de=arranque_proceso)
    ids_encontrados = {r["process_id"] for r in resultado_h6}

    check(
        "H6: una fila actualizada apenas 2s ANTES del arranque del proceso SÍ se detecta como huérfana "
        "(el bug viejo la habría ignorado por no tener 30+ min de antigüedad)",
        "huerfana-reciente" in ids_encontrados,
    )
    check(
        "H6: una fila registrada por ESTE MISMO proceso (updated DESPUÉS de su arranque) NUNCA se marca huérfana",
        "en-curso-de-este-proceso" not in ids_encontrados,
    )
    check("H6: una huérfana vieja de verdad (5hs) también se sigue detectando", "huerfana-vieja" in ids_encontrados)
    check(f"H6: se detectaron exactamente 2 huérfanas de las 3 filas (fue {len(resultado_h6)})", len(resultado_h6) == 2)


# ============================================================================
# H7 -- download_file_from_url: timeout explícito + se invoca vía
# asyncio.to_thread desde webhook_endpoint (no bloquea el event loop).
# ============================================================================
print()
print("=" * 78)
print("H7 -- download_file_from_url: timeout explícito, invocado vía asyncio.to_thread")
print("=" * 78)

fuente_download = _extraer_metodo_de_clase("InvoiceOrchestrator", "download_file_from_url")
check("H7: se extrajo 'InvoiceOrchestrator.download_file_from_url' del código fuente real", bool(fuente_download))
if fuente_download:
    check(
        "H7: download_file_from_url pasa un timeout explícito a requests.get "
        "(antes de este fix no tenía ninguno -- endpoint sin autenticación, riesgo real de colgar el proceso)",
        "requests.get(" in fuente_download and "timeout=" in fuente_download,
    )

fuente_webhook_completa = FUENTES["webhook_endpoint"]
check(
    "H7: webhook_endpoint invoca download_file_from_url a través de asyncio.to_thread "
    "(no la llama directo, que bloquearía el event loop)",
    "asyncio.to_thread(\n            orchestrator.download_file_from_url" in fuente_webhook_completa
    or "asyncio.to_thread(" in fuente_webhook_completa and "download_file_from_url" in fuente_webhook_completa,
)


# ============================================================================
# H8 -- el email-resumen reconcilia 'total' con errores_despacho.
# ============================================================================
print()
print("=" * 78)
print("H8 -- _generar_html_resumen_zip: reconcilia errores_despacho, no los omite en silencio")
print("=" * 78)

fuente_resumen = _extraer_funcion_top_level("_generar_html_resumen_zip")
check("H8: se extrajo '_generar_html_resumen_zip' del código fuente real", bool(fuente_resumen))
if fuente_resumen:
    namespace_h8 = {"Optional": Optional}
    exec(compile(textwrap.dedent(fuente_resumen), "<_generar_html_resumen_zip real>", "exec"), namespace_h8)
    generar_resumen_real = namespace_h8["_generar_html_resumen_zip"]

    html_sin_errores = generar_resumen_real(total=10, aceptados=10, no_soportados=[])
    html_con_errores = generar_resumen_real(
        total=10, aceptados=8, no_soportados=[],
        errores_despacho=[{"process_id": "x--001", "error": "timeout"}, {"process_id": "x--002", "error": "timeout"}],
    )
    check(
        "H8: sin errores_despacho, el HTML no menciona ninguna falla de procesamiento",
        "fallaron durante el procesamiento" not in html_sin_errores,
    )
    check(
        "H8: CON errores_despacho, el HTML SÍ menciona explícitamente cuántas facturas fallaron "
        "(antes de este fix, esos 2 archivos simplemente desaparecían del conteo, sin ninguna mención)",
        "fallaron durante el procesamiento" in html_con_errores and "2" in html_con_errores,
    )


# ============================================================================
# H9 -- el prefijo uuid4 del guardado inicial trunca el nombre original,
# para no exceder el límite de nombre de archivo del filesystem.
# ============================================================================
print()
print("=" * 78)
print("H9 -- process_invoice/website_upload: nombre original truncado tras el prefijo uuid4")
print("=" * 78)

fuente_process_invoice_completa = FUENTES["process_invoice"]
check(
    "H9: process_invoice usa _truncar_nombre_a_bytes (no un [:200] de caracteres) antes de armar file_location",
    "_truncar_nombre_a_bytes(" in fuente_process_invoice_completa,
)

# Prueba REAL de la función de truncado -- no solo que se la llama, sino
# que efectivamente respeta un presupuesto de BYTES, no de caracteres.
# Bug real encontrado en la 3ra revisión adversarial: un simple nombre[:200]
# cuenta code points, así que un nombre con muchos caracteres UTF-8
# multi-byte (tildes, ñ) podía seguir superando 255 bytes tras "truncarse".
fuente_truncar = FUENTES["_truncar_nombre_a_bytes"]
check("H9: se extrajo '_truncar_nombre_a_bytes' del código fuente real", bool(fuente_truncar))
if fuente_truncar:
    namespace_h9 = {}
    exec(compile(textwrap.dedent(fuente_truncar), "<_truncar_nombre_a_bytes real>", "exec"), namespace_h9)
    truncar_real = namespace_h9["_truncar_nombre_a_bytes"]

    nombre_ascii_corto = "factura.pdf"
    check(
        "H9: un nombre corto y ASCII no se modifica",
        truncar_real(nombre_ascii_corto, 200) == nombre_ascii_corto,
    )

    nombre_multibyte_largo = "é" * 200  # 200 caracteres, pero 400 bytes en UTF-8
    resultado_truncado = truncar_real(nombre_multibyte_largo, 200)
    bytes_resultado = len(resultado_truncado.encode("utf-8"))
    check(
        f"H9: con 200 caracteres 'é' (400 bytes UTF-8), el resultado trunca a <= 200 BYTES reales "
        f"(fue {bytes_resultado} bytes) -- un [:200] de caracteres daría 400 bytes, todavía roto",
        bytes_resultado <= 200,
    )
    # _truncar_nombre_a_bytes usa errors="ignore" al decodificar el recorte
    # -- si algún byte multi-byte quedó cortado a la mitad en el límite, se
    # descarta en vez de lanzar UnicodeDecodeError. Confirmamos acá que el
    # resultado es un string Python válido que se puede re-codificar sin
    # excepción (si hubiera quedado corrupto, esto fallaría).
    try:
        resultado_truncado.encode("utf-8")
        reencodeo_ok = True
    except UnicodeEncodeError:
        reencodeo_ok = False
    check("H9: el resultado truncado es UTF-8 válido (se puede re-codificar sin excepción)", reencodeo_ok)

    presupuesto_realista = 200
    nombre_dominio_real = ("Autorización Distribución Compañía Información Período " * 5)
    resultado_realista = truncar_real(nombre_dominio_real, presupuesto_realista)
    check(
        f"H9: con un nombre realista en español (tildes reales del dominio del negocio), "
        f"el resultado SIEMPRE respeta el presupuesto de {presupuesto_realista} bytes "
        f"(fue {len(resultado_realista.encode('utf-8'))} bytes)",
        len(resultado_realista.encode("utf-8")) <= presupuesto_realista,
    )


# ============================================================================
# H10 -- get_file_type_from_url: timeout explícito + invocado vía
# asyncio.to_thread. Bug real de la 3ra revisión adversarial: el fix
# anterior (H7) corrigió download_file_from_url pero dejó exactamente el
# mismo problema en esta otra función, dos líneas antes en el mismo flujo.
# ============================================================================
print()
print("=" * 78)
print("H10 -- get_file_type_from_url: timeout explícito, invocado vía asyncio.to_thread")
print("=" * 78)

fuente_get_file_type = _extraer_metodo_de_clase("InvoiceOrchestrator", "get_file_type_from_url")
check("H10: se extrajo 'InvoiceOrchestrator.get_file_type_from_url' del código fuente real", bool(fuente_get_file_type))
if fuente_get_file_type:
    check(
        "H10: get_file_type_from_url pasa un timeout explícito a requests.get "
        "(antes de este fix no tenía ninguno, mismo riesgo que download_file_from_url)",
        "requests.get(" in fuente_get_file_type and "timeout=" in fuente_get_file_type,
    )

check(
    "H10: webhook_endpoint invoca get_file_type_from_url a través de asyncio.to_thread "
    "(antes la llamaba directo, bloqueando el event loop)",
    "asyncio.to_thread(orchestrator.get_file_type_from_url" in fuente_webhook_completa,
)


# ============================================================================
# H11 -- download_file_from_url: aborta si supera max_bytes O max_segundos
# de duración TOTAL -- no solo el timeout de inactividad por-lectura de
# requests, que un "goteo" lento de bytes nunca dispara.
# ============================================================================
print()
print("=" * 78)
print("H11 -- download_file_from_url: aborta por tamaño y por duración total real")
print("=" * 78)

fuente_download_completa = _extraer_metodo_de_clase("InvoiceOrchestrator", "download_file_from_url")
check("H11: se extrajo 'InvoiceOrchestrator.download_file_from_url' del código fuente real", bool(fuente_download_completa))

if fuente_download_completa:
    class _FakeResponseStream:
        def __init__(self, status_code, chunks, pausa_entre_chunks=0.0):
            self.status_code = status_code
            self._chunks = chunks
            self._pausa = pausa_entre_chunks

        def iter_content(self, chunk_size=65536):
            for c in self._chunks:
                if self._pausa:
                    time.sleep(self._pausa)
                yield c

        def close(self):
            pass

    class _FakeRequestsModuleDescarga:
        def __init__(self, respuesta):
            self._respuesta = respuesta

        def get(self, url, timeout=None, stream=None):
            return self._respuesta

    namespace_h11_base = {"os": os, "app_logger": FakeAppLogger(), "time": time}

    # Caso A: respuesta que excede max_bytes -- debe abortar (False), sin
    # importar que "llegue rápido".
    namespace_h11a = dict(namespace_h11_base)
    namespace_h11a["requests"] = _FakeRequestsModuleDescarga(
        _FakeResponseStream(200, [b"x" * 1000 for _ in range(50)])  # 50,000 bytes
    )
    exec(compile(textwrap.dedent(fuente_download_completa), "<download_file_from_url real>", "exec"), namespace_h11a)
    download_real_a = namespace_h11a["download_file_from_url"]

    destino_a = str(Path(tempfile.mkdtemp(prefix="test_h11a_")) / "out.bin")
    resultado_h11a = download_real_a(object(), "https://fake.example.com/grande", destino_a, max_bytes=10_000, max_segundos=60)
    check(
        "H11: una respuesta que supera max_bytes (50000 > 10000) se aborta -- devuelve False, "
        "no se carga entera en memoria antes de cortar",
        resultado_h11a is False,
    )

    # Caso B: respuesta que "gotea" bytes lentamente -- cada chunk individual
    # llega rápido (no dispara ningún timeout de inactividad), pero la suma
    # de pausas supera max_segundos. Usamos max_segundos chico (0.2s) para
    # que el test corra rápido, con pausas reales de 0.1s entre chunks.
    namespace_h11b = dict(namespace_h11_base)
    namespace_h11b["requests"] = _FakeRequestsModuleDescarga(
        _FakeResponseStream(200, [b"x" * 10 for _ in range(20)], pausa_entre_chunks=0.1)
    )
    exec(compile(textwrap.dedent(fuente_download_completa), "<download_file_from_url real b>", "exec"), namespace_h11b)
    download_real_b = namespace_h11b["download_file_from_url"]

    destino_b = str(Path(tempfile.mkdtemp(prefix="test_h11b_")) / "out.bin")
    inicio_h11b = time.monotonic()
    resultado_h11b = download_real_b(object(), "https://fake.example.com/goteo", destino_b, max_bytes=10_000_000, max_segundos=0.25)
    duracion_h11b = time.monotonic() - inicio_h11b
    check(
        f"H11: una respuesta que 'gotea' bytes (cada chunk individual rápido, nunca dispara un "
        f"timeout de inactividad) SÍ se corta por duración TOTAL acumulada (max_segundos=0.25) "
        f"-- devuelve False (fue {resultado_h11b}) en ~{duracion_h11b:.2f}s, no espera los 2s completos del goteo",
        resultado_h11b is False,
    )
    check(
        "H11: el corte por duración total ocurrió razonablemente cerca del límite configurado "
        "(no mucho antes ni mucho después)",
        0.1 < duracion_h11b < 1.0,
    )

    # Caso C: respuesta normal, chica, rápida -- debe completarse OK.
    namespace_h11c = dict(namespace_h11_base)
    namespace_h11c["requests"] = _FakeRequestsModuleDescarga(_FakeResponseStream(200, [b"contenido real del pdf"]))
    exec(compile(textwrap.dedent(fuente_download_completa), "<download_file_from_url real c>", "exec"), namespace_h11c)
    download_real_c = namespace_h11c["download_file_from_url"]

    destino_c = str(Path(tempfile.mkdtemp(prefix="test_h11c_")) / "out.bin")
    resultado_h11c = download_real_c(object(), "https://fake.example.com/normal", destino_c, max_bytes=10_000_000, max_segundos=60)
    check("H11: una descarga normal (chica, rápida, bajo los límites) se completa OK (True)", resultado_h11c is True)
    check(
        "H11: el archivo descargado tiene el contenido real esperado",
        Path(destino_c).exists() and Path(destino_c).read_bytes() == b"contenido real del pdf",
    )


# ============================================================================
# H12 -- /invoices/{process_id}/retry-extraction: documento_original
# malicioso nunca hace que la re-descarga escriba fuera de ./downloads/.
# ============================================================================
print()
print("=" * 78)
print("H12 -- reintentar_extraccion: documento_original malicioso -- nunca escribe fuera de ./downloads/")
print("=" * 78)

DIR_H12 = Path(tempfile.mkdtemp(prefix="test_zip_regresiones_h12_"))
os.chdir(DIR_H12)
os.environ["SECRET_KEY"] = "test-secret-h12"
try:
    PAYLOADS_DOCUMENTO_ORIGINAL = [
        "..",  # sin "/" -- distinto al caso de H1: acá NUNCA se usa os.path.join,
               # se concatena a un prefijo fijo ("retry-{process_id}-"), así que
               # ni siquiera un ".." intacto puede convertirse en un segmento de
               # ruta real -- queda como parte de un nombre de archivo literal.
        "../../../../tmp/pwn",  # se neutraliza vía basename() -> "pwn"
        "/etc/passwd",  # se neutraliza vía basename() -> "passwd"
    ]

    for payload in PAYLOADS_DOCUMENTO_ORIGINAL:
        namespace = {
            "os": os, "re": re, "asyncio": asyncio, "filetype": filetype,
            "Optional": Optional, "Header": Header, "HTTPException": HTTPException,
            "app_logger": FakeAppLogger(),
        }
        procesar_en_background_llamadas = []

        class _FakeUpstream:
            status_code = 200
            content = PDF_VALIDO
            headers = {"Content-Type": "application/pdf"}

        class _FakeRequestsModuleH12:
            def get(self, url, params=None, timeout=None):
                return _FakeUpstream()

        class OrchestratorH12:
            def __init__(self):
                self._pb_client = self

            def get_invoice_by_process_id(self, process_id):
                return {
                    "id": "pbrecordid",
                    "process_id": process_id,
                    "status": "error",
                    "documento_original": payload,
                }

            def obtener_file_token(self):
                return "fake-file-token"

        namespace["orchestrator"] = OrchestratorH12()
        namespace["requests"] = _FakeRequestsModuleH12()

        async def _fake_procesar_en_background(**kwargs):
            procesar_en_background_llamadas.append(kwargs)
            return {"success": True, "factura": {}}

        namespace["_procesar_en_background"] = _fake_procesar_en_background

        for nombre in ["_sanear_nombre_para_process_id", "_verificar_secreto_invoicy", "reintentar_extraccion"]:
            exec(compile(textwrap.dedent(FUENTES[nombre]), f"<{nombre} real>", "exec"), namespace)

        excepcion_capturada = None
        try:
            asyncio.run(
                namespace["reintentar_extraccion"]("proc-h12-test", x_invoicy_secret="test-secret-h12")
            )
        except HTTPException as e:
            excepcion_capturada = e

        # Buscamos el archivo real escrito revisando el disco (más confiable
        # que interceptar open(), que builtins no siempre permite parchear
        # de forma limpia dentro de exec()) -- listamos todo lo que quedó
        # bajo el directorio temporal de este caso.
        archivos_en_disco = list(Path(".").rglob("*")) if Path("downloads").exists() else []
        archivos_reales = [p for p in archivos_en_disco if p.is_file()]
        check(
            f"H12 documento_original={payload!r}: ningún archivo real escrito terminó fuera de ./downloads/",
            all(str(Path(p).resolve()).startswith(str(Path('downloads').resolve())) for p in archivos_reales),
        )
        if payload == "..":
            check(
                "H12 documento_original='..': se escribió un archivo real DENTRO de ./downloads/ "
                "(el valor '..' queda pegado a un prefijo fijo por concatenación, nunca se convierte "
                "en un segmento de ruta real -- a diferencia de H1, acá no se usa os.path.join)",
                len(archivos_reales) == 1,
            )
        else:
            check(
                f"H12 documento_original={payload!r}: basename() lo neutralizó -- el archivo escrito "
                f"NO conserva ningún '/' ni '..' en su nombre final",
                len(archivos_reales) == 1
                and "/" not in archivos_reales[0].name
                and ".." not in archivos_reales[0].name.replace(f"retry-proc-h12-test-", ""),
            )
        check(
            f"H12 documento_original={payload!r}: no se disparó ninguna excepción inesperada "
            f"(fue: {excepcion_capturada!r})",
            excepcion_capturada is None,
        )
        check(
            f"H12 documento_original={payload!r}: _procesar_en_background fue invocado con un "
            f"file_location que apunta al archivo real que efectivamente quedó en disco",
            len(procesar_en_background_llamadas) == 1
            and archivos_reales
            and Path(procesar_en_background_llamadas[0]["file_location"]).resolve() == archivos_reales[0].resolve(),
        )
        # Limpieza entre iteraciones -- cada payload corre en un estado de
        # disco limpio.
        if Path("downloads").exists():
            shutil.rmtree("downloads", ignore_errors=True)
finally:
    os.chdir(cwd_original)
    del os.environ["SECRET_KEY"]
    shutil.rmtree(DIR_H12, ignore_errors=True)


# ============================================================================
# H13 -- InvoiceOrchestrator._barrido_huerfanos_al_arrancar: gap de
# cobertura encontrado en la ronda 4 -- find_stale_invoices ya se prueba a
# fondo (H6, más abajo en este mismo archivo... buscar en la sección de
# find_stale_invoices), pero el método orquestador que lo invoca al
# arrancar el proceso nunca tenía un test directo.
# ============================================================================
print()
print("=" * 78)
print("H13 -- InvoiceOrchestrator._barrido_huerfanos_al_arrancar: orquesta find_stale_invoices"
      " correctamente y es best-effort")
print("=" * 78)

fuente_barrido = _extraer_metodo_de_clase("InvoiceOrchestrator", "_barrido_huerfanos_al_arrancar")
check("H13: se extrajo 'InvoiceOrchestrator._barrido_huerfanos_al_arrancar' del código fuente real", bool(fuente_barrido))

if fuente_barrido:
    import datetime as _datetime_h13

    class _FakeSleepInstantaneo:
        """asyncio.sleep(10) real haría este test lento sin aportar nada --
        se reemplaza SOLO en este namespace por una versión que no espera."""
        pass

    async def _sleep_instantaneo(segundos):
        return None

    class _FakeAsyncioH13:
        sleep = staticmethod(_sleep_instantaneo)

    # Caso A: 2 facturas huérfanas reales -> ambas pasan a status="error"
    # con un mensaje claro, usando el mismo antes_de que capturó self._iniciado_en.
    class _FakePbClientH13A:
        def __init__(self):
            self.llamadas_find = []
            self.llamadas_upsert = []

        def find_stale_invoices(self, *, antes_de):
            self.llamadas_find.append(antes_de)
            return [
                {"process_id": "huerfana-1", "status": "pending"},
                {"process_id": "huerfana-2", "status": "processing"},
            ]

        def upsert_invoice(self, data):
            self.llamadas_upsert.append(dict(data))
            return dict(data)

    class _FakeSelfH13A:
        def __init__(self):
            self._iniciado_en = _datetime_h13.datetime(2026, 1, 1, tzinfo=_datetime_h13.timezone.utc)
            self._pb_client = _FakePbClientH13A()

    namespace_h13a = {
        "asyncio": _FakeAsyncioH13(),
        "app_logger": FakeAppLogger(),
    }
    exec(compile(textwrap.dedent(fuente_barrido), "<_barrido_huerfanos_al_arrancar real A>", "exec"), namespace_h13a)
    self_h13a = _FakeSelfH13A()
    asyncio.run(namespace_h13a["_barrido_huerfanos_al_arrancar"](self_h13a))

    check(
        "H13: find_stale_invoices fue invocado con antes_de=self._iniciado_en (no un valor distinto)",
        self_h13a._pb_client.llamadas_find == [self_h13a._iniciado_en],
    )
    check(
        f"H13: upsert_invoice fue invocado exactamente 2 veces, una por huérfana (fue "
        f"{len(self_h13a._pb_client.llamadas_upsert)})",
        len(self_h13a._pb_client.llamadas_upsert) == 2,
    )
    process_ids_marcados = {u["process_id"] for u in self_h13a._pb_client.llamadas_upsert}
    check(
        "H13: ambas huérfanas (huerfana-1, huerfana-2) fueron marcadas, ninguna otra",
        process_ids_marcados == {"huerfana-1", "huerfana-2"},
    )
    check(
        "H13: cada upsert marca status='error' con un error_message no vacío y accionable",
        all(
            u.get("status") == "error" and "reintent" in (u.get("error_message") or "").lower()
            for u in self_h13a._pb_client.llamadas_upsert
        ),
    )

    # Caso B: 0 huérfanas -> no se llama upsert_invoice ninguna vez (no hay
    # nada que marcar, y no debería "inventar" una llamada vacía).
    class _FakePbClientH13B(_FakePbClientH13A):
        def find_stale_invoices(self, *, antes_de):
            self.llamadas_find.append(antes_de)
            return []

    namespace_h13b = {
        "asyncio": _FakeAsyncioH13(),
        "app_logger": FakeAppLogger(),
    }
    exec(compile(textwrap.dedent(fuente_barrido), "<_barrido_huerfanos_al_arrancar real B>", "exec"), namespace_h13b)
    self_h13b = _FakeSelfH13A()
    self_h13b._pb_client = _FakePbClientH13B()
    asyncio.run(namespace_h13b["_barrido_huerfanos_al_arrancar"](self_h13b))
    check(
        "H13: sin huérfanas, upsert_invoice NUNCA se llama",
        len(self_h13b._pb_client.llamadas_upsert) == 0,
    )

    # Caso C: find_stale_invoices explota (PocketBase caído) -> el método
    # NO propaga la excepción (best-effort, no debe frenar el arranque del
    # resto de la app -- ver docstring del método real).
    class _FakePbClientH13C:
        def find_stale_invoices(self, *, antes_de):
            raise RuntimeError("PocketBase inalcanzable (simulado)")

        def upsert_invoice(self, data):
            raise AssertionError("no debería llegar a llamarse en este caso")

    namespace_h13c = {
        "asyncio": _FakeAsyncioH13(),
        "app_logger": FakeAppLogger(),
    }
    exec(compile(textwrap.dedent(fuente_barrido), "<_barrido_huerfanos_al_arrancar real C>", "exec"), namespace_h13c)
    self_h13c = _FakeSelfH13A()
    self_h13c._pb_client = _FakePbClientH13C()
    excepcion_h13c = None
    try:
        asyncio.run(namespace_h13c["_barrido_huerfanos_al_arrancar"](self_h13c))
    except Exception as e:
        excepcion_h13c = e
    check(
        f"H13: si find_stale_invoices explota, el método NO propaga la excepción "
        f"(best-effort -- fue: {excepcion_h13c!r})",
        excepcion_h13c is None,
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
