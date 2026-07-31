"""
Importación masiva de facturas desde una carpeta local -- descubrimiento
recursivo (incluidos zips anidados), dedup por contenido, y subida al
backend. Ver docs/plan-importacion-masiva-facturas.md para el diseño
completo; este script implementa la Fase 2 (script local) de ese plan.

Corre en la máquina del usuario, nunca en el droplet -- el backend no tiene
acceso a esta carpeta. Solo hace descubrir → extraer → hashear → subir por
HTTP; todo el trabajo pesado (Gemini, BAS, Sheets, Drive) sigue centralizado
en el servidor, vía los mismos endpoints que ya usa /website-upload.

Protocolo en dos fases, para no gastar ancho de banda subiendo algo que se
va a descartar:
  Fase A -- POST /admin/batch-import/start con el manifiesto completo (solo
            metadatos + hash, SIN bytes). El servidor dedupea (cross-batch
            contra facturas ya existentes, e intra-batch contra el propio
            manifiesto) y devuelve solo los item_id que necesitan subida real.
  Fase B -- POST /admin/batch-import/item/{item_id}/upload, un archivo real
            por request, solo para los "ganadores" de la Fase A.

Uso (desde la raíz de Invoicy, con el venv):
    venv/bin/python scripts/batch_import.py --dry-run
        # solo descubre/extrae/hashea y muestra el manifiesto -- NO llama
        # al backend ni sube nada. Correr SIEMPRE primero.

    venv/bin/python scripts/batch_import.py
        # corre de verdad: crea el batch, sube los archivos únicos, y el
        # backend arranca a procesarlos (Gemini + BAS reales).

    venv/bin/python scripts/batch_import.py --carpeta /otra/ruta --label "lote julio"

Requiere INVOICY_API_BASE_URL y SECRET_KEY en Invoicy/.env (el mismo secret
que ya usan los demás endpoints "admin").
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("batch_import")

EXTENSIONES_PERMITIDAS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
# Basura que macOS/otros SO agregan a un zip -- no es un error, se descarta
# en silencio (no cuenta como "no soportado" en el resumen).
IGNORAR_NOMBRES = {".DS_Store"}
IGNORAR_PREFIJOS = ("__MACOSX/", "._")

MAX_ZIP_DEPTH = 5
MAX_TOTAL_FILES = 500
MAX_DESCOMPRIMIDO_BYTES = 500 * 1024 * 1024  # 500MB
MAX_RATIO_COMPRESION = 100  # zip-bomb: file_size/compress_size sospechoso


class DiscoveredFile:
    def __init__(self, absolute_path: Path, original_path: str, zip_source: str | None):
        self.absolute_path = absolute_path
        self.original_path = original_path  # ej. "facturas.zip/facturas/MercadoPago_4.pdf"
        self.zip_source = zip_source

    def __repr__(self):
        return f"DiscoveredFile({self.original_path})"


def _es_extraccion_segura(destino_base: Path, destino_entrada: Path) -> bool:
    """Zip-slip: una entrada con '../' en el nombre podría escribir fuera del
    directorio de extracción. Rechazar cualquier entrada cuyo path resuelto
    no quede DENTRO de destino_base."""
    try:
        destino_entrada.resolve().relative_to(destino_base.resolve())
        return True
    except ValueError:
        return False


def _extraer_zip_seguro(zip_path: Path, destino: Path) -> None:
    """Valida tamaño/ratio de compresión (anti zip-bomb) y paths (anti
    zip-slip) ANTES de extraer nada -- si algo no pasa, se rechaza el zip
    ENTERO con un motivo explícito, no se extrae parcialmente."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        total_descomprimido = 0
        for zi in zf.infolist():
            if zi.is_dir():
                continue
            total_descomprimido += zi.file_size
            if zi.compress_size > 0 and zi.file_size / zi.compress_size > MAX_RATIO_COMPRESION:
                raise ValueError(
                    f"'{zi.filename}' tiene un ratio de compresión sospechoso "
                    f"({zi.file_size}/{zi.compress_size}) -- posible zip-bomb."
                )
            destino_entrada = destino / zi.filename
            if not _es_extraccion_segura(destino, destino_entrada):
                raise ValueError(
                    f"'{zi.filename}' intenta escribir fuera del directorio de "
                    "extracción (zip-slip) -- zip rechazado."
                )
        if total_descomprimido > MAX_DESCOMPRIMIDO_BYTES:
            raise ValueError(
                f"El zip descomprime a {total_descomprimido / 1024 / 1024:.0f}MB, "
                f"por encima del tope de {MAX_DESCOMPRIMIDO_BYTES / 1024 / 1024:.0f}MB."
            )
        zf.extractall(destino)


