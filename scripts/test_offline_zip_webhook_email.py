"""
Test offline para la rama ZIP del endpoint `webhook_endpoint` (POST
/gemini2/webhook, el que recibe adjuntos de email) en
routes/process_invoice_google_2.py, tras el REDISEÑO de seguridad/
arquitectura del manejo de ZIP (ver docs/plan-fase1-zip-REDISEÑO.md).

Contexto: una revisión adversarial post-implementación encontró 14
hallazgos (2 críticos) sobre el diseño anterior de ZIP. La firma de
`_extraer_zip_y_despachar_individualmente` cambió por completo -- este
archivo reemplaza la versión vieja, que todavía llamaba con
`base_process_id` (parámetro que ya NO existe) y no cubría ni el
email-resumen ni el fire_webhook por factura, que son nuevos.

Qué prueba ESTE archivo puntualmente (`webhook_endpoint`, canal email) --
lo que NO prueban los otros dos archivos de test de ZIP:
  - `test_offline_zip_identidad_seguridad.py` ya cubre a fondo, con el
    CÓDIGO REAL de `_extraer_zip_y_despachar_individualmente`, todo lo
    relativo a identidad server-side (ingest_id vs client_reference), path
    traversal, colisiones y concurrencia real -- ESTE archivo NO duplica
    esos escenarios.
  - `test_offline_zip_extraccion.py` ya cubre a fondo el INTERIOR de
    `_extraer_zip_y_despachar_individualmente` (fallos parciales por
    miembro, durabilidad del upsert "pending", etc.) -- por eso ACÁ esa
    función es un DOBLE (fake) que solo registra con qué kwargs la
    llamaron. Lo único que importa en este archivo es que
    `webhook_endpoint` la invoque bien.

Lo que SÍ prueba este archivo, con el CÓDIGO REAL de `webhook_endpoint` y
`_validar_zip_rapido` extraído vía AST:
  - Que un ZIP válido dispare la extracción con `asyncio.create_task`
    (sin bloquear la respuesta HTTP) pasándole los kwargs nuevos y
    correctos: `origen="email"`, `ingest_id` IGUAL al `process_id`
    generado server-side (el mismo valor, reusado -- no uno nuevo),
    `reservado_trabajo_en_vuelo` igual a la cantidad de miembros del ZIP
    (el número que reservó `_validar_zip_rapido`, no un valor inventado),
    y -- lo nuevo de esta rama de la revisión adversarial (hallazgo #5,
    granularidad de notificaciones) -- `enviar_resumen_por_email=True`,
    `datos_email` con el `from_email`/`subject` reales del email entrante,
    y `on_item_completado` siendo EXACTAMENTE el método bound real
    `orchestrator.fire_webhook` (así se preserva, por canal email, UN
    email de confirmación por ZIP completo pero UN fire_webhook por
    FACTURA individual -- nunca al revés).
  - Que la respuesta de un ZIP válido sea `status="processing"` y que
    `orchestrator.job_queue.put` (el camino viejo, consumido por
    `worker()`, que no sabe descomprimir nada) NUNCA se haya llamado.
  - Que un ZIP rechazado por `_validar_zip_rapido` (ej. vacío) devuelva el
    error real de inmediato, SIN haber despachado ninguna task -- y sin
    inventar el mensaje de error: se lo pide a la función real por
    separado, sobre una copia idéntica del ZIP, y se compara contra la
    respuesta del endpoint.
  - Que un archivo suelto (PDF, no ZIP) siga exactamente el camino viejo
    sin regresión: `job_queue.put` una vez, `process_id` SIN sufijo,
    `status="enqueued"`, y la rama de ZIP nunca se tocó.
  - Que la task despachada por `create_task` realmente LLEGUE A CORRER una
    vez el event loop tiene oportunidad de ejecutarla (no que quede
    "perdida" sin que nadie la haya awaiteado nunca), confirmando que
    corrió con los mismos kwargs con los que fue despachada.

Nota sobre la comparación de `on_item_completado`: en Python, un método
bound (`instancia.metodo`) es un objeto NUEVO creado en cada acceso al
atributo -- por eso `obj.metodo is obj.metodo` da `False` aunque ambos
"sean" el mismo método del mismo objeto (verificado empíricamente). La
forma correcta de comparar "es el mismo método bound" es con `==`, que
compara `__self__` y `__func__` -- eso es lo que se usa acá (ver
`_es_mismo_metodo_bound`), no `is`, que daría un falso negativo aunque
`webhook_endpoint` pase exactamente `orchestrator.fire_webhook`.

Uso: python3 scripts/test_offline_zip_webhook_email.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import ast
import asyncio
import datetime
import io
import os
import re
import shutil
import sys
import tempfile
import textwrap
import threading
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException, Request  # noqa: E402

try:
    import filetype  # noqa: E402
except ImportError:  # pragma: no cover - dependencia real del repo
    filetype = None

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE = REPO_ROOT / "routes" / "process_invoice_google_2.py"

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


# ============================================================================
# Extracción AST del código real -- NUNCA reimplementar la lógica bajo test.
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
    return ast.get_source_segment(codigo_fuente, nodo) if nodo else None


# Orden de dependencia: _reservar/_liberar_trabajo_en_vuelo_zip (las usa
# _validar_zip_rapido) -> _validar_zip_rapido -> _carpeta_ingest_zip (la usa
# webhook_endpoint) -> webhook_endpoint (usa las 2 anteriores).
# `_extraer_zip_y_despachar_individualmente` NO se extrae acá a propósito --
# en este archivo es un FAKE (ver docstring): su interior ya se prueba a
# fondo en test_offline_zip_extraccion.py, acá solo importa con qué kwargs
# la invoca webhook_endpoint.
NOMBRES_A_EXTRAER = [
    "_reservar_trabajo_en_vuelo_zip",
    "_liberar_trabajo_en_vuelo_zip",
    "_validar_zip_rapido",
    "_carpeta_ingest_zip",
    "_sanear_nombre_para_process_id",
    "webhook_endpoint",
]
FUENTES = {}
for nombre in NOMBRES_A_EXTRAER:
    FUENTES[nombre] = _extraer_funcion_top_level(nombre)
    check(f"Se extrajo '{nombre}' del código fuente real", bool(FUENTES[nombre]))

if FALLOS:
    print("\nNo se pudo extraer el código real -- abortando.")
    sys.exit(1)


# ============================================================================
# Fixtures de bytes reales (verificadas en este repo) y helpers de ZIP.
# ============================================================================
PDF_VALIDO = b"%PDF-1.4\n" + b"X" * 50 + b"\n%%EOF\n"
PNG_VALIDO = bytes.fromhex("89504e470d0a1a0a") + b"0" * 20


def construir_zip_bytes(miembros):
    """miembros: lista de tuplas (nombre_archivo, contenido_bytes)."""
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


class FakeRequest:
    """Doble de fastapi.Request -- solo implementa lo que usa
    webhook_endpoint: .json() y .url"""

    def __init__(self, data):
        self._data = data
        self.url = "http://test/gemini2/webhook"

    async def json(self):
        return self._data


def obtener_process_id(respuesta):
    # La respuesta de éxito usa la clave "process_id"; la de error (rechazo
    # de _validar_zip_rapido) usa "id". Cubrimos ambas.
    return respuesta.get("process_id") or respuesta.get("id")


def _es_mismo_metodo_bound(a, b):
    """Compara si `a` y `b` son el mismo método bound (misma instancia +
    misma función real). NO usa `is`: un método bound (`instancia.metodo`)
    es un objeto envoltorio NUEVO creado en cada acceso al atributo, así
    que `is` da falso negativo aunque ambos accesos apunten exactamente al
    mismo `self` y a la misma función subyacente (verificado empíricamente:
    `obj.metodo is obj.metodo` -> False). `==` sobre métodos bound sí
    compara `__self__` y `__func__`, que es la noción correcta de "es el
    mismo método" acá."""
    return a is not None and b is not None and a == b


