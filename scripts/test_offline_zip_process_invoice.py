"""
Prueba offline de la rama ZIP del endpoint POST /gemini2/process-invoice
(routes/process_invoice_google_2.py, función `process_invoice`, protegida
con secret_key) DESPUÉS del rediseño de seguridad/arquitectura documentado
en docs/plan-fase1-zip-REDISEÑO.md -- LEER ese archivo para el porqué de
cada decisión; este test solo verifica el comportamiento real observable.

Por qué importa este archivo puntualmente: `process_invoice` es el ÚNICO
caller, de los tres que ingresan facturas (email, formulario web, y este
endpoint directo), que despacha `_extraer_zip_y_despachar_individualmente`
con `notificar_no_soportados_por_webhook=True` explícito. Antes de la
migración a manejo de ZIP, este endpoint tenía además un bug real de
"kind.media_type" (AttributeError) en la rama de archivo suelto que rompía
el request entero con un 500 -- este test cubre esa rama (D4/D6) además de
la rama ZIP para evitar que un cambio futuro vuelva a romper cualquiera de
los dos caminos.

Qué cambió respecto a la versión anterior de este archivo (que ya NO es
válida: usaba un parámetro `base_process_id` que no existe más en la firma
real): `_extraer_zip_y_despachar_individualmente` ahora recibe `ingest_id`
como parámetro OBLIGATORIO generado SIEMPRE por el caller (server-side, con
uuid.uuid4().hex) -- el `id` que manda el cliente de este endpoint ahora se
llama `client_reference` y es puramente informativo, NUNCA se usa para
construir ninguna ruta de disco ni ninguna clave de PocketBase. También
recibe `reservado_trabajo_en_vuelo` (obligatorio, viene de lo que reservó
`_validar_zip_rapido`). Este archivo verifica que `process_invoice` arma
esa llamada correctamente con la firma nueva -- NO reprueba la garantía de
identidad/filesystem en sí (path traversal, colisiones, concurrencia,
thread-safety del contador), que ya está cubierta exhaustivamente y a nivel
de la función compartida por scripts/test_offline_zip_identidad_seguridad.py.
Acá solo hay una verificación liviana adicional (D7) de que este endpoint
puntual no reintroduce el `id` crudo del cliente en ningún lado ANTES de
llegar a esa función compartida.

Escenarios cubiertos:
  D1. secret_key correcta + ZIP real válido (1 pdf soportado + 1 .txt no
      soportado) -> se despacha `_extraer_zip_y_despachar_individualmente`
      con: origen="process-invoice", un ingest_id nuevo generado por el
      endpoint (uuid, DISTINTO del "id" que mandó el caller),
      reservado_trabajo_en_vuelo == cantidad real de miembros del ZIP,
      client_reference == el "id" que mandó el caller (tal cual, sin
      modificar), y notificar_no_soportados_por_webhook=True EXPLÍCITO --
      el chequeo más importante del archivo (SOLO este endpoint prende ese
      flag). enviar_resumen_por_email NO debe quedar en True acá (ese es
      solo para el canal de email, ver webhook_endpoint).
  D2. secret_key incorrecta -> HTTPException 401, ninguna fake fue llamada
      (regresión: la validación de secret_key no debe quedar rota por el
      rediseño de ZIP).
  D3. ZIP que `_validar_zip_rapido` rechaza (vacío, 0 miembros) ->
      HTTPException 400 con el mensaje real devuelto por `_validar_zip_rapido`
      (se compara contra el resultado de invocar la función real
      directamente, nunca contra un texto inventado), y la fake de
      despacho nunca se llama.
  D4. Archivo suelto (pdf válido, no ZIP, regresión) -> sigue el camino
      viejo de `_procesar_en_background` (fake) con process_id == el "id"
      tal cual sin modificar (este camino no-zip no forma parte del
      rediseño de identidad -- nunca tuvo el problema de path traversal
      porque nunca construyó una ruta a partir de "id"), la fake de ZIP
      nunca se llama.
  D5. Extensión no permitida (.docx) -> HTTPException 400, igual que antes
      del rediseño (regresión).
  D6. Extensión .pdf permitida pero contenido real irreconocible (texto
      plano) -> HTTPException 400 "Tipo de archivo no permitido." (NO un
      500 por AttributeError). Regresión de un bug real encontrado en esta
      misma implementación: filetype.guess() devuelve None para contenido
      irreconocible, y el código accedía a kind.mime sin chequear None
      primero.
  D7. Un "id" del caller que contiene un payload de path traversal (ej.
      "../../etc", "/etc/passwd") -> el ZIP se procesa correctamente (no
      explota), y ese valor NUNCA aparece en ninguna llamada real a
      os.makedirs/shutil.move hecha por el propio endpoint ANTES de llegar
      a la función compartida (interceptando esas 2 funciones REALES con
      el mismo patrón de monkeypatch que usa
      scripts/test_offline_zip_identidad_seguridad.py), y el ingest_id que
      termina llegando a la fake de despacho es un uuid real, nunca el
      "id" crudo. La cobertura exhaustiva de path traversal/colisiones ya
      está en test_offline_zip_identidad_seguridad.py a nivel de la
      función compartida -- esto es solo la confirmación de que este
      endpoint puntual no reintroduce el problema antes de llegar ahí.

No se reimplementa la lógica de `process_invoice`, `_validar_zip_rapido`,
`_carpeta_ingest_zip` ni de `_reservar_trabajo_en_vuelo_zip` /
`_liberar_trabajo_en_vuelo_zip` en este test: las cinco se extraen vía AST
del código fuente real del repo y se ejecutan vía exec() en un namespace
con dobles de prueba SOLO para las dos funciones de background pesadas
(`_procesar_en_background` y `_extraer_zip_y_despachar_individualmente`,
que se prueban a fondo en scripts/test_offline_zip_identidad_seguridad.py).
Todo lo demás (zipfile, filetype, os, shutil, threading) corre de verdad,
contra archivos ZIP reales creados en un directorio temporal.

Uso: python3 scripts/test_offline_zip_process_invoice.py
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
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import File, Form, HTTPException, UploadFile  # noqa: E402
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


def _extraer_asignacion_top_level(nombre):
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
    return ast.get_source_segment(codigo_fuente, nodo) if nodo else None


FUENTES_FUNCIONES = {}
NOMBRES_FUNCIONES_A_EXTRAER = [
    "process_invoice",
    "_validar_zip_rapido",
    "_carpeta_ingest_zip",
    "_reservar_trabajo_en_vuelo_zip",
    "_liberar_trabajo_en_vuelo_zip",
    "_truncar_nombre_a_bytes",
]
for nombre in NOMBRES_FUNCIONES_A_EXTRAER:
    FUENTES_FUNCIONES[nombre] = _extraer_funcion_top_level(nombre)
    check(f"Se extrajo '{nombre}' del código fuente real", bool(FUENTES_FUNCIONES[nombre]))

FUENTES_CONSTANTES = {}
NOMBRES_CONSTANTES_A_EXTRAER = [
    "MAX_ARCHIVOS_ZIP",
    "MAX_ZIP_DESCOMPRIMIDO_BYTES",
    "MAX_TRABAJO_EN_VUELO_ZIP",
]
for nombre in NOMBRES_CONSTANTES_A_EXTRAER:
    FUENTES_CONSTANTES[nombre] = _extraer_asignacion_top_level(nombre)
    check(f"Se extrajo la constante '{nombre}' del código fuente real", bool(FUENTES_CONSTANTES[nombre]))

if FALLOS:
    print("\nNo se pudo extraer el código real -- abortando.")
    sys.exit(1)


# ============================================================================
# Dobles de prueba (fakes) -- SOLO para las dos funciones de background
# pesadas, ya probadas a fondo en scripts/test_offline_zip_identidad_seguridad.py.
# Todo lo demás (zipfile, filetype, os, shutil, threading, HTTPException)
# corre de verdad, contra archivos ZIP reales creados en un directorio
# temporal.
# ============================================================================
os.environ["SECRET_KEY"] = "test-secret"


class _FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


class FakeUploadFile:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.file = io.BytesIO(content)


llamadas_extraer_zip = []
llamadas_procesar_en_background = []


async def _coro_vacia():
    return None


def fake_extraer_zip_y_despachar_individualmente(
    *,
    zip_path,
    ingest_id,
    origen,
    reservado_trabajo_en_vuelo,
    client_reference=None,
    notificar_no_soportados_por_webhook=False,
    enviar_resumen_por_email=False,
    datos_email=None,
    on_item_completado=None,
):
    """Doble de prueba con la firma NUEVA real (post-rediseño) --
    `ingest_id` y `reservado_trabajo_en_vuelo` son obligatorios porque
    `process_invoice` real SIEMPRE los pasa; el resto conserva sus
    defaults reales para poder distinguir un valor explícito de uno que
    quedó en su default (clave para el chequeo D1 de
    notificar_no_soportados_por_webhook / enviar_resumen_por_email)."""
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
    return _coro_vacia()


def fake_procesar_en_background(*, file_location, file_name, extension, media_type, process_id):
    llamadas_procesar_en_background.append(
        {
            "file_location": file_location,
            "file_name": file_name,
            "extension": extension,
            "media_type": media_type,
            "process_id": process_id,
        }
    )
    return _coro_vacia()


def construir_namespace():
    """Namespace fresco por escenario -- `_trabajo_en_vuelo_zip` arranca en
    0 cada vez para que el contador de trabajo-en-vuelo (usado por
    `_validar_zip_rapido` REAL a través de `_reservar_trabajo_en_vuelo_zip`
    REAL) no arrastre estado de un escenario al siguiente. os/shutil son
    los módulos REALES (necesario para D7, que los espía con un
    monkeypatch temporal sobre los módulos reales)."""
    namespace = {
        "os": os,
        "re": re,
        "shutil": shutil,
        "asyncio": asyncio,
        "zipfile": zipfile,
        "zlib": zlib,
        "filetype": filetype,
        "uuid": uuid,
        "threading": threading,
        "Optional": Optional,
        "Callable": Callable,
        "HTTPException": HTTPException,
        "Form": Form,
        "File": File,
        "UploadFile": UploadFile,
        "app_logger": _FakeLogger(),
        "_procesar_en_background": fake_procesar_en_background,
        "_extraer_zip_y_despachar_individualmente": fake_extraer_zip_y_despachar_individualmente,
        "_trabajo_en_vuelo_zip": 0,
        "_trabajo_en_vuelo_zip_lock": threading.Lock(),
        "ZIP_EXTRACTION_SEMAPHORE": asyncio.Semaphore(2),
    }
    # Orden de dependencia: constantes primero, después las funciones que
    # las usan (_reservar/_liberar_trabajo_en_vuelo_zip dependen de
    # MAX_TRABAJO_EN_VUELO_ZIP; _validar_zip_rapido depende de
    # MAX_ARCHIVOS_ZIP/MAX_ZIP_DESCOMPRIMIDO_BYTES/_reservar_trabajo_en_vuelo_zip;
    # process_invoice depende de todo lo anterior).
    for nombre in NOMBRES_CONSTANTES_A_EXTRAER:
        exec(compile(textwrap.dedent(FUENTES_CONSTANTES[nombre]), f"<{nombre} real>", "exec"), namespace)
    for nombre in [
        "_reservar_trabajo_en_vuelo_zip",
        "_liberar_trabajo_en_vuelo_zip",
        "_carpeta_ingest_zip",
        "_validar_zip_rapido",
        "_truncar_nombre_a_bytes",
        "process_invoice",
    ]:
        exec(compile(textwrap.dedent(FUENTES_FUNCIONES[nombre]), f"<{nombre} real>", "exec"), namespace)
    return namespace


ns_verificacion = construir_namespace()
check(
    "MAX_ARCHIVOS_ZIP real de producción == 100",
    ns_verificacion.get("MAX_ARCHIVOS_ZIP") == 100,
)
check(
    "MAX_ZIP_DESCOMPRIMIDO_BYTES real de producción == 500MB (500 * 1024 * 1024)",
    ns_verificacion.get("MAX_ZIP_DESCOMPRIMIDO_BYTES") == 500 * 1024 * 1024,
)
check(
    "MAX_TRABAJO_EN_VUELO_ZIP real de producción == 200",
    ns_verificacion.get("MAX_TRABAJO_EN_VUELO_ZIP") == 200,
)
check(
    "process_invoice (código real) se pudo compilar y ejecutar vía exec",
    callable(ns_verificacion.get("process_invoice")),
)
check(
    "_validar_zip_rapido (código real) se pudo compilar y ejecutar vía exec",
    callable(ns_verificacion.get("_validar_zip_rapido")),
)

if FALLOS:
    print("\nNo se pudo compilar el código real -- abortando.")
    sys.exit(1)


async def _ejecutar_con_captura(coro):
    """Corre la corutina real y deja avanzar el loop una vez más para que
    cualquier tarea en background (las fakes, disparadas vía
    asyncio.create_task) alcance a correr, sin depender de eso para
    capturar la llamada -- las fakes ya registran la llamada de forma
    síncrona en el momento en que se las invoca para construir la
    corutina que se le pasa a asyncio.create_task."""
    try:
        resultado = await coro
        await asyncio.sleep(0)
        return ("ok", resultado)
    except Exception as e:  # noqa: BLE001
        return ("error", e)


class RegistradorLlamadasFS:
    def __init__(self):
        self.makedirs = []
        self.move = []


@contextmanager
def espiar_fs_real(registrador):
    """Monkeypatch TEMPORAL de os.makedirs/shutil.move -- envuelve las
    funciones REALES (siguen escribiendo a disco de verdad, dentro del
    tempdir aislado del test), solo agrega el registro de argumentos. Se
    restaura siempre, incluso si el bloque `with` lanza. Mismo patrón que
    usa scripts/test_offline_zip_identidad_seguridad.py."""
    makedirs_original = os.makedirs
    move_original = shutil.move

    def makedirs_espiado(path, *a, **k):
        registrador.makedirs.append(path)
        return makedirs_original(path, *a, **k)

    def move_espiado(src, dst, *a, **k):
        registrador.move.append(dst)
        return move_original(src, dst, *a, **k)

    os.makedirs = makedirs_espiado
    shutil.move = move_espiado
    try:
        yield registrador
    finally:
        os.makedirs = makedirs_original
        shutil.move = move_original


# ============================================================================
# Fixtures de bytes ya verificados en este repo.
# ============================================================================
PDF_VALIDO = b"%PDF-1.4\n" + b"X" * 50 + b"\n%%EOF\n"
TEXTO_NO_SOPORTADO = b"hola esto es un texto plano"


def crear_zip_bytes(miembros: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for nombre, contenido in miembros.items():
            zf.writestr(nombre, contenido)
    return buf.getvalue()


def crear_zip_vacio_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    return buf.getvalue()


# ============================================================================
# Directorio de trabajo aislado -- process_invoice escribe rutas relativas
# ("./downloads/..."), así que corremos todo dentro de un tempdir para no
# ensuciar el repo real.
# ============================================================================
directorio_original = os.getcwd()
directorio_trabajo = tempfile.mkdtemp(prefix="test_offline_zip_process_invoice_")
os.chdir(directorio_trabajo)

try:
    # ========================================================================
    print("=" * 78)
    print("D1 -- secret_key correcta + ZIP real válido (pdf + txt no soportado)")
    print("=" * 78)
    # ========================================================================
    llamadas_extraer_zip.clear()
    llamadas_procesar_en_background.clear()
    ns = construir_namespace()
    process_invoice = ns["process_invoice"]

    MIEMBROS_ZIP_D1 = {"factura.pdf": PDF_VALIDO, "nota.txt": TEXTO_NO_SOPORTADO}
    contenido_zip_d1 = crear_zip_bytes(MIEMBROS_ZIP_D1)

    ID_CLIENTE_D1 = "id-d1-del-cliente"
    archivo_d1 = FakeUploadFile("factura_d1.zip", contenido_zip_d1)
    tipo, valor = asyncio.run(
        _ejecutar_con_captura(process_invoice(id=ID_CLIENTE_D1, secret_key="test-secret", file=archivo_d1))
    )

    check("D1: process_invoice no lanzó excepción", tipo == "ok")
    if tipo == "ok":
        check("D1: la respuesta indica éxito (success=True)", valor.get("success") is True)
        check("D1: status_code de la respuesta es 201", valor.get("status_code") == 201)

    check(
        "D1: _extraer_zip_y_despachar_individualmente (fake) fue llamada exactamente una vez",
        len(llamadas_extraer_zip) == 1,
    )
    check(
        "D1: _procesar_en_background (fake) NUNCA fue llamada (no es el camino de archivo suelto)",
        len(llamadas_procesar_en_background) == 0,
    )

    if llamadas_extraer_zip:
        llamada_d1 = llamadas_extraer_zip[0]
        check("D1: origen == 'process-invoice'", llamada_d1.get("origen") == "process-invoice")
        check(
            "D1: notificar_no_soportados_por_webhook is True EXPLÍCITO "
            "(chequeo clave: SOLO este endpoint prende ese flag)",
            llamada_d1.get("notificar_no_soportados_por_webhook") is True,
        )
        check(
            "D1: enviar_resumen_por_email NO quedó en True (ese flag es exclusivo del canal de email)",
            llamada_d1.get("enviar_resumen_por_email") is not True,
        )
        check(
            "D1: reservado_trabajo_en_vuelo == cantidad real de miembros del ZIP "
            f"({len(MIEMBROS_ZIP_D1)})",
            llamada_d1.get("reservado_trabajo_en_vuelo") == len(MIEMBROS_ZIP_D1),
        )
        check(
            "D1: client_reference == el 'id' enviado por el caller, SIN modificar",
            llamada_d1.get("client_reference") == ID_CLIENTE_D1,
        )
        ingest_id_d1 = llamada_d1.get("ingest_id") or ""
        check(
            "D1: ingest_id es un uuid4.hex real (32 caracteres hexadecimales)",
            bool(re.fullmatch(r"[0-9a-f]{32}", ingest_id_d1)),
        )
        check(
            "D1: ingest_id es DISTINTO del 'id' que mandó el caller (identidad server-side, "
            "no derivada del cliente)",
            ingest_id_d1 != ID_CLIENTE_D1,
        )
        zip_path_d1 = llamada_d1.get("zip_path") or ""
        check("D1: zip_path apunta a un .zip", zip_path_d1.endswith(".zip"))
        check("D1: el zip_path despachado existe realmente en disco", Path(zip_path_d1).is_file())
        check(
            "D1: el zip_path despachado vive dentro de la carpeta del ingest_id real (no de 'id-d1-del-cliente')",
            ingest_id_d1 in zip_path_d1 and ID_CLIENTE_D1 not in zip_path_d1,
        )

    # ========================================================================
    print()
    print("=" * 78)
    print("D2 -- secret_key incorrecta")
    print("=" * 78)
    # ========================================================================
    llamadas_extraer_zip.clear()
    llamadas_procesar_en_background.clear()
    ns = construir_namespace()
    process_invoice = ns["process_invoice"]

    archivo_d2 = FakeUploadFile("factura_d2.pdf", PDF_VALIDO)
    tipo, valor = asyncio.run(
        _ejecutar_con_captura(process_invoice(id="id-d2", secret_key="clave-incorrecta", file=archivo_d2))
    )

    check("D2: process_invoice lanzó una excepción (secret_key incorrecta)", tipo == "error")
    if tipo == "error":
        check("D2: la excepción es HTTPException", isinstance(valor, HTTPException))
        if isinstance(valor, HTTPException):
            check("D2: status_code == 401", valor.status_code == 401)
    check("D2: ninguna fake fue llamada (_extraer_zip_y_despachar_individualmente)", len(llamadas_extraer_zip) == 0)
    check("D2: ninguna fake fue llamada (_procesar_en_background)", len(llamadas_procesar_en_background) == 0)

    # ========================================================================
    print()
    print("=" * 78)
    print("D3 -- ZIP que _validar_zip_rapido rechaza (vacío, 0 miembros)")
    print("=" * 78)
    # ========================================================================
    llamadas_extraer_zip.clear()
    llamadas_procesar_en_background.clear()
    ns = construir_namespace()
    process_invoice = ns["process_invoice"]
    _validar_zip_rapido_real = ns["_validar_zip_rapido"]

    contenido_zip_vacio = crear_zip_vacio_bytes()

    # Ground truth: qué dice la validación real para este ZIP vacío -- el
    # detail de la HTTPException se compara contra ESTO, nunca contra un
    # texto inventado a mano. Se corre sobre un archivo separado, en su
    # propio namespace (mismo módulo `os`/`shutil` reales, pero contador de
    # trabajo-en-vuelo propio) para no interferir con la reserva que hará
    # la llamada real de más abajo.
    os.makedirs("downloads", exist_ok=True)
    ruta_chequeo_d3 = Path("downloads") / "chequeo_validacion_d3.zip"
    ruta_chequeo_d3.write_bytes(contenido_zip_vacio)
    validacion_real_d3 = _validar_zip_rapido_real(str(ruta_chequeo_d3))
    ruta_chequeo_d3.unlink()

    check("D3: _validar_zip_rapido REAL rechaza el ZIP vacío (ok=False)", validacion_real_d3.get("ok") is False)
    check("D3: _validar_zip_rapido REAL trae un mensaje de error", bool(validacion_real_d3.get("error")))

    archivo_d3 = FakeUploadFile("vacio_d3.zip", contenido_zip_vacio)
    tipo, valor = asyncio.run(
        _ejecutar_con_captura(process_invoice(id="id-d3", secret_key="test-secret", file=archivo_d3))
    )

    check("D3: process_invoice lanzó una excepción (ZIP rechazado por _validar_zip_rapido)", tipo == "error")
    if tipo == "error":
        check("D3: la excepción es HTTPException", isinstance(valor, HTTPException))
        if isinstance(valor, HTTPException):
            check("D3: status_code == 400", valor.status_code == 400)
            check(
                "D3: el detail coincide EXACTO con el error real devuelto por _validar_zip_rapido",
                valor.detail == validacion_real_d3.get("error"),
            )
    check(
        "D3: _extraer_zip_y_despachar_individualmente (fake) NUNCA fue llamada (el ZIP fue rechazado antes)",
        len(llamadas_extraer_zip) == 0,
    )

    # ========================================================================
    print()
    print("=" * 78)
    print("D4 -- archivo suelto (PDF válido, no ZIP) -- regresión")
    print("=" * 78)
    # ========================================================================
    llamadas_extraer_zip.clear()
    llamadas_procesar_en_background.clear()
    ns = construir_namespace()
    process_invoice = ns["process_invoice"]

    archivo_d4 = FakeUploadFile("factura_d4.pdf", PDF_VALIDO)
    tipo, valor = asyncio.run(
        _ejecutar_con_captura(process_invoice(id="id-d4", secret_key="test-secret", file=archivo_d4))
    )

    check("D4: process_invoice no lanzó excepción", tipo == "ok")
    if tipo == "ok":
        check("D4: la respuesta indica éxito (success=True)", valor.get("success") is True)
        check("D4: status_code de la respuesta es 201", valor.get("status_code") == 201)

    check(
        "D4: _procesar_en_background (fake) fue llamada exactamente una vez (camino viejo de archivo suelto)",
        len(llamadas_procesar_en_background) == 1,
    )
    check(
        "D4: _extraer_zip_y_despachar_individualmente (fake) NUNCA fue llamada (no es un ZIP)",
        len(llamadas_extraer_zip) == 0,
    )

    if llamadas_procesar_en_background:
        llamada_d4 = llamadas_procesar_en_background[0]
        check(
            "D4: process_id == id enviado ('id-d4'), SIN modificar (este camino no-zip no forma "
            "parte del rediseño de identidad -- nunca construyó una ruta a partir de 'id')",
            llamada_d4.get("process_id") == "id-d4",
        )
        check("D4: file_name == filename original ('factura_d4.pdf')", llamada_d4.get("file_name") == "factura_d4.pdf")
        check("D4: extension == 'pdf'", llamada_d4.get("extension") == "pdf")
        check("D4: media_type == 'application/pdf'", llamada_d4.get("media_type") == "application/pdf")

    # ========================================================================
    print()
    print("=" * 78)
    print("D5 -- extensión no permitida (.docx)")
    print("=" * 78)
    # ========================================================================
    llamadas_extraer_zip.clear()
    llamadas_procesar_en_background.clear()
    ns = construir_namespace()
    process_invoice = ns["process_invoice"]

    archivo_d5 = FakeUploadFile("documento_d5.docx", b"contenido irrelevante para este chequeo")
    tipo, valor = asyncio.run(
        _ejecutar_con_captura(process_invoice(id="id-d5", secret_key="test-secret", file=archivo_d5))
    )

    check("D5: process_invoice lanzó una excepción (extensión no permitida)", tipo == "error")
    if tipo == "error":
        check("D5: la excepción es HTTPException", isinstance(valor, HTTPException))
        if isinstance(valor, HTTPException):
            check("D5: status_code == 400", valor.status_code == 400)
    check("D5: ninguna fake fue llamada (_extraer_zip_y_despachar_individualmente)", len(llamadas_extraer_zip) == 0)
    check("D5: ninguna fake fue llamada (_procesar_en_background)", len(llamadas_procesar_en_background) == 0)

    # ========================================================================
    print()
    print("=" * 78)
    print("D6 -- extensión .pdf permitida pero contenido real irreconocible (texto plano)")
    print("=" * 78)
    # Regresión de un bug real encontrado en esta misma implementación:
    # filetype.guess() devuelve None para contenido irreconocible, y el
    # código accedía a kind.mime sin chequear None primero -- eso disparaba
    # un AttributeError sin capturar específicamente, que el except genérico
    # convertía en 500 en vez del 400 claro que corresponde. El fix agrega un
    # chequeo explícito "if kind is None" antes de tocar kind.mime.
    # ========================================================================
    llamadas_extraer_zip.clear()
    llamadas_procesar_en_background.clear()
    ns = construir_namespace()
    process_invoice = ns["process_invoice"]

    archivo_d6 = FakeUploadFile("factura_d6.pdf", TEXTO_NO_SOPORTADO)
    tipo, valor = asyncio.run(
        _ejecutar_con_captura(process_invoice(id="id-d6", secret_key="test-secret", file=archivo_d6))
    )

    check("D6: process_invoice lanzó una excepción (contenido irreconocible)", tipo == "error")
    if tipo == "error":
        check("D6: la excepción es HTTPException (NO un 500 por AttributeError sin capturar)", isinstance(valor, HTTPException))
        if isinstance(valor, HTTPException):
            check("D6: status_code == 400 (no 500)", valor.status_code == 400)
            check("D6: detail == 'Tipo de archivo no permitido.'", valor.detail == "Tipo de archivo no permitido.")
    check("D6: ninguna fake fue llamada (_extraer_zip_y_despachar_individualmente)", len(llamadas_extraer_zip) == 0)
    check("D6: ninguna fake fue llamada (_procesar_en_background)", len(llamadas_procesar_en_background) == 0)

    # ========================================================================
    print()
    print("=" * 78)
    print("D7 -- 'id' del caller con payload de path traversal -- nunca llega a disco")
    print("=" * 78)
    # ========================================================================
    PAYLOADS_MALICIOSOS_D7 = ["../../etc", "/etc/passwd", "../../../../tmp/pwn"]

    for payload in PAYLOADS_MALICIOSOS_D7:
        llamadas_extraer_zip.clear()
        llamadas_procesar_en_background.clear()
        ns = construir_namespace()
        process_invoice = ns["process_invoice"]

        contenido_zip_d7 = crear_zip_bytes({"factura.pdf": PDF_VALIDO})
        archivo_d7 = FakeUploadFile("factura_d7.zip", contenido_zip_d7)
        registrador = RegistradorLlamadasFS()

        with espiar_fs_real(registrador):
            tipo, valor = asyncio.run(
                _ejecutar_con_captura(process_invoice(id=payload, secret_key="test-secret", file=archivo_d7))
            )

        check(f"D7 payload {payload!r}: process_invoice no lanzó excepción (el ZIP se procesa igual)", tipo == "ok")
        if tipo == "ok":
            check(f"D7 payload {payload!r}: la respuesta indica éxito (success=True)", valor.get("success") is True)

        rutas_reales = registrador.makedirs + registrador.move
        check(
            f"D7 payload {payload!r}: NINGUNA llamada real a os.makedirs/shutil.move (hechas por el "
            "propio endpoint, antes de llegar a la función compartida) contiene el payload",
            all(payload not in ruta for ruta in rutas_reales),
        )
        check(
            f"D7 payload {payload!r}: hubo al menos 1 llamada real a os.makedirs/shutil.move "
            "(el ZIP sí llegó a guardarse en disco)",
            len(rutas_reales) >= 1,
        )

        if llamadas_extraer_zip:
            llamada_d7 = llamadas_extraer_zip[0]
            ingest_id_d7 = llamada_d7.get("ingest_id") or ""
            check(
                f"D7 payload {payload!r}: el ingest_id pasado a la fake de despacho es un uuid4.hex "
                "real, nunca el 'id' crudo del cliente",
                bool(re.fullmatch(r"[0-9a-f]{32}", ingest_id_d7)),
            )
            check(
                f"D7 payload {payload!r}: client_reference conserva el payload TAL CUAL (es solo "
                "trazabilidad, nunca se usa para construir rutas)",
                llamada_d7.get("client_reference") == payload,
            )
            zip_path_d7 = llamada_d7.get("zip_path") or ""
            check(
                f"D7 payload {payload!r}: el payload NO aparece en el zip_path despachado",
                payload not in zip_path_d7,
            )
        else:
            check(f"D7 payload {payload!r}: se despachó la fake de extracción de ZIP", False)

finally:
    os.chdir(directorio_original)
    shutil.rmtree(directorio_trabajo, ignore_errors=True)

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
