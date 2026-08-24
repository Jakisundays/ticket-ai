"""
Test offline de _extraer_zip_y_despachar_individualmente y sus funciones
auxiliares (_validar_zip_rapido, _carpeta_ingest_zip, _sanear_nombre_para_process_id,
_reservar/_liberar_trabajo_en_vuelo_zip, _generar_html_resumen_zip) en
routes/process_invoice_google_2.py, DESPUÉS del rediseño de seguridad/arquitectura
documentado en docs/plan-fase1-zip-REDISEÑO.md.

Contexto: una revisión adversarial post-implementación encontró 14 hallazgos
sobre el manejo de ZIP en la ingesta de facturas (2 críticos: path traversal
vía el `process_id` que mandaba el cliente, y colisión de identidad entre
requests). El fix de identidad (ingest_id server-side vía uuid4,
client_reference puramente informativo) ya se prueba a fondo, de forma
adversarial, en scripts/test_offline_zip_identidad_seguridad.py -- ESTE
archivo no repite esos escenarios (path traversal, colisiones, concurrencia,
thread-safety del contador). Lo que prueba este archivo es el RESTO del
rediseño, puntual por puntual:

  - Fallos parciales POR MIEMBRO: se eliminó zip_ref.testzip() (el gate
    global que abortaba TODO el zip por un solo miembro corrupto/encriptado).
    Ahora zip_ref.extract() detecta corrupción y encriptación aisladas, por
    archivo -- un ZIP con 1 miembro roto + N válidos despacha los N. Esta es
    la verificación más importante del archivo: es exactamente lo que el
    rediseño cambió respecto al comportamiento viejo (A1, A2).

  - Durabilidad: la Fase A (síncrona, en un hilo real vía asyncio.to_thread)
    hace upsert_invoice(status="pending") para cada archivo aceptado DE
    INMEDIATO, antes de que le toque su turno de procesamiento pesado en la
    Fase B -- si el proceso se reiniciara entre medio, ya habría fila
    "pending" para TODOS los archivos del ZIP, no solo para el primero. A3
    verifica el ORDEN real de los eventos (no solo que ambos pasen), con un
    event log compartido entre los 2 fakes.

  - Trabajo en vuelo: _validar_zip_rapido reserva capacidad por adelantado y
    _extraer_zip_y_despachar_individualmente es responsable de liberarla,
    miembro por miembro, sin importar por qué rama salió cada uno (aceptado
    y despachado, o descartado en Fase A). A4 prueba el ciclo completo
    reserva->liberación con el contador real; A5 prueba el rechazo temprano
    cuando el sistema ya está saturado, sin reservar de más.

  - Zip Slip: el código usa el valor de RETORNO de zip_ref.extract() (nunca
    una ruta recalculada a mano) precisamente porque la stdlib de Python ya
    sanea los componentes ".." de un nombre de miembro al extraer -- A6
    ejercita esa rama con un nombre construido a propósito para tener ".."
    en el medio y confirma que el archivo no se pierde silenciosamente.

  - ZIP anidado y notificación de no-soportados por webhook (A7, A8) y los
    casos puntuales de saneo de nombre (A9) se re-verifican acá porque son
    parte del comportamiento actual de la misma función bajo prueba, con la
    firma NUEVA (ingest_id + reservado_trabajo_en_vuelo obligatorios).

  - Cleanup de la carpeta temporal (A10).

  - Durabilidad de RECUPERACIÓN (A11/A11b): además del upsert(status="pending")
    que ya probaba A3, Fase A también llama adjuntar_archivo_original ANTES de
    que arranque la Fase B, para cada miembro aceptado -- esto es lo que cierra
    el hueco encontrado en la 2da/3ra revisión adversarial post-rediseño: sin
    esto, una factura que quedaba huérfana DENTRO de la ventana "pending, en
    cola esperando turno" (la más común para ZIPs grandes, según el análisis)
    recuperaba su fila vía el barrido de huérfanos pero /retry-extraction le
    devolvía 422 por no tener documento_original. A11 prueba el orden real de
    eventos (mismo patrón que A3); A11b prueba que un fallo de PocketBase acá
    es best-effort y no saca al archivo de la cola de despacho.

  - Extra: granularidad de notificaciones del canal de email (sección 5 del
    rediseño) -- 1 email-resumen por ZIP completo (enviar_resumen_por_email)
    pero 1 invocación de on_item_completado POR FACTURA. Ninguna otra prueba
    de este repo ejercita este contrato a este nivel: los tests de
    webhook_endpoint (scripts/test_offline_zip_webhook_email.py) reemplazan
    _extraer_zip_y_despachar_individualmente entero por un fake, así que
    nunca llegan a invocar su lógica interna de notificación.

Todas las funciones bajo prueba se EXTRAEN vía AST del archivo fuente real y
se ejecutan con exec() -- nunca se reimplementa la lógica a mano acá. Las
únicas dependencias reemplazadas por fakes son el pipeline pesado
(_procesar_en_background) y el cliente externo (orchestrator, con su
_pb_client.upsert_invoice y su fire_webhook/enviar_email) -- todo lo demás
(zipfile, filetype, os, shutil, zlib) corre de verdad, contra archivos ZIP
reales en un directorio temporal aislado (os.chdir + cleanup en finally).

Uso: python3 scripts/test_offline_zip_extraccion.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import ast
import asyncio
import io
import os
import re  # necesario en el namespace del exec() -- _sanear_nombre_para_process_id lo usa
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

import filetype  # noqa: E402 -- dependencia real del repo, se usa de verdad (no se mockea)

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE = REPO_ROOT / "routes" / "process_invoice_google_2.py"

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


# ============================================================================
# Fixtures de bytes ya verificadas en este repo.
# ============================================================================
PDF_VALIDO = b"%PDF-1.4\n" + b"X" * 50 + b"\n%%EOF\n"
TEXTO_NO_SOPORTADO = b"hola esto es un texto plano"
# BMP: filetype.guess() SÍ detecta un tipo real (no None) -- a diferencia de
# TEXTO_NO_SOPORTADO -- pero "bmp" no está en extensiones_soportadas. Hace
# falta un tipo_real no-None para ejercitar de verdad el acceso a su
# atributo .mime en el payload del webhook (A8): con TEXTO_NO_SOPORTADO el
# ternario "tipo_real.mime if tipo_real else None" nunca llega a tocar el
# atributo, y el escenario no probaría nada real (bug histórico real de este
# módulo: "kind.media_type" en vez del atributo correcto, AttributeError).
BMP_TIPO_DETECTADO_NO_SOPORTADO = b"BM" + b"\x00" * 30


def construir_zip_bytes(miembros):
    """miembros: lista de (nombre_dentro_del_zip, contenido_bytes)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for nombre, contenido in miembros:
            zf.writestr(nombre, contenido)
    return buf.getvalue()