# ============================================================================
# Namespace de exec() para el código real.
# ============================================================================
def crear_namespace(file_type, contenido_archivo, download_ok=True):
    """
    Arma un namespace de exec() fresco con:
      - orchestrator FAKE (get_file_type_from_url / download_file_from_url /
        job_queue.put / fire_webhook) que registra cada llamada, sin pegarle
        a red real: get_file_type_from_url devuelve el `file_type` fijado
        por el test, y download_file_from_url escribe `contenido_archivo`
        directamente en el destino que le pase el código real (sin
        descargar nada).
      - app_logger FAKE (no-op).
      - `_extraer_zip_y_despachar_individualmente` FAKE: registra los
        kwargs con los que se la invocó de INMEDIATO, de forma síncrona
        (extraer_zip_llamadas) -- eso es lo que `asyncio.create_task`
        recibe como argumento, y ya está disponible apenas se llama, sin
        que el event loop tenga que darle tiempo de ejecución. Por
        separado, registra cuándo su cuerpo efectivamente corrió una vez
        el event loop despachó la task (extraer_zip_ejecutadas). Esta
        separación importa: en asyncio, `create_task(coro)` solo AGENDA la
        corrutina -- no ejecuta nada de su cuerpo hasta que el loop tiene
        una oportunidad de correrla (típicamente tras un `await` que cede
        el control). Así se puede distinguir "el endpoint despachó la task
        con estos kwargs" (verificable de inmediato, escenario B1) de "la
        task despachada realmente corrió" (solo verificable tras ceder el
        control al loop, escenario B4).
      - `_reservar_trabajo_en_vuelo_zip` / `_liberar_trabajo_en_vuelo_zip` /
        `_validar_zip_rapido` / `_carpeta_ingest_zip` / `webhook_endpoint`:
        CÓDIGO REAL extraído del archivo fuente vía AST, exec'eado en este
        mismo namespace -- así la llamada interna de webhook_endpoint a
        `_validar_zip_rapido` (y la de ésta a `_reservar_trabajo_en_vuelo_zip`)
        resuelve a la versión real, no a un mock.
      - Las constantes reales de producción (MAX_ARCHIVOS_ZIP=100,
        MAX_ZIP_DESCOMPRIMIDO_BYTES=500MB, MAX_TRABAJO_EN_VUELO_ZIP=200) y
        el estado en memoria que necesitan (`_trabajo_en_vuelo_zip` fresco
        en 0 por namespace, con su propio lock).
    Devuelve (namespace, llamadas, orchestrator_fake).
    """
    llamadas = {
        "get_file_type_from_url": [],
        "download_file_from_url": [],
        "job_queue_put": [],
        "extraer_zip_llamadas": [],
        "extraer_zip_ejecutadas": [],
    }

    class FakeJobQueue:
        async def put(self, job):
            llamadas["job_queue_put"].append(job)

    class FakeOrchestrator:
        def __init__(self):
            self.job_queue = FakeJobQueue()

        def get_file_type_from_url(self, attachments):
            llamadas["get_file_type_from_url"].append(attachments)
            return file_type

        def download_file_from_url(self, url, destino):
            llamadas["download_file_from_url"].append({"url": url, "destino": destino})
            with open(destino, "wb") as f:
                f.write(contenido_archivo)
            return download_ok

        async def fire_webhook(self, data):
            # No lo invoca este test (el interior de
            # _extraer_zip_y_despachar_individualmente está faked), pero
            # tiene que existir como método bound real para que
            # `on_item_completado == orchestrator.fire_webhook` tenga
            # sentido comparar.
            pass

    class FakeLogger:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

    orchestrator_fake = FakeOrchestrator()

    def fake_extraer_zip_y_despachar_individualmente(**kwargs):
        # Registro SÍNCRONO -- corre apenas se llama la función, exactamente
        # como lo vería `asyncio.create_task(...)` al recibir su argumento.
        llamadas["extraer_zip_llamadas"].append(kwargs)

        async def _cuerpo():
            # Esto solo corre cuando el event loop efectivamente despacha la
            # Task creada por asyncio.create_task (p.ej. tras un
            # `await asyncio.sleep(0)`).
            llamadas["extraer_zip_ejecutadas"].append(kwargs)

        return _cuerpo()

    namespace = {
        "os": os,
        "re": re,
        "shutil": shutil,
        "zipfile": zipfile,
        "zlib": zlib,
        "filetype": filetype,
        "Optional": Optional,
        "Callable": Callable,
        "uuid": uuid,
        "datetime": datetime,
        "asyncio": asyncio,
        "threading": threading,
        "HTTPException": HTTPException,
        "Request": Request,
        "orchestrator": orchestrator_fake,
        "app_logger": FakeLogger(),
        "_extraer_zip_y_despachar_individualmente": fake_extraer_zip_y_despachar_individualmente,
        "MAX_ARCHIVOS_ZIP": 100,
        "MAX_ZIP_DESCOMPRIMIDO_BYTES": 500 * 1024 * 1024,
        "MAX_TRABAJO_EN_VUELO_ZIP": 200,
        "_trabajo_en_vuelo_zip": 0,
        "_trabajo_en_vuelo_zip_lock": threading.Lock(),
        "ZIP_EXTRACTION_SEMAPHORE": asyncio.Semaphore(2),
    }

    for nombre in NOMBRES_A_EXTRAER:
        exec(compile(textwrap.dedent(FUENTES[nombre]), f"<{nombre} real>", "exec"), namespace)

    return namespace, llamadas, orchestrator_fake


