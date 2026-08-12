"""
Importación masiva de facturas -- ver docs/plan-importacion-masiva-facturas.md.

Router separado (no crece más el ya sobrecargado process_invoice_google_2.py),
pero reusa TODO lo pesado de ahí: el orchestrator singleton, el mismo
PocketBaseClient, y _procesar_imagen_o_pdf (que ya trae su propio gate de
concurrencia vía PROCESSING_SEMAPHORE -- ver el comentario de esa variable).

Protegido con el mismo header X-Invoicy-Secret que ya usan los endpoints
"admin" existentes (retry-orden-pago, reintentar-extraccion, crear-orden-pago,
eliminar-invoice) -- pensado para ser llamado por scripts/batch_import.py
(corre en la máquina del usuario) y, para retry-failed, por el dashboard
Next.js server-side (que ya valida la sesión antes de reenviar, mismo patrón
que el resto de las rutas /api/*/route.ts del dashboard).

Flujo:
  1) POST /admin/batch-import/start -- recibe el manifiesto (metadatos +
     hash, SIN bytes) armado por el script local, hace el dedup (cross-batch
     contra invoices.content_hash + intra-batch contra el propio manifiesto),
     crea el batch y sus items, y devuelve solo los item_id que necesitan
     subir bytes de verdad.
  2) POST /admin/batch-import/item/{item_id}/upload -- multipart, un archivo
     real por request. Guarda a disco, dispara el procesamiento en
     background, responde 201 de inmediato (mismo patrón anti-timeout-de-
     nginx que ya usan /website-upload y /process-invoice).
  3) POST /admin/batch-import/{batch_id}/retry-failed -- reintenta items en
     status=error y items huérfanos (colgados en uploading/processing tras
     un restart del backend a mitad de un batch -- ver find_stale_batch_items).
"""

import asyncio
import datetime
import os
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from pydantic import BaseModel

from routes.process_invoice_google_2 import (
    _procesar_imagen_o_pdf,
    _verificar_secreto_invoicy,
    orchestrator,
    app_logger,
)

router = APIRouter(prefix="/admin/batch-import")

EXTENSIONES_PERMITIDAS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}


class ManifestEntry(BaseModel):
    original_path: str
    file_name: str
    content_hash: str
    size: Optional[int] = None
    zip_source: Optional[str] = None


class StartBatchBody(BaseModel):
    label: Optional[str] = None
    manifest: list[ManifestEntry]
    created_by: Optional[str] = None
    # Para importar facturas REALES sin impacto contable real -- ver
    # docs/plan-importacion-masiva-facturas.md, Fase 5/6. Cuando se pasa,
    # CADA item de este batch se procesa con este monto en vez del real
    # extraído (BAS, Sheets e invoices.total, no solo el payload de BAS --
    # ver InvoiceOrchestrator.procesar_factura_en_bas y
    # _aplicar_monto_override en process_invoice_google_2.py).
    monto_override: Optional[float] = None


@router.post("/start", summary="Iniciar un batch de importación masiva")
async def start_batch(
    body: StartBatchBody,
    x_invoicy_secret: Optional[str] = Header(default=None, alias="X-Invoicy-Secret"),
):
    _verificar_secreto_invoicy(x_invoicy_secret)

    if not body.manifest:
        raise HTTPException(status_code=400, detail="El manifiesto está vacío.")

    ahora = datetime.datetime.utcnow().isoformat() + "Z"
    batch = orchestrator._pb_client.create_import_batch(
        label=body.label,
        total_files=len(body.manifest),
        started_at=ahora,
        created_by=body.created_by,
        monto_override=body.monto_override,
    )
    if batch is None or not batch.get("id"):
        raise HTTPException(
            status_code=500, detail="No se pudo crear el batch en PocketBase."
        )
    batch_id = batch["id"]

    items_a_subir = []
    # Primera aparición de cada hash gana (orden = el orden del manifiesto,
    # que el script arma con Path.rglob -- determinístico dentro de una
    # misma corrida, aunque no garantiza un orden "natural" para el humano).
    ganador_por_hash: dict[str, str] = {}
    total_duplicados = 0

    for entrada in body.manifest:
        # Dedup cross-batch/histórico: ¿ya existe una invoice (de CUALQUIER
        # fuente, no solo batches anteriores) con este hash?
        invoice_existente = orchestrator._pb_client.find_invoice_by_content_hash(
            entrada.content_hash
        )
        if invoice_existente is not None:
            orchestrator._pb_client.create_batch_item(
                batch=batch_id,
                original_path=entrada.original_path,
                file_name=entrada.file_name,
                content_hash=entrada.content_hash,
                zip_source=entrada.zip_source or "",
                file_size=entrada.size or 0,
                status="skipped_duplicate",
                duplicate_of_invoice=invoice_existente["id"],
            )
            total_duplicados += 1
            continue

        # Dedup intra-batch: ¿ya vino este mismo hash antes en ESTE manifiesto?
        item_ganador_id = ganador_por_hash.get(entrada.content_hash)
        if item_ganador_id is not None:
            orchestrator._pb_client.create_batch_item(
                batch=batch_id,
                original_path=entrada.original_path,
                file_name=entrada.file_name,
                content_hash=entrada.content_hash,
                zip_source=entrada.zip_source or "",
                file_size=entrada.size or 0,
                status="skipped_duplicate",
                duplicate_of=item_ganador_id,
            )
            total_duplicados += 1
            continue

        nuevo_item = orchestrator._pb_client.create_batch_item(
            batch=batch_id,
            original_path=entrada.original_path,
            file_name=entrada.file_name,
            content_hash=entrada.content_hash,
            zip_source=entrada.zip_source or "",
            file_size=entrada.size or 0,
            status="pending",
        )
        if nuevo_item is None or not nuevo_item.get("id"):
            app_logger.warning(
                f"batch_import: no se pudo crear el item para {entrada.original_path}"
            )
            continue
        item_id = nuevo_item["id"]
        ganador_por_hash[entrada.content_hash] = item_id
        orchestrator._pb_client.update_batch_item(
            item_id, process_id=f"batch:{batch_id}/{item_id}"
        )
        items_a_subir.append({"item_id": item_id, "original_path": entrada.original_path})

    orchestrator._pb_client.update_import_batch(
        batch_id,
        total_unique=len(items_a_subir),
        total_duplicates=total_duplicados,
    )

    return {
        "batch_id": batch_id,
        "total_files": len(body.manifest),
        "total_unique": len(items_a_subir),
        "total_duplicates": total_duplicados,
        "items_to_upload": items_a_subir,
    }