def descubrir_archivos(carpeta_raiz: Path) -> tuple[list[DiscoveredFile], dict]:
    """Recorre carpeta_raiz recursivamente, extrayendo zips anidados
    (zip-dentro-de-zip) hasta MAX_ZIP_DEPTH. Devuelve (archivos_validos,
    resumen_de_lo_omitido) -- lo omitido NUNCA frena el descubrimiento del
    resto, solo se registra."""
    resumen = {
        "skipped_unsupported": [],
        "skipped_zip_depth_exceeded": [],
        "skipped_zip_error": [],
        "skipped_file_cap_exceeded": [],
    }
    encontrados: list[DiscoveredFile] = []
    temp_dirs_a_limpiar: list[str] = []

    # Cola de trabajo (path, original_path_prefix, zip_source, depth) --
    # iterativa a propósito, no recursión por call stack, para no depender
    # de qué tan hondo esté realmente anidado un zip.
    cola = [(carpeta_raiz, "", None, 0)]

    while cola:
        carpeta_actual, prefijo, zip_source, depth = cola.pop(0)
        try:
            entradas = sorted(carpeta_actual.iterdir(), key=lambda p: p.name)
        except (FileNotFoundError, PermissionError) as e:
            log.warning(f"No se pudo leer {carpeta_actual}: {e}")
            continue

        for entrada in entradas:
            if entrada.name in IGNORAR_NOMBRES or any(
                str(entrada).startswith(p) for p in IGNORAR_PREFIJOS
            ):
                continue

            if entrada.is_dir():
                cola.append((entrada, f"{prefijo}{entrada.name}/", zip_source, depth))
                continue

            original_path = f"{prefijo}{entrada.name}"

            if entrada.suffix.lower() == ".zip":
                if depth >= MAX_ZIP_DEPTH:
                    resumen["skipped_zip_depth_exceeded"].append(original_path)
                    continue
                if len(encontrados) >= MAX_TOTAL_FILES:
                    resumen["skipped_file_cap_exceeded"].append(original_path)
                    continue
                temp_dir = tempfile.mkdtemp(prefix="invoicy-batch-")
                temp_dirs_a_limpiar.append(temp_dir)
                try:
                    _extraer_zip_seguro(entrada, Path(temp_dir))
                except (zipfile.BadZipFile, ValueError) as e:
                    resumen["skipped_zip_error"].append(f"{original_path}: {e}")
                    continue
                # El prefijo de los archivos extraídos arranca con el
                # original_path del ZIP mismo (no vacío) -- si no, dos
                # zips distintos con la misma estructura interna (ej.
                # ambos con "facturas/algo.pdf" adentro) generarían el
                # mismo original_path y se pisarían entre sí, tanto acá
                # como en el lookup por diccionario de más abajo.
                nuevo_zip_source = original_path
                cola.append(
                    (Path(temp_dir), f"{original_path}/", nuevo_zip_source, depth + 1)
                )
                continue

            if len(encontrados) >= MAX_TOTAL_FILES:
                resumen["skipped_file_cap_exceeded"].append(original_path)
                continue

            if entrada.suffix.lower() not in EXTENSIONES_PERMITIDAS:
                resumen["skipped_unsupported"].append(original_path)
                continue

            encontrados.append(
                DiscoveredFile(
                    absolute_path=entrada,
                    original_path=original_path,
                    zip_source=zip_source,
                )
            )

    return encontrados, resumen, temp_dirs_a_limpiar