# ============================================================================
# Sanity check: el código real extraído se pudo compilar y quedó invocable.
# ============================================================================
_namespace_sanity, _llamadas_sanity, _orch_sanity = crear_namespace("application/pdf", PDF_VALIDO)
for nombre in NOMBRES_A_EXTRAER:
    check(
        f"La función real '{nombre}' (código real) se pudo compilar vía exec",
        callable(_namespace_sanity.get(nombre)),
    )

if FALLOS:
    print("\nNo se pudo compilar el código real extraído -- abortando.")
    sys.exit(1)


# ============================================================================
# Escenarios
# ============================================================================
async def correr_pruebas():
    payload_base = {
        "from_email": "cliente@example.com",
        "from_name": "Cliente Test",
        "subject": "Facturas de agosto",
        "body": "Adjunto para procesar",
        "to_email": "invoicy@platinumhomes.example.com",
    }

    # ------------------------------------------------------------------
    print("=" * 78)
    print("B1 -- ZIP valido (2 miembros): despacha create_task con los kwargs nuevos")
    print("=" * 78)

    zip_bytes_b1 = construir_zip_bytes(
        [("factura_a.pdf", PDF_VALIDO), ("factura_b.png", PNG_VALIDO)]
    )
    namespace_b1, llamadas_b1, orch_b1 = crear_namespace("application/zip", zip_bytes_b1)
    webhook_b1 = namespace_b1["webhook_endpoint"]

    payload_b1 = dict(
        payload_base,
        attachments="https://fake-storage.example.com/facturas.zip",
        file_name="facturas.zip",
    )
    respuesta_b1 = await webhook_b1(FakeRequest(payload_b1))
    pid_b1 = obtener_process_id(respuesta_b1)

    check("B1: la respuesta tiene success=True", respuesta_b1.get("success") is True)
    check(
        "B1: la respuesta tiene status='processing' (NO 'enqueued')",
        respuesta_b1.get("status") == "processing",
    )
    check(
        "B1: total_count de la respuesta coincide con la cantidad de miembros del ZIP (2)",
        respuesta_b1.get("total_count") == 2,
    )
    check(
        "B1: orchestrator.job_queue.put NUNCA fue llamado (el ZIP no va por el camino viejo)",
        len(llamadas_b1["job_queue_put"]) == 0,
    )
    check(
        "B1: _extraer_zip_y_despachar_individualmente fue despachada (create_task) exactamente 1 vez",
        len(llamadas_b1["extraer_zip_llamadas"]) == 1,
    )

    if llamadas_b1["extraer_zip_llamadas"]:
        kwargs_b1 = llamadas_b1["extraer_zip_llamadas"][0]
        check("B1: se despachó con origen='email'", kwargs_b1.get("origen") == "email")
        check(
            "B1: ingest_id es IGUAL al process_id generado por el endpoint (mismo valor, reusado)",
            bool(pid_b1) and kwargs_b1.get("ingest_id") == pid_b1,
        )
        check(
            "B1: reservado_trabajo_en_vuelo == cantidad de miembros del ZIP de prueba (2)",
            kwargs_b1.get("reservado_trabajo_en_vuelo") == 2,
        )
        check(
            "B1: enviar_resumen_por_email quedó en True (un email de confirmación por ZIP)",
            kwargs_b1.get("enviar_resumen_por_email") is True,
        )
        check(
            "B1: datos_email trae el from_email/subject reales del email entrante",
            kwargs_b1.get("datos_email")
            == {"from_email": payload_b1["from_email"], "subject": payload_b1["subject"]},
        )
        check(
            "B1: on_item_completado es exactamente orchestrator.fire_webhook "
            "(mismo self y misma función real -- ver _es_mismo_metodo_bound)",
            _es_mismo_metodo_bound(kwargs_b1.get("on_item_completado"), orch_b1.fire_webhook),
        )
        check(
            "B1: notificar_no_soportados_por_webhook NO quedó forzado a True (default, "
            "ese flag es cosa de /process-invoice, no del canal email)",
            kwargs_b1.get("notificar_no_soportados_por_webhook", False) is not True,
        )
        check(
            "B1: client_reference no se pasó (default None -- el canal email no tiene ese concepto)",
            kwargs_b1.get("client_reference") is None,
        )
    else:
        check("B1: se despachó con origen='email'", False)
        check("B1: ingest_id es IGUAL al process_id generado por el endpoint (mismo valor, reusado)", False)
        check("B1: reservado_trabajo_en_vuelo == cantidad de miembros del ZIP de prueba (2)", False)
        check("B1: enviar_resumen_por_email quedó en True (un email de confirmación por ZIP)", False)
        check("B1: datos_email trae el from_email/subject reales del email entrante", False)
        check("B1: on_item_completado es exactamente orchestrator.fire_webhook", False)

    print()
    # ------------------------------------------------------------------
    print("=" * 78)
    print("B2 -- ZIP rechazado por _validar_zip_rapido (zip vacio) -> error, sin task")
    print("=" * 78)

    zip_bytes_b2 = construir_zip_vacio_bytes()

    dir_control_b2 = tempfile.mkdtemp(prefix="invoicy_test_zip_control_")
    ruta_control_b2 = os.path.join(dir_control_b2, "vacio.zip")
    Path(ruta_control_b2).write_bytes(zip_bytes_b2)

    namespace_b2, llamadas_b2, orch_b2 = crear_namespace("application/zip", zip_bytes_b2)

    # Llamamos primero a la función real _validar_zip_rapido directamente
    # (sobre una copia idéntica del ZIP) para conocer el mensaje de error
    # REAL que produce, en vez de adivinarlo o reimplementar su lógica.
    try:
        validacion_esperada_b2 = namespace_b2["_validar_zip_rapido"](ruta_control_b2)
        check(
            "B2 (control): la función real _validar_zip_rapido rechaza el ZIP vacío (ok=False)",
            validacion_esperada_b2.get("ok") is False,
        )
    finally:
        shutil.rmtree(dir_control_b2, ignore_errors=True)

    webhook_b2 = namespace_b2["webhook_endpoint"]
    payload_b2 = dict(
        payload_base,
        attachments="https://fake-storage.example.com/vacio.zip",
        file_name="vacio.zip",
    )
    respuesta_b2 = await webhook_b2(FakeRequest(payload_b2))

    check("B2: la respuesta tiene success=False", respuesta_b2.get("success") is False)
    check("B2: la respuesta tiene status='error'", respuesta_b2.get("status") == "error")
    check(
        "B2: el mensaje de la respuesta coincide con el error real de _validar_zip_rapido",
        respuesta_b2.get("message") == validacion_esperada_b2.get("error"),
    )
    check(
        "B2: NUNCA se despachó ninguna task de extracción de ZIP",
        len(llamadas_b2["extraer_zip_llamadas"]) == 0,
    )
    check(
        "B2: orchestrator.job_queue.put NUNCA fue llamado",
        len(llamadas_b2["job_queue_put"]) == 0,
    )

    print()
    # ------------------------------------------------------------------
    print("=" * 78)
    print("B3 -- PDF individual (rama NO zip) -- regresion del pipeline viejo")
    print("=" * 78)

    namespace_b3, llamadas_b3, orch_b3 = crear_namespace("application/pdf", PDF_VALIDO)
    webhook_b3 = namespace_b3["webhook_endpoint"]
    payload_b3 = dict(
        payload_base,
        attachments="https://fake-storage.example.com/factura_suelta.pdf",
        file_name="factura_suelta.pdf",
    )
    respuesta_b3 = await webhook_b3(FakeRequest(payload_b3))
    pid_b3 = obtener_process_id(respuesta_b3)

    check(
        "B3: la respuesta tiene status='enqueued' (NO 'processing')",
        respuesta_b3.get("status") == "enqueued",
    )
    check(
        "B3: orchestrator.job_queue.put fue llamado exactamente 1 vez",
        len(llamadas_b3["job_queue_put"]) == 1,
    )
    items_b3 = []
    if llamadas_b3["job_queue_put"]:
        items_b3 = llamadas_b3["job_queue_put"][0].get("items_to_process", [])
    check("B3: el job tiene exactamente 1 item en items_to_process", len(items_b3) == 1)
    if items_b3:
        check(
            "B3: el item tiene process_id IGUAL al process_id generado (sin sufijo de ZIP)",
            items_b3[0].get("process_id") == pid_b3,
        )
    else:
        check("B3: el item tiene process_id IGUAL al process_id generado (sin sufijo de ZIP)", False)
    check(
        "B3: NUNCA se despachó ninguna task de extracción de ZIP (rama vieja intacta)",
        len(llamadas_b3["extraer_zip_llamadas"]) == 0,
    )

    print()
    # ------------------------------------------------------------------
    print("=" * 78)
    print("B4 -- igual a B1, pero se espera a que la task creada REALMENTE corra")
    print("=" * 78)

    zip_bytes_b4 = construir_zip_bytes(
        [("factura_a.pdf", PDF_VALIDO), ("factura_b.png", PNG_VALIDO)]
    )
    namespace_b4, llamadas_b4, orch_b4 = crear_namespace("application/zip", zip_bytes_b4)
    webhook_b4 = namespace_b4["webhook_endpoint"]

    payload_b4 = dict(
        payload_base,
        attachments="https://fake-storage.example.com/facturas2.zip",
        file_name="facturas2.zip",
    )
    respuesta_b4 = await webhook_b4(FakeRequest(payload_b4))

    check("B4: la respuesta tiene status='processing'", respuesta_b4.get("status") == "processing")
    check(
        "B4: create_task despachó la fake exactamente 1 vez (kwargs registrados de inmediato)",
        len(llamadas_b4["extraer_zip_llamadas"]) == 1,
    )
    check(
        "B4: ANTES de ceder el control al event loop, el cuerpo de la task todavía NO corrió",
        len(llamadas_b4["extraer_zip_ejecutadas"]) == 0,
    )

    # Cedemos el control al event loop un par de veces para que la Task
    # creada por asyncio.create_task() efectivamente se ejecute.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    check(
        "B4: tras ceder el control (sleep(0) x2), la fake fue efectivamente AWAITED/ejecutada",
        len(llamadas_b4["extraer_zip_ejecutadas"]) == 1,
    )
    if llamadas_b4["extraer_zip_ejecutadas"]:
        check(
            "B4: los kwargs con los que corrió el cuerpo coinciden con los del despacho original",
            llamadas_b4["extraer_zip_ejecutadas"][0] == llamadas_b4["extraer_zip_llamadas"][0],
        )
        kwargs_b4 = llamadas_b4["extraer_zip_ejecutadas"][0]
        check(
            "B4: el cuerpo que realmente corrió también trae on_item_completado == "
            "orchestrator.fire_webhook",
            _es_mismo_metodo_bound(kwargs_b4.get("on_item_completado"), orch_b4.fire_webhook),
        )
    else:
        check(
            "B4: los kwargs con los que corrió el cuerpo coinciden con los del despacho original",
            False,
        )
        check(
            "B4: el cuerpo que realmente corrió también trae on_item_completado == "
            "orchestrator.fire_webhook",
            False,
        )

    # Drenamos cualquier task pendiente que haya quedado de escenarios
    # previos (p.ej. la de B1, que deliberadamente no se esperó) para no
    # dejar corrutinas sin awaitear al cerrar el event loop.
    for _ in range(5):
        await asyncio.sleep(0)


# ============================================================================
# Aislamos por completo los efectos en disco: todo corre dentro de un
# tempdir dedicado (chdir), restaurado siempre en el finally.
# ============================================================================
cwd_original = os.getcwd()
dir_temp_run = tempfile.mkdtemp(prefix="invoicy_test_zip_webhook_email_")
os.chdir(dir_temp_run)
try:
    asyncio.run(correr_pruebas())
finally:
    os.chdir(cwd_original)
    shutil.rmtree(dir_temp_run, ignore_errors=True)

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