def _crear_zip_con_miembro_crc_corrupto():
    """Receta verificada: corrompe un byte adentro de los datos comprimidos
    del 2do miembro (a.pdf queda intacto). zip_ref.extract() detecta esto
    aislado, por miembro -- ya NO existe testzip() (gate global) en el
    diseño nuevo."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.pdf", PDF_VALIDO)
        zf.writestr("b.pdf", b"%PDF-1.4\n" + b"Y" * 50 + b"\n%%EOF\n")
    raw = bytearray(buf.getvalue())
    idx = raw.find(b"PK\x03\x04", 4)  # arranque del SEGUNDO local file header
    raw[idx + 40] ^= 0xFF  # corrompe un byte adentro de los datos comprimidos de "b.pdf"
    return bytes(raw)


def _crear_zip_con_miembro_encriptado():
    """Receta verificada empíricamente contra la librería zipfile real de
    este mismo entorno (sin pyzipper): NO hace falta encriptar los datos de
    verdad para ejercitar la rama de detección de encriptación de
    zip_ref.extract() -- alcanza con prender el bit 0 (0x1, "file is
    encrypted") del campo flag_bits en el registro de directorio central
    del 2do miembro, dejando el 1er miembro intacto. zip_ref.extract() lee
    zinfo.flag_bits (poblado desde ESE registro al reabrir el ZIP con
    zipfile.ZipFile(...).infolist(), exactamente como hace el código real)
    y levanta RuntimeError("... is encrypted, password required for
    extraction") ANTES de intentar descomprimir nada -- mismo tipo de
    excepción (RuntimeError) que el código real ya atrapa junto con
    BadZipFile/zlib.error. Verificado que a.pdf se extrae normalmente y que
    b.pdf dispara RuntimeError, aislado.

    Limitación documentada: esto simula la señal que zipfile usa para
    decidir "está encriptado" (el flag_bits real de un ZIP encriptado con
    password real tendría el mismo bit prendido), pero los bytes de b.pdf
    en este fixture NO están realmente cifrados -- no hace falta que lo
    estén: la excepción se dispara por el chequeo de flag_bits, antes de
    que el contenido se toque."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.pdf", PDF_VALIDO)
        zf.writestr("b.pdf", b"%PDF-1.4\n" + b"Z" * 50 + b"\n%%EOF\n")
    raw = bytearray(buf.getvalue())
    idx_cd_1 = raw.find(b"PK\x01\x02")
    idx_cd_2 = raw.find(b"PK\x01\x02", idx_cd_1 + 4)
    assert idx_cd_1 != -1 and idx_cd_2 != -1, "no se encontraron ambos registros de directorio central"
    # Layout del central directory file header: signature(4) + version_made_by(2)
    # + version_needed(2) = 8 bytes antes de flag_bits (2 bytes, little-endian).
    offset_flag_bits = idx_cd_2 + 8
    raw[offset_flag_bits] |= 0x01
    return bytes(raw)


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


def _extraer_constante_modulo(nombre):
    """Lee una constante real vía AST (asignación real a nivel de módulo,
    NUNCA por regex sobre el texto crudo -- una regex también matchea
    menciones dentro de comentarios/docstrings)."""
    nodo = next(
        (
            n
            for n in arbol.body
            if isinstance(n, ast.Assign)
            and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == nombre
        ),
        None,
    )
    if nodo is None:
        return None
    segmento = ast.get_source_segment(codigo_fuente, nodo)
    ns_tmp = {}
    exec(compile(segmento, f"<constante {nombre}>", "exec"), ns_tmp)
    return ns_tmp[nombre]


# Orden de dependencia: las que no dependen de otras primero
# (_sanear_nombre_para_process_id, _carpeta_ingest_zip,
# _reservar/_liberar_trabajo_en_vuelo_zip), después _validar_zip_rapido
# (llama a _reservar_trabajo_en_vuelo_zip), _generar_html_resumen_zip, y por
# último _extraer_zip_y_despachar_individualmente (llama a casi todas las
# anteriores).
NOMBRES_A_EXTRAER = [
    "_sanear_nombre_para_process_id",
    "_carpeta_ingest_zip",
    "_reservar_trabajo_en_vuelo_zip",
    "_liberar_trabajo_en_vuelo_zip",
    "_validar_zip_rapido",
    "_generar_html_resumen_zip",
    "_extraer_zip_y_despachar_individualmente",
]
FUENTES = {}
for nombre in NOMBRES_A_EXTRAER:
    FUENTES[nombre] = _extraer_funcion_top_level(nombre)
    check(f"Se extrajo '{nombre}' del código fuente real", bool(FUENTES[nombre]))

MAX_ARCHIVOS_ZIP_REAL = _extraer_constante_modulo("MAX_ARCHIVOS_ZIP")
MAX_ZIP_DESCOMPRIMIDO_BYTES_REAL = _extraer_constante_modulo("MAX_ZIP_DESCOMPRIMIDO_BYTES")
check("Se encontró la constante real MAX_ARCHIVOS_ZIP en el código fuente", MAX_ARCHIVOS_ZIP_REAL is not None)
check("Se encontró la constante real MAX_ZIP_DESCOMPRIMIDO_BYTES en el código fuente", MAX_ZIP_DESCOMPRIMIDO_BYTES_REAL is not None)

if FALLOS:
    print("\nNo se pudo extraer el código/las constantes reales -- abortando.")
    sys.exit(1)


# ============================================================================
# Fakes -- SOLO para estado/red externos (PocketBase, webhook, email,
# logger, pipeline pesado). Todo lo demás (zipfile, filetype, os, shutil,
# zlib) es la librería real, contra ZIPs reales en disco.
# ============================================================================
class FakeAppLogger:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.errors = []

    def info(self, mensaje):
        self.infos.append(mensaje)

    def warning(self, mensaje):
        self.warnings.append(mensaje)

    def error(self, mensaje):
        self.errors.append(mensaje)


class FakeOrchestrator:
    """`eventos`, si se pasa, es una lista COMPARTIDA con el
    FakeProcesadorEnBackground de la misma corrida -- así A3 puede comparar
    el orden real de aparición de "upsert" vs "despacho" para el mismo
    process_id, sin asumir nada sobre el orden interno de cada lista propia
    por separado."""

    def __init__(self, eventos=None):
        self.upserts = []
        self.webhooks = []
        self.emails = []
        self.adjuntos = []
        self.soft_deletes = []
        self._eventos = eventos if eventos is not None else []
        self._pb_client = self

    def upsert_invoice(self, data):
        registro = dict(data)
        self.upserts.append(registro)
        self._eventos.append(("upsert", registro.get("process_id")))
        # El "id" real de PocketBase (distinto de process_id) se sintetiza
        # solo en el valor de RETORNO -- lo que queda en self.upserts (lo
        # que consultan A1-A10/Extra) sigue siendo el dict de entrada tal
        # cual, sin tocar ninguna aserción existente.
        return {**registro, "id": f"pbid-{registro.get('process_id')}"}

    def soft_delete_invoice(self, process_id, *, deleted_by=None, reason=None):
        self.soft_deletes.append(
            {"process_id": process_id, "deleted_by": deleted_by, "reason": reason}
        )
        self._eventos.append(("soft_delete", process_id))
        return {"process_id": process_id, "deleted_at": "2026-01-01T00:00:00Z"}

    def adjuntar_archivo_original(self, record_id, file_path, filename, mime_type):
        self.adjuntos.append(
            {
                "record_id": record_id,
                "file_path": file_path,
                "filename": filename,
                "mime_type": mime_type,
                "existia_en_disco": bool(file_path) and Path(file_path).exists(),
            }
        )
        self._eventos.append(("adjunto", record_id))
        return True

    async def fire_webhook(self, payload):
        self.webhooks.append(payload)

    def enviar_email(self, destinatario, asunto, cuerpo):
        self.emails.append({"destinatario": destinatario, "asunto": asunto, "cuerpo": cuerpo})
        return True


class FakeProcesadorEnBackground:
    """Reemplaza a _procesar_en_background (el pipeline pesado real:
    Gemini, BAS, etc.) -- NUNCA debe correr el real acá, dispararía llamadas
    de red reales. Registra con qué kwargs lo llamaron, en orden; puede
    fallar para ciertos archivos (por file_name) para simular un despacho
    que revuelve success=False sin tirar abajo el resto del ZIP (la función
    real ya atrapa sus propias excepciones y siempre devuelve un dict, así
    que el fake hace lo mismo en vez de lanzar)."""

    def __init__(self, eventos=None, fallar_para_file_name=None):
        self.llamadas = []
        self._eventos = eventos if eventos is not None else []
        self.fallar_para_file_name = fallar_para_file_name or set()

    async def __call__(self, **kwargs):
        self.llamadas.append(dict(kwargs))
        self._eventos.append(("despacho", kwargs.get("process_id")))
        if kwargs.get("file_name") in self.fallar_para_file_name:
            return {"success": False, "error": f"fallo simulado de pipeline pesado para {kwargs.get('file_name')}"}
        # Mismos campos que realmente devuelve _procesar_imagen_o_pdf_impl
        # (ver docs/plan-fase1-zip-REDISEÑO.md, comparación de contrato) --
        # así los tests que verifican el payload de fire_webhook por
        # factura pueden confirmar que saved/bas/drive_file_id se propagan
        # de verdad, no solo que la clave "factura" existe.
        return {
            "success": True,
            "factura": {
                "id": kwargs.get("process_id"),
                "saved_sheet": True,
                "saved_items": True,
                "bas": {"comprobante": "simulado"},
                "drive_file_id": f"drive-{kwargs.get('process_id')}",
            },
        }


def construir_namespace(
    *,
    orchestrator=None,
    app_logger=None,
    procesador=None,
    max_archivos_zip=None,
    max_zip_descomprimido_bytes=None,
    max_trabajo_en_vuelo=200,
    trabajo_en_vuelo_inicial=0,
):
    """Namespace fresco de exec() con las 7 funciones reales cargadas, en
    orden de dependencia. `os"/"shutil"/"zipfile"/"zlib"/"filetype" son las
    librerías REALES -- todo I/O de disco de las funciones bajo prueba
    ocurre de verdad, dentro del directorio temporal aislado por chdir."""
    orchestrator = orchestrator if orchestrator is not None else FakeOrchestrator()
    app_logger_obj = app_logger if app_logger is not None else FakeAppLogger()
    procesador = procesador if procesador is not None else FakeProcesadorEnBackground()

    namespace = {
        "os": os,
        "re": re,
        "shutil": shutil,
        "zipfile": zipfile,
        "zlib": zlib,
        "filetype": filetype,
        "Optional": Optional,
        "Callable": Callable,
        "asyncio": asyncio,
        "orchestrator": orchestrator,
        "app_logger": app_logger_obj,
        "_procesar_en_background": procesador,
        "MAX_ARCHIVOS_ZIP": max_archivos_zip if max_archivos_zip is not None else MAX_ARCHIVOS_ZIP_REAL,
        "MAX_ZIP_DESCOMPRIMIDO_BYTES": max_zip_descomprimido_bytes if max_zip_descomprimido_bytes is not None else MAX_ZIP_DESCOMPRIMIDO_BYTES_REAL,
        "MAX_TRABAJO_EN_VUELO_ZIP": max_trabajo_en_vuelo,
        "_trabajo_en_vuelo_zip": trabajo_en_vuelo_inicial,
        "_trabajo_en_vuelo_zip_lock": threading.Lock(),
        "ZIP_EXTRACTION_SEMAPHORE": asyncio.Semaphore(2),
        "ZIP_REGISTRO_SEMAPHORE": asyncio.Semaphore(8),
    }
    for nombre in NOMBRES_A_EXTRAER:
        exec(compile(textwrap.dedent(FUENTES[nombre]), f"<{nombre} real>", "exec"), namespace)

    namespace["_orchestrator_fake"] = orchestrator
    namespace["_app_logger_fake"] = app_logger_obj
    namespace["_procesador_fake"] = procesador
    return namespace


ns_check = construir_namespace()
check(
    "Las 7 funciones extraídas (código real) se pudieron compilar y ejecutar vía exec",
    all(callable(ns_check.get(n)) for n in NOMBRES_A_EXTRAER),
)
if FALLOS:
    print("\nLas funciones reales no se pudieron cargar -- abortando.")
    sys.exit(1)


def _run(coro):
    return asyncio.run(coro)


def _nuevo_ingest_id(sufijo):
    return f"test-zip-extraccion-{sufijo}-{uuid.uuid4().hex[:8]}"


# ============================================================================
# Aislamiento de disco: todo el resto del script corre dentro de un
# directorio temporal (incluida la carpeta "./downloads/..." que las
# funciones reales crean/borran) -- se restaura el cwd original al final.
# ============================================================================
cwd_original = os.getcwd()
DIR_TEMP = Path(tempfile.mkdtemp(prefix="test_offline_zip_extraccion_"))
os.chdir(DIR_TEMP)

try:
    # ========================================================================
    print("=" * 78)
    print("A1 -- 1 miembro con CRC corrupto + 1 válido -> el corrupto se aísla,")
    print("      el válido SE DESPACHA (fallos parciales por miembro, sin testzip())")
    print("=" * 78)
    Path("a1.zip").write_bytes(_crear_zip_con_miembro_crc_corrupto())
    ns = construir_namespace()
    ingest_id_a1 = _nuevo_ingest_id("a1")
    validacion_a1 = ns["_validar_zip_rapido"]("a1.zip")
    check("A1: _validar_zip_rapido acepta el zip (no valida CRC, solo metadata)", validacion_a1["ok"])
    resultado_a1 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a1.zip",
            ingest_id=ingest_id_a1,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a1["reservado"],
        )
    )
    check("A1: error_zip es None (no aborta TODO el zip por 1 miembro corrupto)", resultado_a1["error_zip"] is None)
    check("A1: aceptados == 1 (a.pdf se despachó igual)", resultado_a1["aceptados"] == 1)
    check("A1: hay exactamente 1 entrada en no_soportados (b.pdf)", len(resultado_a1["no_soportados"]) == 1)
    if resultado_a1["no_soportados"]:
        motivo_a1 = resultado_a1["no_soportados"][0]["motivo"]
        check(f"A1: el motivo menciona corrupción (fue: {motivo_a1!r})", "corrupto" in motivo_a1.lower())
        check("A1: el miembro aislado es b.pdf", resultado_a1["no_soportados"][0]["nombre"] == "b.pdf")
    check("A1: _procesar_en_background fue llamada exactamente 1 vez (solo a.pdf)", len(ns["_procesador_fake"].llamadas) == 1)
    if ns["_procesador_fake"].llamadas:
        check("A1: el archivo despachado es a.pdf", ns["_procesador_fake"].llamadas[0]["file_name"] == "a.pdf")
    # Pass 0 pre-registra a AMBOS miembros (a.pdf y b.pdf) antes de saber
    # cuál está corrupto -- b.pdf, rechazado en Pass 1, debe descartar su
    # placeholder (soft-delete) para no quedar como factura pendiente.
    check(
        f"A1: Pass 0 pre-registró los 2 miembros (fueron {len(ns['_orchestrator_fake'].upserts)} upserts)",
        len(ns["_orchestrator_fake"].upserts) == 2,
    )
    check(
        "A1: soft_delete_invoice fue llamado exactamente 1 vez, para el miembro rechazado (b.pdf)",
        len(ns["_orchestrator_fake"].soft_deletes) == 1,
    )
    if ns["_orchestrator_fake"].soft_deletes:
        check(
            "A1: el process_id descartado corresponde a b.pdf, no a a.pdf",
            "b.pdf" in ns["_orchestrator_fake"].soft_deletes[0]["process_id"],
        )
        check(
            # deleted_by es relation -> users en PocketBase real (ver
            # migración 1783483896_add_soft_delete_fields.js) -- una
            # limpieza de sistema (Fase A, sin ningún humano de por medio)
            # NUNCA debe mandar un string acá, o PocketBase real lo rechaza
            # con 400 validation_missing_rel_records (bug real encontrado
            # en producción vía el smoke test post-deploy). Debe ir None.
            f"A1: soft_delete_invoice se llamó con deleted_by=None, nunca un string de sistema "
            f"(fue {ns['_orchestrator_fake'].soft_deletes[0]['deleted_by']!r})",
            ns["_orchestrator_fake"].soft_deletes[0]["deleted_by"] is None,
        )
        check(
            "A1: soft_delete_invoice se llamó con reason mencionando la corrupción",
            "corrupto" in (ns["_orchestrator_fake"].soft_deletes[0]["reason"] or "").lower(),
        )
        check(
            "A1: reason también lleva la identidad del actor de sistema (ya que deleted_by no "
            "puede llevarla)",
            "sistema:zip-fase-a-rechazo" in (ns["_orchestrator_fake"].soft_deletes[0]["reason"] or ""),
        )

    # ========================================================================
    print()
    print("=" * 78)
    print("A2 -- 1 miembro encriptado + 1 válido -> mismo aislamiento por miembro")
    print("=" * 78)
    Path("a2.zip").write_bytes(_crear_zip_con_miembro_encriptado())
    ns = construir_namespace()
    ingest_id_a2 = _nuevo_ingest_id("a2")
    validacion_a2 = ns["_validar_zip_rapido"]("a2.zip")
    check("A2: _validar_zip_rapido acepta el zip", validacion_a2["ok"])
    resultado_a2 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a2.zip",
            ingest_id=ingest_id_a2,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a2["reservado"],
        )
    )
    check("A2: error_zip es None", resultado_a2["error_zip"] is None)
    check("A2: aceptados == 1 (a.pdf se despachó igual)", resultado_a2["aceptados"] == 1)
    check("A2: hay exactamente 1 entrada en no_soportados (b.pdf, encriptado)", len(resultado_a2["no_soportados"]) == 1)
    if resultado_a2["no_soportados"]:
        motivo_a2 = resultado_a2["no_soportados"][0]["motivo"]
        check(f"A2: el motivo menciona encriptación (fue: {motivo_a2!r})", "encriptado" in motivo_a2.lower())
        check("A2: el miembro aislado es b.pdf", resultado_a2["no_soportados"][0]["nombre"] == "b.pdf")
    check("A2: _procesar_en_background fue llamada exactamente 1 vez (solo a.pdf)", len(ns["_procesador_fake"].llamadas) == 1)
    check(
        f"A2: Pass 0 pre-registró los 2 miembros (fueron {len(ns['_orchestrator_fake'].upserts)} upserts)",
        len(ns["_orchestrator_fake"].upserts) == 2,
    )
    check(
        "A2: soft_delete_invoice fue llamado exactamente 1 vez, para el miembro rechazado (b.pdf, encriptado)",
        len(ns["_orchestrator_fake"].soft_deletes) == 1
        and "b.pdf" in ns["_orchestrator_fake"].soft_deletes[0]["process_id"],
    )
    check(
        f"A2: soft_delete_invoice se llamó con deleted_by=None (limpieza de sistema, ver A1 -- fue "
        f"{ns['_orchestrator_fake'].soft_deletes[0]['deleted_by']!r})",
        ns["_orchestrator_fake"].soft_deletes[0]["deleted_by"] is None,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("A3 -- durabilidad: upsert_invoice('pending') ocurre ANTES de que")
    print("      _procesar_en_background sea invocado, PARA CADA archivo")
    print("=" * 78)
    Path("a3.zip").write_bytes(construir_zip_bytes([("f1.pdf", PDF_VALIDO), ("f2.pdf", PDF_VALIDO)]))
    eventos_a3 = []
    orch_a3 = FakeOrchestrator(eventos=eventos_a3)
    procesador_a3 = FakeProcesadorEnBackground(eventos=eventos_a3)
    ns = construir_namespace(orchestrator=orch_a3, procesador=procesador_a3)
    ingest_id_a3 = _nuevo_ingest_id("a3")
    validacion_a3 = ns["_validar_zip_rapido"]("a3.zip")
    resultado_a3 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a3.zip",
            ingest_id=ingest_id_a3,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a3["reservado"],
        )
    )
    check("A3: aceptados == 2", resultado_a3["aceptados"] == 2)
    check("A3: hubo 2 upserts registrados", len(orch_a3.upserts) == 2)
    check("A3: hubo 2 despachos registrados", len(procesador_a3.llamadas) == 2)
    process_ids_a3 = [u["process_id"] for u in orch_a3.upserts]
    check("A3: los 2 process_id de los upserts son distintos", len(set(process_ids_a3)) == 2)

    for pid in process_ids_a3:
        idx_upsert = next(i for i, ev in enumerate(eventos_a3) if ev == ("upsert", pid))
        idx_despacho = next(i for i, ev in enumerate(eventos_a3) if ev == ("despacho", pid))
        check(
            f"A3: para process_id {pid!r}, el upsert('pending') (evento #{idx_upsert}) "
            f"ocurrió ANTES que _procesar_en_background (evento #{idx_despacho})",
            idx_upsert < idx_despacho,
        )

    # Verificación más fuerte todavía: la Fase A completa (TODOS los
    # upserts) corre ANTES de que la Fase B empiece a procesar CUALQUIERA --
    # si el backend se reiniciara justo después de la Fase A, ya habría fila
    # "pending" para las 2 facturas, no solo para la primera.
    ultimo_upsert = max(i for i, ev in enumerate(eventos_a3) if ev[0] == "upsert")
    primer_despacho = min(i for i, ev in enumerate(eventos_a3) if ev[0] == "despacho")
    check(
        "A3: el ÚLTIMO upsert de la Fase A ocurrió antes que el PRIMER despacho de la Fase B "
        "(la Fase A completa corre entera antes de que empiece la Fase B)",
        ultimo_upsert < primer_despacho,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("A4 -- trabajo en vuelo: reserva 3 (2 válidos + 1 no soportado),")
    print("      el contador vuelve a 0 tras terminar (todo se liberó)")
    print("=" * 78)
    Path("a4.zip").write_bytes(
        construir_zip_bytes([("f1.pdf", PDF_VALIDO), ("f2.pdf", PDF_VALIDO), ("notas.txt", TEXTO_NO_SOPORTADO)])
    )
    ns = construir_namespace(max_trabajo_en_vuelo=200, trabajo_en_vuelo_inicial=0)
    ingest_id_a4 = _nuevo_ingest_id("a4")
    validacion_a4 = ns["_validar_zip_rapido"]("a4.zip")
    check("A4: _validar_zip_rapido acepta el zip", validacion_a4["ok"])
    check(f"A4: reservado == 3 (fue {validacion_a4.get('reservado')})", validacion_a4.get("reservado") == 3)
    check(f"A4: el contador quedó en 3 tras reservar (fue {ns['_trabajo_en_vuelo_zip']})", ns["_trabajo_en_vuelo_zip"] == 3)
    resultado_a4 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a4.zip",
            ingest_id=ingest_id_a4,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a4["reservado"],
        )
    )
    check("A4: aceptados == 2", resultado_a4["aceptados"] == 2)
    check("A4: 1 archivo en no_soportados (notas.txt)", len(resultado_a4["no_soportados"]) == 1)
    check(
        f"A4: el contador _trabajo_en_vuelo_zip volvió a 0 al terminar (fue {ns['_trabajo_en_vuelo_zip']})",
        ns["_trabajo_en_vuelo_zip"] == 0,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("A5 -- trabajo en vuelo: rechazo temprano cuando el sistema ya está")
    print("      saturado, SIN reservar nada de más")
    print("=" * 78)
    Path("a5.zip").write_bytes(construir_zip_bytes([("solo.pdf", PDF_VALIDO)]))
    ns = construir_namespace(max_trabajo_en_vuelo=2, trabajo_en_vuelo_inicial=2)
    check("A5: precondición -- el contador arranca en 2 (== MAX_TRABAJO_EN_VUELO_ZIP)", ns["_trabajo_en_vuelo_zip"] == 2)
    validacion_a5 = ns["_validar_zip_rapido"]("a5.zip")
    check("A5: ok == False (sistema saturado)", validacion_a5["ok"] is False)
    check(
        f"A5: el mensaje de error es claro y accionable (fue: {validacion_a5.get('error')!r})",
        bool(validacion_a5.get("error")) and "reintent" in validacion_a5.get("error", "").lower(),
    )
    check(
        f"A5: el contador NO se movió (sigue en 2, fue {ns['_trabajo_en_vuelo_zip']}) -- no se reservó nada de más",
        ns["_trabajo_en_vuelo_zip"] == 2,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("A6 -- Zip Slip: miembro 'a/../a/evil.pdf' -- se extrae usando el")
    print("      valor de RETORNO de extract() y termina despachado, no se pierde")
    print("=" * 78)
    Path("a6.zip").write_bytes(construir_zip_bytes([("a/../a/evil.pdf", PDF_VALIDO), ("normal.pdf", PDF_VALIDO)]))
    ns = construir_namespace()
    ingest_id_a6 = _nuevo_ingest_id("a6")
    # La carpeta del ingest se borra (shutil.rmtree) al FINAL de la función,
    # una vez despachados todos los archivos -- para confirmar que evil.pdf
    # existía de verdad en disco, DENTRO de esa carpeta, en el momento del
    # despacho (y no después, cuando ya se limpió todo), este fake verifica
    # la ruta EN EL MOMENTO en que es invocado, no después de que la función
    # ya terminó y retornó. Reemplaza a "_procesar_en_background" en el
    # namespace ya construido (los nombres globales se resuelven al llamar,
    # no al definir, así que esto es válido).
    carpeta_esperada_a6 = ns["_carpeta_ingest_zip"](origen="test", ingest_id=ingest_id_a6)
    verificaciones_a6 = []

    async def _procesador_a6(**kwargs):
        ruta = kwargs.get("file_location")
        existe_ahora = bool(ruta) and Path(ruta).exists()
        dentro_de_carpeta = existe_ahora and os.path.realpath(ruta).startswith(os.path.realpath(carpeta_esperada_a6))
        verificaciones_a6.append(
            {
                "file_name": kwargs.get("file_name"),
                "existe_al_momento_del_despacho": existe_ahora,
                "dentro_de_la_carpeta_del_ingest": dentro_de_carpeta,
            }
        )
        return {"success": True, "factura": {"id": kwargs.get("process_id")}}

    ns["_procesar_en_background"] = _procesador_a6

    validacion_a6 = ns["_validar_zip_rapido"]("a6.zip")
    resultado_a6 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a6.zip",
            ingest_id=ingest_id_a6,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a6["reservado"],
        )
    )
    check("A6: error_zip es None", resultado_a6["error_zip"] is None)
    check("A6: aceptados == 2 (ninguno se pierde ni se rechaza por 'ruta interna inválida')", resultado_a6["aceptados"] == 2)
    check("A6: no_soportados está vacío", resultado_a6["no_soportados"] == [])
    nombres_despachados_a6 = sorted(v["file_name"] for v in verificaciones_a6)
    check(
        f"A6: se despachó 'evil.pdf' (basename del miembro con '..') (fueron: {nombres_despachados_a6})",
        "evil.pdf" in nombres_despachados_a6,
    )
    verificacion_evil = next((v for v in verificaciones_a6 if v["file_name"] == "evil.pdf"), None)
    check(
        "A6: evil.pdf existía REALMENTE en disco en el momento del despacho (no se perdió)",
        verificacion_evil is not None and verificacion_evil["existe_al_momento_del_despacho"],
    )
    check(
        "A6: esa ruta caía DENTRO de la carpeta del ingest -- extract() usó su valor de retorno, "
        "no una ruta recalculada a mano que pudiera divergir",
        verificacion_evil is not None and verificacion_evil["dentro_de_la_carpeta_del_ingest"],
    )
    check("A6: la carpeta del ingest ya no existe tras terminar (cleanup normal, igual que A10)", not Path(carpeta_esperada_a6).exists())

    # ========================================================================
    print()
    print("=" * 78)
    print("A7 -- ZIP anidado (nombre termina en .zip) -> aislado en")
    print("      no_soportados, NUNCA se extrae su contenido")
    print("=" * 78)
    zip_interior_a7 = construir_zip_bytes([("factura_interna.pdf", PDF_VALIDO)])
    Path("a7.zip").write_bytes(
        construir_zip_bytes([("factura.pdf", PDF_VALIDO), ("adentro.zip", zip_interior_a7)])
    )
    ns = construir_namespace()
    ingest_id_a7 = _nuevo_ingest_id("a7")
    validacion_a7 = ns["_validar_zip_rapido"]("a7.zip")
    resultado_a7 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a7.zip",
            ingest_id=ingest_id_a7,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a7["reservado"],
        )
    )
    check("A7: aceptados == 1 (solo factura.pdf)", resultado_a7["aceptados"] == 1)
    motivos_a7 = [n["motivo"] for n in resultado_a7["no_soportados"] if n["nombre"] == "adentro.zip"]
    check("A7: 'adentro.zip' quedó en no_soportados con motivo 'ZIP anidado no soportado'", motivos_a7 == ["ZIP anidado no soportado"])
    check(
        "A7: el .zip anidado NUNCA se extrajo a disco (ningún despacho menciona su contenido)",
        not any("factura_interna" in c["file_name"] for c in ns["_procesador_fake"].llamadas),
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("A8 -- notificar_no_soportados_por_webhook=True + archivo no soportado")
    print("      -> fire_webhook llamado 1 vez, con media_type real (no crashea)")
    print("=" * 78)
    Path("a8.zip").write_bytes(construir_zip_bytes([("imagen.bmp", BMP_TIPO_DETECTADO_NO_SOPORTADO)]))
    orch_a8 = FakeOrchestrator()
    ns = construir_namespace(orchestrator=orch_a8)
    ingest_id_a8 = _nuevo_ingest_id("a8")
    validacion_a8 = ns["_validar_zip_rapido"]("a8.zip")
    resultado_a8 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a8.zip",
            ingest_id=ingest_id_a8,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a8["reservado"],
            notificar_no_soportados_por_webhook=True,
        )
    )
    check("A8: nada explotó (error_zip es None)", resultado_a8["error_zip"] is None)
    check("A8: fire_webhook fue llamado exactamente 1 vez", len(orch_a8.webhooks) == 1)
    if orch_a8.webhooks:
        payload_a8 = orch_a8.webhooks[0]
        check("A8: el payload trae error == 'Tipo de archivo no permitido.'", payload_a8.get("error") == "Tipo de archivo no permitido.")
        check(
            f"A8: media_type tiene un valor REAL, sin AttributeError (fue: {payload_a8.get('media_type')!r})",
            payload_a8.get("media_type") == "image/bmp",
        )
    check("A8: el archivo no soportado no quedó en aceptados", resultado_a8["aceptados"] == 0)

    # ========================================================================
    print()
    print("=" * 78)
    print("A9 -- _sanear_nombre_para_process_id: casos puntuales")
    print("=" * 78)
    ns = construir_namespace()
    sanear = ns["_sanear_nombre_para_process_id"]
    check("A9: nombre simple se mantiene", sanear("factura.pdf") == "factura.pdf")
    check("A9: basename -- se descarta cualquier ruta previa", sanear("carpeta/subcarpeta/factura.pdf") == "factura.pdf")
    check("A9: caracteres raros se reemplazan por '_'", sanear("factura #1 (final).pdf") == "factura__1__final_.pdf")
    check("A9: nombre vacío después de sanear cae en 'archivo'", sanear("") == "archivo")
    check("A9: se trunca a 80 caracteres", len(sanear("a" * 200 + ".pdf")) <= 80)
    check(
        "A9: un intento de path traversal en el nombre no deja '..' fuera del basename",
        ".." not in sanear("../../etc/algo.pdf").replace(os.path.basename("../../etc/algo.pdf"), ""),
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("A10 -- al terminar exitosamente, la carpeta de _carpeta_ingest_zip(...)")
    print("       ya no existe en disco (cleanup)")
    print("=" * 78)
    Path("a10.zip").write_bytes(construir_zip_bytes([("factura.pdf", PDF_VALIDO)]))
    ns = construir_namespace()
    ingest_id_a10 = _nuevo_ingest_id("a10")
    carpeta_esperada_a10 = ns["_carpeta_ingest_zip"](origen="test", ingest_id=ingest_id_a10)
    validacion_a10 = ns["_validar_zip_rapido"]("a10.zip")
    resultado_a10 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a10.zip",
            ingest_id=ingest_id_a10,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a10["reservado"],
        )
    )
    check("A10: aceptados == 1", resultado_a10["aceptados"] == 1)
    check(f"A10: la carpeta '{carpeta_esperada_a10}' ya NO existe en disco tras terminar", not Path(carpeta_esperada_a10).exists())

    # ========================================================================
    print()
    print("=" * 78)
    print("A11 -- durabilidad de recuperación: adjuntar_archivo_original ocurre")
    print("       en Fase A, para CADA miembro, ANTES de que empiece la Fase B")
    print("       (cierra el hueco de retry-extraction=422 para huérfanas de Fase A)")
    print("=" * 78)
    Path("a11.zip").write_bytes(construir_zip_bytes([("f1.pdf", PDF_VALIDO), ("f2.pdf", PDF_VALIDO)]))
    eventos_a11 = []
    orch_a11 = FakeOrchestrator(eventos=eventos_a11)
    procesador_a11 = FakeProcesadorEnBackground(eventos=eventos_a11)
    ns = construir_namespace(orchestrator=orch_a11, procesador=procesador_a11)
    ingest_id_a11 = _nuevo_ingest_id("a11")
    validacion_a11 = ns["_validar_zip_rapido"]("a11.zip")
    resultado_a11 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a11.zip",
            ingest_id=ingest_id_a11,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a11["reservado"],
        )
    )
    check("A11: aceptados == 2", resultado_a11["aceptados"] == 2)
    check(
        f"A11: adjuntar_archivo_original fue llamado exactamente 2 veces, una por miembro aceptado (fue {len(orch_a11.adjuntos)})",
        len(orch_a11.adjuntos) == 2,
    )
    check(
        "A11: cada llamada a adjuntar_archivo_original recibió una ruta que EXISTÍA en disco al momento de la llamada",
        bool(orch_a11.adjuntos) and all(a["existia_en_disco"] for a in orch_a11.adjuntos),
    )
    record_ids_a11 = {a["record_id"] for a in orch_a11.adjuntos}
    process_ids_a11 = {u["process_id"] for u in orch_a11.upserts}
    check(
        "A11: cada adjunto usó el id de PocketBase devuelto por SU PROPIO upsert (no el process_id crudo)",
        record_ids_a11 == {f"pbid-{pid}" for pid in process_ids_a11},
    )

    ultimo_adjunto_a11 = max(i for i, ev in enumerate(eventos_a11) if ev[0] == "adjunto")
    primer_despacho_a11 = min(i for i, ev in enumerate(eventos_a11) if ev[0] == "despacho")
    check(
        "A11: el ÚLTIMO adjuntar_archivo_original de la Fase A ocurrió antes que el PRIMER "
        "despacho de la Fase B -- si el proceso se reinicia justo después de la Fase A, TODAS "
        "las facturas ya tienen documento_original, no solo la primera (retry-extraction no "
        "devolvería 422 para ninguna)",
        ultimo_adjunto_a11 < primer_despacho_a11,
    )
    for pid in process_ids_a11:
        idx_upsert = next(i for i, ev in enumerate(eventos_a11) if ev == ("upsert", pid))
        idx_adjunto = next(i for i, ev in enumerate(eventos_a11) if ev == ("adjunto", f"pbid-{pid}"))
        check(
            f"A11: para process_id {pid!r}, el adjunto (evento #{idx_adjunto}) ocurrió DESPUÉS "
            f"del upsert 'pending' (evento #{idx_upsert}) que le dio su id de PocketBase",
            idx_upsert < idx_adjunto,
        )

    # Garantía MÁS FUERTE agregada en la ronda 4 (Pass 1 / Pass 2): el
    # PRIMER adjunto de TODO el ZIP ocurre después del ÚLTIMO upsert de TODO
    # el ZIP -- es decir, TODOS los miembros ya tienen su fila "pending"
    # antes de que arranque el primer PATCH multipart (más lento). Antes de
    # este reordenamiento, upsert y adjunto de un mismo miembro estaban
    # intercalados uno con otro -- un reinicio a mitad del ZIP podía dejar a
    # los miembros DESPUÉS del punto de corte sin ninguna fila en absoluto.
    # Con Pass 1/Pass 2 separadas, ese riesgo queda acotado a un reinicio
    # DURANTE la Pass 1 (upserts, rápidos) -- nunca durante la Pass 2
    # (adjuntos, lentos), que es la ventana que más se agrandó con el fix de
    # esta ronda.
    ultimo_upsert_a11 = max(i for i, ev in enumerate(eventos_a11) if ev[0] == "upsert")
    primer_adjunto_a11 = min(i for i, ev in enumerate(eventos_a11) if ev[0] == "adjunto")
    check(
        "A11: el PRIMER adjunto de TODO el zip ocurrió DESPUÉS del ÚLTIMO upsert de TODO el zip "
        "(Pass 1 completa entera antes de que arranque la Pass 2 -- ya no están intercalados "
        "por miembro)",
        ultimo_upsert_a11 < primer_adjunto_a11,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("A11b -- best-effort: si adjuntar_archivo_original explota para un")
    print("        miembro, igual se despacha a Fase B (un fallo temprano de")
    print("        PocketBase no debe sacar al archivo de la cola)")
    print("=" * 78)
    Path("a11b.zip").write_bytes(construir_zip_bytes([("solo.pdf", PDF_VALIDO)]))

    class OrchestratorAdjuntoFalla(FakeOrchestrator):
        def adjuntar_archivo_original(self, record_id, file_path, filename, mime_type):
            raise RuntimeError("PocketBase caído (simulado)")

    orch_a11b = OrchestratorAdjuntoFalla()
    ns = construir_namespace(orchestrator=orch_a11b)
    ingest_id_a11b = _nuevo_ingest_id("a11b")
    validacion_a11b = ns["_validar_zip_rapido"]("a11b.zip")
    resultado_a11b = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a11b.zip",
            ingest_id=ingest_id_a11b,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a11b["reservado"],
        )
    )
    check(
        "A11b: aceptados == 1 (el fallo de adjuntar_archivo_original no lo saca de la cola)",
        resultado_a11b["aceptados"] == 1,
    )
    check("A11b: error_zip sigue siendo None", resultado_a11b["error_zip"] is None)
    check(
        "A11b: _procesar_en_background SÍ fue llamado para el archivo pese al fallo del adjunto",
        len(ns["_procesador_fake"].llamadas) == 1,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("A12 -- Pass 0: TODOS los miembros candidatos se pre-registran ANTES")
    print("       de que Pass 1 produzca CUALQUIER resultado (aceptar o")
    print("       rechazar) para CUALQUIERA de ellos -- cierra el hueco donde")
    print("       un reinicio a mitad de la clasificación dejaba sin ninguna")
    print("       fila a los miembros todavía no alcanzados por el loop")
    print("=" * 78)
    # a.pdf válido + b.pdf corrupto (misma receta verificada de A1), más un
    # 3er miembro (ZIP anidado) agregado al mismo archivo.
    Path("a12.zip").write_bytes(_crear_zip_con_miembro_crc_corrupto())
    with zipfile.ZipFile("a12.zip", "a") as zf_a12b:
        zf_a12b.writestr("anidado.zip", construir_zip_bytes([("interno.pdf", PDF_VALIDO)]))

    eventos_a12 = []
    orch_a12 = FakeOrchestrator(eventos=eventos_a12)
    procesador_a12 = FakeProcesadorEnBackground(eventos=eventos_a12)
    ns = construir_namespace(orchestrator=orch_a12, procesador=procesador_a12)
    ingest_id_a12 = _nuevo_ingest_id("a12")
    validacion_a12 = ns["_validar_zip_rapido"]("a12.zip")
    resultado_a12 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a12.zip",
            ingest_id=ingest_id_a12,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a12["reservado"],
        )
    )
    check("A12: aceptados == 1 (solo a.pdf)", resultado_a12["aceptados"] == 1)
    check(
        "A12: no_soportados tiene 2 entradas (b.pdf corrupto + anidado.zip)",
        len(resultado_a12["no_soportados"]) == 2,
    )
    check(
        f"A12: Pass 0 pre-registró exactamente 2 miembros -- a.pdf y b.pdf, NUNCA anidado.zip "
        f"(fueron {len(orch_a12.upserts)} upserts)",
        len(orch_a12.upserts) == 2,
    )
    check(
        "A12: soft_delete_invoice se llamó exactamente 1 vez, para b.pdf (rechazado en Pass 1)",
        len(orch_a12.soft_deletes) == 1 and "b.pdf" in orch_a12.soft_deletes[0]["process_id"],
    )
    # La aserción central de A12: el ÚLTIMO evento de Pass 0 (upsert) ocurre
    # ANTES que el PRIMER resultado que Pass 1 produce para CUALQUIER
    # miembro (ya sea aceptarlo -> despacho, o rechazarlo -> soft_delete).
    # Si esto se cumple, un reinicio en CUALQUIER punto de Pass 1 en
    # adelante deja a TODOS los miembros candidatos con al menos una fila.
    ultimo_evento_pass0_a12 = max(i for i, ev in enumerate(eventos_a12) if ev[0] == "upsert")
    primer_resultado_pass1_a12 = min(
        i for i, ev in enumerate(eventos_a12) if ev[0] in ("soft_delete", "despacho")
    )
    check(
        "A12: el ÚLTIMO upsert de Pass 0 ocurrió ANTES que el PRIMER resultado de Pass 1 "
        "(aceptación o rechazo) para cualquier miembro -- Pass 0 completa entera antes de "
        "que Pass 1 produzca ningún resultado",
        ultimo_evento_pass0_a12 < primer_resultado_pass1_a12,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("A13 -- resiliencia: si upsert_invoice de Pass 0 falla (best-effort,")
    print("       devuelve None) para UN miembro puntual, el resto del ZIP")
    print("       sigue clasificándose y despachándose con normalidad -- y ese")
    print("       miembro puntual igual se despacha (se auto-recupera cuando")
    print("       Fase B haga su propio upsert real, ver _procesar_imagen_o_pdf_impl)")
    print("=" * 78)
    Path("a13.zip").write_bytes(
        construir_zip_bytes([("falla_registro.pdf", PDF_VALIDO), ("ok.pdf", PDF_VALIDO)])
    )

    class OrchestratorPass0Falla(FakeOrchestrator):
        def upsert_invoice(self, data):
            if "falla_registro.pdf" in (data.get("process_id") or ""):
                self._eventos.append(("upsert_fallido", data.get("process_id")))
                return None  # mismo contrato que el upsert_invoice real: None en vez de raise
            return super().upsert_invoice(data)

    orch_a13 = OrchestratorPass0Falla()
    ns = construir_namespace(orchestrator=orch_a13)
    ingest_id_a13 = _nuevo_ingest_id("a13")
    validacion_a13 = ns["_validar_zip_rapido"]("a13.zip")
    resultado_a13 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a13.zip",
            ingest_id=ingest_id_a13,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a13["reservado"],
        )
    )
    check(
        "A13: aceptados == 2 (AMBOS archivos se despachan, incluido el que falló al registrarse en Pass 0)",
        resultado_a13["aceptados"] == 2,
    )
    check("A13: error_zip sigue siendo None (un fallo puntual de registro no aborta el ZIP)", resultado_a13["error_zip"] is None)
    nombres_despachados_a13 = sorted(c["file_name"] for c in ns["_procesador_fake"].llamadas)
    check(
        f"A13: ambos archivos, incluido falla_registro.pdf, llegaron a despacharse (fueron {nombres_despachados_a13})",
        nombres_despachados_a13 == ["falla_registro.pdf", "ok.pdf"],
    )
    check(
        "A13: no se llamó adjuntar_archivo_original para falla_registro.pdf (no tenía id de "
        "PocketBase -- Pass 2 lo salta con seguridad en vez de fallar)",
        not any("falla_registro" in a["file_path"] for a in orch_a13.adjuntos),
    )
    check(
        "A13: SÍ se llamó adjuntar_archivo_original para ok.pdf (su registro de Pass 0 sí funcionó)",
        any("ok.pdf" in a["file_path"] for a in orch_a13.adjuntos),
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("A14 -- invariante global: todo miembro que Pass 0 pre-registró termina")
    print("       en UNO de dos estados finales -- ACEPTADO (despachado a Fase B)")
    print("       o DESCARTADO (soft_delete_invoice) -- ninguno queda en el limbo")
    print("=" * 78)
    Path("a14.zip").write_bytes(
        construir_zip_bytes(
            [
                ("ok1.pdf", PDF_VALIDO),
                ("ok2.pdf", PDF_VALIDO),
                ("notas.txt", TEXTO_NO_SOPORTADO),
                ("anidado.zip", construir_zip_bytes([("x.pdf", PDF_VALIDO)])),
            ]
        )
    )
    orch_a14 = FakeOrchestrator()
    ns = construir_namespace(orchestrator=orch_a14)
    ingest_id_a14 = _nuevo_ingest_id("a14")
    validacion_a14 = ns["_validar_zip_rapido"]("a14.zip")
    resultado_a14 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a14.zip",
            ingest_id=ingest_id_a14,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a14["reservado"],
        )
    )
    check("A14: aceptados == 2 (ok1.pdf, ok2.pdf)", resultado_a14["aceptados"] == 2)
    check(
        "A14: no_soportados tiene 2 entradas (notas.txt tipo no soportado, anidado.zip)",
        len(resultado_a14["no_soportados"]) == 2,
    )
    check(
        f"A14: Pass 0 registró exactamente 3 miembros -- todos menos anidado.zip, que nunca "
        f"tuvo placeholder (fueron {len(orch_a14.upserts)} upserts)",
        len(orch_a14.upserts) == 3,
    )
    check(
        f"A14: soft_delete_invoice se llamó exactamente 1 vez -- solo para notas.txt, el único "
        f"miembro CON placeholder que terminó rechazado (fueron {len(orch_a14.soft_deletes)})",
        len(orch_a14.soft_deletes) == 1,
    )
    if orch_a14.soft_deletes:
        check(
            "A14: el descarte fue para notas.txt específicamente",
            "notas.txt" in orch_a14.soft_deletes[0]["process_id"],
        )
        check(
            f"A14: soft_delete_invoice se llamó con deleted_by=None (limpieza de sistema, ver "
            f"A1 -- fue {orch_a14.soft_deletes[0]['deleted_by']!r})",
            orch_a14.soft_deletes[0]["deleted_by"] is None,
        )
    check(
        "A14: invariante -- cada uno de los 3 miembros pre-registrados en Pass 0 terminó en "
        "EXACTAMENTE uno de los 2 estados finales (aceptados + soft_deletes == upserts de Pass 0)",
        resultado_a14["aceptados"] + len(orch_a14.soft_deletes) == len(orch_a14.upserts),
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("A15 -- Pass 1 adjunta el archivo de un miembro aceptado EN LA MISMA")
    print("       iteración que lo acepta, NO en una pasada separada al final:")
    print("       los eventos de adjunto/descarte quedan INTERCALADOS en el")
    print("       mismo orden que los miembros del ZIP, no agrupados")
    print("=" * 78)
    Path("a15.zip").write_bytes(
        construir_zip_bytes(
            [
                ("m0_ok.pdf", PDF_VALIDO),
                ("m1_bad.txt", TEXTO_NO_SOPORTADO),
                ("m2_ok.pdf", PDF_VALIDO),
                ("m3_bad.txt", TEXTO_NO_SOPORTADO),
                ("m4_ok.pdf", PDF_VALIDO),
            ]
        )
    )
    eventos_a15 = []
    orch_a15 = FakeOrchestrator(eventos=eventos_a15)
    procesador_a15 = FakeProcesadorEnBackground(eventos=eventos_a15)
    ns = construir_namespace(orchestrator=orch_a15, procesador=procesador_a15)
    ingest_id_a15 = _nuevo_ingest_id("a15")
    validacion_a15 = ns["_validar_zip_rapido"]("a15.zip")
    resultado_a15 = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="a15.zip",
            ingest_id=ingest_id_a15,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_a15["reservado"],
        )
    )
    check("A15: aceptados == 3 (m0, m2, m4)", resultado_a15["aceptados"] == 3)
    check("A15: no_soportados tiene 2 entradas (m1, m3)", len(resultado_a15["no_soportados"]) == 2)
    # El orden REAL de eventos de Pass 1 (ignorando los upserts de Pass 0,
    # que ya se probaron por separado en A12) debe ser exactamente:
    # adjunto(m0), soft_delete(m1), adjunto(m2), soft_delete(m3), adjunto(m4)
    # -- intercalado en el mismo orden que los miembros del ZIP. Si Pass 1
    # todavía agrupara todos los adjuntos al final (diseño viejo), este
    # patrón NO se cumpliría: los 3 adjuntos aparecerían juntos, después de
    # los 2 soft_deletes (o en cualquier orden que no siga los índices).
    eventos_pass1_a15 = [ev for ev in eventos_a15 if ev[0] in ("adjunto", "soft_delete")]
    orden_esperado_a15 = ["m0_ok", "m1_bad", "m2_ok", "m3_bad", "m4_ok"]
    orden_real_a15 = []
    for tipo_evento, identificador in eventos_pass1_a15:
        for nombre in orden_esperado_a15:
            if nombre.split("_")[0] in identificador:
                orden_real_a15.append(nombre.split("_")[0])
                break
    check(
        f"A15: el orden real de adjunto/descarte sigue EXACTAMENTE el orden de los miembros "
        f"del ZIP (m0..m4), sin agrupar -- fue {orden_real_a15}",
        orden_real_a15 == ["m0", "m1", "m2", "m3", "m4"],
    )
    tipos_en_orden_a15 = [ev[0] for ev in eventos_pass1_a15]
    check(
        f"A15: los TIPOS de evento en ese mismo orden son adjunto/descarte/adjunto/descarte/adjunto "
        f"(fueron {tipos_en_orden_a15})",
        tipos_en_orden_a15 == ["adjunto", "soft_delete", "adjunto", "soft_delete", "adjunto"],
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("A16 -- Pass 0 corre bajo su PROPIO semáforo (ZIP_REGISTRO_SEMAPHORE),")
    print("       desacoplado de ZIP_EXTRACTION_SEMAPHORE -- no queda esperando")
    print("       en cola detrás de la extracción pesada de otros ZIPs (cierra")
    print("       el hallazgo de la ronda 5: un ZIP podía perderse sin rastro")
    print("       si el proceso reiniciaba mientras esperaba ese semáforo)")
    print("=" * 78)
    Path("a16.zip").write_bytes(construir_zip_bytes([("solo.pdf", PDF_VALIDO)]))
    orch_a16 = FakeOrchestrator()
    ns = construir_namespace(orchestrator=orch_a16)
    ingest_id_a16 = _nuevo_ingest_id("a16")
    validacion_a16 = ns["_validar_zip_rapido"]("a16.zip")

    async def _correr_a16():
        # Satura ZIP_EXTRACTION_SEMAPHORE (capacidad 2) adquiriéndolo de
        # antemano -- simula 2 ZIPs pesados ya en Pass 1. Si Pass 0
        # compartiera este semáforo, quedaría esperando detrás de estos 2
        # "held" y no correría dentro de la ventana corta de este test.
        await ns["ZIP_EXTRACTION_SEMAPHORE"].acquire()
        await ns["ZIP_EXTRACTION_SEMAPHORE"].acquire()
        task = asyncio.create_task(
            ns["_extraer_zip_y_despachar_individualmente"](
                zip_path="a16.zip",
                ingest_id=ingest_id_a16,
                origen="test",
                reservado_trabajo_en_vuelo=validacion_a16["reservado"],
            )
        )
        await asyncio.sleep(0.3)  # deja correr Pass 0 -- su semáforo está libre
        pass0_corrio_pese_a_extraction_saturado = len(orch_a16.upserts) >= 1
        ns["ZIP_EXTRACTION_SEMAPHORE"].release()
        ns["ZIP_EXTRACTION_SEMAPHORE"].release()
        resultado = await task
        return pass0_corrio_pese_a_extraction_saturado, resultado

    pass0_corrio_a16, resultado_a16 = _run(_correr_a16())
    check(
        "A16: Pass 0 (upsert_invoice) corrió y completó AUNQUE ZIP_EXTRACTION_SEMAPHORE "
        "estuviera 100% saturado por otros 2 ZIPs -- Pass 0 no espera detrás de la "
        "extracción pesada",
        pass0_corrio_a16,
    )
    check(
        "A16: una vez liberado el semáforo de extracción, el ZIP termina de procesarse "
        "normalmente (aceptados == 1)",
        resultado_a16["aceptados"] == 1,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("Extra -- notificaciones (sección 5 del rediseño): 1 email-resumen por")
    print("         ZIP completo, pero 1 on_item_completado POR FACTURA")
    print("=" * 78)
    Path("extra_email.zip").write_bytes(
        construir_zip_bytes([("ok1.pdf", PDF_VALIDO), ("ok2.pdf", PDF_VALIDO), ("falla.pdf", PDF_VALIDO)])
    )
    orch_extra = FakeOrchestrator()
    procesador_extra = FakeProcesadorEnBackground(fallar_para_file_name={"falla.pdf"})
    ns = construir_namespace(orchestrator=orch_extra, procesador=procesador_extra)
    ingest_id_extra = _nuevo_ingest_id("extra")
    validacion_extra = ns["_validar_zip_rapido"]("extra_email.zip")

    completados = []

    async def _on_item_completado(payload):
        completados.append(payload)

    resultado_extra = _run(
        ns["_extraer_zip_y_despachar_individualmente"](
            zip_path="extra_email.zip",
            ingest_id=ingest_id_extra,
            origen="test",
            reservado_trabajo_en_vuelo=validacion_extra["reservado"],
            enviar_resumen_por_email=True,
            datos_email={"from_email": "cliente@example.com", "subject": "Facturas de agosto"},
            on_item_completado=_on_item_completado,
        )
    )
    check("Extra: aceptados == 2, 1 error de despacho (falla.pdf)", resultado_extra["aceptados"] == 2 and len(resultado_extra["errores_despacho"]) == 1)
    check(
        f"Extra: orchestrator.enviar_email fue llamado exactamente 1 VEZ para todo el ZIP (fue {len(orch_extra.emails)})",
        len(orch_extra.emails) == 1,
    )
    if orch_extra.emails:
        check("Extra: el email va al from_email del formulario original", orch_extra.emails[0]["destinatario"] == "cliente@example.com")
        check("Extra: el asunto referencia el original ('Re: ...')", orch_extra.emails[0]["asunto"].startswith("Re:") and "Facturas de agosto" in orch_extra.emails[0]["asunto"])
    check(
        f"Extra: on_item_completado fue invocado 1 VEZ POR FACTURA (3 archivos válidos -> 3 llamadas, fue {len(completados)})",
        len(completados) == 3,
    )
    exitosos_extra = [p for p in completados if p.get("success")]
    fallidos_extra = [p for p in completados if not p.get("success")]
    check("Extra: 2 completados con success=True, 1 con success=False", len(exitosos_extra) == 2 and len(fallidos_extra) == 1)
    check(
        "Extra: los payloads exitosos traen 'factura' y NO traen 'error'",
        all("factura" in p and "error" not in p for p in exitosos_extra),
    )
    check(
        "Extra: el payload fallido trae 'error' y NO trae 'factura'",
        all("error" in p and "factura" not in p for p in fallidos_extra),
    )
    check("Extra: todos los payloads traen file_name", all("file_name" in p for p in completados))
    # Contrato EXACTO de worker() (comparación campo por campo documentada
    # en docs/plan-fase1-zip-REDISEÑO.md): éxito usa la clave "id", error
    # usa "process_id" -- inconsistencia real que worker() ya tenía y que
    # se replica tal cual, a propósito, para compatibilidad real con
    # integraciones existentes (no se "corrige" acá).
    check(
        "Extra: los payloads EXITOSOS usan la clave 'id' (no 'process_id'), igual que worker()",
        all("id" in p and "process_id" not in p for p in exitosos_extra),
    )
    check(
        "Extra: el payload FALLIDO usa la clave 'process_id' (no 'id'), igual que worker()",
        all("process_id" in p and "id" not in p for p in fallidos_extra),
    )
    check(
        "Extra: los payloads exitosos propagan saved/saved_items/bas/drive_file_id "
        "(no solo el dict 'factura' entero)",
        all(
            p.get("saved") is True
            and p.get("saved_items") is True
            and p.get("bas") == {"comprobante": "simulado"}
            and str(p.get("drive_file_id") or "").startswith("drive-")
            for p in exitosos_extra
        ),
    )

finally:
    os.chdir(cwd_original)
    shutil.rmtree(DIR_TEMP, ignore_errors=True)


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