def _sha256_de(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def armar_manifiesto(archivos: list[DiscoveredFile]) -> list[dict]:
    manifiesto = []
    for a in archivos:
        manifiesto.append(
            {
                "original_path": a.original_path,
                "file_name": a.absolute_path.name,
                "content_hash": _sha256_de(a.absolute_path),
                "size": a.absolute_path.stat().st_size,
                "zip_source": a.zip_source,
            }
        )
    return manifiesto


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--carpeta",
        default=str(Path(__file__).resolve().parent.parent / "facturas"),
        help="Carpeta a importar (default: Invoicy/facturas)",
    )
    parser.add_argument("--label", default=None, help="Etiqueta legible para el batch")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo descubre/hashea y muestra el manifiesto -- NO llama al backend.",
    )
    parser.add_argument(
        "--monto-override",
        type=float,
        default=None,
        help=(
            "Para importar facturas REALES sin impacto contable real: cada "
            "factura se registra en BAS (y se persiste en Sheets/PocketBase) "
            "con este monto en vez del real extraído del documento. Ej.: "
            "--monto-override 1"
        ),
    )
    args = parser.parse_args()

    load_dotenv()
    base_url = os.getenv("INVOICY_API_BASE_URL", "http://localhost:8000").rstrip("/")
    secret = os.getenv("SECRET_KEY")
    if not args.dry_run and not secret:
        log.error("❌ Falta SECRET_KEY en Invoicy/.env -- no se puede autenticar contra el backend.")
        sys.exit(1)

    carpeta_raiz = Path(args.carpeta).resolve()
    if not carpeta_raiz.is_dir():
        log.error(f"❌ La carpeta no existe: {carpeta_raiz}")
        sys.exit(1)

    log.info("=" * 78)
    log.info(f"PASO 1 — Descubrimiento recursivo de {carpeta_raiz}")
    log.info("=" * 78)
    archivos, resumen, temp_dirs = descubrir_archivos(carpeta_raiz)
    try:
        log.info(f"Archivos válidos encontrados: {len(archivos)}")
        for clave, valores in resumen.items():
            if valores:
                log.info(f"  {clave}: {len(valores)} -> {valores[:5]}{' …' if len(valores) > 5 else ''}")

        if not archivos:
            log.warning("No se encontró ningún archivo procesable. Nada que hacer.")
            return

        log.info("=" * 78)
        log.info("PASO 2 — Armando manifiesto (hash sha256 de cada archivo)")
        log.info("=" * 78)
        manifiesto = armar_manifiesto(archivos)
        for m in manifiesto:
            origen = f" (de {m['zip_source']})" if m["zip_source"] else ""
            log.info(f"  {m['original_path']}{origen} -- {m['content_hash'][:12]}… ({m['size']} bytes)")

        if args.dry_run:
            log.info("=" * 78)
            log.info(f"dry_run=True -> no se llamó al backend. {len(manifiesto)} archivos en el manifiesto.")
            log.info("Corré sin --dry-run para crear el batch y subir los archivos únicos de verdad.")
            log.info("=" * 78)
            manifest_path = Path(tempfile.gettempdir()) / "invoicy_batch_manifest_dryrun.json"
            manifest_path.write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False))
            log.info(f"Manifiesto completo guardado en: {manifest_path}")
            return

        log.info("=" * 78)
        log.info("PASO 3 — POST /admin/batch-import/start (Fase A: solo metadatos, sin bytes)")
        log.info("=" * 78)
        if args.monto_override is not None:
            log.warning(
                f"⚠️  monto_override activo: Total={args.monto_override} en TODAS las "
                "facturas de este batch, en vez del monto real extraído de cada documento."
            )
        headers = {"X-Invoicy-Secret": secret}
        resp = requests.post(
            f"{base_url}/admin/batch-import/start",
            json={
                "label": args.label,
                "manifest": manifiesto,
                "monto_override": args.monto_override,
            },
            headers=headers,
            timeout=60,
        )
        if resp.status_code >= 400:
            log.error(f"❌ {resp.status_code}: {resp.text[:500]}")
            sys.exit(1)
        data = resp.json()
        batch_id = data["batch_id"]
        items_a_subir = data["items_to_upload"]
        log.info(
            f"✅ Batch creado: {batch_id} -- {data['total_files']} encontrados, "
            f"{data['total_unique']} únicos, {data['total_duplicates']} duplicados omitidos"
        )

        log.info("=" * 78)
        log.info(f"PASO 4 — Subiendo {len(items_a_subir)} archivos únicos (Fase B: bytes reales)")
        log.info("=" * 78)
        por_path = {m["original_path"]: a for m, a in zip(manifiesto, archivos)}
        ok, fallidos = 0, 0
        for item in items_a_subir:
            archivo = por_path[item["original_path"]]
            try:
                with open(archivo.absolute_path, "rb") as f:
                    r = requests.post(
                        f"{base_url}/admin/batch-import/item/{item['item_id']}/upload",
                        files={"file": (archivo.absolute_path.name, f)},
                        headers=headers,
                        timeout=60,
                    )
                if r.status_code >= 400:
                    log.error(f"  ❌ {item['original_path']}: {r.status_code} {r.text[:200]}")
                    fallidos += 1
                else:
                    log.info(f"  ✅ {item['original_path']} subido")
                    ok += 1
            except requests.exceptions.RequestException as e:
                log.error(f"  ❌ {item['original_path']}: {e}")
                fallidos += 1

        log.info("=" * 78)
        log.info(
            f"🎉 Subida terminada: {ok} ok, {fallidos} fallidos. El backend sigue "
            f"procesando en background -- seguí el progreso en el dashboard, "
            f"/lotes/{batch_id}."
        )
        log.info("=" * 78)
    finally:
        for td in temp_dirs:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    main()
