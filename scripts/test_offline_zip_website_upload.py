"""
Prueba offline de la rama ZIP del endpoint público website_upload
(POST /gemini2/website-upload) en routes/process_invoice_google_2.py,
DESPUÉS del rediseño de seguridad/arquitectura documentado en
docs/plan-fase1-zip-REDISEÑO.md.

Qué NO prueba este archivo (a propósito, para no duplicar):

- El rediseño de IDENTIDAD (`ingest_id` server-side vs `client_reference`
  del cliente, aislamiento de path traversal, colisiones, concurrencia,
  thread-safety de _reservar/_liberar_trabajo_en_vuelo_zip) está cubierto a
  fondo, de forma adversarial, por scripts/test_offline_zip_identidad_seguridad.py.
  Ese archivo también prueba _extraer_zip_y_despachar_individualmente REAL
  contra el filesystem real (con os.makedirs/shutil.move/shutil.rmtree
  espiados). Acá, en cambio, _extraer_zip_y_despachar_individualmente es un
  FAKE -- lo único que importa de este archivo es CON QUÉ KWARGS website_upload
  la invoca, no lo que ella hace por dentro.

Qué SÍ prueba este archivo -- el comportamiento propio de website_upload:

1. Que el camino ZIP siga despachando _extraer_zip_y_despachar_individualmente
   (fake) con un `ingest_id` fresco generado por el propio endpoint
   (uuid4().hex, nunca el `process_id` que mandó el formulario), origen
   fijo "website-upload", y `reservado_trabajo_en_vuelo` igual a lo que
   devolvió _validar_zip_rapido -- ver rediseño sección 1 y 4.

2. LA PARTE NUEVA Y MÁS IMPORTANTE (rediseño sección 7): la limpieza del
   placeholder huérfano. El formulario público primero llama a
   POST /website-upload/init (fuera del alcance de este archivo), que
   reserva un `process_id` y crea un row status="pending" en PocketBase,
   pensado para el camino de archivo SUELTO. Si el usuario en realidad
   arrastra un .zip, ese row queda huérfano -- nadie lo va a completar
   nunca, porque el contenido real se despacha con un `ingest_id` nuevo,
   no con ese `process_id`. website_upload ahora detecta esto (backend es
   la fuente de verdad de "esto es un ZIP", no el frontend) y limpia el
   placeholder ANTES de despachar el ZIP, pero SOLO si:
     (a) realmente vino un process_id en el form, Y
     (b) ese process_id REALMENTE existe en PocketBase, Y
     (c) su status es EXACTAMENTE "pending" (nunca se toca un placeholder
         en otro estado -- podría ser una factura real en curso cuyo id
         fue reusado/adivinado por error o abuso).
   Esta prueba verifica las 4 combinaciones relevantes: sin process_id
   (C1), con process_id + pending real (C2, el caso feliz de limpieza),
   con process_id + otro estado (C3, protección), y con process_id que no
   existe en PocketBase (C4, cliente manda cualquier cosa). Para C2
   también se verifica el ORDEN: el soft-delete del placeholder pasa ANTES
   de que se despache la extracción del ZIP (no importa si ambas cosas
   pasan, importa que la limpieza no llegue tarde).

3. Regresión del camino viejo (archivo suelto, C5) y de los dos rechazos
   previos a la feature ZIP (extensión no permitida C6; magic bytes que no
   matchean ningún tipo conocido C7 -- esto último ya fue corregido en una
   fase anterior de este mismo trabajo: filetype.guess() devuelve el
   singleton None para contenido irreconocible, y el código real ahora
   chequea `if kind is None` ANTES de tocar `kind.mime`, así que se espera
   400 "Tipo de archivo no permitido.", no un 500 por AttributeError).

No se reimplementa la lógica real: website_upload, _validar_zip_rapido y
_carpeta_ingest_zip (más _reservar_trabajo_en_vuelo_zip/_liberar_trabajo_en_vuelo_zip,
que _validar_zip_rapido necesita para poder ejecutarse) se extraen vía AST
del archivo fuente real y se ejecutan tal cual (exec), contra archivos ZIP
reales en un directorio temporal aislado. Solo se reemplazan (fakes) las
dos funciones de despacho pesado (_extraer_zip_y_despachar_individualmente,
_procesar_en_background) y el `orchestrator._pb_client` (para poder
parametrizar, por escenario, qué devuelve get_invoice_by_process_id sin
tocar PocketBase real).

Uso: python3 scripts/test_offline_zip_website_upload.py
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
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402
import filetype  # noqa: E402

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


# _reservar_trabajo_en_vuelo_zip/_liberar_trabajo_en_vuelo_zip no son
# llamadas directamente por website_upload, pero _validar_zip_rapido (que sí
# se extrae y se ejecuta real) las necesita como nombres globales resueltos
# en el mismo namespace al momento de correr -- si no están, revienta con
# NameError apenas se procesa el primer ZIP.
NOMBRES_A_EXTRAER = [
    "website_upload",
    "_validar_zip_rapido",
    "_carpeta_ingest_zip",
    "_reservar_trabajo_en_vuelo_zip",
    "_liberar_trabajo_en_vuelo_zip",
    "_truncar_nombre_a_bytes",
]
FUENTES = {}
for nombre in NOMBRES_A_EXTRAER:
    FUENTES[nombre] = _extraer_funcion_top_level(nombre)
    check(f"Se extrajo '{nombre}' del código fuente real", bool(FUENTES[nombre]))

if FALLOS:
    print("\nNo se pudo extraer el código real -- abortando.")
    sys.exit(1)


# ============================================================================
# Fakes -- SOLO para las dos funciones de despacho pesado y para
# orchestrator._pb_client. Todo lo demás (zipfile, filetype, os, shutil,
# uuid, asyncio, HTTPException, y la lógica real de website_upload /
# _validar_zip_rapido / _carpeta_ingest_zip / _reservar_trabajo_en_vuelo_zip
# / _liberar_trabajo_en_vuelo_zip) corre de verdad, contra archivos ZIP
# reales en un directorio temporal.
# ============================================================================
llamadas_extraer_zip = []
llamadas_procesar_background = []
# Registro compartido de orden de eventos entre el fake de soft-delete y el
# fake de despacho de extracción -- así C2 puede verificar que la limpieza
# del placeholder huérfano pasa ANTES de despachar el ZIP, no solo que
# ambas cosas pasaron.
orden_eventos = []


async def _extraer_zip_y_despachar_individualmente_fake(
    *,
    zip_path=None,
    ingest_id=None,
    origen=None,
    reservado_trabajo_en_vuelo=None,
    client_reference=None,
    notificar_no_soportados_por_webhook=False,
    enviar_resumen_por_email=False,
    datos_email=None,
    on_item_completado=None,
):
    orden_eventos.append("despacho_extraccion")
    llamadas_extraer_zip.append(
        {
            "zip_path": zip_path,
            "ingest_id": ingest_id,
            "origen": origen,
            "reservado_trabajo_en_vuelo": reservado_trabajo_en_vuelo,
            "client_reference": client_reference,
            "notificar_no_soportados_por_webhook": notificar_no_soportados_por_webhook,
            "enviar_resumen_por_email": enviar_resumen_por_email,
            "datos_email": datos_email,
            "on_item_completado": on_item_completado,
        }
    )


async def _procesar_en_background_fake(
    file_location=None,
    file_name=None,
    extension=None,
    media_type=None,
    process_id=None,
):
    llamadas_procesar_background.append(
        {
            "file_location": file_location,
            "file_name": file_name,
            "extension": extension,
            "media_type": media_type,
            "process_id": process_id,
        }
    )


class _AppLoggerFake:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class _PocketBaseClientFake:
    """Fake de orchestrator._pb_client. get_invoice_by_process_id es
    PARAMETRIZABLE por escenario vía el atributo `respuesta_get_invoice`
    (None, o un dict con el status que el escenario necesite) -- así cada
    test controla exactamente qué "existe" en PocketBase sin tocarla de
    verdad. soft_delete_invoice registra cada llamada completa (kwargs) y
    además deja constancia en `orden_eventos` para poder verificar que pasa
    ANTES del despacho de la extracción."""

    def __init__(self):
        self.respuesta_get_invoice = None
        self.llamadas_get_invoice_by_process_id = []
        self.llamadas_soft_delete_invoice = []
        self.llamadas_upsert_invoice = []

    def get_invoice_by_process_id(self, process_id):
        self.llamadas_get_invoice_by_process_id.append(process_id)
        return self.respuesta_get_invoice

    def soft_delete_invoice(self, process_id, deleted_by=None, reason=None):
        orden_eventos.append("soft_delete_invoice")
        self.llamadas_soft_delete_invoice.append(
            {"process_id": process_id, "deleted_by": deleted_by, "reason": reason}
        )

    def upsert_invoice(self, data):
        self.llamadas_upsert_invoice.append(dict(data))
        return dict(data)


class _OrchestratorFake:
    def __init__(self):
        self._pb_client = _PocketBaseClientFake()


orchestrator_fake = _OrchestratorFake()

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
    "asyncio": asyncio,
    "threading": threading,
    "HTTPException": HTTPException,
    "Request": object,
    "UploadFile": object,
    "File": lambda *a, **k: None,
    "Form": lambda *a, **k: None,
    "app_logger": _AppLoggerFake(),
    "orchestrator": orchestrator_fake,
    "_procesar_en_background": _procesar_en_background_fake,
    "_extraer_zip_y_despachar_individualmente": _extraer_zip_y_despachar_individualmente_fake,
    "MAX_ARCHIVOS_ZIP": 100,
    "MAX_ZIP_DESCOMPRIMIDO_BYTES": 500 * 1024 * 1024,
    "MAX_TRABAJO_EN_VUELO_ZIP": 200,
    "_trabajo_en_vuelo_zip": 0,
    "_trabajo_en_vuelo_zip_lock": threading.Lock(),
    "ZIP_EXTRACTION_SEMAPHORE": asyncio.Semaphore(2),
}

# Orden de dependencia: primero lo que no depende de nada (reservar/liberar,
# carpeta_ingest_zip), después _validar_zip_rapido (necesita
# _reservar_trabajo_en_vuelo_zip), al final website_upload (necesita todo lo
# anterior más los fakes de despacho).
for nombre in [
    "_reservar_trabajo_en_vuelo_zip",
    "_liberar_trabajo_en_vuelo_zip",
    "_carpeta_ingest_zip",
    "_validar_zip_rapido",
    "_truncar_nombre_a_bytes",
    "website_upload",
]:
    exec(compile(textwrap.dedent(FUENTES[nombre]), f"<{nombre} real>", "exec"), namespace)

website_upload = namespace["website_upload"]
_validar_zip_rapido_real = namespace["_validar_zip_rapido"]

check(
    "La función extraída 'website_upload' (código real) se pudo compilar y ejecutar vía exec",
    callable(website_upload),
)
check(
    "La función extraída '_validar_zip_rapido' (código real) se pudo compilar y ejecutar vía exec",
    callable(_validar_zip_rapido_real),
)


# ============================================================================
# Utilidades del test
# ============================================================================
class FakeUploadFile:
    def __init__(self, filename, content: bytes):
        self.filename = filename
        self.file = io.BytesIO(content)


def crear_zip_bytes(archivos: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for nombre, contenido in archivos.items():
            zf.writestr(nombre, contenido)
    return buf.getvalue()


async def _esperar_tareas_pendientes():
    # website_upload despacha con asyncio.create_task (fire-and-forget) --
    # sin esto, la corrida podría terminar antes de que el fake de despacho
    # llegue a ejecutarse y los asserts verían las listas vacías.
    pendientes = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pendientes:
        await asyncio.gather(*pendientes, return_exceptions=True)


def ejecutar(coro):
    async def _wrapper():
        resultado = await coro
        await _esperar_tareas_pendientes()
        return resultado

    return asyncio.run(_wrapper())


def intentar_ejecutar(coro):
    """Corre ejecutar(coro) sin dejar que una excepción inesperada tumbe todo
    el script -- así un escenario roto se reporta como FALLA (con el detalle
    de la excepción) y los escenarios siguientes igual corren."""
    try:
        return ejecutar(coro), None
    except Exception as e:  # noqa: BLE001 -- acá sí queremos atrapar cualquier cosa
        return None, e


def es_ingest_id_valido(texto) -> bool:
    """El código real genera ingest_id con uuid.uuid4().hex -- 32 caracteres
    hexadecimales, sin guiones (a diferencia de str(uuid.uuid4()))."""
    try:
        return (
            isinstance(texto, str)
            and len(texto) == 32
            and uuid.UUID(hex=texto).hex == texto
        )
    except (ValueError, AttributeError, TypeError):
        return False


def limpiar_estado_compartido(pb_client):
    llamadas_extraer_zip.clear()
    llamadas_procesar_background.clear()
    orden_eventos.clear()
    pb_client.llamadas_get_invoice_by_process_id.clear()
    pb_client.llamadas_soft_delete_invoice.clear()
    pb_client.llamadas_upsert_invoice.clear()


PDF_BYTES = b"%PDF-1.4\n" + b"X" * 50 + b"\n%%EOF\n"
TEXTO_NO_SOPORTADO = b"hola esto es un texto plano"

ZIP_VALIDO_BYTES = crear_zip_bytes({"a.pdf": PDF_BYTES, "b.pdf": PDF_BYTES})
CANTIDAD_MIEMBROS_ZIP_VALIDO = 2

pb_client = orchestrator_fake._pb_client


# ============================================================================
# Aislamiento de filesystem: website_upload escribe bajo "./downloads/..."
# relativo al cwd. Nos movemos a un tempdir para no ensuciar el repo real.
# ============================================================================
cwd_original = os.getcwd()
tempdir = tempfile.mkdtemp(prefix="test_offline_zip_website_upload_")

try:
    os.chdir(tempdir)

    # ========================================================================
    print("=" * 78)
    print("C1 -- ZIP válido, SIN process_id en el form (no vino de /init)")
    print("=" * 78)
    limpiar_estado_compartido(pb_client)
    pb_client.respuesta_get_invoice = None  # irrelevante acá: no debería ni consultarse

    upload_c1 = FakeUploadFile("facturas.zip", ZIP_VALIDO_BYTES)
    resultado_c1, error_c1 = intentar_ejecutar(website_upload(None, upload_c1, None))

    check(f"C1: no se lanzó ninguna excepción inesperada (fue {error_c1!r})", error_c1 is None)
    resultado_c1 = resultado_c1 or {}
    check("C1: respuesta success=True", resultado_c1.get("success") is True)
    check("C1: respuesta status_code=201", resultado_c1.get("status_code") == 201)
    check(
        "C1: se despachó exactamente 1 llamada a _extraer_zip_y_despachar_individualmente",
        len(llamadas_extraer_zip) == 1,
    )
    if llamadas_extraer_zip:
        kwargs_c1 = llamadas_extraer_zip[0]
        check(
            f"C1: ingest_id es un uuid4().hex fresco generado por el servidor (fue {kwargs_c1.get('ingest_id')!r})",
            es_ingest_id_valido(kwargs_c1.get("ingest_id")),
        )
        check("C1: origen == 'website-upload'", kwargs_c1.get("origen") == "website-upload")
        check(
            f"C1: reservado_trabajo_en_vuelo == cantidad de miembros del ZIP ({CANTIDAD_MIEMBROS_ZIP_VALIDO})",
            kwargs_c1.get("reservado_trabajo_en_vuelo") == CANTIDAD_MIEMBROS_ZIP_VALIDO,
        )
        check("C1: client_reference is None (no vino process_id)", kwargs_c1.get("client_reference") is None)
    check(
        "C1: get_invoice_by_process_id NUNCA fue llamado (no había nada que limpiar)",
        len(pb_client.llamadas_get_invoice_by_process_id) == 0,
    )
    check(
        "C1: soft_delete_invoice NUNCA fue llamado",
        len(pb_client.llamadas_soft_delete_invoice) == 0,
    )
    check(
        "C1: _procesar_en_background (camino de archivo suelto) NUNCA fue llamada",
        len(llamadas_procesar_background) == 0,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("C2 -- ZIP válido, CON process_id, placeholder existe y status='pending'")
    print("=" * 78)
    limpiar_estado_compartido(pb_client)
    pb_client.respuesta_get_invoice = {"process_id": "website-abc", "status": "pending"}

    upload_c2 = FakeUploadFile("facturas.zip", ZIP_VALIDO_BYTES)
    resultado_c2, error_c2 = intentar_ejecutar(website_upload(None, upload_c2, "website-abc"))

    check(f"C2: no se lanzó ninguna excepción inesperada (fue {error_c2!r})", error_c2 is None)
    resultado_c2 = resultado_c2 or {}
    check("C2: respuesta success=True", resultado_c2.get("success") is True)
    check(
        "C2: get_invoice_by_process_id('website-abc') fue llamado exactamente 1 vez",
        pb_client.llamadas_get_invoice_by_process_id == ["website-abc"],
    )
    check(
        "C2: soft_delete_invoice('website-abc', ...) fue llamado exactamente 1 vez",
        len(pb_client.llamadas_soft_delete_invoice) == 1,
    )
    if pb_client.llamadas_soft_delete_invoice:
        llamada_sd_c2 = pb_client.llamadas_soft_delete_invoice[0]
        check(
            "C2: soft_delete_invoice se llamó con process_id == 'website-abc'",
            llamada_sd_c2.get("process_id") == "website-abc",
        )
        check(
            # deleted_by es relation -> users en PocketBase (ver migración
            # 1783483896_add_soft_delete_fields.js): "quien pide el borrado
            # siempre es un humano autenticado en el dashboard". Una limpieza
            # de sistema (sin ningún humano de por medio) NUNCA debe mandar
            # un string acá -- PocketBase lo rechaza con 400
            # validation_missing_rel_records (bug real encontrado en
            # producción durante el smoke test post-deploy de la Fase 1 de
            # ZIP). El campo es opcional (minSelect=0): debe ir None.
            f"C2: soft_delete_invoice se llamó con deleted_by=None (limpieza de sistema, sin "
            f"usuario humano -- fue {llamada_sd_c2.get('deleted_by')!r})",
            llamada_sd_c2.get("deleted_by") is None,
        )
        check(
            f"C2: soft_delete_invoice se llamó con reason no vacío -- ahí va la identidad del "
            f"actor de sistema, no en deleted_by (fue {llamada_sd_c2.get('reason')!r})",
            bool(llamada_sd_c2.get("reason")),
        )
    check(
        "C2: se despachó exactamente 1 llamada a _extraer_zip_y_despachar_individualmente",
        len(llamadas_extraer_zip) == 1,
    )
    if llamadas_extraer_zip:
        check(
            "C2: client_reference pasado a la fake de extracción == 'website-abc'",
            llamadas_extraer_zip[0].get("client_reference") == "website-abc",
        )
    check(
        f"C2: el orden real de eventos fue [soft_delete_invoice, despacho_extraccion] (fue {orden_eventos!r})",
        orden_eventos == ["soft_delete_invoice", "despacho_extraccion"],
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("C3 -- process_id existe pero status='processing' (NO 'pending') -> no se toca")
    print("=" * 78)
    limpiar_estado_compartido(pb_client)
    pb_client.respuesta_get_invoice = {"process_id": "website-abc", "status": "processing"}

    upload_c3 = FakeUploadFile("facturas.zip", ZIP_VALIDO_BYTES)
    resultado_c3, error_c3 = intentar_ejecutar(website_upload(None, upload_c3, "website-abc"))

    check(f"C3: no se lanzó ninguna excepción inesperada (fue {error_c3!r})", error_c3 is None)
    resultado_c3 = resultado_c3 or {}
    check("C3: respuesta success=True", resultado_c3.get("success") is True)
    check(
        "C3: get_invoice_by_process_id('website-abc') SÍ fue llamado (se consultó antes de decidir)",
        pb_client.llamadas_get_invoice_by_process_id == ["website-abc"],
    )
    check(
        "C3: soft_delete_invoice NUNCA fue llamado (status no era 'pending' -- protección contra tocar una factura real)",
        len(pb_client.llamadas_soft_delete_invoice) == 0,
    )
    check(
        "C3: el ZIP se despachó igual (1 llamada a _extraer_zip_y_despachar_individualmente)",
        len(llamadas_extraer_zip) == 1,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("C4 -- process_id NO existe en PocketBase (get_invoice_by_process_id -> None)")
    print("=" * 78)
    limpiar_estado_compartido(pb_client)
    pb_client.respuesta_get_invoice = None

    upload_c4 = FakeUploadFile("facturas.zip", ZIP_VALIDO_BYTES)
    resultado_c4, error_c4 = intentar_ejecutar(website_upload(None, upload_c4, "website-inexistente"))

    check(f"C4: no se lanzó ninguna excepción inesperada (fue {error_c4!r})", error_c4 is None)
    resultado_c4 = resultado_c4 or {}
    check("C4: respuesta success=True", resultado_c4.get("success") is True)
    check(
        "C4: get_invoice_by_process_id('website-inexistente') SÍ fue llamado",
        pb_client.llamadas_get_invoice_by_process_id == ["website-inexistente"],
    )
    check(
        "C4: soft_delete_invoice NUNCA fue llamado (nada real que limpiar)",
        len(pb_client.llamadas_soft_delete_invoice) == 0,
    )
    check(
        "C4: el ZIP se despachó igual, sin explotar (1 llamada a _extraer_zip_y_despachar_individualmente)",
        len(llamadas_extraer_zip) == 1,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("C5 -- archivo suelto factura.pdf (regresión del camino viejo)")
    print("=" * 78)
    limpiar_estado_compartido(pb_client)

    upload_c5 = FakeUploadFile("factura.pdf", PDF_BYTES)
    resultado_c5, error_c5 = intentar_ejecutar(website_upload(None, upload_c5, "proc-c5"))

    check(f"C5: no se lanzó ninguna excepción inesperada (fue {error_c5!r})", error_c5 is None)
    resultado_c5 = resultado_c5 or {}
    check("C5: respuesta success=True", resultado_c5.get("success") is True)
    check("C5: respuesta status_code=201", resultado_c5.get("status_code") == 201)
    check(
        "C5: se despachó exactamente 1 llamada a _procesar_en_background",
        len(llamadas_procesar_background) == 1,
    )
    if llamadas_procesar_background:
        kwargs_c5 = llamadas_procesar_background[0]
        check("C5: file_name == 'factura.pdf'", kwargs_c5.get("file_name") == "factura.pdf")
        check("C5: process_id == 'proc-c5'", kwargs_c5.get("process_id") == "proc-c5")
        check("C5: media_type == 'application/pdf'", kwargs_c5.get("media_type") == "application/pdf")
        check("C5: extension == 'pdf'", kwargs_c5.get("extension") == "pdf")
    check(
        "C5: _extraer_zip_y_despachar_individualmente (camino ZIP) NUNCA fue llamada",
        len(llamadas_extraer_zip) == 0,
    )
    check(
        "C5: get_invoice_by_process_id NUNCA fue llamado (la limpieza de placeholder es SOLO del camino ZIP)",
        len(pb_client.llamadas_get_invoice_by_process_id) == 0,
    )
    check(
        "C5: soft_delete_invoice NUNCA fue llamado",
        len(pb_client.llamadas_soft_delete_invoice) == 0,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("C6 -- extensión no permitida (factura.docx) -> 400, ninguna fake llamada")
    print("=" * 78)
    limpiar_estado_compartido(pb_client)

    upload_c6 = FakeUploadFile("factura.docx", b"contenido cualquiera, no se llega a leer")
    excepcion_c6 = None
    try:
        ejecutar(website_upload(None, upload_c6, "proc-c6"))
    except HTTPException as exc:
        excepcion_c6 = exc

    check("C6: se lanzó HTTPException", excepcion_c6 is not None)
    if excepcion_c6 is not None:
        check(
            f"C6: status_code == 400 (fue {excepcion_c6.status_code})",
            excepcion_c6.status_code == 400,
        )
    check("C6: _extraer_zip_y_despachar_individualmente NUNCA fue llamada", len(llamadas_extraer_zip) == 0)
    check("C6: _procesar_en_background NUNCA fue llamada", len(llamadas_procesar_background) == 0)
    check(
        "C6: get_invoice_by_process_id NUNCA fue llamado",
        len(pb_client.llamadas_get_invoice_by_process_id) == 0,
    )
    check(
        "C6: soft_delete_invoice NUNCA fue llamado",
        len(pb_client.llamadas_soft_delete_invoice) == 0,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("C7 -- extensión permitida pero contenido irreconocible (filetype.guess() -> None)")
    print("=" * 78)
    limpiar_estado_compartido(pb_client)

    upload_c7 = FakeUploadFile("factura.pdf", TEXTO_NO_SOPORTADO)
    excepcion_c7 = None
    try:
        ejecutar(website_upload(None, upload_c7, "proc-c7"))
    except HTTPException as exc:
        excepcion_c7 = exc

    check("C7: se lanzó HTTPException", excepcion_c7 is not None)
    if excepcion_c7 is not None:
        check(
            f"C7: status_code == 400 (fue {excepcion_c7.status_code}, detail={excepcion_c7.detail!r}) "
            "-- regresión del fix de AttributeError por 'NoneType' object has no attribute 'mime'",
            excepcion_c7.status_code == 400,
        )
        check(
            "C7: detail == 'Tipo de archivo no permitido.'",
            excepcion_c7.detail == "Tipo de archivo no permitido.",
        )
    check("C7: _extraer_zip_y_despachar_individualmente NUNCA fue llamada", len(llamadas_extraer_zip) == 0)
    check("C7: _procesar_en_background NUNCA fue llamada", len(llamadas_procesar_background) == 0)

finally:
    os.chdir(cwd_original)
    shutil.rmtree(tempdir, ignore_errors=True)


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