@router.post("/item/{item_id}/upload", summary="Subir el archivo real de un item del batch")
async def upload_batch_item(
    item_id: str,
    file: UploadFile = File(...),
    x_invoicy_secret: Optional[str] = Header(default=None, alias="X-Invoicy-Secret"),
):
    _verificar_secreto_invoicy(x_invoicy_secret)

    item = orchestrator._pb_client.get_batch_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"No existe el item {item_id}.")
    if item.get("status") not in ("pending",):
        # Idempotencia: si el script reintenta el POST (timeout de red, no
        # sabe si el primero llegó) y el item ya avanzó de "pending", no
        # dispara un segundo procesamiento en paralelo del mismo archivo.
        return {
            "item_id": item_id,
            "status": item.get("status"),
            "already_dispatched": True,
        }

    batch_id = item.get("batch")
    file_name = item.get("file_name") or file.filename or "archivo"
    extension = os.path.splitext(file_name)[1].lower()
    if extension not in EXTENSIONES_PERMITIDAS:
        raise HTTPException(
            status_code=400,
            detail=f"Extensión no permitida: {extension or '(sin extensión)'}",
        )

    carpeta = f"./downloads/batches/{batch_id}"
    os.makedirs(carpeta, exist_ok=True)
    file_location = f"{carpeta}/{item_id}_{file_name}"
    with open(file_location, "wb") as f:
        f.write(await file.read())

    orchestrator._pb_client.update_batch_item(item_id, status="uploading")

    media_type = file.content_type or "application/octet-stream"
    asyncio.create_task(
        _procesar_item_de_batch(
            item_id=item_id,
            batch_id=batch_id,
            file_location=file_location,
            file_name=file_name,
            extension=extension.lstrip("."),
            media_type=media_type,
        )
    )

    return {"item_id": item_id, "status": "uploading", "already_dispatched": False}


async def _procesar_item_de_batch(
    *,
    item_id: str,
    batch_id: str,
    file_location: str,
    file_name: str,
    extension: str,
    media_type: str,
) -> None:
    """Wrapper por-item: actualiza import_batch_items (no invoices
    directamente -- eso ya lo hace _procesar_imagen_o_pdf) y, al terminar,
    chequea si el batch entero ya está listo para cerrarse. Cada item corre
    en su PROPIA task disparada por su PROPIA request de upload -- a
    diferencia del for-loop compartido de los ZIP existentes, acá un error
    en un item no puede afectar la programación de los demás, porque todos
    ya fueron disparados antes de que este termine."""
    item_actual = orchestrator._pb_client.get_batch_item(item_id)
    intento_previo = (item_actual or {}).get("attempt_count") or 0

    batch = orchestrator._pb_client.get_import_batch(batch_id)
    monto_override = (batch or {}).get("monto_override")
    # 0 es un override válido en teoría, pero acá lo tratamos igual que None
    # -- nadie va a querer registrar una factura por $0 a propósito, y
    # PocketBase puede devolver 0 en vez de omitir el campo.
    if not monto_override:
        monto_override = None

    orchestrator._pb_client.update_batch_item(item_id, status="processing")

    process_id = f"batch:{batch_id}/{item_id}"
    try:
        await _procesar_imagen_o_pdf(
            file_location=file_location,
            file_name=file_name,
            extension=extension,
            media_type=media_type,
            process_id=process_id,
            monto_override=monto_override,
        )
        invoice = orchestrator._pb_client.get_invoice_by_process_id(process_id)
        orchestrator._pb_client.update_batch_item(
            item_id,
            status="completed",
            attempt_count=intento_previo + 1,
            **({"invoice": invoice["id"]} if invoice and invoice.get("id") else {}),
        )
    except Exception as e:
        app_logger.warning(f"batch_import: item {item_id} falló: {e}")
        orchestrator._pb_client.update_batch_item(
            item_id,
            status="error",
            error_message=str(e)[:2000],
            attempt_count=intento_previo + 1,
        )
    finally:
        await _cerrar_batch_si_termino(batch_id)


