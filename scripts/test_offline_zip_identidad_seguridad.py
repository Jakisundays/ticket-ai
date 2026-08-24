"""
Test offline CRÍTICO del rediseño de identidad/filesystem para el manejo de
ZIP (docs/plan-fase1-zip-REDISEÑO.md sección 1) en
routes/process_invoice_google_2.py.

Contexto: la revisión adversarial post-implementación encontró 2 hallazgos
CRÍTICOS en el diseño anterior: (1) el `id`/`process_id` que manda el
cliente se usaba DIRECTAMENTE para construir rutas de disco
(f"./downloads/{id}"), permitiendo path traversal real (".." -> raíz de la
app) y `shutil.rmtree` arbitrario sin autenticación en /website-upload
(público); (2) nada garantizaba que ese valor fuera único, así que dos
requests con el mismo id (reintento, o abuso) se pisaban en disco y en
PocketBase.

El fix estructural: el SERVIDOR genera su propio `ingest_id` (uuid4) para
cada ingesta de ZIP -- el valor del cliente (`client_reference`) queda
SOLO como metadata de logging, nunca toca una ruta de disco ni una clave
de PocketBase. Este test verifica esa garantía de forma directa y
adversarial: prueba valores diseñados específicamente para escapar (path
traversal, "..", "/", rutas absolutas) y confirma que NINGUNO de ellos
aparece jamás en una llamada real a os.makedirs/shutil.move/shutil.rmtree
-- interceptando esas 3 funciones REALES (monkeypatch temporal sobre los
módulos os/shutil reales, restaurado en cada finally) para registrar cada
argumento con el que fueron invocadas de verdad, en vez de solo confiar en
que el código "debería" sanitizar. El resto de os/shutil (path.join,
basename, splitext, remove, etc.) se deja intacto -- no se reimplementa
ninguna lógica de filesystem, solo se espían 3 funciones puntuales.

También prueba, con el CÓDIGO REAL extraído vía AST:
  - Dos invocaciones con el mismo client_reference -> ingest_id distinto,
    sin colisión de carpeta ni de process_id en PocketBase.
  - Dos invocaciones REALMENTE concurrentes (asyncio.gather) con el mismo
    client_reference y nombres de archivo internos idénticos -> ningún
    process_id se repite entre ambas.
  - El thread-safety real del contador de trabajo-en-vuelo
    (_reservar_trabajo_en_vuelo_zip/_liberar_trabajo_en_vuelo_zip, que usan
    threading.Lock porque se llaman tanto desde el hilo de
    asyncio.to_thread como desde el event loop) bajo carga concurrente real
    con threading.Thread, no solo coroutines.
  - Que la carpeta que termina pasándose a shutil.rmtree SIEMPRE resuelve
    (realpath) adentro de ./downloads -- nunca afuera, sin importar qué
    mandó el cliente.

Uso: python3 scripts/test_offline_zip_identidad_seguridad.py
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


NOMBRES_A_EXTRAER = [
    "_extraer_zip_y_despachar_individualmente",
    "_validar_zip_rapido",
    "_carpeta_ingest_zip",
    "_sanear_nombre_para_process_id",
    "_generar_html_resumen_zip",
    "_reservar_trabajo_en_vuelo_zip",
    "_liberar_trabajo_en_vuelo_zip",
]
FUENTES = {}
for nombre in NOMBRES_A_EXTRAER:
    FUENTES[nombre] = _extraer_funcion_top_level(nombre)
    check(f"Se extrajo '{nombre}' del código fuente real", bool(FUENTES[nombre]))

if FALLOS:
    print("\nNo se pudo extraer el código real -- abortando.")
    sys.exit(1)


# ============================================================================
# Fixtures reales verificadas.
# ============================================================================
PDF_VALIDO = b"%PDF-1.4\n" + b"X" * 50 + b"\n%%EOF\n"


def construir_zip_bytes(miembros):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for nombre, contenido in miembros:
            zf.writestr(nombre, contenido)
    return buf.getvalue()


class RegistradorLlamadasFS:
    def __init__(self):
        self.makedirs = []
        self.move = []
        self.rmtree = []


@contextmanager
def espiar_fs_real(registrador):
    """Monkeypatch TEMPORAL de os.makedirs/shutil.move/shutil.rmtree --
    envuelve las funciones REALES (siguen escribiendo a disco de verdad,
    dentro del tempdir aislado del test), solo agrega el registro de
    argumentos. Se restaura siempre, incluso si el bloque `with` lanza."""
    makedirs_original = os.makedirs
    move_original = shutil.move
    rmtree_original = shutil.rmtree

    def makedirs_espiado(path, *a, **k):
        registrador.makedirs.append(path)
        return makedirs_original(path, *a, **k)

    def move_espiado(src, dst, *a, **k):
        registrador.move.append(dst)
        return move_original(src, dst, *a, **k)

    def rmtree_espiado(path, *a, **k):
        registrador.rmtree.append(path)
        return rmtree_original(path, *a, **k)

    os.makedirs = makedirs_espiado
    shutil.move = move_espiado
    shutil.rmtree = rmtree_espiado
    try:
        yield registrador
    finally:
        os.makedirs = makedirs_original
        shutil.move = move_original
        shutil.rmtree = rmtree_original


class FakeAppLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class FakeOrchestrator:
    def __init__(self):
        self.upserts = []
        self.webhooks = []
        self.emails = []
        self.adjuntos = []
        self.soft_deletes = []
        self._pb_client = self

    def upsert_invoice(self, data):
        registro = dict(data)
        self.upserts.append(registro)
        # "id" real de PocketBase sintetizado SOLO en el retorno -- ver
        # mismo patrón en test_offline_zip_extraccion.py. Necesario para que
        # adjuntar_archivo_original (Pass 2, agregado en la ronda 4) y el
        # pre-registro de Pass 0 (ronda 5) tengan un id real para trabajar,
        # en vez de silenciosamente no hacer nada por faltar "id".
        return {**registro, "id": f"pbid-{registro.get('process_id')}"}

    def adjuntar_archivo_original(self, record_id, file_path, filename, mime_type):
        self.adjuntos.append(
            {"record_id": record_id, "file_path": file_path, "filename": filename}
        )
        return True

    def soft_delete_invoice(self, process_id, *, deleted_by=None, reason=None):
        self.soft_deletes.append({"process_id": process_id, "deleted_by": deleted_by, "reason": reason})
        return {"process_id": process_id, "deleted_at": "2026-01-01T00:00:00Z"}

    async def fire_webhook(self, data):
        self.webhooks.append(data)

    def enviar_email(self, destinatario, asunto, cuerpo):
        self.emails.append({"destinatario": destinatario, "asunto": asunto, "cuerpo": cuerpo})
        return True


async def _procesar_en_background_fake(**kwargs):
    return {"success": True, "factura": {"id": kwargs.get("process_id")}}


def construir_namespace(orchestrator, max_trabajo_en_vuelo=200):
    """Namespace fresco -- os/shutil REALES (el monkeypatch de
    espiar_fs_real actúa sobre los módulos reales, así que cualquier
    namespace que use "os"/"shutil" tal cual ve las funciones espiadas
    mientras el `with espiar_fs_real(...)` esté activo)."""
    namespace = {
        "os": os,
        "re": re,
        "shutil": shutil,
        "zipfile": zipfile,
        "zlib": zlib,
        "filetype": filetype,
        "Optional": Optional,
        "Callable": Callable,
        "orchestrator": orchestrator,
        "app_logger": FakeAppLogger(),
        "_procesar_en_background": _procesar_en_background_fake,
        "asyncio": asyncio,
        "MAX_ARCHIVOS_ZIP": 100,
        "MAX_ZIP_DESCOMPRIMIDO_BYTES": 500 * 1024 * 1024,
        "MAX_TRABAJO_EN_VUELO_ZIP": max_trabajo_en_vuelo,
        "_trabajo_en_vuelo_zip": 0,
        "_trabajo_en_vuelo_zip_lock": threading.Lock(),
        "ZIP_EXTRACTION_SEMAPHORE": asyncio.Semaphore(2),
        "ZIP_REGISTRO_SEMAPHORE": asyncio.Semaphore(8),
    }
    for nombre in NOMBRES_A_EXTRAER:
        exec(compile(textwrap.dedent(FUENTES[nombre]), f"<{nombre} real>", "exec"), namespace)
    return namespace


cwd_original = os.getcwd()

# ============================================================================
# G1 -- client_reference con payloads de path traversal -- NUNCA debe
# aparecer en ninguna llamada real a os.makedirs/shutil.move/shutil.rmtree.
# ============================================================================
print("=" * 78)
print("G1 -- client_reference maliciosa nunca llega a una ruta de disco real")
print("=" * 78)

DIR_TEMP_G1 = Path(tempfile.mkdtemp(prefix="test_zip_identidad_g1_"))
os.chdir(DIR_TEMP_G1)

PAYLOADS_MALICIOSOS = [
    "..",
    "../../../../tmp/pwn",
    "/etc/passwd",
    "../../etc",
    "a/../../b",
    "//etc//passwd",
    "..%2f..%2fetc",
]

try:
    for payload in PAYLOADS_MALICIOSOS:
        orch = FakeOrchestrator()
        ns = construir_namespace(orch)
        registrador = RegistradorLlamadasFS()

        zip_bytes = construir_zip_bytes([("factura.pdf", PDF_VALIDO)])
        zip_path = DIR_TEMP_G1 / f"test_{uuid.uuid4().hex}.zip"
        zip_path.write_bytes(zip_bytes)

        with espiar_fs_real(registrador):
            validacion = ns["_validar_zip_rapido"](str(zip_path))
            check(f"payload {payload!r}: _validar_zip_rapido acepta el zip de prueba", validacion["ok"])

            resultado = asyncio.run(
                ns["_extraer_zip_y_despachar_individualmente"](
                    zip_path=str(zip_path),
                    ingest_id=uuid.uuid4().hex,
                    origen="test",
                    reservado_trabajo_en_vuelo=validacion.get("reservado", 1),
                    client_reference=payload,
                )
            )
        check(f"payload {payload!r}: no hubo error_zip", resultado.get("error_zip") is None)

        todas_las_rutas = registrador.makedirs + registrador.move + registrador.rmtree
        check(
            f"payload {payload!r}: NINGUNA ruta real (makedirs/move/rmtree) contiene el payload",
            all(payload not in ruta for ruta in todas_las_rutas),
        )
        check(
            f"payload {payload!r}: NINGUN process_id despachado contiene el payload",
            all(payload not in u.get("process_id", "") for u in orch.upserts),
        )
finally:
    os.chdir(cwd_original)
    shutil.rmtree(DIR_TEMP_G1, ignore_errors=True)


# ============================================================================
# G2 -- cleanup (rmtree) SIEMPRE resuelve adentro de ./downloads, nunca afuera.
# ============================================================================
print()
print("=" * 78)
print("G2 -- shutil.rmtree del cleanup final siempre resuelve DENTRO de downloads/")
print("=" * 78)

DIR_TEMP_G2 = Path(tempfile.mkdtemp(prefix="test_zip_identidad_g2_"))
os.chdir(DIR_TEMP_G2)
try:
    for payload in PAYLOADS_MALICIOSOS:
        orch = FakeOrchestrator()
        ns = construir_namespace(orch)
        registrador = RegistradorLlamadasFS()

        zip_bytes = construir_zip_bytes([("factura.pdf", PDF_VALIDO)])
        zip_path = DIR_TEMP_G2 / f"test_{uuid.uuid4().hex}.zip"
        zip_path.write_bytes(zip_bytes)

        with espiar_fs_real(registrador):
            validacion = ns["_validar_zip_rapido"](str(zip_path))
            asyncio.run(
                ns["_extraer_zip_y_despachar_individualmente"](
                    zip_path=str(zip_path),
                    ingest_id=uuid.uuid4().hex,
                    origen="test",
                    reservado_trabajo_en_vuelo=validacion.get("reservado", 1),
                    client_reference=payload,
                )
            )

        raiz_downloads = os.path.realpath("./downloads")
        check(f"payload {payload!r}: hubo al menos 1 llamada a rmtree", len(registrador.rmtree) >= 1)
        for ruta_rmtree in registrador.rmtree:
            check(
                f"payload {payload!r}: rmtree({ruta_rmtree!r}) resuelve dentro de downloads/",
                os.path.realpath(ruta_rmtree).startswith(raiz_downloads),
            )
finally:
    os.chdir(cwd_original)
    shutil.rmtree(DIR_TEMP_G2, ignore_errors=True)


# ============================================================================
# G3 -- mismo client_reference en 2 invocaciones -> ingest_id distinto,
# sin colisión de carpeta ni de process_id.
# ============================================================================
print()
print("=" * 78)
print("G3 -- mismo client_reference en 2 invocaciones -> sin colisión")
print("=" * 78)

DIR_TEMP_G3 = Path(tempfile.mkdtemp(prefix="test_zip_identidad_g3_"))
os.chdir(DIR_TEMP_G3)
try:
    orch = FakeOrchestrator()
    ns = construir_namespace(orch)
    registrador = RegistradorLlamadasFS()

    zip_bytes = construir_zip_bytes([("factura.pdf", PDF_VALIDO)])
    zip_path_1 = DIR_TEMP_G3 / "z1.zip"
    zip_path_2 = DIR_TEMP_G3 / "z2.zip"
    zip_path_1.write_bytes(zip_bytes)
    zip_path_2.write_bytes(zip_bytes)

    MISMO_CLIENT_REF = "lote-agosto"

    with espiar_fs_real(registrador):
        v1 = ns["_validar_zip_rapido"](str(zip_path_1))
        v2 = ns["_validar_zip_rapido"](str(zip_path_2))

        r1 = asyncio.run(
            ns["_extraer_zip_y_despachar_individualmente"](
                zip_path=str(zip_path_1), ingest_id=uuid.uuid4().hex, origen="test",
                reservado_trabajo_en_vuelo=v1.get("reservado", 1), client_reference=MISMO_CLIENT_REF,
            )
        )
        r2 = asyncio.run(
            ns["_extraer_zip_y_despachar_individualmente"](
                zip_path=str(zip_path_2), ingest_id=uuid.uuid4().hex, origen="test",
                reservado_trabajo_en_vuelo=v2.get("reservado", 1), client_reference=MISMO_CLIENT_REF,
            )
        )

    check("G3: ambas invocaciones aceptaron 1 factura cada una", r1["aceptados"] == 1 and r2["aceptados"] == 1)
    check("G3: ingest_id de ambas invocaciones es distinto", r1["ingest_id"] != r2["ingest_id"])
    pids = [u["process_id"] for u in orch.upserts]
    check("G3: los process_id despachados en ambas invocaciones son TODOS distintos", len(pids) == len(set(pids)))
    check(
        "G3: ninguna carpeta de makedirs se repitió entre ambas invocaciones",
        len(registrador.makedirs) == len(set(registrador.makedirs)),
    )
finally:
    os.chdir(cwd_original)
    shutil.rmtree(DIR_TEMP_G3, ignore_errors=True)


# ============================================================================
# G4 -- 2 invocaciones REALMENTE concurrentes (asyncio.gather), mismo
# client_reference Y mismos nombres de archivo internos -> sin colisión.
# ============================================================================
print()
print("=" * 78)
print("G4 -- 2 invocaciones concurrentes (asyncio.gather), mismo client_reference")
print("=" * 78)

DIR_TEMP_G4 = Path(tempfile.mkdtemp(prefix="test_zip_identidad_g4_"))
os.chdir(DIR_TEMP_G4)
try:
    orch = FakeOrchestrator()
    ns = construir_namespace(orch)
    registrador = RegistradorLlamadasFS()

    zip_bytes = construir_zip_bytes([("factura.pdf", PDF_VALIDO), ("factura2.pdf", PDF_VALIDO)])
    zip_path_a = DIR_TEMP_G4 / "a.zip"
    zip_path_b = DIR_TEMP_G4 / "b.zip"
    zip_path_a.write_bytes(zip_bytes)
    zip_path_b.write_bytes(zip_bytes)

    with espiar_fs_real(registrador):
        va = ns["_validar_zip_rapido"](str(zip_path_a))
        vb = ns["_validar_zip_rapido"](str(zip_path_b))

        async def _correr_concurrente():
            return await asyncio.gather(
                ns["_extraer_zip_y_despachar_individualmente"](
                    zip_path=str(zip_path_a), ingest_id=uuid.uuid4().hex, origen="test",
                    reservado_trabajo_en_vuelo=va.get("reservado", 2), client_reference="mismo-id-cliente",
                ),
                ns["_extraer_zip_y_despachar_individualmente"](
                    zip_path=str(zip_path_b), ingest_id=uuid.uuid4().hex, origen="test",
                    reservado_trabajo_en_vuelo=vb.get("reservado", 2), client_reference="mismo-id-cliente",
                ),
            )

        ra, rb = asyncio.run(_correr_concurrente())

    check("G4: ambas corridas concurrentes aceptaron sus 2 facturas", ra["aceptados"] == 2 and rb["aceptados"] == 2)
    pids = [u["process_id"] for u in orch.upserts]
    check(
        f"G4: los {len(pids)} process_id de ambas corridas concurrentes son TODOS distintos (sin colisión real)",
        len(pids) == len(set(pids)),
    )
finally:
    os.chdir(cwd_original)
    shutil.rmtree(DIR_TEMP_G4, ignore_errors=True)


# ============================================================================
# G5 -- thread-safety real del contador de trabajo-en-vuelo bajo carga
# concurrente con threading.Thread real (no solo coroutines).
# ============================================================================
print()
print("=" * 78)
print("G5 -- _reservar/_liberar_trabajo_en_vuelo_zip son thread-safe de verdad")
print("=" * 78)

orch_g5 = FakeOrchestrator()
ns_g5 = construir_namespace(orch_g5, max_trabajo_en_vuelo=100_000)

N_HILOS = 20
RESERVAS_POR_HILO = 200
exitos = []
exitos_lock = threading.Lock()


def _trabajador():
    reservados_localmente = 0
    for _ in range(RESERVAS_POR_HILO):
        if ns_g5["_reservar_trabajo_en_vuelo_zip"](1):
            reservados_localmente += 1
            time.sleep(0)  # fuerza más oportunidades de interleaving real
    with exitos_lock:
        exitos.append(reservados_localmente)
    for _ in range(reservados_localmente):
        ns_g5["_liberar_trabajo_en_vuelo_zip"](1)


hilos = [threading.Thread(target=_trabajador) for _ in range(N_HILOS)]
for h in hilos:
    h.start()
for h in hilos:
    h.join()

valor_final = ns_g5.get("_trabajo_en_vuelo_zip")
check(
    f"G5: {N_HILOS} hilos x {RESERVAS_POR_HILO} reservas/liberaciones -> contador final == 0 (fue {valor_final})",
    valor_final == 0,
)
check(f"G5: los {N_HILOS} hilos reservaron ALGO (no todos en 0 por starvation)", sum(exitos) > 0)


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