async def _cerrar_batch_si_termino(batch_id: str) -> None:
    """Self-closing: no hace falta un endpoint explícito de "finish" ni un
    orquestador aparte. Los contadores NO se persisten con incrementos --
    se tallan acá mismo, en memoria, sobre los items reales del batch (misma
    filosofía anti-condición-de-carrera que usa el resumen del dashboard).
    Riesgo menor aceptado: dos items terminando en el mismo instante podrían
    evaluar esto en paralelo y escribir el cierre dos veces -- inofensivo,
    porque ambos escriben el mismo valor terminal (idempotente)."""
    items = orchestrator._pb_client.list_batch_items(batch_id)
    if not items:
        return
    pendientes = [
        it for it in items if it.get("status") in ("pending", "uploading", "processing")
    ]
    if pendientes:
        return
    con_error = sum(1 for it in items if it.get("status") == "error")
    status_final = "completed_with_errors" if con_error > 0 else "completed"
    orchestrator._pb_client.update_import_batch(
        batch_id,
        status=status_final,
        finished_at=datetime.datetime.utcnow().isoformat() + "Z",
    )


@router.post("/{batch_id}/retry-failed", summary="Reintentar items fallidos o huérfanos de un batch")
async def retry_failed(
    batch_id: str,
    x_invoicy_secret: Optional[str] = Header(default=None, alias="X-Invoicy-Secret"),
):
    _verificar_secreto_invoicy(x_invoicy_secret)

    batch = orchestrator._pb_client.get_import_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"No existe el batch {batch_id}.")

    items = orchestrator._pb_client.list_batch_items(batch_id)
    fallidos = [it for it in items if it.get("status") == "error"]
    # Items huérfanos: colgados en uploading/processing porque el backend se
    # reinició a mitad de camino -- el cierre self-closing nunca dispara en
    # ese caso (ver docs/plan-importacion-masiva-facturas.md, sección 6).
    huerfanos = orchestrator._pb_client.find_stale_batch_items(batch_id)
    ids_ya_incluidos = {it["id"] for it in fallidos}
    a_reintentar = fallidos + [h for h in huerfanos if h["id"] not in ids_ya_incluidos]

    if not a_reintentar:
        return {"batch_id": batch_id, "reintentados": 0}

    # Reabre el batch -- si ya había cerrado (completed_with_errors), que no
    # quede mostrando "listo" mientras hay reintentos en vuelo.
    orchestrator._pb_client.update_import_batch(batch_id, status="running", finished_at=None)

    reintentados = 0
    for item in a_reintentar:
        item_id = item["id"]
        file_name = item.get("file_name") or ""
        extension = os.path.splitext(file_name)[1].lower().lstrip(".")
        file_location = f"./downloads/batches/{batch_id}/{item_id}_{file_name}"

        if not os.path.exists(file_location):
            # Caso borde: el archivo ya no está en disco -- normalmente pasa
            # solo si _procesar_imagen_o_pdf había llegado a borrarlo (éxito
            # real) pero un paso NUESTRO posterior (el update_batch_item de
            # "completed") falló y por eso el item quedó marcado error/
            # colgado. No hay nada que reprocesar; hace falta revisar a mano
            # si la invoice ya existe.
            orchestrator._pb_client.update_batch_item(
                item_id,
                status="error",
                error_message=(
                    "El archivo original ya no está en disco -- es posible que "
                    "el procesamiento haya terminado bien y solo haya fallado "
                    "la actualización del item. Revisar manualmente si la "
                    "factura ya existe antes de reintentar de nuevo."
                ),
            )
            continue

        orchestrator._pb_client.update_batch_item(item_id, status="uploading")
        media_type_por_ext = {
            "pdf": "application/pdf",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
            "gif": "image/gif",
        }
        asyncio.create_task(
            _procesar_item_de_batch(
                item_id=item_id,
                batch_id=batch_id,
                file_location=file_location,
                file_name=file_name,
                extension=extension,
                media_type=media_type_por_ext.get(extension, "application/octet-stream"),
            )
        )
        reintentados += 1

    return {"batch_id": batch_id, "reintentados": reintentados}
