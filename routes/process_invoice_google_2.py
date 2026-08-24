# FastAPI imports
from fastapi import Form, APIRouter, HTTPException, UploadFile, File, Request, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app_logger = logging.getLogger("app_logger")

# Standard library imports
import os
import shutil
import json
import base64
import asyncio
import threading
import hashlib
import ssl
import uuid
import datetime
import time
import unicodedata
import re
import zlib
from pathlib import Path
from typing import Callable, Dict, Optional, Union, TypedDict
import fitz  # PyMuPDF
from PIL import Image
import io
from dotenv import load_dotenv

# Third-party imports
import aiohttp
import certifi
import filetype
import requests
import zipfile
from jsonschema import validate, ValidationError
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Local imports
from tools import tools
from tools_standard import build_tools
from utils.bas import BasClient, BasApiError
from utils.pocketbase_client import PocketBaseClient
from utils.rate_limit import limiter
from utils.bas_item_resolver import resolver_codigo_item
from utils.bas_payload import construir_comprobante_totales_e_items
from utils.bas_config import (
    fecha_hoy_bas,
    BAS_EMPRESA,
    BAS_SUCURSAL,
    BAS_DEPOSITO,
    BAS_CAJA,
    BAS_METODO_PAGO_CTA_CTE,
    BAS_TIPO_ENTREGA_SIN_STOCK,
    BAS_CENTRO_APROPIACION_SD,
    BAS_EMITIDO_POR_CAE,
    BAS_TRAT_IMPOSITIVO_RI,
    BAS_TRAT_IMPOSITIVO_PROV_RI,
    BAS_PREFIJO_TALONARIO_MA,
    BAS_PREFIJO_TALONARIO_OP,
    BAS_IMPUTACION_CONTABLE_PROVEEDORES,
    METODO_PAGO_ARRAY_BAS,
)
from utils.validaciones_pre_bas import (
    validar_factura_antes_de_pago_real,
    combinar_numero_comprobante,
)
import google.auth.transport.requests as google_auth_requests

load_dotenv()

# Crea una instancia del router de FastAPI
router = APIRouter(prefix="/gemini2")

# Cuántos pipelines completos (Gemini + BAS + Sheets + Drive + PocketBase)
# pueden correr a la vez en todo el proceso. Vive a nivel módulo, no dentro
# de un endpoint puntual, y envuelve _procesar_imagen_o_pdf en su propia
# definición (ver más abajo) -- así protege por igual los dos loops de ZIP
# ya existentes (/website-upload, /process-invoice) Y el nuevo flujo de
# importación masiva (routes/batch_import.py), sin que ninguno le robe
# recursos al otro. Default=1 (secuencial), mismo criterio ya deliberado en
# el código de ZIP ("el Droplet tiene 1 vCPU/960MB... un ZIP gigante
# saturaría el background task de abajo por horas"). Cap duro a 2 en código
# -- que nadie lo suba a un valor que tumbe el droplet vía env var.
# Ver docs/plan-importacion-masiva-facturas.md, sección 5.
PROCESSING_SEMAPHORE = asyncio.Semaphore(
    min(int(os.getenv("INVOICY_MAX_CONCURRENT_PROCESSING", "1")), 2)
)

# Tope de archivos por ZIP y de peso total descomprimido -- compartido por
# los 2 canales que aceptan ZIP (email, /website-upload) y por /process-invoice
# (que antes tenía su propio MAX_ARCHIVOS_ZIP=20 local). Si se supera
# cualquiera de los dos, se rechaza el ZIP COMPLETO con un error claro --
# a propósito no se procesan parcialmente los primeros N: el cliente no
# tiene forma de saber, mirando la cola, que "solo llegaron los primeros 100"
# en vez de todo el ZIP. Ver docs/plan-fase1-zip-FINAL.md, sección 8.
MAX_ARCHIVOS_ZIP = 100
MAX_ZIP_DESCOMPRIMIDO_BYTES = 500 * 1024 * 1024  # 500MB

# Gate de concurrencia para la Pass 0 -- pre-registro de TODOS los
# miembros candidatos de un ZIP en PocketBase, ANTES de extraer/clasificar
# ninguno (ver _extraer_zip_y_despachar_individualmente). Deliberadamente
# SEPARADO de ZIP_EXTRACTION_SEMAPHORE (más abajo), con más concurrencia
# permitida: la Pass 0 es liviana (solo upsert_invoice, un GET+POST/PATCH
# JSON chico por miembro, sin extraer ni subir ningún archivo -- ver
# utils/pocketbase_client.py:_upsert), no compite por CPU/disco/zlib como
# la extracción real. Compartir el mismo semáforo de concurrencia=2 que la
# extracción pesada dejaba a un ZIP recién llegado esperando en cola
# potencialmente varios minutos (mientras 2 ZIPs anteriores terminan TODA
# su Fase A) sin haber registrado ni una sola fila en PocketBase -- si el
# proceso se reiniciaba en esa espera, el ZIP entero desaparecía sin
# rastro, con el cliente ya habiendo recibido un 201 de éxito (hallazgo
# real de la revisión adversarial de la ronda 5). Con Pass 0 desacoplada y
# con más cupo, ese registro ocurre casi de inmediato, incluso con varios
# ZIPs pesados ya en curso. Igual acotada (no ilimitada): sigue habiendo un
# techo, y el límite real de backlog total sigue siendo
# MAX_TRABAJO_EN_VUELO_ZIP, no este número. Ver
# docs/informe-final-zip-fase1.md sección 6.
ZIP_REGISTRO_SEMAPHORE = asyncio.Semaphore(8)

# Gate de concurrencia para la Pass 1 -- clasificación real (extracción,
# CRC/encriptación, Zip Slip, tipo soportado) Y, para cada miembro
# ACEPTADO, el adjunto de su archivo (adjuntar_archivo_original, PATCH
# multipart que sube el contenido completo) inmediatamente después de
# aceptarlo -- ya no en una Pass 2 separada al final (ver
# _extraer_zip_y_despachar_individualmente para el motivo: adjuntar de
# inmediato, no al final del loop, es lo que garantiza que "Reintentar"
# funcione para los miembros YA clasificados aunque el proceso muera antes
# de terminar de clasificar el resto del ZIP). I/O de disco + zlib
# (CPU-bound) Y llamadas HTTP bloqueantes a PocketBase reales: hasta
# MAX_ARCHIVOS_ZIP adjuntos (1 PATCH cada uno) más hasta MAX_ARCHIVOS_ZIP
# descartes de placeholder para los rechazados (soft_delete_invoice = 1
# GET + 1 PATCH cada uno, ver utils/pocketbase_client.py). Separado de
# PROCESSING_SEMAPHORE (que gatea el trabajo pesado real de Gemini/BAS) a
# propósito: si compartieran el mismo semáforo, una extracción larga
# podría bloquear turnos de facturas sueltas que no tienen nada que ver
# con ningún ZIP (starvation cruzada), o viceversa. Tamaño chico a
# propósito: la extracción ya corre en asyncio.to_thread, este semáforo
# solo evita que demasiadas extracciones pesadas (disco+zlib+red a
# PocketBase) compitan a la vez por la única vCPU real del droplet. Ver
# docs/plan-fase1-zip-REDISEÑO.md, sección 5, y
# docs/informe-final-zip-fase1.md sección 6.
ZIP_EXTRACTION_SEMAPHORE = asyncio.Semaphore(2)

# "Trabajo en vuelo" = facturas de ZIP ya registradas (status="pending"/
# "processing" real en PocketBase) pero todavía no en un estado terminal
# (completed/error). A diferencia de PROCESSING_SEMAPHORE (que limita
# CONCURRENCIA de ejecución) esto limita BACKLOG ACEPTADO -- sin esto,
# varios ZIPs grandes podrían aceptarse todos y quedar horas en cola
# invisible para el caller. Contador en memoria de proceso, protegido con
# threading.Lock (NO asyncio.Lock) a propósito: se reserva/libera tanto
# desde la fase de extracción -- que corre en un hilo real de
# asyncio.to_thread, no en el event loop -- como desde el event loop mismo
# (Fase B) -- mismo criterio exacto que _comprobante_locks_guard más abajo
# para el mismo tipo de problema (estado compartido entre hilo real y
# coroutines). Se resetea a 0 en cada reinicio del proceso -- aceptable: el
# barrido de huérfanos (sección de durabilidad) es quien garantiza que
# ninguna factura "pending" real se pierda, este contador es solo
# backpressure blando, no la fuente de verdad de qué hay pendiente.
# Alcance deliberadamente limitado a ZIP (no cubre facturas sueltas): el
# riesgo nuevo que motiva este límite es específicamente "1 request vale
# hasta 100 unidades de trabajo", que no existe para /website-upload ni
# /process-invoice de archivo individual (ya bounded 1:1 por sus propios
# límites existentes). Ver docs/plan-fase1-zip-REDISEÑO.md, sección 6.
MAX_TRABAJO_EN_VUELO_ZIP = 200
_trabajo_en_vuelo_zip = 0
_trabajo_en_vuelo_zip_lock = threading.Lock()


def _reservar_trabajo_en_vuelo_zip(cantidad: int) -> bool:
    global _trabajo_en_vuelo_zip
    with _trabajo_en_vuelo_zip_lock:
        if _trabajo_en_vuelo_zip + cantidad > MAX_TRABAJO_EN_VUELO_ZIP:
            return False
        _trabajo_en_vuelo_zip += cantidad
        return True


def _liberar_trabajo_en_vuelo_zip(cantidad: int) -> None:
    global _trabajo_en_vuelo_zip
    if cantidad <= 0:
        return
    with _trabajo_en_vuelo_zip_lock:
        _trabajo_en_vuelo_zip = max(0, _trabajo_en_vuelo_zip - cantidad)

# Lock en memoria de proceso, keyed por proveedor+comprobante externo, para
# serializar la secuencia "GET ConsultaComprobantesExternos -> si no existe,
# POST ComprobantesCompra" (dentro de BasClient.crear_orden_de_pago_desde_factura).
# Sin esto, dos invocaciones concurrentes para LA MISMA factura -- worker()
# automático, /retry-op, /payment-orders/create, o un ítem de importación
# masiva -- podrían ambas leer "no existe" antes de que cualquiera confirme
# su alta, duplicando el comprobante en BAS. threading.Lock (no asyncio.Lock)
# a propósito: se usa tanto desde código sync (procesar_factura_en_bas) como
# desde endpoints async, sin necesitar volver ese método async. Hoy esta
# condición de carrera ya está mitigada de hecho porque los llamados HTTP a
# BAS (utils/bas.py, librería `requests`) son bloqueantes y con un solo
# worker de uvicorn no hay interleaving real -- este lock es la protección
# explícita para que eso siga siendo cierto aunque cambie el despliegue
# (más workers, o si se offloadea a un threadpool para no bloquear el loop).
_comprobante_locks: Dict[str, threading.Lock] = {}
_comprobante_locks_guard = threading.Lock()


def _lock_comprobante(proveedor_codigo, prefijo_externo, numero_externo) -> threading.Lock:
    clave = f"{proveedor_codigo}:{prefijo_externo}:{numero_externo}"
    with _comprobante_locks_guard:
        lock = _comprobante_locks.get(clave)
        if lock is None:
            lock = threading.Lock()
            _comprobante_locks[clave] = lock
        return lock


# Encabezados de la pestaña de ítems (una fila por ítem de factura).
# El orden DEBE coincidir con el de _construir_filas_items().
ITEMS_SHEET_HEADERS = [
    "process_id",
    "fecha_registro",
    "numero_comprobante",
    "fecha_emision",
    "tipo_comprobante",
    "emisor_nombre",
    "emisor_id_fiscal",
    "receptor_nombre",
    "receptor_id_fiscal",
    "moneda",
    "linea",
    "descripcion",
    "cantidad",
    "precio_unitario",
    "precio_total",
]

# Palabras clave (normalizadas: minúsculas, sin acentos) para detectar líneas
# de descuento/bonificación que el modelo a veces extrae como un ítem más.
# Editar esta lista si aparecen falsos positivos/negativos.
DISCOUNT_KEYWORDS = (
    "descuento",
    "descto",
    "bonif",        # cubre "bonificación", "bonif."
    "rebaja",
    "promocion",
    "promo",
)


def _extraer_prefijo_numero_comprobante_externo(comprobante: dict):
    """
    Extrae (prefijo_externo, numero_externo) del número de comprobante con
    formato "PPPPP-NNNNNNNN", tal como lo espera BAS (PrefijoComprobanteExterno /
    NumeroComprobanteExterno). Misma lógica de parseo que usa internamente
    InvoiceOrchestrator.procesar_factura_en_bas() -- duplicada acá (en vez de
    leerla de ahí) para no tener que tocar la firma/retorno de ese método
    existente. Usada tanto para persistir en PocketBase como por el endpoint
    de reintento de orden de pago.

    combinar_numero_comprobante (no normalizar_numero_comprobante solo,
    2026-08-14): este helper es la ÚNICA función que arma prefijo/numero
    para BAS en todo el archivo -- los 4 call sites pasan por acá (dos con
    el dict crudo de Gemini, que SÍ trae "punto_de_venta"; dos con un dict
    sintético {"numero": invoice.get("numero_comprobante")} ya combinado
    en la ingesta, que no trae esa clave y no la necesita). Antes usaba
    normalizar_numero_comprobante a secas, así que el pase automático de
    ingesta (InvoiceOrchestrator.procesar_factura_en_bas, worker()) mandaba
    una consulta real a BAS con NumeroComprobanteExterno mal armado (ej.
    "00000066" sin punto de venta -> Prefijo="00000066"/Numero=0) para
    cualquier factura con el layout AFIP estándar (Punto de Venta y Comp.
    Nro impresos por separado) -- sin escribir nada real porque ese pase
    siempre corre en dry_run, pero consultando con la clave equivocada.
    Con combinar_numero_comprobante acá, los 4 call sites quedan
    consistentes automáticamente -- no hace falta tocar ninguno de ellos.
    """
    numero_completo = (
        combinar_numero_comprobante(
            (comprobante or {}).get("numero"), (comprobante or {}).get("punto_de_venta")
        )
        or ""
    )
    numero_completo = numero_completo.replace(" ", "")
    prefijo_externo, _, numero_externo_str = numero_completo.partition("-")
    numero_externo = int(numero_externo_str) if numero_externo_str.isdigit() else 0
    return prefijo_externo, numero_externo


def _normalizar_texto(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar descripciones de forma robusta."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(texto))
    sin_acentos = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sin_acentos.lower().strip()


# Convierte un archivo PDF a string base64
def pdf_to_base64(file_path: str) -> Union[str, None]:
    try:
        with open(file_path, "rb") as pdf_file:
            binary_data = pdf_file.read()
            base_64_encoded_data = base64.b64encode(binary_data)
            base64_string = base_64_encoded_data.decode("utf-8")
        return base64_string
    except Exception as e:
        app_logger.error(f"An error occurred: {e}")
        return None


# Formatea la información de retenciones en texto legible
def formatear_retenciones(retenciones):
    if not retenciones:
        return ""
    resultado = []
    for i, r in enumerate(retenciones, start=1):
        base_imponible = r.get('base_imponible')
        base_imponible_str = f"${base_imponible:.2f}" if base_imponible is not None else "No especificada"
        texto = (
            f"Retención #{i}:\n"
            f"  - Tipo: {r['tipo']}\n"
            f"  - Descripción: {r.get('description', 'No especificada')}\n"
            f"  - Base Imponible: {base_imponible_str}\n"
        )
        resultado.append(texto)
    return "\n".join(resultado)


# Formatea la información de impuestos en texto legible, incluyendo campos opcionales
def formatear_impuestos(impuestos):
    if not impuestos:
        return ""
    resultado = []
    for i, imp in enumerate(impuestos, start=1):
        base_imponible = imp.get('base_imponible')
        base_imponible_str = f"${base_imponible:.2f}" if base_imponible is not None else "No especificada"
        importe = imp.get('importe')
        importe_str = f"${importe:.2f}" if importe is not None else "No especificado"
        
        # Construye el texto base con campos requeridos
        texto = (
            f"Impuesto #{i}:\n"
            f"  - Tipo: {imp['tipo']}\n"
            f"  - Base Imponible: {base_imponible_str}\n"
            f"  - Importe: {importe_str}\n"
        )

        # Agrega descripción si está presente
        if "descripcion" in imp and imp["descripcion"] is not None:
            texto = texto.replace(
                f"  - Tipo: {imp['tipo']}\n",
                f"  - Tipo: {imp['tipo']}\n" f"  - Descripción: {imp['descripcion']}\n",
            )

        # Agrega alícuota si está presente
        if "alicuota" in imp and imp["alicuota"] is not None:
            texto = texto.replace(
                f"  - Base Imponible: {base_imponible_str}\n",
                f"  - Base Imponible: {base_imponible_str}\n"
                f"  - Alícuota: {imp['alicuota']:.2f}%\n",
            )

        resultado.append(texto)
    return "\n".join(resultado)


def _extraer_alicuota_iva(impuestos: list) -> Optional[float]:
    """Alícuota real de IVA de la factura, a partir de la lista extraída por
    Gemini (tool impuestos_y_retenciones_de_la_factura) -- hasta ahora ese
    dato se calculaba pero solo se usaba para formatear_impuestos (texto de
    Sheets/email), nunca se persistía ni llegaba al payload de BAS. Se usa
    para calcular ImporteIva/TotalIva de cabecera (ver
    utils/bas_payload.py:construir_comprobante_totales_e_items, único lugar
    que arma esto desde 2026-08-19). Desde P0-E (2026-08-12)
    TAMBIÉN se usa para elegir el CodigoItem: confirmado real que BAS no
    cruza ImporteIva/TasaIva contra la tasa configurada del catálogo para
    el CodigoItem elegido (no hay riesgo de 409 por esto), así que conectar
    la alícuota real a la resolución (ver utils.bas_item_resolver.resolver_codigo_item
    -> utils.bas_config.resolver_item_bas) solo mejora la imputación
    contable (ej. "Ec. Alim 10.5%" en vez de siempre "Ec. Alim 21%" para una
    factura al 10.5%) -- si no hay una variante confirmada para la alícuota
    exacta, cae al 21% de esa categoría (comportamiento histórico), pero
    ese candidato SIEMPRE se valida contra bas_items antes de usarse.

    Solo devuelve un valor si hay EXACTAMENTE un impuesto tipo "IVA" con
    alícuota -- si Gemini no detectó ninguno, o detectó más de uno (factura
    con líneas a distintas tasas, caso que este pipeline no modela por
    ítem), se prefiere no adivinar: devuelve None.

    IMPORTANTE -- `None` significa "no se pudo determinar", NUNCA "es 0%".
    El caller (ver upsert_invoice en process_invoice_google_2.py y
    worker()) debe tratar `None` como "no tocar iva_alicuota" (omitir el
    campo del payload, no mandar 0 ni mandar None -- PocketBase convierte
    None a 0 en un campo number, confirmado real, ver
    docs/incidente-2026-08-04-pagos-solo-neto.md sección 10), para no pisar
    con un 0 espurio un valor bueno ya guardado de un intento anterior en un
    reprocesamiento. Solo cuando esta función devuelve un float (0.0
    incluido -- Gemini puede decir explícitamente "IVA 0%") es seguro
    persistirlo: ahí sí es un 0 confirmado, no un "no sé".

    Cada rama que devuelve None loguea el motivo (a propósito, en las
    cuatro) -- así se puede detectar el patrón en producción grepeando
    logs, sin depender de mirar la base de datos."""
    if not impuestos:
        app_logger.info(
            "_extraer_alicuota_iva: no se extrajo ningún impuesto -- no se puede "
            "determinar la alícuota (se conserva el valor previo si lo había)."
        )
        return None
    ivas = [
        imp
        for imp in impuestos
        if str(imp.get("tipo", "")).strip().upper() == "IVA" and imp.get("alicuota") is not None
    ]
    if not ivas:
        # Hay impuestos extraídos pero ninguno matcheó "IVA" exacto -- puede
        # ser una factura sin IVA (legítimo) o Gemini devolvió una variante
        # del string ("IVA 21%", "I.V.A.", etc.) que este match exacto no
        # captura. No se puede distinguir un caso del otro acá, así que no
        # se adivina.
        app_logger.info(
            f"_extraer_alicuota_iva: {len(impuestos)} impuesto(s) extraído(s), "
            "ninguno con tipo='IVA' exacto -- no se puede determinar la alícuota "
            "(se conserva el valor previo si lo había)."
        )
        return None
    if len(ivas) > 1:
        # Más de un impuesto tipo "IVA" (ej. factura con líneas a distintas
        # tasas) -- este pipeline no modela IVA por ítem, así que no hay una
        # única alícuota de cabecera que elegir.
        app_logger.info(
            f"_extraer_alicuota_iva: {len(ivas)} impuestos con tipo='IVA' -- "
            "ambiguo, no se puede determinar una única alícuota (se conserva "
            "el valor previo si lo había)."
        )
        return None
    try:
        return round(float(ivas[0]["alicuota"]), 2)
    except (TypeError, ValueError):
        app_logger.info(
            f"_extraer_alicuota_iva: alícuota no numérica ({ivas[0].get('alicuota')!r}) "
            "-- no se puede determinar (se conserva el valor previo si lo había)."
        )
        return None


# Definición de tipo para elementos en cola que contienen información del archivo
class QueueItem(TypedDict):
    file_name: str
    file_extension: str
    file_path: str
    media_type: str
    process_id: str


class InvoiceOrchestrator:
    def __init__(
        self,
        secret: str,
        webhook_url: str,
        api_key: str,
        recharge_cooldown: int,
        queue_check_cooldown: int,
        model: str,
        semaphore: int,
    ):
        # Capturado ANTES que cualquier otra cosa -- es el corte real que
        # usa _barrido_huerfanos_al_arrancar para distinguir "huérfana de un
        # proceso anterior" (updated < este momento) de "en curso, la está
        # procesando este mismo proceso" (updated >= este momento). Un
        # umbral de antigüedad fijo (ej. "hace 30 minutos") no sirve: con
        # auto-restart real el proceso vuelve a arrancar en segundos, así
        # que una fila recién huérfana nunca llega a acumular 30 minutos
        # antes de que el barrido (que corre una sola vez) ya haya pasado.
        self._iniciado_en = datetime.datetime.now(datetime.timezone.utc)
        self.secret = secret
        self.webhook_url = webhook_url
        self.api_key = api_key
        self.recharge_cooldown = recharge_cooldown
        self.queue_check_cooldown = queue_check_cooldown
        self.model = model
        self.semaphore = asyncio.Semaphore(semaphore)
        self.queue = asyncio.Queue()
        self.active_comparisons = {}
        self.processed_jobs = set()  # Para idempotencia
        self._ensured_item_tabs = set()  # Cache de pestañas de ítems ya verificadas
        self._bas_client = BasClient()
        self._proveedores_bas_cache = {}  # Cache de proveedores BAS ya verificados/creados (key: CUIT normalizado)
        self._pb_client = PocketBaseClient()  # Persistencia (facturas/items/jobs/estado BAS); ver utils/pocketbase_client.py
        self.job_queue = asyncio.Queue()  # Cola para jobs
        asyncio.create_task(self.worker())
        asyncio.create_task(self._barrido_huerfanos_al_arrancar())

    async def _barrido_huerfanos_al_arrancar(self):
        """Recuperación de facturas interrumpidas por un reinicio del
        backend a mitad de procesamiento -- ver Fase A/B de
        _extraer_zip_y_despachar_individualmente y
        docs/plan-fase1-zip-REDISEÑO.md sección 3. Corre UNA VEZ al
        arrancar el proceso (no periódico): el único momento en que "trabajo
        que estaba en curso" puede quedar huérfano es exactamente un
        reinicio, no un chequeo recurrente mientras el proceso sigue vivo.
        Pasa cada factura vieja "pending"/"processing" a status="error" con
        un mensaje claro -- reusa el botón "Reintentar" que ya existe
        (/invoices/{process_id}/retry-extraction) para la recuperación, sin
        ningún endpoint ni UI nueva. Best-effort: un fallo acá nunca debe
        frenar el arranque del resto de la app.

        Usa self._iniciado_en (el momento real en que ESTE proceso arrancó)
        como corte -- NO un umbral de antigüedad fijo. Bug real encontrado
        en la revisión adversarial: con auto-restart real (systemd/
        supervisor/docker restart=always) el proceso vuelve a levantarse en
        segundos, así que con un umbral tipo "hace 30 minutos" el barrido
        (que corre una sola vez, a los 10s de este mismo arranque) nunca
        encontraba nada que recuperar -- las filas recién huérfanas todavía
        no habían acumulado 30 minutos de antigüedad, y como el barrido no
        se repite, quedaban "pending" invisibles para siempre. Comparar
        contra el arranque del proceso es correcto sin importar cuánto haya
        tardado el restart (ver docstring de find_stale_invoices)."""
        await asyncio.sleep(10)  # deja que el resto de la app termine de iniciar
        try:
            huerfanas = self._pb_client.find_stale_invoices(antes_de=self._iniciado_en)
            for inv in huerfanas:
                self._pb_client.upsert_invoice(
                    {
                        "process_id": inv["process_id"],
                        "status": "error",
                        "error_message": (
                            "Interrumpido por un reinicio del servidor durante "
                            "el procesamiento -- usá 'Reintentar' para "
                            "procesarla de nuevo."
                        ),
                    }
                )
            if huerfanas:
                app_logger.info(
                    f"Barrido de huérfanos al arrancar: {len(huerfanas)} "
                    "factura(s) recuperada(s) a status=error tras reinicio"
                )
        except Exception as e:
            app_logger.warning(f"Barrido de huérfanos al arrancar falló (no crítico): {e}")

    async def worker(self):
        app_logger.info("Iniciando worker")
        while True:
            job = await self.job_queue.get()
            from_email = job["from_email"]
            subject = job["subject"]
            temp_dir = job["temp_dir"]
            process_id = job["process_id"]

            app_logger.info(f"From email: {from_email}")
            app_logger.info(f"Subject: {subject}")

            file_name = temp_dir.split("/")[-1]
            app_logger.info(f"File name: {file_name}")

            subject_for_file = f"{subject} terminamos con el archivo {file_name}"
            app_logger.info(f"Subject for file: {subject_for_file}")

            if process_id in self.processed_jobs:
                app_logger.info(f"Job {process_id} ya procesado, skipping")
                self.job_queue.task_done()
                continue

            # Respaldo de idempotencia que sobrevive un restart (self.processed_jobs
            # es en memoria y se pierde). Fail open: si PocketBase no responde, no
            # bloqueamos el procesamiento -- solo logueamos y seguimos con el
            # chequeo en memoria de arriba.
            try:
                job_previo = self._pb_client.get_processing_job(process_id)
                if job_previo is not None and job_previo.get("status") == "done":
                    app_logger.info(
                        f"Job {process_id} ya marcado 'done' en PocketBase (restart), skipping"
                    )
                    self.processed_jobs.add(process_id)
                    self.job_queue.task_done()
                    continue
            except Exception as e:
                app_logger.warning(
                    f"PocketBase: error chequeando idempotencia de {process_id}: {e}"
                )

            self.processed_jobs.add(process_id)

            items_to_process = job["items_to_process"]
            total_items = len(items_to_process)
            app_logger.info(
                f"Iniciando procesamiento del job {process_id} - {total_items} archivos en cola"
            )

            # Best-effort: registra el arranque del job. Aislado -- un fallo acá
            # no debe impedir el procesamiento.
            try:
                # total_items no es un campo del schema de processing_jobs (ver
                # contrato) -- se omite para no mandar un campo que PocketBase
                # ignora silenciosamente. from_email/subject/file_name sí lo son
                # y ya están disponibles acá -- se envían para no perder
                # trazabilidad de origen del job.
                self._pb_client.update_processing_job(
                    process_id,
                    status="processing",
                    from_email=from_email,
                    subject=subject,
                    file_name=file_name,
                )
            except Exception as e:
                app_logger.warning(f"PocketBase: error creando/actualizando processing_job {process_id}: {e}")

            try:
                self.active_comparisons[process_id] = job
                processed_count = 0

                for i, item in enumerate(items_to_process, 1):
                    file_name = item["file_name"]
                    media_type = item["media_type"]
                    app_logger.info(
                        f"[{process_id}] Procesando archivo {i}/{total_items}: {file_name} (tipo: {media_type})"
                    )

                    try:
                        # Procesamiento según tipo de archivo
                        if item["media_type"].startswith("image/"):
                            app_logger.info(
                                f"[{process_id}] Ejecutando toolchain de imagen para {file_name}"
                            )
                            respuestas = await self.run_image_toolchain(item)
                        else:
                            app_logger.info(
                                f"[{process_id}] Ejecutando toolchain de PDF para {file_name}"
                            )
                            respuestas = await self.run_pdf_toolchain(item)

                        app_logger.info(
                            f"[{process_id}] Toolchain completado para {file_name}, formateando factura"
                        )
                        factura = self.formatear_factura(respuestas["data"])

                        # Guardar factura como JSON en el directorio temporal
                        # temp_dir = job["temp_dir"]
                        # json_filename = (
                        #     f"factura_{process_id}.json"
                        # )
                        # json_path = os.path.join(temp_dir, json_filename)
                        # try:
                        #     with open(json_filename, "w", encoding="utf-8") as f:
                        #         json.dump(
                        #             {"factura": factura},
                        #             f,
                        #             ensure_ascii=False,
                        #             indent=2,
                        #         )
                        #     app_logger.info(
                        #         f"[{process_id}] Factura guardada como JSON: {json_path}"
                        #     )
                        # except Exception as e:
                        #     app_logger.error(
                        #         f"[{process_id}] Error guardando JSON para {file_name}: {e}"
                        #     )

                        app_logger.info(
                            f"[{process_id}] Guardando factura en sheets para {file_name}"
                        )
                        saved = self.guardar_factura_completa_en_sheets(factura["data"])
                        app_logger.info(
                            f"[{process_id}] Factura guardada en sheets para {file_name}"
                        )

                        # Guardar los ítems (una fila por ítem) en su pestaña.
                        # Aislado: si falla, no rompe el resto del procesamiento.
                        saved_items = self.guardar_items_en_sheets(
                            factura["data"], process_id
                        )
                        app_logger.info(
                            f"[{process_id}] Ítems guardados en sheets: {saved_items}"
                        )

                        # Persistencia en PocketBase (invoice + items). Aislado a
                        # propósito, mismo criterio que guardar_items_en_sheets: un
                        # fallo acá NO debe afectar Sheets/BAS/Drive/email.
                        #
                        # Nombres de campo alineados EXACTO con el schema real de
                        # ticket-ai-infra/pocketbase/pb_migrations/ (no improvisar
                        # nombres nuevos -- "status" es requerido y "invoice" en
                        # invoice_items/bas_processing_status es una relation
                        # requerida al id del record de "invoices", no al process_id).
                        _pb_invoice_record = None
                        try:
                            _er = factura["data"].get("emisor_receptor", {})
                            _cmp = _er.get("comprobante", {})
                            _emisor = _er.get("emisor", {})
                            _receptor = _er.get("receptor", {})
                            _otros = _er.get("otros", {})
                            _items_info = factura["data"].get("items", {})
                            _detalles = _items_info.get("detalles", []) or []
                            _impuestos_info = factura["data"].get("impuestos", {})
                            _alicuota_iva = _extraer_alicuota_iva(_impuestos_info.get("impuestos", []))

                            # Campos extraídos por Gemini -- CUALQUIERA de ellos puede
                            # venir None si la corrida actual falló/fue ambigua en esa
                            # parte puntual (ver el comentario largo de
                            # _extraer_alicuota_iva sobre el caso real que lo confirmó,
                            # 2026-08-04, factura MEDINA/SHOW IMPORT: la extracción de
                            # impuestos falló en un reintento y pisó con 0 una alícuota
                            # de 21% ya guardada de un intento anterior bueno). Si esto
                            # es un reprocesamiento del mismo process_id, mandar None
                            # explícito PISARÍA un valor bueno ya guardado (o, para
                            # campos number como iva_alicuota, PocketBase lo convierte
                            # en 0 -- ver docs/incidente-2026-08-04-pagos-solo-neto.md
                            # sección 10). Se omite el campo entero en vez de mandar
                            # None, así upsert_invoice (merge por process_id) conserva
                            # lo que ya había. Si es la primera vez (no hay valor
                            # previo), omitir tampoco pierde nada: PocketBase usa su
                            # default de todos modos.
                            _campos_extraidos = {
                                # combinar_numero_comprobante (2026-08-14):
                                # arma "puntoDeVenta-numero" cuando el
                                # comprobante los imprime como dos campos
                                # separados (el header AFIP estándar) --
                                # antes solo se usaba _cmp["numero"], que
                                # para ese layout llega sin el punto de
                                # venta y termina rechazado más adelante
                                # por no tener el separador "-". Ver
                                # utils/validaciones_pre_bas.py para el
                                # detalle completo (caso real, reglas,
                                # idempotencia).
                                "numero_comprobante": combinar_numero_comprobante(
                                    _cmp.get("numero"), _cmp.get("punto_de_venta")
                                ),
                                "fecha_emision": _cmp.get("fecha_emision"),
                                "tipo_comprobante": _cmp.get("tipo"),
                                "subtipo_comprobante": _cmp.get("subtipo"),
                                "moneda": _cmp.get("moneda"),
                                "emisor_nombre": _emisor.get("nombre"),
                                "emisor_cuit": _emisor.get("id_fiscal"),
                                "receptor_nombre": _receptor.get("nombre"),
                                "receptor_cuit": _receptor.get("id_fiscal"),
                                "subtotal": _items_info.get("subtotal"),
                                "total": _items_info.get("total"),
                                "cae": _otros.get("CAE"),
                                "cae_vencimiento": _otros.get("vencimiento_CAE"),
                                "forma_pago": _otros.get("forma_pago"),
                                "iva_alicuota": _alicuota_iva,
                            }
                            _pb_invoice_record = self._pb_client.upsert_invoice(
                                {
                                    "process_id": process_id,
                                    **{k: v for k, v in _campos_extraidos.items() if v is not None},
                                    "sheets_saved": bool(saved),
                                    "status": "processing",
                                }
                            )
                            if _pb_invoice_record and _pb_invoice_record.get("id"):
                                self._pb_client.bulk_create_invoice_items(
                                    _pb_invoice_record["id"],
                                    [
                                        {
                                            "process_id": process_id,
                                            "linea": idx,
                                            "descripcion": d.get("descripcion"),
                                            "cantidad": d.get("cantidad"),
                                            "precio_unitario": d.get("precio_unitario"),
                                            "precio_total": d.get("precio_total"),
                                            "categoria": d.get("categoria"),
                                            # Sugerencia informativa para el dashboard --
                                            # NUNCA la fuente del CodigoItem real que se
                                            # manda a BAS (eso lo decide
                                            # procesar_factura_en_bas, más abajo, con el
                                            # mismo resolver). Si no se puede resolver un
                                            # ítem válido, queda vacío en vez de guardar
                                            # un valor inventado (P0-E, regla 5).
                                            "bas_codigo_item": resolver_codigo_item(
                                                categoria=d.get("categoria", ""),
                                                alicuota=_alicuota_iva,
                                                pb_client=self._pb_client,
                                            ).get("codigo_item")
                                            or "",
                                        }
                                        for idx, d in enumerate(_detalles, 1)
                                    ],
                                )
                            else:
                                app_logger.warning(
                                    f"[{process_id}] PocketBase: upsert_invoice no devolvió "
                                    "un record válido, se omiten los ítems y el estado BAS."
                                )
                        except Exception as e:
                            app_logger.warning(
                                f"[{process_id}] PocketBase: error persistiendo invoice/items: {e}"
                            )

                        # Integración con BAS (ERP): registra la factura de compra y
                        # best-effort intenta la orden de pago. Aislado a propósito:
                        # un fallo acá (incluido el bloqueador conocido de OrdenesPago)
                        # NO debe impedir que se suba a Drive ni se mande el email.
                        resultado_bas = self.procesar_factura_en_bas(
                            factura["data"], process_id
                        )
                        app_logger.info(
                            f"[{process_id}] Resultado integración BAS: {resultado_bas}"
                        )

                        # Persistencia en PocketBase del resultado de BAS. Aislado,
                        # mismo criterio que el resto de las llamadas a PocketBase.
                        # Requiere el id del record de "invoices" (relation
                        # requerida) -- si el paso anterior no lo consiguió, no hay
                        # forma de crear este record (PocketBase lo rechazaría de
                        # todos modos), así que se omite entero.
                        try:
                            if _pb_invoice_record and _pb_invoice_record.get("id"):
                                _cmp_bas = factura["data"].get("emisor_receptor", {}).get("comprobante", {})
                                _prefijo_ext, _numero_ext = _extraer_prefijo_numero_comprobante_externo(_cmp_bas)
                                _proveedor_info = resultado_bas.get("proveedor") or {}
                                _orden_pago_info = resultado_bas.get("orden_pago")
                                if _orden_pago_info is None:
                                    # Schema solo acepta pending/success/failed -- "no
                                    # intentado todavía" mapea a "pending".
                                    _orden_pago_status = "pending"
                                elif isinstance(_orden_pago_info, dict) and _orden_pago_info.get("_error"):
                                    _orden_pago_status = "failed"
                                else:
                                    _orden_pago_status = "success"
                                self._pb_client.upsert_bas_processing_status(
                                    process_id,
                                    invoice=_pb_invoice_record["id"],
                                    proveedor_resuelto=bool(resultado_bas.get("proveedor")),
                                    proveedor_codigo=_proveedor_info.get("codigo"),
                                    comprobante_prefijo=_prefijo_ext,
                                    comprobante_numero=_numero_ext,
                                    comprobante_registrado=bool(resultado_bas.get("comprobante")),
                                    orden_pago_status=_orden_pago_status,
                                    orden_pago_error=resultado_bas.get("error"),
                                )
                        except Exception as e:
                            app_logger.warning(
                                f"[{process_id}] PocketBase: error persistiendo bas_processing_status: {e}"
                            )

                        # Subir archivo a Google Drive
                        app_logger.info(f"[{process_id}] Iniciando subida a Google Drive para el archivo: {file_name}")
                        drive_file_id = self.subir_archivo_a_drive(
                            file_path=item["file_path"],
                            file_name=file_name,
                            mime_type=media_type
                        )
                        
                        if drive_file_id:
                            app_logger.info(f"[{process_id}] ✅ Archivo subido exitosamente a Drive. ID: {drive_file_id}")
                        else:
                            app_logger.error(f"[{process_id}] ❌ Falló la subida del archivo a Google Drive.")

                        # Cierra el ciclo de vida del record en PocketBase: recién acá
                        # se conoce drive_file_id, así que el upsert inicial (arriba)
                        # no podía incluirlo. Mismo aislamiento try/except de siempre.
                        try:
                            if _pb_invoice_record and _pb_invoice_record.get("id"):
                                # Recalculado acá (no reusado del try anterior)
                                # a propósito: ese try tiene su propio
                                # except que puede haber cortado antes de
                                # asignarlo -- no depender de leakage de
                                # variable entre bloques try separados.
                                _proveedor_info_invoice = resultado_bas.get("proveedor") or {}
                                self._pb_client.upsert_invoice(
                                    {
                                        "process_id": process_id,
                                        "drive_file_id": drive_file_id,
                                        "status": "completed",
                                        # Nuevo alcance 2026-08-10 -- ver
                                        # docstring de bas_registration_status
                                        # en la migración
                                        # 1784500000_add_bas_traceability_and_state.js.
                                        "bas_registration_status": resultado_bas.get("bas_registration_status") or "",
                                        "bas_provider": _proveedor_info_invoice.get("pb_id") or "",
                                    }
                                )
                        except Exception as e:
                            app_logger.warning(
                                f"[{process_id}] PocketBase: error actualizando drive_file_id/status: {e}"
                            )

                        html_body = self.generar_html_factura(factura["data"])

                        self.enviar_email(from_email, subject_for_file, html_body)

                        result = {
                            "id": process_id,
                            "file_name": item["file_name"],
                            "factura": factura,
                            "saved": saved,
                            "saved_items": saved_items,
                            "bas": resultado_bas,
                            "drive_file_id": drive_file_id,
                            "status": "procesada",
                            "success": True,
                        }

                        app_logger.info(
                            f"[{process_id}] Enviando webhook para {file_name}"
                        )
                        await self.fire_webhook(result)

                        processed_count += 1
                        app_logger.info(
                            f"[{process_id}] ✅ Archivo {file_name} procesado exitosamente ({processed_count}/{total_items})"
                        )

                    except Exception as e:
                        app_logger.error(
                            f"[{process_id}] ❌ Error procesando {file_name}: {e}"
                        )
                        await self.fire_webhook(
                            {
                                "process_id": process_id,
                                "file_name": item["file_name"],
                                "error": str(e),
                                "status": "error",
                                "success": False,
                            }
                        )

                # Cleanup
                app_logger.info(
                    f"[{process_id}] Limpiando directorio temporal: {os.path.dirname(job['temp_dir'])}"
                )
                shutil.rmtree(os.path.dirname(job["temp_dir"]))
                app_logger.info(
                    f"[{process_id}] 🎉 Job completado - {processed_count}/{total_items} archivos procesados exitosamente"
                )

                try:
                    # processed_count no es un campo del schema de processing_jobs
                    # (ver contrato) -- se omite, mismo criterio que arriba.
                    self._pb_client.update_processing_job(
                        process_id,
                        status="done",
                        from_email=from_email,
                        subject=subject,
                        file_name=file_name,
                    )
                except Exception as e:
                    app_logger.warning(
                        f"[{process_id}] PocketBase: error marcando processing_job done: {e}"
                    )

            except Exception as e:
                app_logger.error(f"[{process_id}] ❌ Error crítico en job: {e}")
                try:
                    self._pb_client.update_processing_job(
                        process_id,
                        status="error",
                        error_message=str(e),
                        from_email=from_email,
                        subject=subject,
                        file_name=file_name,
                    )
                except Exception as pb_e:
                    app_logger.warning(
                        f"[{process_id}] PocketBase: error marcando processing_job error: {pb_e}"
                    )
            finally:
                if process_id in self.active_comparisons:
                    del self.active_comparisons[process_id]
                self.job_queue.task_done()

    # Envía resultados vía webhook
    async def fire_webhook(self, data):
        # requests.post es bloqueante -- antes corría directo dentro de un
        # método async, así que su timeout=10 bloqueaba el ÚNICO hilo del
        # event loop (droplet de 1 vCPU) hasta por 10s por cada llamada. El
        # rediseño de ZIP la invoca ahora hasta N veces por ZIP (una por
        # factura), secuencial dentro de la misma task -- sin este fix, un
        # webhook externo lento podía bloquear el servicio ENTERO (no solo
        # el ZIP en curso) hasta N×10s. to_thread saca la llamada del event
        # loop sin cambiar el comportamiento/contrato de la función.
        try:
            res = await asyncio.to_thread(
                requests.post,
                self.webhook_url,
                json=data,
                timeout=10,
            )
            app_logger.info(f"Webhook status code: {res.status_code}")
            return True
        except Exception as e:
            app_logger.error(f"An error occurred while sending webhook: {e}")
            return False

    # Procesa un item según su tipo (imagen o PDF)
    async def process_item(self, item: QueueItem):
        try:
            async with self.semaphore:
                app_logger.info(f"Procesando item {item}")
                if item["media_type"].startswith("image"):
                    respuestas = await self.run_image_toolchain(item)
                elif item["media_type"] == "application/pdf":
                    respuestas = await self.run_pdf_toolchain(item)

                app_logger.info(f"Respuestas: {respuestas}")

                app_logger.info("Tenemos las respuestas")

                factura = orchestrator.formatear_factura(respuestas["data"])

                # Guarda en sheets y formatea respuesta
                saved_sheet = orchestrator.guardar_factura_completa_en_sheets(
                    factura["data"]
                )
                app_logger.info(
                    "Guardamos la factura"
                    if saved_sheet
                    else "No guardamos la factura, error"
                )
                
                factura["id"] = item["process_id"]
                factura["saved_sheet"] = bool(saved_sheet)
                factura["error"] = ""

                return factura
        except Exception as e:
            app_logger.error(f"An error occurred while processing item: {e}")
            raise ValueError(f"Error processing item: {e}")

    def subir_archivo_a_drive(self, file_path: str, file_name: str, mime_type: str):
        """
        Sube un archivo a Google Drive usando la cuenta de servicio.
        """
        try:
            app_logger.info(f"Preparando credenciales para subir {file_name} a Google Drive...")
            scopes = ["https://www.googleapis.com/auth/drive.file"]
            
            client_email = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
            private_key = os.getenv("GOOGLE_PRIVATE_KEY")
            folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
            
            if not client_email or not private_key or not folder_id:
                app_logger.error("❌ Faltan credenciales o el ID de la carpeta de Google Drive (GOOGLE_DRIVE_FOLDER_ID).")
                return None
                
            private_key = private_key.replace("\\n", "\n")

            credentials = service_account.Credentials.from_service_account_info(
                {
                    "type": "service_account",
                    "client_email": client_email,
                    "private_key": private_key,
                    "token_uri": "https://accounts.google.com/o/oauth2/token",
                },
                scopes=scopes,
            )

            app_logger.info("Conectando con la API de Google Drive...")
            service = build("drive", "v3", credentials=credentials)
            
            file_metadata = {
                "name": file_name,
                "parents": [folder_id]
            }
            
            app_logger.info(f"Iniciando transferencia del archivo {file_path}...")
            media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
            
            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id",
                supportsAllDrives=True
            ).execute()
            
            return file.get("id")
            
        except Exception as e:
            app_logger.error(f"❌ Excepción al subir archivo a Google Drive: {str(e)}")
            return None

    # Hace requests a la API con reintentos
    async def make_api_request(
        self, url: str, headers: Dict, data: Dict, process_id: str, retries: int = 5
    ) -> Optional[Dict]:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            for i in range(retries):
                try:
                    async with session.post(
                        url, headers=headers, json=data
                    ) as response:
                        if response.status == 200:
                            return await response.json()
                        elif response.status in [429, 529, 503]:
                            sleep_time = 15 * (i + 1)  # Espera incremental en segundos
                            app_logger.warning(
                                f"API request failed with status {response.status}. Retrying in {sleep_time} seconds..."
                            )
                            await asyncio.sleep(sleep_time)
                        else:
                            app_logger.error(
                                f"API request failed with status {response.status} - {await response.text()}"
                            )
                            raise ValueError(
                                f"Request failed with status {response.status}"
                            )
                except aiohttp.ClientError as e:
                    raise ValueError(f"Request error: {str(e)}")
        raise ValueError("Max retries exceeded.")

    async def tool_handler(
        self,
        tools: list,
        messages: list,
        tool_name: str,
        process_id: str,
        model: str = "gemini-3.5-flash",
        max_retries: int = 6,
    ):
        url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": {
                "type": "function",
                "function": {"name": tool_name},
            },
        }
        schema = next(
            (
                tool["function"]["parameters"]
                for tool in tools
                if tool["function"]["name"] == tool_name
            ),
            None,
        )
        tool_output = None
        for attempt in range(0, max_retries):
            try:
                response = await self.make_api_request(
                    url=url,
                    headers=headers,
                    data=data,
                    process_id=process_id,
                )

                if response["choices"][0]["message"]["tool_calls"][0]["function"][
                    "arguments"
                ]:
                    tool_output = json.loads(
                        response["choices"][0]["message"]["tool_calls"][0]["function"][
                            "arguments"
                        ]
                    )
                else:
                    raise ValueError("No tool output")

                usage = response["usage"]

                validate(instance=tool_output, schema=schema)
                app_logger.info("✅ Validation passed.")
                return {
                    "content": [
                        {
                            "name": tool_name,
                            "input": tool_output,
                        }
                    ],
                    "usage": {
                        "input_tokens": usage["prompt_tokens"],
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": usage["completion_tokens"],
                        "service_tier": "standard",
                    },
                }
                # return {"content": tool_output, "usage": usage, "tool_name": tool_name}
            except ValidationError as e:
                # Notifica error de validación
                app_logger.error(f"❌ Validation error for '{tool_name}': {e.message}")
                # error_message = {
                #     "tool_name": tool_name,
                #     "tool_output": tool_output,
                #     "tool": next(
                #         (tool for tool in tools if tool["name"] == tool_name), None
                #     ),
                #     "error": e.message,
                # }
                # error_response = requests.post(
                #     self.WEBHOOK_URL,
                #     json=error_message,
                #     timeout=10,
                # )
                # app_logger.error(f"Webhook Status Code: {error_response.status_code}")
                # Bug real (18-jul-2026): esta condición usaba "< max_retries" en
                # vez de "< max_retries - 1", así que con range(0, max_retries)
                # (attempt siempre 0..max_retries-1) nunca daba False -- el except
                # de abajo con el ValueError "Max retries exceeded" era código
                # muerto, jamás se ejecutaba. Al agotarse los intentos la función
                # caía al final del for sin return -> devolvía None en silencio,
                # y el caller (formatear_factura) reventaba con un
                # AttributeError críptico ("'NoneType' object has no attribute
                # 'get'") en vez de un mensaje real. Confirmado en vivo con una
                # imagen corrupta real.
                if attempt < max_retries - 1:
                    app_logger.warning(
                        f"🔄 Retrying... (intento {attempt + 2} de {max_retries})"
                    )
                    # Best-effort, mismo patrón que el resto del pipeline: no
                    # debe frenar el reintento si PocketBase no responde. Nota:
                    # las 3 tools corren en paralelo (ver run_image_toolchain),
                    # cada una con su propio contador -- este campo es un
                    # indicador agregado aproximado para la UI ("va por el
                    # intento N"), no un progreso exacto por tool.
                    try:
                        self._pb_client.upsert_invoice(
                            {
                                "process_id": process_id,
                                "status": "processing",
                                "extraction_attempt": attempt + 2,
                            }
                        )
                    except Exception:
                        pass
                    continue
                else:
                    app_logger.error("❌ Max retries exceeded.")
                    raise ValueError(
                        f"Max retries exceeded for '{tool_name}'. Last error: {e.message}"
                    )
            except Exception as e:
                # Notifica error general
                app_logger.error(f"❌ Unexpected error: {e}")
                # error_message = {
                #     "tool_name": tool_name,
                #     "tool_output": tool_output,
                #     "tool": next(
                #         (tool for tool in tools if tool["name"] == tool_name), None
                #     ),
                #     "error": e.message,
                # }
                # error_response = requests.post(
                #     self.WEBHOOK_URL,
                #     json=error_message,
                #     timeout=10,
                # )
                # app_logger.error(f"Webhook Status Code: {error_response.status_code}")
                # Mismo fix de off-by-one que en el except de ValidationError
                # de arriba, ver ese comentario.
                if attempt < max_retries - 1:
                    app_logger.warning(
                        f"🔄 Retrying... (intento {attempt + 2} de {max_retries})"
                    )
                    try:
                        self._pb_client.upsert_invoice(
                            {
                                "process_id": process_id,
                                "status": "processing",
                                "extraction_attempt": attempt + 2,
                            }
                        )
                    except Exception:
                        pass
                    continue
                else:
                    app_logger.error("❌ Max retries exceeded.")
                    # e.message NO existe en una Exception genérica (solo en
                    # ValidationError, ver el except de arriba) -- este
                    # AttributeError también era código muerto hasta el fix
                    # del off-by-one; usar str(e) acá.
                    raise ValueError(
                        f"Max retries exceeded for '{tool_name}'. Last error: {str(e)}"
                    )

    # Procesa imágenes con Claude Vision
    async def run_image_toolchain(
        self,
        item: QueueItem,
    ):
        # Categorías vigentes (PocketBase, cacheadas 60s -- ver bas_config.py)
        # en vez del enum estático de antes: agregar/sacar una categoría es
        # un cambio de datos en /category-map, no un deploy.
        tools_standard = build_tools()

        # Convierte imagen a base64
        image_file = Path(item["file_path"])
        base64_string = base64.b64encode(image_file.read_bytes()).decode()

        response = await self.tool_handler(
            tools=[tool["data"] for tool in tools_standard],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": tools_standard[0]["prompt"]},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{item['media_type']};base64,{base64_string}"
                            },
                        },
                    ],
                }
            ],
            tool_name=tools_standard[0]["data"]["function"]["name"],
            process_id=item["process_id"],
        )

        # Procesa con resto de herramientas en paralelo
        tasks = []
        for tool in tools_standard[1:]:
            tool_res = self.tool_handler(
                tools=[tool["data"] for tool in tools_standard],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{item['media_type']};base64,{base64_string}"
                                },
                            },
                            {"type": "text", "text": tool["prompt"]},
                        ],
                    }
                ],
                tool_name=tool["data"]["function"]["name"],
                process_id=item["process_id"],
            )
            tasks.append(tool_res)
        results = await asyncio.gather(*tasks)
        respuestas = [response] + results
        item["data"] = respuestas
        return item

    # Procesa PDFs con Claude
    async def run_pdf_toolchain(
        self,
        item: QueueItem,
    ):
        # Ver comentario equivalente en run_image_toolchain.
        tools_standard = build_tools()

        doc = fitz.open(item["file_path"])
        base64_images = []
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=150)  # convertimos a imagen

            # Convertimos el pixmap a imagen PIL
            img = Image.open(io.BytesIO(pix.tobytes("png")))

            # Guardamos en memoria y convertimos a base64
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            base64_images.append(img_base64)

        image_messages = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_b64}"},
            }
            for img_b64 in base64_images
        ]

        response = await self.tool_handler(
            tools=[tool["data"] for tool in tools_standard],
            messages=[
                {
                    "role": "user",
                    "content": [
                        *image_messages,
                        {"type": "text", "text": tools_standard[0]["prompt"]},
                    ],
                }
            ],
            tool_name=tools_standard[0]["data"]["function"]["name"],
            process_id=item["process_id"],
        )

        # Procesa con resto de herramientas en paralelo
        tasks = []
        for tool in tools_standard[1:]:
            tool_res = self.tool_handler(
                tools=[tool["data"] for tool in tools_standard],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            *image_messages,
                            {"type": "text", "text": tool["prompt"]},
                        ],
                    }
                ],
                tool_name=tool["data"]["function"]["name"],
                process_id=item["process_id"],
            )
            tasks.append(tool_res)
        results = await asyncio.gather(*tasks)
        respuestas = [response] + results
        item["data"] = respuestas
        return item

    # Guarda los datos de la factura en Google Sheets
    def guardar_factura_completa_en_sheets(
        self,
        factura_data: dict,  # El input ahora es el diccionario formateado
        range_name: str = "A2:M2",  # Es mejor especificar la hoja, ej: 'Facturas!A1'
    ):
        """
        Toma los datos de una factura ya formateada y los guarda como una fila en Google Sheets.
        """
        try:
            app_logger.info("Preparando datos para guardar en Google Sheets...")

            sheet_id = os.getenv("SHEET_ID_2")
            if not sheet_id:
                app_logger.error("No se encontró el ID de la hoja de cálculo.")
                return None

            # --- Lógica de Extracción de Datos (Ahora mucho más simple) ---

            # Obtener los bloques de datos principales. Usamos .get({}, {}) para evitar errores.
            emisor_receptor = factura_data.get("emisor_receptor", {})
            items_info = factura_data.get("items", {})
            impuestos_info = factura_data.get("impuestos", {})

            # Extraer sub-bloques de datos
            comprobante = emisor_receptor.get("comprobante", {})
            emisor = emisor_receptor.get("emisor", {})
            receptor = emisor_receptor.get("receptor", {})
            otros = emisor_receptor.get("otros", {})

            # Extraer detalles de los items
            detalles = items_info.get("detalles", [])
            descripcion_items = "; ".join(
                [
                    f"Desc: {d.get('descripcion', '')}, Cant: {d.get('cantidad', '')}, Total: ${d.get('precio_total', '')}"
                    for d in detalles
                ]
            )
            subtotal = items_info.get("subtotal", "")
            total = items_info.get("total", "")
            observaciones = items_info.get("observaciones", "")

            # Extraer impuestos y retenciones
            impuestos = impuestos_info.get("impuestos", [])
            retenciones = impuestos_info.get("retenciones", [])

            # --- Preparación de la Fila (La lógica es casi la misma) ---
            fila_para_sheets = [
                # Datos del Comprobante
                comprobante.get("tipo", ""),
                comprobante.get("subtipo", ""),
                comprobante.get("jurisdiccion_fiscal", ""),
                comprobante.get("punto_de_venta", ""),
                comprobante.get("numero", ""),
                comprobante.get("fecha_emision", ""),
                comprobante.get("moneda", ""),
                # Datos del Emisor
                emisor.get("nombre", ""),
                emisor.get("id_fiscal", ""),
                emisor.get("condicion_iva", ""),
                emisor.get("direccion", ""),
                # Datos del Receptor
                receptor.get("nombre", ""),
                receptor.get("id_fiscal", ""),
                receptor.get("condicion_iva", ""),
                receptor.get("direccion", ""),
                # Detalles de la Factura
                descripcion_items,
                subtotal,
                formatear_impuestos(impuestos),
                formatear_retenciones(retenciones),
                total,
                observaciones,
                # Otros datos
                otros.get("CAE", ""),
                otros.get("vencimiento_CAE", ""),
                otros.get("forma_pago", ""),
            ]

            # Envolvemos la fila en otra lista porque la API espera una lista de filas
            valores_para_api = [fila_para_sheets]

            app_logger.info("\nFila a enviar a Google Sheets:")
            app_logger.info(valores_para_api)

            # --- Conexión y Escritura en Google Sheets (Sin cambios) ---

            # NOTA: La siguiente sección es para la ejecución real.
            # Si solo quieres probar la lógica de formateo, puedes detenerte aquí.

            app_logger.info("\nConectando con Google Sheets API...")
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]

            # Carga de credenciales desde variables de entorno (o un archivo de secretos)
            # service_account_info = json.loads(
            #     os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
            # )
            client_email = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
            if not client_email:
                app_logger.error("No se encontró el correo electrónico del servicio.")
                return None
            private_key = os.getenv("GOOGLE_PRIVATE_KEY")
            if not private_key:
                app_logger.error("No se encontró la clave privada.")
                return None
            private_key = private_key.replace("\\n", "\n")

            credentials = service_account.Credentials.from_service_account_info(
                {
                    "type": "service_account",
                    "client_email": client_email,
                    "private_key": private_key,
                    "token_uri": "https://accounts.google.com/o/oauth2/token",
                },
                scopes=scopes,
            )

            service = build("sheets", "v4", credentials=credentials)
            sheet = service.spreadsheets()

            body = {"values": valores_para_api}

            # Usamos append para añadir la fila al final de la tabla
            response = (
                sheet.values()
                .append(
                    spreadsheetId=sheet_id,
                    range=range_name,
                    valueInputOption="USER_ENTERED",  # USER_ENTERED interpreta los datos como si los escribiera un usuario
                    insertDataOption="INSERT_ROWS",
                    body=body,
                )
                .execute()
            )
            app_logger.info("¡Factura guardada con éxito en Google Sheets!")
            app_logger.info(response)
            return True

        except Exception as e:
            app_logger.error(f"Error al guardar la factura en Google Sheets: {e}")
            # En caso de error, es útil imprimir la fila que se intentó guardar
            if "fila_para_sheets" in locals():
                app_logger.error("Datos que fallaron:", fila_para_sheets)
            return False

    # === Persistencia de ítems (una fila por ítem en su propia pestaña) ===

    def _get_sheets_service(self):
        """Construye el cliente de Google Sheets a partir de las credenciales de servicio."""
        try:
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            client_email = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
            private_key = os.getenv("GOOGLE_PRIVATE_KEY")
            if not client_email or not private_key:
                app_logger.error("Faltan credenciales de Google para conectar con Sheets.")
                return None
            private_key = private_key.replace("\\n", "\n")
            credentials = service_account.Credentials.from_service_account_info(
                {
                    "type": "service_account",
                    "client_email": client_email,
                    "private_key": private_key,
                    "token_uri": "https://accounts.google.com/o/oauth2/token",
                },
                scopes=scopes,
            )
            return build("sheets", "v4", credentials=credentials)
        except Exception as e:
            app_logger.error(f"Error al construir el servicio de Google Sheets: {e}")
            return None

    def _asegurar_pestana_items(self, service, sheet_id: str, tab_name: str) -> bool:
        """
        Garantiza que la pestaña de ítems exista (con encabezados).
        Si no existe, la crea. Cachea el resultado para no repetir la verificación.
        """
        cache_key = f"{sheet_id}:{tab_name}"
        if cache_key in self._ensured_item_tabs:
            return True

        metadata = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        existentes = {
            s["properties"]["title"] for s in metadata.get("sheets", [])
        }

        if tab_name not in existentes:
            app_logger.info(f"La pestaña '{tab_name}' no existe; creándola...")
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
            ).execute()
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=f"{tab_name}!A1",
                valueInputOption="RAW",
                body={"values": [ITEMS_SHEET_HEADERS]},
            ).execute()
            app_logger.info(f"✅ Pestaña '{tab_name}' creada con encabezados.")

        self._ensured_item_tabs.add(cache_key)
        return True

    def _es_descuento(self, item: dict) -> bool:
        """
        Determina si una línea de `detalles` es en realidad un descuento/bonificación
        (no un ítem real). Usa dos señales: monto negativo o descripción con palabra
        clave de descuento.
        """
        # Señal 1: importe negativo (el descuento se resta del total).
        for campo in ("precio_total", "precio_unitario", "cantidad"):
            valor = item.get(campo)
            if isinstance(valor, (int, float)) and valor < 0:
                return True

        # Señal 2: la descripción coincide con una palabra clave de descuento.
        descripcion = _normalizar_texto(item.get("descripcion", ""))
        return any(kw in descripcion for kw in DISCOUNT_KEYWORDS)

    def _construir_filas_items(self, factura_data: dict, process_id: str, timestamp: str):
        """
        Función pura: transforma los datos formateados de una factura en una lista
        de filas (una por ítem) para la pestaña de ítems. Devuelve [] si no hay ítems.
        Las líneas de descuento/bonificación se omiten (ver _es_descuento).
        """
        emisor_receptor = factura_data.get("emisor_receptor", {})
        comprobante = emisor_receptor.get("comprobante", {})
        emisor = emisor_receptor.get("emisor", {})
        receptor = emisor_receptor.get("receptor", {})
        detalles = factura_data.get("items", {}).get("detalles", []) or []

        filas = []
        descartados = 0
        linea = 0
        for item in detalles:
            if self._es_descuento(item):
                descartados += 1
                continue
            linea += 1
            filas.append(
                [
                    process_id or "",
                    timestamp,
                    comprobante.get("numero", ""),
                    comprobante.get("fecha_emision", ""),
                    comprobante.get("tipo", ""),
                    emisor.get("nombre", ""),
                    emisor.get("id_fiscal", ""),
                    receptor.get("nombre", ""),
                    receptor.get("id_fiscal", ""),
                    comprobante.get("moneda", ""),
                    linea,
                    item.get("descripcion", ""),
                    item.get("cantidad", ""),
                    item.get("precio_unitario", ""),
                    item.get("precio_total", ""),
                ]
            )

        if descartados:
            app_logger.info(
                f"Pestaña de ítems: se omitieron {descartados} línea(s) de "
                f"descuento/bonificación."
            )
        return filas

    def guardar_items_en_sheets(
        self,
        factura_data: dict,
        process_id: str,
        tab_name: str = None,
    ) -> bool:
        """
        Guarda los ítems de una factura como filas individuales en una pestaña aparte,
        manteniendo el enlace con la factura (process_id + clave compuesta).

        Aislado a propósito: cualquier fallo aquí NO debe afectar el guardado de la
        factura principal, el email ni el webhook. Devuelve True/False.
        """
        try:
            tab_name = tab_name or os.getenv("SHEET_TAB_ITEMS", "Detalle_Items")
            sheet_id = os.getenv("SHEET_ID_2")
            if not sheet_id:
                app_logger.error("No se encontró SHEET_ID_2 para guardar los ítems.")
                return False

            timestamp = datetime.datetime.utcnow().isoformat() + "Z"
            filas = self._construir_filas_items(factura_data, process_id, timestamp)
            if not filas:
                app_logger.info(
                    "La factura no tiene ítems; no se escribe nada en la pestaña de ítems."
                )
                return True

            service = self._get_sheets_service()
            if service is None:
                return False

            self._asegurar_pestana_items(service, sheet_id, tab_name)

            response = (
                service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=sheet_id,
                    range=f"{tab_name}!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": filas},
                )
                .execute()
            )
            app_logger.info(
                f"✅ {len(filas)} ítem(s) guardados en la pestaña '{tab_name}'."
            )
            app_logger.info(response)
            return True

        except Exception as e:
            app_logger.error(f"❌ Error al guardar los ítems en Google Sheets: {e}")
            if "filas" in locals():
                app_logger.error(f"Filas de ítems que fallaron: {filas}")
            return False

    # === Integración con BAS (ERP) ===

    def _buscar_proveedor_bas(self, cuit: str, razon_social: str = ""):
        """
        Busca un proveedor en BAS por CUIT. SOLO LECTURA -- nunca crea ni
        modifica el maestro de proveedores de BAS. Alcance confirmado
        2026-08-10: BAS es la única fuente de verdad para proveedores; si
        el CUIT no existe, el flujo debe quedar bloqueado
        (bas_registration_status="awaiting_provider_match") hasta que un
        humano lo cree a mano en BAS -- nunca se da de alta desde acá.

        Reemplaza a `_obtener_o_verificar_proveedor_bas`, que buscaba-o-creaba
        y además "reparaba" la cuenta corriente de proveedores existentes
        (ver utils/bas.py: verificar_o_dar_de_alta_proveedor y
        asegurar_cuenta_corriente_proveedor, ambas DEPRECATED, no borradas,
        sin ningún caller en producción a partir de este cambio).

        `razon_social` ya no se usa para dar de alta (no hay alta) -- se
        conserva solo para loguearla en el caso "no encontrado", así quien
        lea el log sabe qué nombre debería tener el proveedor a crear en
        BAS.

        Devuelve un dict con forma BAS ("Codigo", "RazonSocial", ...) más
        dos claves internas (prefijo "_", mismo criterio que ya usaba el
        código viejo con "_nuevo"): "_nuevo" (siempre False -- ya no hay
        alta automática, se mantiene la clave por compatibilidad de forma
        con callers existentes) y "_pb_id" (el id del record de PocketBase
        "bas_providers" que cachea este proveedor -- lo necesita el caller
        para setear invoices.bas_provider, una relation). None si el CUIT
        viene vacío o si no existe en BAS.
        """
        cuit_normalizado = "".join(c for c in (cuit or "") if c.isdigit())
        if not cuit_normalizado:
            return None
        if cuit_normalizado in self._proveedores_bas_cache:
            return self._proveedores_bas_cache[cuit_normalizado]

        # Cache persistente de 2do nivel (sobrevive un restart). Aislado: si
        # PocketBase falla/no está configurado, no debe impedir resolver el
        # proveedor contra BAS -- solo se pierde el ahorro de la consulta.
        try:
            proveedor_cacheado = self._pb_client.get_provider_cache(cuit_normalizado)
        except Exception as e:
            proveedor_cacheado = None
            app_logger.warning(f"PocketBase: error consultando get_provider_cache({cuit_normalizado}): {e}")
        if proveedor_cacheado is not None:
            self._proveedores_bas_cache[cuit_normalizado] = proveedor_cacheado
            return proveedor_cacheado

        proveedor = self._bas_client.buscar_proveedor_por_cuit(cuit_normalizado)
        if proveedor is None:
            app_logger.warning(
                f"BAS: proveedor CUIT {cuit_normalizado} (razón social extraída: "
                f"{razon_social!r}) no existe en el maestro de BAS. No se crea "
                "automáticamente -- requiere alta manual en BAS."
            )
        else:
            proveedor = dict(proveedor)
            proveedor["_nuevo"] = False
            try:
                registro_pb = self._pb_client.set_provider_cache(cuit_normalizado, proveedor)
                proveedor["_pb_id"] = (registro_pb or {}).get("id")
            except Exception as e:
                app_logger.warning(f"PocketBase: error en set_provider_cache({cuit_normalizado}): {e}")
                proveedor["_pb_id"] = None
        self._proveedores_bas_cache[cuit_normalizado] = proveedor
        return proveedor

    def procesar_factura_en_bas(
        self,
        factura_data: dict,
        process_id: str,
        dry_run: bool = True,
        monto_override: Optional[float] = None,
    ):
        """
        Orquesta el registro de la factura y (best-effort) la orden de pago en BAS.

        Aislado a propósito, igual que guardar_items_en_sheets: cualquier fallo
        (incluido el bloqueador conocido de OrdenesPago, ver
        docs/bas-orden-de-pago-research.md) se loguea y se devuelve en el
        resultado, pero NUNCA relanza -- no debe romper Sheets/Drive/email.

        `dry_run=True` (default) arma los payloads y consulta BAS pero NO
        escribe. Pasar dry_run=False solo tras validar en la verificación
        end-to-end -- ver plan de integración.

        `monto_override`: para importar facturas REALES sin el impacto
        contable real (ver Invoicy/docs/plan-importacion-masiva-facturas.md,
        Fase 5/6) -- misma regla de seguridad ya establecida para cualquier
        prueba real contra BAS (Total=1). Cuando se pasa, se descarta la
        lista de ítems extraída y se manda un único ítem sintético por
        `monto_override`, preservando la categoría del primer ítem real (si
        hay) para seguir probando la resolución de CodigoItem de verdad.
        Proveedor/CUIT/número de comprobante externo NUNCA se tocan -- son
        la identidad real del documento, no el impacto contable.
        """
        resultado = {
            "proveedor": None,
            "comprobante": None,
            "orden_pago": None,
            "error": None,
            # Nuevo alcance 2026-08-10 -- estado de registro que los callers
            # persisten en invoices.bas_registration_status (campo agregado
            # en la migración 1784500000_add_bas_traceability_and_state.js).
            # None acá = no se llegó ni a intentar resolver el proveedor
            # (ej. sin CUIT, ver más abajo).
            "bas_registration_status": None,
        }
        try:
            emisor_receptor = factura_data.get("emisor_receptor", {})
            emisor = emisor_receptor.get("emisor", {})
            comprobante = emisor_receptor.get("comprobante", {})
            otros = emisor_receptor.get("otros", {})
            items_info = factura_data.get("items", {})
            detalles = items_info.get("detalles", []) or []
            total = items_info.get("total")
            impuestos_info = factura_data.get("impuestos", {})
            alicuota_iva = _extraer_alicuota_iva(impuestos_info.get("impuestos", []))

            if monto_override is not None:
                # OJO al leer este log: `total` acá puede ya venir pisado
                # por _aplicar_monto_override (llamado antes, en
                # _procesar_imagen_o_pdf_impl) -- este mensaje NO garantiza
                # mostrar el monto original del documento, solo confirma
                # que ESTE registro en BAS usa monto_override, no lo que
                # sea que haya en `total` en este punto.
                app_logger.info(
                    f"[{process_id}] BAS: monto_override activo -- se registra con Total={monto_override}."
                )
                total = monto_override

            cuit_emisor = emisor.get("id_fiscal", "")
            if not cuit_emisor:
                resultado["error"] = "Sin CUIT de emisor; no se puede resolver proveedor en BAS."
                app_logger.warning(f"[{process_id}] BAS: {resultado['error']}")
                return resultado

            proveedor = self._buscar_proveedor_bas(cuit_emisor, emisor.get("nombre", ""))
            if proveedor is None:
                # No se crea automáticamente (alcance confirmado 2026-08-10)
                # -- este NO es el mismo caso que antes ("no se pudo
                # resolver/crear"): acá está garantizado que no existe en
                # BAS, hay que darlo de alta a mano y reintentar (ver
                # endpoint recheck-provider, P0-C).
                resultado["error"] = (
                    f"Proveedor con CUIT {cuit_emisor} no existe en BAS. Invoicy no "
                    "crea proveedores automáticamente -- debe darse de alta "
                    "manualmente en BAS y luego reintentar."
                )
                resultado["bas_registration_status"] = "awaiting_provider_match"
                app_logger.warning(f"[{process_id}] BAS: {resultado['error']}")
                return resultado
            resultado["proveedor"] = {
                "codigo": proveedor.get("Codigo"),
                "nuevo": proveedor.get("_nuevo"),
                "pb_id": proveedor.get("_pb_id"),
            }
            # El Servicio/CodigoItem real (catálogo BAS, ver
            # utils/bas_items_sync.py) se resuelve y VALIDA acá (P0-E,
            # 2026-08-12, ver utils/bas_item_resolver.resolver_codigo_item).
            # Estado por default hasta confirmar que se pudo resolver un
            # ítem válido para TODAS las líneas: "esperando que se
            # elija/confirme el servicio", no "listo para registrar" --
            # si la resolución falla más abajo, la factura queda en este
            # estado (regla 4 del alcance: nunca construir un payload con
            # un CodigoItem inventado o faltante).
            resultado["bas_registration_status"] = "awaiting_service_selection"

            # Total/TotalGravado/TotalIva/CodigoItem anclados a `total`
            # (el valor real extraído por Gemini, o monto_override si está
            # seteado -- ya resuelto más arriba) -- NUNCA a la suma de
            # `detalles`. Decisión de arquitectura 2026-08-19, misma
            # función que crear_orden_pago/registrar_comprobante -- ver
            # utils/bas_payload.py. Reemplaza las dos ramas viejas
            # (monto_override / detalles) que existían acá: la elección de
            # categoría "dominante" (o el catch-all) cubre ambos casos sin
            # duplicar lógica, y ya no bloquea la factura entera si algún
            # ítem no resuelve -- este flujo siempre corre en dry_run
            # (nunca escribe nada real), así que antes bloqueaba una
            # ingesta automática entera por un solo ítem sin categoría,
            # sin necesidad real de hacerlo.
            #
            # Con monto_override, la alícuota se fuerza a 0 -- mismo
            # criterio que ya existía (monto de prueba sintético para
            # importación masiva, sin impacto contable real, ver docstring
            # de monto_override arriba).
            alicuota_para_bas = 0 if monto_override is not None else alicuota_iva
            try:
                totales_e_items = construir_comprobante_totales_e_items(
                    total_bruto=total,
                    alicuota_iva=alicuota_para_bas,
                    items=detalles,
                    pb_client=self._pb_client,
                )
            except ValueError as e:
                resultado["error"] = str(e)
                app_logger.warning(f"[{process_id}] BAS: {resultado['error']}")
                return resultado
            if totales_e_items["fallback_catch_all"]:
                app_logger.info(
                    f"[{process_id}] BAS: ningún ítem resolvió categoría -- se usa "
                    f"el catch-all ({totales_e_items['Items'][0]['CodigoItem']})."
                )

            # La factura ya está lista para el registro real (P0-F):
            # totales_e_items siempre resuelve un CodigoItem válido (real
            # o catch-all) o levanta ValueError arriba -- nunca llega acá
            # con un payload a medio construir. "ready_to_register" es el
            # valor reservado exactamente para esto desde P0-A (ver
            # migración 1784500000_add_bas_traceability_and_state.js).
            resultado["bas_registration_status"] = "ready_to_register"

            # Número de comprobante externo: "PPPPP-NNNNNNNN" -> prefijo/numero.
            # (misma lógica que _extraer_prefijo_numero_comprobante_externo,
            # reusada acá en vez de duplicada para no perder el fix de
            # combinar_numero_comprobante si diverge más adelante -- este
            # `comprobante` es el dict crudo de Gemini, con "punto_de_venta"
            # disponible, así que el helper arma bien el prefijo aunque el
            # documento los imprima como dos campos separados).
            prefijo_externo, numero_externo = _extraer_prefijo_numero_comprobante_externo(comprobante)

            comprobante_compra_payload = {
                "Comprobante": "MA",
                "Prefijo": BAS_PREFIJO_TALONARIO_MA,
                # "Fecha" es la fecha de ASIENTO en el subdiario de BAS, no la
                # fecha del documento -- usar fecha_emision real rompe con 409
                # "fecha anterior al cierre operativo del subdiario"
                # (NUETRANSAC/SP_ICR_COMPROB_COMPRA) en cuanto la factura tiene
                # más de un período contable de atraso (ej. Litoral Gas,
                # emitida 2025-05-21, registrada recién el 2026-07-21). La
                # fecha real del documento va aparte en FechaComprobanteExterno
                # (no participa de este chequeo, solo de la búsqueda por
                # ConsultaComprobantesExternos). "hoy" en huso ARGENTINO
                # (fecha_hoy_bas(), NO datetime.date.today() -- ver
                # utils/bas_config.py:ZONA_HORARIA_BAS) siempre cae en el
                # período contable abierto, sea cual sea.
                "Fecha": fecha_hoy_bas().isoformat(),
                # Total/TotalGravado/TotalIva anclados a `total` (ver
                # utils/bas_payload.py) -- garantizan Total == TotalGravado +
                # TotalIva por construcción, la regla exacta de
                # SP_VALIDA_TOTALES que causó el incidente de MEDINA
                # (2026-08-04, ver docs/incidente-2026-08-04-pagos-solo-
                # neto.md).
                "Total": totales_e_items["Total"],
                "TotalGravado": totales_e_items["TotalGravado"],
                "TotalIva": totales_e_items["TotalIva"],
                "EmitidoPor": BAS_EMITIDO_POR_CAE,
                "Empresa": BAS_EMPRESA,
                "Sucursal": BAS_SUCURSAL,
                "Deposito": BAS_DEPOSITO,
                "Caja": BAS_CAJA,
                "MetodoPago": BAS_METODO_PAGO_CTA_CTE,
                "Proveedor": proveedor.get("Codigo"),
                "PrefijoComprobanteExterno": prefijo_externo,
                "NumeroComprobanteExterno": numero_externo,
                "FechaComprobanteExterno": comprobante.get("fecha_emision"),
                "NumeroCAIoCAE": otros.get("CAE"),
                "VencimientoCAIoCAE": otros.get("vencimiento_CAE"),
                # Importe = Total -- tiene que coincidir con "Total" de la
                # cabecera (ver comentario ahí arriba). Este flujo siempre
                # corre en dry_run así que hoy no escribe nada real.
                "Vencimientos": [
                    {"FechaVencimiento": comprobante.get("fecha_emision"), "Importe": totales_e_items["Total"]}
                ],
                "Items": totales_e_items["Items"],
            }

            with _lock_comprobante(proveedor.get("Codigo"), prefijo_externo, numero_externo):
                flujo = self._bas_client.crear_orden_de_pago_desde_factura(
                    empresa=BAS_EMPRESA,
                    sucursal=BAS_SUCURSAL,
                    comprobante_factura="MA",
                    prefijo_externo=prefijo_externo,
                    numero_externo=numero_externo,
                    importe=totales_e_items["Total"],
                    fecha_externo=comprobante.get("fecha_emision"),
                    prefijo_op=BAS_PREFIJO_TALONARIO_OP,
                    caja_op=BAS_CAJA,
                    prefijo_ctacte="P",
                    codigo_ctacte=proveedor.get("Codigo"),
                    # Medio de pago "1" (efectivo): candidato identificado en la
                    # investigación previa (pasó la validación de existencia contra
                    # BAS a diferencia de otros códigos probados). No hay endpoint
                    # que exponga el catálogo real -- ver docs/bas-orden-de-pago-research.md.
                    pagos={
                        "Efectivos": [
                            {"MedioPago": "1", "Importe": totales_e_items["Total"], "IngresooEgreso": "E"}
                        ]
                    },
                    comprobante_compra_payload=comprobante_compra_payload,
                    imputacion_contable=BAS_IMPUTACION_CONTABLE_PROVEEDORES,
                    dry_run=dry_run,
                )
            resultado["comprobante"] = flujo.get("factura")
            resultado["orden_pago"] = flujo.get("orden_pago")
            if isinstance(resultado["orden_pago"], dict) and resultado["orden_pago"].get("_error"):
                op_huerfana = resultado["orden_pago"].get("_op_sin_aplicar")
                if op_huerfana:
                    # Caso grave y distinto de un fallo limpio: la OP SÍ se creó
                    # en BAS y quedó sin aplicar (ver la advertencia de no
                    # atomicidad en crear_orden_de_pago_desde_factura). Hay que
                    # reconciliarla a mano; reintentar el flujo crearía una
                    # segunda OP por la misma factura.
                    resultado["error"] = (
                        f"Orden de pago {op_huerfana.get('Prefijo')}-{op_huerfana.get('Numero')} "
                        f"creada en BAS pero SIN aplicar -- reconciliar a mano, no reintentar. "
                        f"Detalle: {resultado['orden_pago']['detail']}"
                    )
                    app_logger.error(f"[{process_id}] BAS: {resultado['error']}")
                else:
                    sufijo = " -- requiere soporte de BAS, no reintentar" if resultado["orden_pago"].get("_requiere_soporte_bas") else ""
                    resultado["error"] = f"Orden de pago falló{sufijo}: {resultado['orden_pago']['detail']}"
                    app_logger.warning(
                        f"[{process_id}] BAS: factura registrada OK; OP falló: {resultado['error']}"
                    )
            else:
                app_logger.info(f"[{process_id}] BAS: factura registrada; orden de pago creada y aplicada.")

        except BasApiError as e:
            # El fallo esperado de OrdenesPago ya se maneja arriba (queda contenido
            # dentro de crear_orden_de_pago_desde_factura). Si llegamos acá, falló
            # algo ANTES de eso -- típicamente el registro de la propia factura
            # (ComprobantesCompra) o la consulta previa -- y sí es un error real.
            resultado["error"] = f"BasApiError {e.status_code} en {e.path}: {e.detail}"
            app_logger.error(f"[{process_id}] BAS: fallo registrando la factura: {resultado['error']}")
        except Exception as e:
            resultado["error"] = str(e)
            app_logger.error(f"[{process_id}] Error inesperado integrando con BAS: {e}")

        return resultado

    # Formatea los datos de la factura para la respuesta

    def formatear_factura(self, factura_completa):
        """
        Formatea una lista de respuestas de la API en un único diccionario
        estructurado con los datos de la factura y el total de tokens utilizados.
        """
        app_logger.info("Formateando factura...")
        datos_factura = {}
        total_tokens = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }

        # Itera sobre cada diccionario en la lista 'factura_completa'
        for respuesta in factura_completa:
            # 1. Acumula los tokens de uso
            # El .get("usage", {}) previene errores si la clave 'usage' no existe
            for token_type, value in respuesta.get("usage", {}).items():
                if token_type == "service_tier":
                    continue
                total_tokens[token_type] += value

            # 2. Extrae los datos de la factura del 'content'
            # Verifica que 'content' exista y no esté vacío
            if respuesta.get("content") and len(respuesta["content"]) > 0:
                # Accede al primer (y único) elemento de la lista 'content'
                tool_content = respuesta["content"][0]

                # Asigna los datos a la clave correcta según el 'name' de la herramienta
                if tool_content.get("name") == "datos_del_emisor_y_receptor":
                    datos_factura["emisor_receptor"] = tool_content.get("input", {})
                elif tool_content.get("name") == "detalle_de_items_facturados":
                    datos_factura["items"] = tool_content.get("input", {})
                elif (
                    tool_content.get("name") == "impuestos_y_retenciones_de_la_factura"
                ):
                    datos_factura["impuestos"] = tool_content.get("input", {})

        app_logger.info("Formateo completado.")
        return {
            "data": datos_factura,
            "tokens": total_tokens,
        }

    def get_file_type_from_url(self, url: str) -> str:
        # Mismo fix y mismo motivo que download_file_from_url (ver su
        # comentario): esta llamada corre dentro del mismo endpoint sin
        # ninguna autenticación, DOS LÍNEAS antes de la que ya se había
        # corregido -- bug real encontrado en la revisión adversarial (el
        # primer fix cerró un vector y dejó exactamente el mismo abierto,
        # dos líneas más arriba, en el mismo flujo).
        try:
            response = requests.get(url, stream=True, timeout=15)
            if response.status_code == 200:
                content = next(response.iter_content(262))
                kind = filetype.guess(content)
                if kind:
                    return kind.mime
            return None
        except Exception as e:
            return None

    def parse_filenames(self, file_string):
        # Si hay coma, devolvemos lista
        if "," in file_string:
            return [f.strip() for f in file_string.split(",")]
        # Si no hay coma, devolvemos el string tal cual
        return file_string

    def download_file_from_url(self, url: str, file_path: str, *, max_bytes: int = 100 * 1024 * 1024, max_segundos: float = 60):
        # Este método corre síncrono dentro de webhook_endpoint, el ÚNICO
        # de los 3 canales de ingesta SIN NINGUNA autenticación (sin
        # secret_key, sin X-Invoicy-Secret, sin rate limiting) -- `url`
        # viene tal cual del body del request, así que un caller sin
        # credenciales controla de dónde se descarga y a qué ritmo llegan
        # los bytes. Dos defensas, no una sola:
        #   1. timeout=15 -- connect+read timeout de requests. Bug real
        #      encontrado en la revisión adversarial: este es un timeout de
        #      INACTIVIDAD por lectura (se reinicia con cada byte que
        #      llega), NO un límite de duración total -- una respuesta que
        #      "gotea" 1 byte cada 10s nunca lo dispara.
        #   2. stream=True + loop manual con reloj propio (max_segundos) y
        #      tope de tamaño (max_bytes) -- cierra el caso que (1) no
        #      cubre: aborta la descarga en cuanto se supera CUALQUIERA de
        #      los dos límites, sin importar cuántos bytes hayan llegado o
        #      qué tan espaciados vengan. Con response.content (el código
        #      viejo) no había ningún tope de tamaño: un archivo de varios
        #      GB se cargaba entero en memoria antes de escribir el primer
        #      byte, en un droplet de 960MB de RAM.
        inicio = time.monotonic()
        response = requests.get(url, timeout=15, stream=True)
        if response.status_code != 200:
            app_logger.error(f"Failed to download file: {response.status_code}")
            return False
        total_descargado = 0
        try:
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    total_descargado += len(chunk)
                    if total_descargado > max_bytes:
                        app_logger.error(
                            f"download_file_from_url: {url} supera el máximo de "
                            f"{max_bytes} bytes -- descarga abortada"
                        )
                        return False
                    if time.monotonic() - inicio > max_segundos:
                        app_logger.error(
                            f"download_file_from_url: {url} supera el máximo de "
                            f"{max_segundos}s de duración total -- descarga abortada"
                        )
                        return False
                    f.write(chunk)
            return True
        finally:
            response.close()

    def generar_html_factura(self, data):
        receptor = data.get("emisor_receptor", {}).get("receptor", {})
        emisor = data.get("emisor_receptor", {}).get("emisor", {})
        comprobante = data.get("emisor_receptor", {}).get("comprobante", {})
        otros = data.get("emisor_receptor", {}).get("otros", {})
        items = data.get("items", {}).get("detalles", [])
        subtotal = data.get("items", {}).get("subtotal")
        total = data.get("items", {}).get("total")

        html = f"""
        <html>
        <head>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    color: #333;
                    padding: 20px;
                }}
                .factura {{
                    max-width: 700px;
                    margin: auto;
                    border: 1px solid #ccc;
                    padding: 20px;
                    border-radius: 10px;
                }}
                h1 {{
                    text-align: center;
                    margin-bottom: 30px;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-bottom: 20px;
                }}
                th, td {{
                    border: 1px solid #ccc;
                    padding: 8px;
                    text-align: left;
                }}
                th {{
                    background-color: #f2f2f2;
                }}
                .total {{
                    text-align: right;
                    font-size: 1.2em;
                    font-weight: bold;
                }}
            </style>
        </head>
        <body>
            <div class="factura">
                <h1>{comprobante["tipo"]} N° {comprobante["numero"]}</h1>
                <p><strong>Fecha de emisión:</strong> {comprobante["fecha_emision"]}</p>
                <p><strong>Moneda:</strong> {comprobante["moneda"]} | <strong>Jurisdicción:</strong> {comprobante["jurisdiccion_fiscal"]}</p>

                <h2>Emisor</h2>
                <p>{emisor["nombre"]}</p>
                <p>{emisor["direccion"]}</p>
                <p><strong>Condición IVA:</strong> {emisor["condicion_iva"]}</p>
                <p><strong>ID Fiscal:</strong> {emisor["id_fiscal"]}</p>

                <h2>Receptor</h2>
                <p>{receptor["nombre"]}</p>
                <p>{receptor["direccion"]}</p>
                <p><strong>Condición IVA:</strong> {receptor["condicion_iva"]}</p>

                <h2>Detalles</h2>
                <table>
                    <tr>
                        <th>Descripción</th>
                        <th>Cantidad</th>
                        <th>Precio Unitario</th>
                        <th>Total</th>
                    </tr>
                    {''.join(f"<tr><td>{item['descripcion']}</td><td>{item['cantidad']}</td><td>${item['precio_unitario']:.2f}</td><td>${item['precio_total']:.2f}</td></tr>" for item in items)}
                </table>

                <p class="total">Subtotal: ${f"{subtotal:.2f}" if subtotal is not None else ""}</p>
                <p class="total">Total: ${f"{total:.2f}" if total is not None else ""}</p>

                <p><strong>CAE:</strong> {otros.get("CAE", "")} | <strong>Vencimiento CAE:</strong> {otros.get("vencimiento_CAE", "")}</p>
            </div>
        </body>
        </html>
        """
        return html

    def enviar_email(self, destinatario, asunto, cuerpo):
        GMAIL_USER = os.getenv("GOOGLE_APP_EMAIL")  # tu correo
        GMAIL_PASS = os.getenv("GOOGLE_APP_PASSWORD")  # tu contraseña de aplicación

        try:
            # Crear el mensaje
            mensaje = MIMEMultipart()
            mensaje["From"] = GMAIL_USER
            mensaje["To"] = destinatario
            mensaje["Subject"] = asunto

            # Cuerpo del mensaje
            mensaje.attach(MIMEText(cuerpo, "html"))

            # Conectar al servidor SMTP de Gmail -- timeout explícito
            # (antes no tenía ninguno): sin esto, un SMTP colgado dejaba el
            # hilo bloqueado indefinidamente. Esta función ya se invoca
            # dentro de asyncio.to_thread desde el flujo de ZIP (nunca
            # bloquea el event loop), pero sin timeout propio podía agotar
            # de todas formas el pool de hilos compartido si varios ZIPs
            # entraban a la vez y el SMTP no respondía.
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
                server.login(GMAIL_USER, GMAIL_PASS)
                server.sendmail(GMAIL_USER, destinatario, mensaje.as_string())

            app_logger.info(f"✅ Email sent successfully to {destinatario}")
            return True

        except Exception as e:
            app_logger.error(f"❌ Error sending email to {destinatario}: {str(e)}")
            return False


# Inicializa el orquestador principal - es el cerebro de todo el sistema
# secret: clave para autenticar las requests
# webhook_url: donde mandamos updates del proceso
# api_key: para usar Claude AI
# recharge_cooldown: tiempo entre recargas (45 segs)
# queue_check_cooldown: cada cuanto revisamos la cola (20 segs)
# model: versión de Claude que usamos
# semaphore: cuántos procesos paralelos permitimos (3 max)

orchestrator = InvoiceOrchestrator(
    secret=os.getenv("SECRET_KEY"),
    webhook_url=os.getenv("WEBHOOK_URL"),
    api_key=os.getenv("GOOGLE_API_KEY"),
    recharge_cooldown=45,
    queue_check_cooldown=20,
    model="gemini-3.5-flash",
    semaphore=3,
)


async def _procesar_imagen_o_pdf(
    file_location: str,
    file_name: str,
    extension: str,
    media_type: str,
    process_id: str,
    monto_override: Optional[float] = None,
) -> dict:
    """Wrapper delgado de _procesar_imagen_o_pdf_impl() que solo agrega el
    gate de concurrencia global (PROCESSING_SEMAPHORE, ver su comentario más
    arriba) -- separado de la implementación para que el semáforo cubra a
    TODOS los callers reales (el único hoy es _procesar_en_background, pero
    eso alcanza para proteger tanto los dos loops de ZIP existentes como el
    nuevo flujo de importación masiva, que también pasa por acá) sin tener
    que reindentar toda la función.

    `monto_override`: ver el docstring de InvoiceOrchestrator.procesar_factura_en_bas
    -- se reenvía tal cual, None en el 100% de los callers salvo el flujo de
    importación masiva cuando el batch se creó con esa opción."""
    async with PROCESSING_SEMAPHORE:
        return await _procesar_imagen_o_pdf_impl(
            file_location, file_name, extension, media_type, process_id, monto_override
        )


def _calcular_content_hash(file_location: str) -> Optional[str]:
    """sha256 del archivo en disco -- motor del dedup de importación masiva
    (ver docs/plan-importacion-masiva-facturas.md, sección 3). Se calcula acá
    (no solo en el flujo de batch) para que TODA factura, sin importar por
    qué puerta entró (/website-upload, /process-invoice, o batch), quede con
    su content_hash y sea detectable como duplicado en el futuro. None en
    caso de error de lectura -- no debe frenar el resto del procesamiento."""
    try:
        h = hashlib.sha256()
        with open(file_location, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        app_logger.warning(f"Error calculando content_hash de {file_location}: {e}")
        return None


def _aplicar_monto_override(factura_data: dict, monto_override: float) -> None:
    """Muta factura_data["items"] IN PLACE para reemplazar el monto real
    extraído por monto_override -- usado por la importación masiva de
    facturas reales sin impacto contable real (ver
    docs/plan-importacion-masiva-facturas.md, Fase 5/6). Se llama ANTES de
    Sheets/BAS/PocketBase, así el monto de $1 queda consistente en todo lo
    que se persiste, no solo en el payload de BAS -- si no, invoices.total
    igual quedaría con el monto real, y ESE es el valor que usa
    crear_orden_pago cuando alguien confirma la factura más tarde y pide la
    Orden de Pago de verdad (un endpoint separado que no reextrae nada).

    Preserva un solo ítem sintético con la categoría del primero real (si
    hay), para seguir ejercitando la resolución real de CodigoItem en vez
    de una hardcodeada. Proveedor/CUIT/número de comprobante externo/fechas
    NO se tocan acá -- eso vive en emisor_receptor, intacto."""
    items_info = factura_data.get("items") or {}
    detalles = items_info.get("detalles") or []
    categoria_real = detalles[0].get("categoria", "") if detalles else ""
    descripcion_real = detalles[0].get("descripcion", "") if detalles else "Importación masiva (monto de prueba)"

    factura_data["items"] = {
        **items_info,
        "detalles": [
            {
                "descripcion": descripcion_real,
                "cantidad": 1,
                "precio_unitario": monto_override,
                "precio_total": monto_override,
                "categoria": categoria_real,
            }
        ],
        "subtotal": monto_override,
        "total": monto_override,
    }


async def _procesar_imagen_o_pdf_impl(
    file_location: str,
    file_name: str,
    extension: str,
    media_type: str,
    process_id: str,
    monto_override: Optional[float] = None,
) -> dict:
    """Procesa sincrónicamente una imagen o PDF de factura: extracción Gemini,
    Sheets, integración BAS (dry_run por default) y persistencia en
    PocketBase. Compartido por /process-invoice (protegido con secret_key) y
    /website-upload (público, rate-limited) -- misma lógica de negocio, dos
    puertas de entrada distintas. Borra el archivo local al terminar.
    """
    item = {
        "file_name": file_name,
        "file_extension": extension,
        "file_path": file_location,
        "media_type": media_type,
        "process_id": process_id,
    }

    content_hash = _calcular_content_hash(file_location)

    # Placeholder "processing" ANTES de arrancar la extracción -- ver bug
    # real 2026-07-18: si run_image_toolchain/run_pdf_toolchain (Gemini) o
    # cualquier paso más abajo tira una excepción, _procesar_en_background la
    # traga y loguea, pero hasta ahora el invoice recién se creaba al FINAL
    # (tras Gemini+Sheets+BAS) -- una falla temprana dejaba CERO rastro en
    # PocketBase: no aparecía en Facturas ni en la cola, "no pasaba nada"
    # para quien subió el archivo. Este upsert (mismo process_id que el
    # upsert final de más abajo, así que se pisan entre sí, no duplican)
    # garantiza que toda subida deje un registro desde el arranque, y que
    # una falla real termine en status="error" (ver _procesar_en_background)
    # en vez de desaparecer en silencio.
    #
    # error_message/extraction_attempt se resetean acá a propósito: este
    # mismo código corre también en un REINTENTO manual (ver endpoint
    # /invoices/{process_id}/retry-extraction), así que si no se limpian
    # quedarían pegados el mensaje de error y el contador del intento
    # anterior encima de un resultado nuevo.
    _pb_invoice_record_inicial = orchestrator._pb_client.upsert_invoice(
        {
            "process_id": process_id,
            "status": "processing",
            "error_message": "",
            "extraction_attempt": 1,
            # Vacío si _calcular_content_hash falló -- no vale la pena
            # bloquear el procesamiento por esto, el dedup simplemente no
            # va a atrapar esta factura puntual si el hash no se pudo leer.
            **({"content_hash": content_hash} if content_hash else {}),
        }
    )

    # Adjunta el archivo original DESDE EL ARRANQUE, no solo si el
    # procesamiento termina bien (a diferencia de como era antes, ver más
    # abajo) -- así el panel de revisión puede mostrar el documento aunque
    # la extracción falle, y el endpoint de reintento manual tiene de dónde
    # volver a leerlo sin pedirle al usuario que lo suba de nuevo.
    # Best-effort: un fallo acá no debe frenar el procesamiento.
    if _pb_invoice_record_inicial and _pb_invoice_record_inicial.get("id"):
        try:
            orchestrator._pb_client.adjuntar_archivo_original(
                _pb_invoice_record_inicial["id"], file_location, file_name, media_type
            )
        except Exception as e:
            app_logger.warning(
                f"[{process_id}] PocketBase: error adjuntando archivo original (temprano): {e}"
            )

    # Procesa según tipo
    if media_type.startswith("image"):
        app_logger.info("Tenemos una imagen")
        respuestas = await orchestrator.run_image_toolchain(item)
    else:
        app_logger.info("Tenemos un PDF")
        respuestas = await orchestrator.run_pdf_toolchain(item)

    factura = orchestrator.formatear_factura(respuestas["data"])

    if monto_override is not None:
        # Acá, ANTES de Sheets/PocketBase (no solo antes de BAS): si el
        # override solo tocara procesar_factura_en_bas, el monto real
        # extraído igual quedaría persistido en invoices.total/
        # invoice_items.precio_total -- y ESE es el valor que lee
        # crear_orden_pago cuando alguien confirma la factura más tarde y
        # pide la Orden de Pago de verdad, un endpoint totalmente separado
        # que no pasa por acá. Mutar la estructura extraída acá cubre todo
        # el recorrido de una sola vez: Sheets, BAS y lo que se persiste.
        _aplicar_monto_override(factura["data"], monto_override)

    saved_sheet = orchestrator.guardar_factura_completa_en_sheets(
        factura["data"]
    )
    saved_items = orchestrator.guardar_items_en_sheets(factura["data"], process_id)

    # Integración con BAS (ERP): mismo patrón aislado que en worker().
    resultado_bas = orchestrator.procesar_factura_en_bas(
        factura["data"], process_id, monto_override=monto_override
    )

    # Copia del archivo original en Google Drive. Hasta este cambio solo
    # subía worker() (el flujo viejo de email/ZIP), así que las facturas
    # que entran como archivo suelto -- que son el caso real de uso --
    # quedaban sin drive_file_id: en producción las 5 facturas existentes
    # lo tenían vacío y la columna "Drive" del dashboard mostraba "—".
    #
    # Es una copia de respaldo, NO la fuente de verdad: el archivo se sigue
    # guardando en `documento_original` (campo file nativo de PocketBase),
    # que es de donde lee el visor del panel de revisión. Por eso un fallo
    # acá no rompe nada -- se loguea y se sigue, mismo criterio que worker().
    #
    # El archivo todavía está en disco (file_location se borra recién al
    # final de esta función), así que se puede subir tal cual.
    drive_file_id = None
    try:
        app_logger.info(f"[{process_id}] Subiendo archivo original a Google Drive: {file_name}")
        drive_file_id = orchestrator.subir_archivo_a_drive(
            file_path=file_location,
            file_name=file_name,
            mime_type=media_type,
        )
        if drive_file_id:
            app_logger.info(f"[{process_id}] ✅ Archivo subido a Drive. ID: {drive_file_id}")
        else:
            # subir_archivo_a_drive ya loguea el motivo (faltan credenciales,
            # error de la API, etc.) y devuelve None en vez de tirar.
            app_logger.warning(
                f"[{process_id}] No se pudo subir a Drive; la factura sigue "
                "igual, el archivo original queda en PocketBase."
            )
    except Exception as e:
        app_logger.warning(f"[{process_id}] Error inesperado subiendo a Drive: {e}")

    # Persistencia en PocketBase (invoice + items + estado BAS). Mismo
    # patrón y mismos nombres de campo que worker() (más abajo en esta
    # clase) -- aislado a propósito, un fallo acá NO debe afectar
    # Sheets/BAS ni la respuesta al llamador. Necesario para que las
    # facturas subidas como archivo suelto (el caso real de uso -- a
    # diferencia del branch ZIP, que encola vía job_queue/worker())
    # también queden persistidas y visibles en el dashboard.
    _pb_invoice_record = None
    try:
        _er = factura["data"].get("emisor_receptor", {})
        _cmp = _er.get("comprobante", {})
        _emisor = _er.get("emisor", {})
        _receptor = _er.get("receptor", {})
        _otros = _er.get("otros", {})
        _items_info = factura["data"].get("items", {})
        _detalles = _items_info.get("detalles", []) or []
        _impuestos_info = factura["data"].get("impuestos", {})
        _alicuota_iva = _extraer_alicuota_iva(_impuestos_info.get("impuestos", []))

        # Campos extraídos por Gemini -- CUALQUIERA de ellos puede venir None
        # si la corrida actual falló/fue ambigua en esa parte puntual (ver el
        # comentario largo de _extraer_alicuota_iva sobre el caso real que lo
        # confirmó, 2026-08-04, factura MEDINA/SHOW IMPORT: la extracción de
        # impuestos falló en un reintento y pisó con 0 una alícuota de 21%
        # ya guardada de un intento anterior bueno). Si esto es un
        # reprocesamiento del mismo process_id (retry-extraction, o una
        # subida duplicada), mandar None explícito PISARÍA un valor bueno ya
        # guardado (o, para campos number como iva_alicuota, PocketBase lo
        # convierte en 0 -- ver docs/incidente-2026-08-04-pagos-solo-neto.md
        # sección 10). Se omite el campo entero en vez de mandar None, así
        # upsert_invoice (merge por process_id) conserva lo que ya había.
        # Mismo criterio que ya se usaba para drive_file_id, ahora
        # generalizado a todos los campos extraídos.
        _campos_extraidos = {
            # combinar_numero_comprobante -- ver comentario equivalente en
            # worker() (mismo criterio, mismo caso real, misma función).
            "numero_comprobante": combinar_numero_comprobante(
                _cmp.get("numero"), _cmp.get("punto_de_venta")
            ),
            "fecha_emision": _cmp.get("fecha_emision"),
            "tipo_comprobante": _cmp.get("tipo"),
            "subtipo_comprobante": _cmp.get("subtipo"),
            "moneda": _cmp.get("moneda"),
            "emisor_nombre": _emisor.get("nombre"),
            "emisor_cuit": _emisor.get("id_fiscal"),
            "receptor_nombre": _receptor.get("nombre"),
            "receptor_cuit": _receptor.get("id_fiscal"),
            "subtotal": _items_info.get("subtotal"),
            "total": _items_info.get("total"),
            "cae": _otros.get("CAE"),
            "cae_vencimiento": _otros.get("vencimiento_CAE"),
            "forma_pago": _otros.get("forma_pago"),
            "iva_alicuota": _alicuota_iva,
            "drive_file_id": drive_file_id,
        }
        _proveedor_info_invoice = resultado_bas.get("proveedor") or {}
        _pb_invoice_record = orchestrator._pb_client.upsert_invoice(
            {
                "process_id": process_id,
                **{k: v for k, v in _campos_extraidos.items() if v is not None},
                "sheets_saved": bool(saved_sheet),
                "status": "completed",
                # Nuevo alcance 2026-08-10 -- ver docstring de
                # bas_registration_status en la migración
                # 1784500000_add_bas_traceability_and_state.js. Se escribe
                # siempre (no se omite si vacío como el resto de
                # _campos_extraidos): a diferencia de esos campos, este SÍ
                # debe reflejar el resultado de ESTE intento, no preservar
                # uno viejo -- si el proveedor dejó de resolverse en un
                # reprocesamiento, el estado tiene que reflejarlo.
                "bas_registration_status": resultado_bas.get("bas_registration_status") or "",
                "bas_provider": _proveedor_info_invoice.get("pb_id") or "",
            }
        )
        if _pb_invoice_record and _pb_invoice_record.get("id"):
            orchestrator._pb_client.bulk_create_invoice_items(
                _pb_invoice_record["id"],
                [
                    {
                        "process_id": process_id,
                        "linea": idx,
                        "descripcion": d.get("descripcion"),
                        "cantidad": d.get("cantidad"),
                        "precio_unitario": d.get("precio_unitario"),
                        "precio_total": d.get("precio_total"),
                        "categoria": d.get("categoria"),
                        # Sugerencia informativa para el dashboard -- ver
                        # comentario equivalente en worker() (mismo criterio,
                        # mismo resolver, P0-E).
                        "bas_codigo_item": resolver_codigo_item(
                            categoria=d.get("categoria", ""),
                            alicuota=_alicuota_iva,
                            pb_client=orchestrator._pb_client,
                        ).get("codigo_item")
                        or "",
                    }
                    for idx, d in enumerate(_detalles, 1)
                ],
            )
        else:
            app_logger.warning(
                f"[{process_id}] PocketBase: upsert_invoice no devolvió "
                "un record válido, se omiten los ítems y el estado BAS."
            )
    except Exception as e:
        app_logger.warning(
            f"[{process_id}] PocketBase: error persistiendo invoice/items: {e}"
        )

    try:
        if _pb_invoice_record and _pb_invoice_record.get("id"):
            _cmp_bas = factura["data"].get("emisor_receptor", {}).get("comprobante", {})
            _prefijo_ext, _numero_ext = _extraer_prefijo_numero_comprobante_externo(_cmp_bas)
            _proveedor_info = resultado_bas.get("proveedor") or {}
            _orden_pago_info = resultado_bas.get("orden_pago")
            if _orden_pago_info is None:
                # Schema solo acepta pending/success/failed -- "no
                # intentado todavía" mapea a "pending".
                _orden_pago_status = "pending"
            elif isinstance(_orden_pago_info, dict) and _orden_pago_info.get("_error"):
                _orden_pago_status = "failed"
            else:
                _orden_pago_status = "success"
            orchestrator._pb_client.upsert_bas_processing_status(
                process_id,
                invoice=_pb_invoice_record["id"],
                proveedor_resuelto=bool(resultado_bas.get("proveedor")),
                proveedor_codigo=_proveedor_info.get("codigo"),
                comprobante_prefijo=_prefijo_ext,
                comprobante_numero=_numero_ext,
                comprobante_registrado=bool(resultado_bas.get("comprobante")),
                orden_pago_status=_orden_pago_status,
                orden_pago_error=resultado_bas.get("error"),
            )
    except Exception as e:
        app_logger.warning(
            f"[{process_id}] PocketBase: error persistiendo bas_processing_status: {e}"
        )

    factura["id"] = process_id
    factura["saved_sheet"] = bool(saved_sheet)
    factura["saved_items"] = bool(saved_items)
    factura["bas"] = resultado_bas
    # drive_file_id ya se calculaba más arriba (para el upsert a
    # PocketBase) pero nunca se propagaba al dict que esta función
    # devuelve -- lo necesita el payload de fire_webhook por factura del
    # flujo de ZIP, para que coincida con el contrato que ya emite
    # worker() (compatibilidad, ver docs/plan-fase1-zip-REDISEÑO.md).
    factura["drive_file_id"] = drive_file_id
    factura["status_code"] = 200

    # El archivo original ya se adjuntó al arranque de esta función (ver el
    # placeholder "processing" más arriba) -- no hace falta repetirlo acá.

    os.remove(file_location)

    return factura


async def _procesar_en_background(**kwargs) -> dict:
    """Corre _procesar_imagen_o_pdf() sin bloquear la respuesta HTTP.

    El procesamiento real (Gemini + búsqueda de proveedor en BAS) puede
    superar los 200s de timeout del gateway para /gemini2/ (ver nginx.conf)
    incluso cuando termina bien del lado del servidor -- verificado en
    producción con facturas reales: 7/7 PDFs de una tanda de prueba
    devolvieron 504 al cliente, pero 6/7 igual terminaron persistidas
    correctamente en PocketBase unos minutos después (el request HTTP no se
    cancela solo porque nginx se desconectó). El cliente veía un error falso
    mientras el backend seguía trabajando -- confuso, y arriesga que alguien
    reintente y duplique el procesamiento de la misma factura.

    Devuelve {"success": True, "factura": {...}} o {"success": False,
    "error": "..."} -- los 2 callers históricos (process_invoice,
    website_upload) ignoran el valor de retorno sin ningún problema (nunca
    lo leían); _extraer_zip_y_despachar_individualmente sí lo usa, para
    saber qué payload de webhook disparar por factura.
    """
    try:
        factura = await _procesar_imagen_o_pdf(**kwargs)
        return {"success": True, "factura": factura}
    except Exception as exc:
        process_id = kwargs.get("process_id", "?")
        app_logger.info(f"[{process_id}] Error procesando en background: {exc}")
        # Deja rastro real en PocketBase en vez de tragar la excepción en
        # silencio -- convierte el placeholder "processing" (creado al
        # arranque de _procesar_imagen_o_pdf) en "error", así la factura
        # aparece en Facturas con el motivo real en vez de desaparecer.
        if process_id != "?":
            orchestrator._pb_client.upsert_invoice(
                {
                    "process_id": process_id,
                    "status": "error",
                    "error_message": str(exc)[:1000],
                }
            )
        return {"success": False, "error": str(exc)[:1000]}


def _sanear_nombre_para_process_id(nombre: str) -> str:
    """Solo para legibilidad del process_id resultante y de los logs -- la
    unicidad real la garantiza el índice de posición dentro del ZIP (ver
    _extraer_zip_y_despachar_individualmente), no este nombre saneado. Se
    queda con el basename (descarta cualquier carpeta interna del ZIP) y
    reemplaza todo lo que no sea alfanumérico/./-/_ por "_", para que el
    resultado sea seguro de usar en una ruta de disco, en un process_id y en
    una URL de los 8 endpoints que lo reciben como path param."""
    base = os.path.basename(nombre)
    seguro = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return seguro[:80] or "archivo"


def _truncar_nombre_a_bytes(nombre: str, max_bytes: int) -> str:
    """Trunca un nombre de archivo a `max_bytes` BYTES reales (no
    caracteres) -- a diferencia de nombre[:N] de Python, que cuenta code
    points: un nombre con tildes/ñ/otros caracteres UTF-8 multi-byte puede
    pesar hasta 4x más en bytes que en caracteres, así que un simple [:200]
    puede seguir superando el límite típico de 255 bytes de nombre de
    archivo del filesystem (bug real encontrado en la revisión adversarial
    -- el truncado por caracteres no cumplía la garantía que decía dar).
    Preserva el nombre original tal cual cuando entra en el presupuesto;
    solo lo recorta cuando hace falta, descartando con errors="ignore"
    cualquier secuencia multi-byte que quede cortada a la mitad en el
    límite."""
    codificado = nombre.encode("utf-8")
    if len(codificado) <= max_bytes:
        return nombre
    return codificado[:max_bytes].decode("utf-8", errors="ignore")


def _validar_zip_rapido(zip_path: str) -> dict:
    """Chequeos baratos de METADATA del índice del ZIP (sin descomprimir
    nada) -- corren síncronamente en el handler HTTP, antes de crear
    cualquier background task, para que el caller reciba un rechazo claro e
    inmediato en los casos más comunes (corrupto, vacío, demasiados
    archivos, demasiado pesado, sistema saturado). zip_ref.file_size
    declarado por miembro no se puede falsificar para esconder un payload
    más grande: ZipExtFile trunca la lectura al tamaño declarado
    (verificado empíricamente), así que sumar file_size acá es una defensa
    real contra zip bombs.

    A propósito NO valida integridad de CRC/encriptación acá (eso era
    zip_ref.testzip() en el diseño anterior -- medido ~2617x más lento que
    estos chequeos de metadata para un ZIP grande, y además era un gate
    TODO-o-nada que abortaba el ZIP entero por un solo miembro corrupto).
    Esa validación ahora es POR MIEMBRO, dentro de
    _extraer_zip_y_despachar_individualmente (zip_ref.extract() ya detecta
    CRC corrupto/encriptación por sí solo, aislado por archivo -- ver su
    docstring).

    Última validación (más cara relativamente, por eso al final): reserva
    capacidad de "trabajo en vuelo" para los miembros de este ZIP (ver
    MAX_TRABAJO_EN_VUELO_ZIP) -- si el sistema ya tiene demasiadas facturas
    pendientes/en proceso, rechaza el ZIP COMPLETO de forma clara e
    inmediata en vez de aceptarlo y dejarlo esperando horas en una cola
    invisible. La reserva queda a cargo del caller de liberarla
    (_extraer_zip_y_despachar_individualmente lo hace, miembro por
    miembro, a medida que cada uno llega a un estado terminal o se
    descarta).

    Devuelve {"ok": True, "miembros": [...], "reservado": N} o
    {"ok": False, "error": "..."}.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            miembros = [i for i in zip_ref.infolist() if not i.is_dir()]
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
        RuntimeError,
        UnicodeError,
        OSError,
    ) as e:
        return {"ok": False, "error": f"El ZIP está corrupto o no es un ZIP válido: {e}"}

    if len(miembros) == 0:
        return {"ok": False, "error": "El ZIP no contiene archivos."}

    if len(miembros) > MAX_ARCHIVOS_ZIP:
        return {
            "ok": False,
            "error": (
                f"El ZIP tiene {len(miembros)} archivos, el máximo permitido "
                f"es {MAX_ARCHIVOS_ZIP}. Dividilo en varios envíos más chicos."
            ),
        }

    total_descomprimido = sum(i.file_size for i in miembros)
    if total_descomprimido > MAX_ZIP_DESCOMPRIMIDO_BYTES:
        return {
            "ok": False,
            "error": (
                f"El ZIP pesa demasiado descomprimido "
                f"({total_descomprimido / (1024 * 1024):.0f}MB, máximo "
                f"{MAX_ZIP_DESCOMPRIMIDO_BYTES // (1024 * 1024)}MB)."
            ),
        }

    if not _reservar_trabajo_en_vuelo_zip(len(miembros)):
        return {
            "ok": False,
            "error": (
                "El sistema está procesando demasiadas facturas en este "
                "momento. Esperá unos minutos y reintentá."
            ),
        }

    return {"ok": True, "miembros": miembros, "reservado": len(miembros)}


def _carpeta_ingest_zip(*, origen: str, ingest_id: str) -> str:
    """Única fuente de verdad para el nombre de la carpeta temporal de UNA
    ingesta de ZIP -- usada tanto por los 3 endpoints (para guardar el .zip
    crudo recién subido/descargado) como por _extraer_zip_y_despachar_individualmente
    (que extrae los miembros DENTRO de esta misma carpeta y al final la
    borra completa con shutil.rmtree, limpiando .zip crudo + miembros
    extraídos en un solo paso). `origen` y `ingest_id` son SIEMPRE
    server-side (una constante fija por canal + un uuid4 generado en el
    backend) -- nunca un valor de cliente, ver docs/plan-fase1-zip-REDISEÑO.md
    sección 1."""
    return f"./downloads/zip-{origen}-{ingest_id}"


def _generar_html_resumen_zip(
    *, total: int, aceptados: int, no_soportados: list, errores_despacho: Optional[list] = None
) -> str:
    """Email-resumen de UN ZIP (no uno por factura -- ver decisión en
    docs/plan-fase1-zip-REDISEÑO.md sección 4). Deliberadamente chico: no
    reusa generar_html_factura (que arma el detalle de UNA factura ya
    procesada), esto es solo un acuse de recibo del envío completo.

    errores_despacho (archivos que SÍ eran del tipo correcto pero fallaron
    al procesarse -- Gemini/BAS, no un rechazo de Fase A) se lista aparte
    de no_soportados: antes no se mencionaban en absoluto, así que
    "aceptados" + "no_soportados" no sumaba "total" cuando había alguno, y
    el destinatario veía un conteo que no cerraba, sin ninguna explicación
    de qué pasó con la diferencia."""
    errores_despacho = errores_despacho or []
    lineas_no_soportados = "".join(
        f"<li>{n.get('nombre', '?')} -- {n.get('motivo', 'no soportado')}</li>"
        for n in no_soportados[:20]
    )
    if len(no_soportados) > 20:
        lineas_no_soportados += f"<li>... y {len(no_soportados) - 20} más.</li>"
    bloque_no_soportados = (
        f"<p>{len(no_soportados)} archivo(s) no se pudieron procesar:</p><ul>{lineas_no_soportados}</ul>"
        if no_soportados
        else ""
    )
    bloque_errores_despacho = (
        f"<p>{len(errores_despacho)} factura(s) fallaron durante el procesamiento "
        "y van a aparecer con un error en el sistema -- se pueden reintentar "
        "desde ahí.</p>"
        if errores_despacho
        else ""
    )
    return (
        f"<p>Recibimos tu ZIP con {total} archivo(s).</p>"
        f"<p>{aceptados} factura(s) están en proceso -- vas a poder verlas "
        "en el sistema en los próximos minutos.</p>"
        f"{bloque_errores_despacho}"
        f"{bloque_no_soportados}"
    )


async def _extraer_zip_y_despachar_individualmente(
    *,
    zip_path: str,
    ingest_id: str,
    origen: str,
    reservado_trabajo_en_vuelo: int,
    client_reference: Optional[str] = None,
    notificar_no_soportados_por_webhook: bool = False,
    enviar_resumen_por_email: bool = False,
    datos_email: Optional[dict] = None,
    on_item_completado: Optional[Callable] = None,
) -> dict:
    """Punto único de entrada de ZIP al pipeline individual existente --
    reutilizado por el webhook de email y por /website-upload (y por
    /process-invoice). Asume que _validar_zip_rapido() ya corrió, pasó, y
    reservó `reservado_trabajo_en_vuelo` unidades de trabajo (ver
    MAX_TRABAJO_EN_VUELO_ZIP) -- esta función es la responsable de liberar
    esas unidades a medida que cada archivo se descarta o termina su
    procesamiento (nunca antes). Pensada para correr SIEMPRE dentro de un
    asyncio.create_task, nunca esperada (await) directamente desde un
    handler HTTP.

    Identidad (ver docs/plan-fase1-zip-REDISEÑO.md sección 1): `ingest_id`
    SIEMPRE lo genera el caller con uuid4() server-side (nunca un valor de
    cliente) -- cada uno de los 3 endpoints lo genera apenas confirma que
    está ante un ZIP y lo reusa tanto para guardar el .zip crudo
    (_carpeta_ingest_zip) como para esta llamada, así ambas cosas terminan
    en la MISMA carpeta y se limpian juntas. Esta función NUNCA recibe ni
    usa ningún valor del cliente para construir rutas de disco ni claves de
    PocketBase -- `client_reference` (si vino) es puramente informativo,
    solo va a los logs. Como `ingest_id` es un uuid4 fresco por invocación
    (nunca derivado del `id`/`process_id` que mandó el cliente), dos
    invocaciones concurrentes -- mismo id de cliente, reintento, o abuso
    deliberado -- nunca comparten carpeta ni prefijo de process_id.

    Dos fases, separadas para durabilidad (sección 3 del rediseño; el
    detalle de Fase A -- 2 sub-pasadas, cada una bajo su propio semáforo --
    se agregó después, ver sección 6 de docs/informe-final-zip-fase1.md):
      Fase A (síncrona, corre en hilos reales vía asyncio.to_thread), 2
      sub-pasadas sobre los mismos miembros, cada una con su propio gate
      de concurrencia (motivo: la Pass 0 es liviana y no debería esperar
      detrás de la Pass 1, que sí es pesada -- ver ZIP_REGISTRO_SEMAPHORE
      vs ZIP_EXTRACTION_SEMAPHORE más arriba en el módulo):
        Pass 0 (bajo ZIP_REGISTRO_SEMAPHORE, mayor concurrencia) --
        pre-registra TODOS los miembros potencialmente válidos
        (upsert_invoice status="pending") ANTES de extraer o clasificar
        ninguno -- así un reinicio en CUALQUIER punto posterior deja a
        todo miembro candidato con al menos una fila recuperable, sin
        importar en qué índice estaba la clasificación NI si la task
        todavía estaba esperando su turno para arrancar.
        Pass 1 (bajo ZIP_EXTRACTION_SEMAPHORE, la extracción pesada de
        siempre) -- clasifica cada miembro (integridad CRC/encriptación
        por miembro vía zip_ref.extract(), Zip Slip, tipo soportado). Un
        miembro ACEPTADO adjunta su archivo original
        (adjuntar_archivo_original) DE INMEDIATO, apenas se acepta --no
        espera a que el loop termine de clasificar el resto del ZIP--, así
        que un reinicio a mitad de la clasificación deja a los miembros YA
        aceptados con su archivo YA adjuntado (reintentables de verdad),
        no solo con una fila "pending" sin archivo. Un miembro RECHAZADO
        descarta su placeholder de la Pass 0 (soft-delete, mismo mecanismo
        que el placeholder huérfano de /website-upload/init) -- nunca
        queda como factura pendiente procesable. La verificación de
        integridad es POR MIEMBRO -- un miembro corrupto o encriptado no
        afecta a los demás -- reemplaza al testzip() global del diseño
        original, que abortaba el ZIP entero por un solo miembro dañado.
      Fase B (async, en el event loop): despacha cada archivo aceptado a
      _procesar_en_background (protegido por PROCESSING_SEMAPHORE, sin
      cambios), libera su unidad de trabajo-en-vuelo al terminar, y
      dispara on_item_completado si vino (usado por el canal de email para
      fire_webhook por factura).

    Fallos aislados en las DOS fases: un archivo que no se puede extraer,
    está corrupto, encriptado, o es de tipo no soportado no aborta el
    resto (queda en "no_soportados"); un archivo que falla al despacharse
    a _procesar_en_background (que ya atrapa sus propias excepciones y las
    deja como status="error" en PocketBase) no frena el despacho de los
    demás. Solo un ZIP que ni siquiera se puede abrir aborta todo -- eso
    es un check GLOBAL legítimo (no hay nada que aislar si no se puede ni
    leer el índice del archivo).
    """
    carpeta_base = _carpeta_ingest_zip(origen=origen, ingest_id=ingest_id)

    if client_reference:
        app_logger.info(
            f"[{ingest_id}] ZIP origen={origen} -- referencia del cliente (solo "
            f"trazabilidad, nunca usada para rutas/claves): {client_reference!r}"
        )

    resultado = {
        "origen": origen,
        "ingest_id": ingest_id,
        "aceptados": 0,
        "no_soportados": [],
        "errores_despacho": [],
        "error_zip": None,
    }

    def _es_zip_anidado(miembro):
        return miembro.filename.lower().endswith(".zip")

    def _pass0_pre_registrar():
        """Corre en un hilo separado, bajo ZIP_REGISTRO_SEMAPHORE (más
        concurrencia que la extracción pesada -- ver el comentario del
        semáforo en la cabecera del módulo). TODO acá es sincrónico
        (upsert_invoice usa `requests` de forma bloqueante). Pre-registra
        TODOS los miembros potencialmente válidos ANTES de que la
        clasificación real (Pass 1) toque a ninguno -- así un reinicio en
        CUALQUIER punto posterior (incluida la espera por el semáforo de
        extracción, más abajo) deja a todo miembro candidato con al menos
        una fila recuperable. Se salta los ZIP anidados (detectables solo
        por nombre, sin extraer nada) para no crear-y-enseguida-descartar
        un placeholder para algo que nunca fue candidato real.

        No es atómico en sentido matemático -- sigue siendo N llamadas de
        red secuenciales, no una transacción. Una garantía dura de "cero
        pérdida bajo cualquier reinicio" requeriría el batch API
        transaccional de PocketBase, que necesita habilitarse
        explícitamente por colección en la configuración real de
        producción -- algo que no podemos verificar ni tocar desde acá sin
        desplegar. Lo que SÍ logra esta Pass 0, sin infraestructura nueva:
        reduce la ventana de riesgo al mínimo práctico alcanzable con lo
        que ya existe. Decisión de arquitectura documentada en
        docs/informe-final-zip-fase1.md sección 6.

        Devuelve (process_ids_por_indice, record_ids_por_process_id,
        error_apertura)."""
        process_ids_por_indice = {}
        # id real de PocketBase (record["id"], NO process_id) de cada
        # upsert "pending" -- la Pass 1 lo necesita para llamar
        # adjuntar_archivo_original sin tener que repetir el upsert.
        record_ids_por_process_id = {}
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                miembros = [i for i in zip_ref.infolist() if not i.is_dir()]
                for indice, miembro in enumerate(miembros):
                    if _es_zip_anidado(miembro):
                        continue
                    nombre_saneado = _sanear_nombre_para_process_id(miembro.filename)
                    process_id = f"{origen}--{ingest_id}--{indice:03d}-{nombre_saneado}"
                    process_ids_por_indice[indice] = process_id
                    registro_pass0 = orchestrator._pb_client.upsert_invoice(
                        {"process_id": process_id, "status": "pending", "error_message": ""}
                    )
                    if registro_pass0 and registro_pass0.get("id"):
                        record_ids_por_process_id[process_id] = registro_pass0["id"]
        except Exception as e:
            return process_ids_por_indice, record_ids_por_process_id, f"Error inesperado abriendo el ZIP: {e}"
        return process_ids_por_indice, record_ids_por_process_id, None

    def _pass1_clasificar_extraer_y_adjuntar(process_ids_por_indice, record_ids_por_process_id):
        """Corre en un hilo separado, bajo ZIP_EXTRACTION_SEMAPHORE. TODO
        acá es sincrónico (I/O de disco, zlib, y las llamadas HTTP
        bloqueantes de adjuntar_archivo_original/soft_delete_invoice). No
        debe hacer ningún `await` ni tocar el event loop. Reusa el
        process_id que ya calculó la Pass 0 -- no lo recalcula, para que
        ambas pasadas siempre hablen exactamente de la misma fila.

        A diferencia del diseño anterior (Pass 2 separada, que adjuntaba
        el archivo de TODOS los miembros recién después de que este loop
        terminara para TODO el ZIP), acá cada miembro ACEPTADO adjunta su
        archivo DE INMEDIATO, en la misma iteración que lo acepta -- así
        un reinicio a mitad de este loop deja a los miembros YA aceptados
        con su archivo YA adjuntado (documento_original set, "Reintentar"
        funciona de verdad), no solo con una fila "pending" sin archivo
        esperando a que el loop termine entero. Ver
        docs/informe-final-zip-fase1.md sección 6.

        Devuelve (archivos_a_despachar, no_soportados_para_webhook,
        error_zip, unidades_liberadas)."""
        archivos_a_despachar = []
        no_soportados_para_webhook = []
        unidades_liberadas = 0

        def _descartar_placeholder_pass0(process_id, *, motivo):
            """Un miembro que la Pass 0 pre-registró como "pending" resultó
            NO ser una factura válida (corrupto, encriptado, Zip Slip, tipo
            no soportado, error inesperado de extracción) -- lo saca de
            PocketBase con el mismo mecanismo de soft-delete ya usado para
            el placeholder huérfano de /website-upload/init (nunca un DELETE
            físico, nunca un endpoint nuevo), para que NO quede como factura
            pendiente/procesable: el estado final debe ser indistinguible de
            "nunca tuvo fila", igual que un miembro rechazado antes de este
            fix. Best-effort -- un fallo acá no debe frenar la clasificación
            de los demás miembros (ver docs/informe-final-zip-fase1.md
            sección 6 para el residuo que esto deja si el proceso muere
            justo en medio de esta llamada)."""
            if not process_id:
                return
            try:
                orchestrator._pb_client.soft_delete_invoice(
                    process_id,
                    # deleted_by es relation -> users (ver migración
                    # 1783483896_add_soft_delete_fields.js): "quien pide el
                    # borrado siempre es un humano autenticado en el
                    # dashboard" -- acá no hay ningún humano, es un rechazo
                    # automático de Fase A, así que deleted_by va None (el
                    # campo es opcional, minSelect=0) y la identidad del
                    # actor queda en `reason`, que sí es texto libre.
                    deleted_by=None,
                    reason=f"sistema:zip-fase-a-rechazo -- {motivo}",
                )
            except Exception as e:
                app_logger.warning(
                    f"[{process_id}] PocketBase: error descartando placeholder "
                    f"de Pass 0 tras rechazo ({motivo}): {e}"
                )

        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                miembros = [i for i in zip_ref.infolist() if not i.is_dir()]
                for indice, miembro in enumerate(miembros):
                    process_id = process_ids_por_indice.get(indice)
                    try:
                        if _es_zip_anidado(miembro):
                            resultado["no_soportados"].append(
                                {"nombre": miembro.filename, "motivo": "ZIP anidado no soportado"}
                            )
                            no_soportados_para_webhook.append(
                                {
                                    "indice": indice,
                                    "nombre_miembro": miembro.filename,
                                    "ruta": None,
                                    "extension": ".zip",
                                    "mime": "application/zip",
                                }
                            )
                            _liberar_trabajo_en_vuelo_zip(1)
                            unidades_liberadas += 1
                            continue

                        carpeta_miembro = f"{carpeta_base}/{indice:03d}"
                        os.makedirs(carpeta_miembro, exist_ok=True)

                        try:
                            # zip_ref.extract() valida CRC (BadZipFile) y
                            # detecta encriptación (RuntimeError) por sí
                            # solo, POR MIEMBRO -- verificado empíricamente
                            # que un miembro corrupto/encriptado no afecta
                            # a los demás. Además usamos su valor de
                            # retorno (la ruta REAL que escribió) en vez de
                            # reconstruirla a mano con os.path.join, que
                            # podía divergir del algoritmo de saneo interno
                            # de zipfile._extract_member y dar falsos
                            # negativos/positivos en el chequeo de Zip Slip
                            # de abajo.
                            ruta_extraida = zip_ref.extract(miembro, carpeta_miembro)
                        except (zipfile.BadZipFile, RuntimeError, zlib.error) as e:
                            resultado["no_soportados"].append(
                                {
                                    "nombre": miembro.filename,
                                    "motivo": f"archivo corrupto o encriptado: {e}",
                                }
                            )
                            _descartar_placeholder_pass0(
                                process_id, motivo=f"archivo corrupto o encriptado: {e}"
                            )
                            _liberar_trabajo_en_vuelo_zip(1)
                            unidades_liberadas += 1
                            continue

                        ruta_extraida = os.path.realpath(ruta_extraida)
                        if not ruta_extraida.startswith(os.path.realpath(carpeta_miembro)):
                            # Zip Slip: la ruta REAL escrita por extract()
                            # terminó fuera de la carpeta del miembro.
                            resultado["no_soportados"].append(
                                {"nombre": miembro.filename, "motivo": "ruta interna inválida"}
                            )
                            _descartar_placeholder_pass0(process_id, motivo="ruta interna inválida")
                            _liberar_trabajo_en_vuelo_zip(1)
                            unidades_liberadas += 1
                            continue

                        tipo_real = filetype.guess(ruta_extraida)
                        extensiones_soportadas = {"pdf", "png", "jpg", "jpeg", "webp", "gif"}
                        if tipo_real is None or tipo_real.extension not in extensiones_soportadas:
                            motivo_no_soportado = (
                                f"tipo no soportado ({tipo_real.mime if tipo_real else 'desconocido'})"
                            )
                            resultado["no_soportados"].append(
                                {"nombre": miembro.filename, "motivo": motivo_no_soportado}
                            )
                            no_soportados_para_webhook.append(
                                {
                                    "indice": indice,
                                    "nombre_miembro": miembro.filename,
                                    "ruta": ruta_extraida,
                                    "extension": os.path.splitext(miembro.filename)[1],
                                    "mime": tipo_real.mime if tipo_real else None,
                                }
                            )
                            _descartar_placeholder_pass0(process_id, motivo=motivo_no_soportado)
                            _liberar_trabajo_en_vuelo_zip(1)
                            unidades_liberadas += 1
                            continue

                        # ACEPTADO -- adjunta el archivo YA, en esta misma
                        # iteración, no en una pasada separada al final (ver
                        # docstring de esta función). Best-effort: un fallo
                        # acá no debe sacar a este miembro de la cola de
                        # despacho -- Fase B vuelve a adjuntar el mismo
                        # archivo cuando le llega su turno real (ver
                        # _procesar_imagen_o_pdf_impl), así que esto es
                        # best-effort redundante, no la única oportunidad.
                        record_id = record_ids_por_process_id.get(process_id)
                        if record_id:
                            try:
                                orchestrator._pb_client.adjuntar_archivo_original(
                                    record_id,
                                    ruta_extraida,
                                    os.path.basename(miembro.filename),
                                    tipo_real.mime,
                                )
                            except Exception as e:
                                app_logger.warning(
                                    f"[{process_id}] PocketBase: error adjuntando archivo "
                                    f"original en Pass 1: {e}"
                                )

                        archivos_a_despachar.append(
                            {
                                "file_location": ruta_extraida,
                                "file_name": os.path.basename(miembro.filename),
                                "extension": tipo_real.extension,
                                "media_type": tipo_real.mime,
                                "process_id": process_id,
                            }
                        )
                    except Exception as e:
                        resultado["no_soportados"].append(
                            {"nombre": miembro.filename, "motivo": f"error de extracción: {e}"}
                        )
                        app_logger.warning(
                            f"[{ingest_id}] No se pudo extraer {miembro.filename}: {e}"
                        )
                        _descartar_placeholder_pass0(process_id, motivo=f"error de extracción: {e}")
                        _liberar_trabajo_en_vuelo_zip(1)
                        unidades_liberadas += 1
                        continue
        except Exception as e:
            # El ZIP ni siquiera se pudo abrir/leer -- acá SÍ es un check
            # global legítimo (no hay nada que aislar por miembro si no se
            # puede ni leer el índice). Libera TODO lo reservado que no se
            # haya liberado todavía.
            return (
                archivos_a_despachar,
                no_soportados_para_webhook,
                f"Error inesperado abriendo el ZIP: {e}",
                unidades_liberadas,
            )
        return archivos_a_despachar, no_soportados_para_webhook, None, unidades_liberadas

    async with ZIP_REGISTRO_SEMAPHORE:
        (
            process_ids_por_indice,
            record_ids_por_process_id,
            error_apertura_pass0,
        ) = await asyncio.to_thread(_pass0_pre_registrar)

    if error_apertura_pass0:
        # Mismo camino que "el ZIP ni siquiera se pudo abrir" de siempre --
        # acá la Pass 0 ya intentó abrirlo y falló, así que la Pass 1 ni
        # siquiera arranca. unidades_liberadas_fase_a=0 porque nada se
        # reservó/liberó todavía en esta rama.
        error_zip = error_apertura_pass0
        unidades_liberadas_fase_a = 0
        archivos_a_despachar = []
        no_soportados_para_webhook = []
    else:
        async with ZIP_EXTRACTION_SEMAPHORE:
            (
                archivos_a_despachar,
                no_soportados_para_webhook,
                error_zip,
                unidades_liberadas_fase_a,
            ) = await asyncio.to_thread(
                _pass1_clasificar_extraer_y_adjuntar, process_ids_por_indice, record_ids_por_process_id
            )

    unidades_pendientes_de_liberar = reservado_trabajo_en_vuelo - unidades_liberadas_fase_a

    if error_zip:
        resultado["error_zip"] = error_zip
        app_logger.error(f"[{ingest_id}] {error_zip}")
        if unidades_pendientes_de_liberar > 0:
            _liberar_trabajo_en_vuelo_zip(unidades_pendientes_de_liberar)
        shutil.rmtree(carpeta_base, ignore_errors=True)
        # Notificación de rechazo total -- semántica existente del canal
        # email (worker() NUNCA mandó un email de error, solo fire_webhook;
        # ver comparación en docs/plan-fase1-zip-REDISEÑO.md): mismo
        # criterio acá, on_item_completado es fire_webhook solo para
        # origen="email", así que esto no afecta a los otros 2 canales, que
        # nunca tuvieron esta notificación.
        if on_item_completado:
            try:
                await on_item_completado(
                    {
                        "process_id": ingest_id,
                        "file_name": os.path.basename(zip_path),
                        "error": error_zip,
                        "status": "error",
                        "success": False,
                    }
                )
            except Exception as e:
                app_logger.warning(
                    f"[{ingest_id}] on_item_completado (rechazo total) falló: {e}"
                )
        return resultado

    async def _notificar_no_soportado_si_corresponde(
        *, indice: int, nombre_miembro: str, ruta: Optional[str], extension: str, mime: Optional[str]
    ) -> None:
        # Preserva un efecto externo que ya existía en /process-invoice (el
        # único de los 3 canales que lo tenía): notificar por webhook cada
        # archivo rechazado dentro de un ZIP. Protegido por su propio
        # try/except: un fallo notificando (red, DNS, timeout) nunca puede
        # impedir que el resto del ZIP se siga procesando.
        if not notificar_no_soportados_por_webhook:
            return
        try:
            await orchestrator.fire_webhook(
                {
                    "file_name": os.path.basename(nombre_miembro),
                    "file_extension": extension,
                    "file_path": ruta,
                    "media_type": mime,
                    "process_id": f"{origen}--{ingest_id}--{indice:03d}-{_sanear_nombre_para_process_id(nombre_miembro)}",
                    "error": "Tipo de archivo no permitido.",
                }
            )
        except Exception as e:
            app_logger.warning(
                f"[{ingest_id}] No se pudo notificar webhook para {nombre_miembro}: {e}"
            )

    for item in no_soportados_para_webhook:
        await _notificar_no_soportado_si_corresponde(**item)

    for archivo in archivos_a_despachar:
        resultado_item = await _procesar_en_background(**archivo)
        if resultado_item.get("success"):
            resultado["aceptados"] += 1
        else:
            resultado["errores_despacho"].append(
                {"process_id": archivo.get("process_id", "?"), "error": resultado_item.get("error")}
            )
            app_logger.error(
                f"[{ingest_id}] Error despachando {archivo.get('process_id', '?')}: "
                f"{resultado_item.get('error')}"
            )

        if on_item_completado:
            # Forma EXACTA del payload que ya emite worker() (el pipeline
            # viejo de email para archivo suelto) por cada item -- incluidas
            # sus inconsistencias tal cual (worker() usa la clave "id" en
            # éxito pero "process_id" en error; success SÍ trae "factura"
            # completo, error NO). Comparación campo por campo contra
            # worker() documentada en docs/plan-fase1-zip-REDISEÑO.md. Se
            # replica así, sin "corregir" las inconsistencias, porque el
            # objetivo es compatibilidad real con integraciones que ya
            # consumen ese contrato, no una forma nueva más prolija.
            if resultado_item.get("success"):
                factura_item = resultado_item.get("factura") or {}
                payload = {
                    "id": archivo["process_id"],
                    "file_name": archivo["file_name"],
                    "factura": factura_item,
                    "saved": factura_item.get("saved_sheet"),
                    "saved_items": factura_item.get("saved_items"),
                    "bas": factura_item.get("bas"),
                    "drive_file_id": factura_item.get("drive_file_id"),
                    "status": "procesada",
                    "success": True,
                }
            else:
                payload = {
                    "process_id": archivo["process_id"],
                    "file_name": archivo["file_name"],
                    "error": resultado_item.get("error"),
                    "status": "error",
                    "success": False,
                }
            try:
                await on_item_completado(payload)
            except Exception as e:
                app_logger.warning(
                    f"[{ingest_id}] on_item_completado falló para {archivo['process_id']}: {e}"
                )

        _liberar_trabajo_en_vuelo_zip(1)
        unidades_pendientes_de_liberar -= 1

    # Safety-net: no debería quedar nada sin liberar (cada rama de arriba
    # ya libera 1 por unidad), pero si algún camino no contemplado se
    # escapó, esto evita que el contador quede "pisado" para siempre.
    if unidades_pendientes_de_liberar > 0:
        app_logger.warning(
            f"[{ingest_id}] {unidades_pendientes_de_liberar} unidad(es) de "
            "trabajo-en-vuelo liberadas por el safety-net (no deberían haber "
            "quedado sin contabilizar -- revisar si se agregó un camino nuevo "
            "sin su propio _liberar_trabajo_en_vuelo_zip)."
        )
        _liberar_trabajo_en_vuelo_zip(unidades_pendientes_de_liberar)

    shutil.rmtree(carpeta_base, ignore_errors=True)

    app_logger.info(
        f"[{ingest_id}] ZIP procesado (origen={origen}): "
        f"{resultado['aceptados']} aceptados, "
        f"{len(resultado['no_soportados'])} no soportados, "
        f"{len(resultado['errores_despacho'])} errores de despacho"
    )

    if enviar_resumen_por_email and datos_email and datos_email.get("from_email"):
        html = _generar_html_resumen_zip(
            total=len(archivos_a_despachar) + len(resultado["no_soportados"]),
            aceptados=resultado["aceptados"],
            no_soportados=resultado["no_soportados"],
            errores_despacho=resultado["errores_despacho"],
        )
        asunto = f"Re: {datos_email.get('subject') or 'Tu ZIP de facturas'}"
        # enviar_email es sincrónico/bloqueante (smtplib) -- to_thread para
        # no bloquear el event loop, mismo criterio que la Fase A.
        await asyncio.to_thread(
            orchestrator.enviar_email, datos_email["from_email"], asunto, html
        )

    return resultado


@router.post(
    "/process-invoice",
    summary="Procesar factura - GEMINI",
    tags=["Procesamiento de facturas"],
    # Tanto archivo suelto como ZIP responden 201 de inmediato y procesan en
    # background (ver _procesar_en_background) -- antes, el archivo suelto
    # devolvía 200 de forma síncrona con los datos ya extraídos, pero eso es
    # lo que producía 504 del gateway con PDFs reales (la extracción +
    # búsqueda de proveedor en BAS puede superar los 200s de nginx.conf).
    response_description="La factura quedó encolada para procesarse en background.",
    response_model=dict,
    responses={
        201: {
            "description": "La factura está siendo procesada.",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "message": "La factura está siendo procesada.",
                        "status_code": 201,
                    }
                }
            },
        },
        400: {"description": "Tipo de archivo no permitido."},
        500: {"description": "Error interno del servidor."},
    },
)
async def process_invoice(
    id: str = Form(None),  # ID único para trackear el proceso
    secret_key: str = Form(None),  # Clave secreta para autenticar
    file: UploadFile = File(
        ...,
        description="Archivo de la factura a procesar. Puede ser PDF o imagen (png, jpg, jpeg, webp, gif).",
    ),  # El archivo de la factura a procesar
):
    app_logger.info("Process Invoice Google")
    try:
        # Chequea que estén todos los campos requeridos
        if not all([id, secret_key, file]):
            missing_fields = [field for field, value in locals().items() if not value]
            raise ValueError(
                f"The following fields are required: {', '.join(missing_fields)}"
            )

        # Valida la clave secreta
        if secret_key != os.getenv("SECRET_KEY"):
            raise HTTPException(status_code=401, detail="Invalid secret key")

        # Valida la extensión del archivo
        extensiones_permitidas = ["zip", "pdf", "png", "jpg", "jpeg", "webp", "gif"]
        extension = file.filename.split(".")[-1].lower()
        if extension not in extensiones_permitidas:
            raise HTTPException(
                status_code=400,
                detail=f"Tipo de archivo no permitido: .{extension}. Solo se aceptan: {', '.join(extensiones_permitidas)}",
            )

        # Guarda el archivo localmente
        os.makedirs("downloads", exist_ok=True)
        # Prefijo uuid4 único por-request -- antes esta ruta se armaba solo
        # con el basename de file.filename, sin ningún componente único:
        # dos uploads concurrentes con el mismo nombre de archivo (nombres
        # genéricos de cámara/scanner, o alguien forzándolo a propósito acá
        # en /website-upload, que es público) se pisaban en disco, mezclando
        # el contenido de dos facturas de dos process_id distintos.
        # Truncado a 200 BYTES reales (_truncar_nombre_a_bytes, no un
        # simple [:200] de caracteres -- ese truncado por code points
        # seguía permitiendo superar el límite de 255 bytes del filesystem
        # con nombres ricos en tildes/ñ/UTF-8 multi-byte, bug real
        # encontrado en la revisión adversarial). El prefijo uuid4.hex (33
        # bytes con el "-") ya reducía el margen disponible.
        file_location = (
            f"./downloads/{uuid.uuid4().hex}-"
            f"{_truncar_nombre_a_bytes(file.filename.split('/')[-1], 200)}"
        )
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        with open(file_location, "rb") as f:
            kind = filetype.guess(f.read(262))

        if kind is None:
            os.remove(file_location)
            raise HTTPException(status_code=400, detail="Tipo de archivo no permitido.")

        app_logger.info(f"Mime type: {kind.mime}")

        # Procesa imagen o PDF -- en background (ver _procesar_en_background):
        # esperarlo acá adentro del request original es lo que producía los
        # 504 con PDFs reales (Gemini + búsqueda de proveedor en BAS puede
        # superar los 200s del gateway).
        if kind.mime.startswith("image") or kind.mime == "application/pdf":
            asyncio.create_task(
                _procesar_en_background(
                    file_location=file_location,
                    file_name=file.filename,
                    extension=extension,
                    media_type=kind.mime,
                    process_id=id,
                )
            )
            return {
                "success": True,
                "message": "La factura está siendo procesada.",
                "status_code": 201,
            }

        # Procesa ZIP -- extracción + despacho a través del pipeline
        # individual compartido (_extraer_zip_y_despachar_individualmente),
        # el mismo que usan el webhook de email y /website-upload. Único
        # caller que pasa notificar_no_soportados_por_webhook=True -- ver su
        # docstring: preserva un efecto externo real que ya existía acá
        # (y solo acá) antes de este cambio. `id` (el que mandó el caller)
        # NUNCA se usa para construir la carpeta -- eso era el path
        # traversal real (hallazgo #1 de la revisión adversarial, ver
        # docs/plan-fase1-zip-REDISEÑO.md sección 1): la carpeta se arma
        # con un ingest_id generado acá mismo, server-side.
        elif (
            kind.mime == "application/zip"
            or kind.mime == "application/x-zip-compressed"
        ):
            app_logger.info("Tenemos un ZIP")
            ingest_id = uuid.uuid4().hex
            zip_carpeta = _carpeta_ingest_zip(origen="process-invoice", ingest_id=ingest_id)
            os.makedirs(zip_carpeta, exist_ok=True)
            zip_path = f"{zip_carpeta}/{os.path.basename(file_location)}"
            shutil.move(file_location, zip_path)

            validacion = _validar_zip_rapido(zip_path)
            if not validacion["ok"]:
                shutil.rmtree(zip_carpeta, ignore_errors=True)
                raise HTTPException(status_code=400, detail=validacion["error"])

            asyncio.create_task(
                _extraer_zip_y_despachar_individualmente(
                    zip_path=zip_path,
                    ingest_id=ingest_id,
                    origen="process-invoice",
                    reservado_trabajo_en_vuelo=validacion["reservado"],
                    client_reference=id,
                    notificar_no_soportados_por_webhook=True,
                )
            )
            return {
                "success": True,
                "message": (
                    f"ZIP recibido ({len(validacion['miembros'])} archivos) -- "
                    "cada factura se procesa de forma independiente."
                ),
                "status_code": 201,
            }

        else:
            raise HTTPException(status_code=400, detail="Tipo de archivo no permitido.")

        return {
            "success": True,
            "message": "La factura está siendo procesada.",
            "status_code": 201,
        }

    except HTTPException:
        raise
    except Exception as e:
        app_logger.info(f"Error: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error interno del servidor: {str(e)}"
        )


@router.post(
    "/website-upload/init",
    summary="Crear el placeholder de una factura ANTES de subir el archivo",
    tags=["Procesamiento de facturas"],
    response_model=dict,
    responses={
        429: {"description": "Demasiadas subidas desde esta IP, reintentar más tarde."},
        500: {"description": "Error interno del servidor."},
    },
)
@limiter.limit("5/minute")
async def website_upload_init(request: Request):
    """Reserva un process_id y deja un row en PocketBase con status="pending"
    ANTES de que el navegador empiece a transferir el archivo. El frontend
    (ver /subir-factura) llama esto primero, muestra el estado localmente
    ("subiendo") mientras transfiere el archivo, y le pasa el mismo
    process_id a POST /website-upload para que reutilice este row en vez de
    crear uno nuevo -- así la cola de revisión puede mostrar la factura desde
    el instante del click, no recién cuando termina la subida.

    Best-effort: si el upsert a PocketBase falla acá, igual devolvemos un
    process_id nuevo (uuid) -- /website-upload sabe crear su propio
    placeholder "processing" si no encuentra uno ya creado con ese id, así
    que un fallo acá no bloquea la subida real, solo pierde la visibilidad
    temprana en la cola.
    """
    process_id = f"website-{uuid.uuid4()}"
    try:
        orchestrator._pb_client.upsert_invoice(
            {"process_id": process_id, "status": "pending", "error_message": ""}
        )
    except Exception as e:
        app_logger.warning(f"[{process_id}] PocketBase: error creando placeholder pending: {e}")
    return {"process_id": process_id}


@router.post(
    "/website-upload",
    summary="Procesar factura subida desde el formulario público del website",
    tags=["Procesamiento de facturas"],
    response_model=dict,
    responses={
        400: {"description": "Tipo de archivo no permitido."},
        429: {"description": "Demasiadas subidas desde esta IP, reintentar más tarde."},
        500: {"description": "Error interno del servidor."},
    },
)
@limiter.limit("5/minute")
async def website_upload(
    request: Request,  # requerido por @limiter.limit para identificar al caller por IP
    file: UploadFile = File(
        ...,
        description=(
            "Archivo de la factura a procesar. Imagen (png, jpg, jpeg, webp, "
            "gif), PDF, o ZIP conteniendo varios de esos archivos -- cada uno "
            "se procesa como una factura independiente."
        ),
    ),
    process_id: str = Form(
        None,
        description=(
            "process_id ya reservado por POST /website-upload/init. Si no se "
            "manda, se genera uno nuevo (comportamiento previo). No aplica "
            "para ZIP -- el frontend no debe llamar a /init para archivos "
            ".zip, ver SubirFacturaForm.tsx."
        ),
    ),
):
    """Puerta de entrada pública (sin secret_key) para el formulario de subida
    del website -- ver Ticket AI Dashboard, página /subir-factura. Protegida
    con rate limiting en vez del secreto compartido que usan wa-bot y el
    webhook de email, porque el caller acá es un navegador anónimo, no un
    backend de confianza que pueda guardar un secreto.
    """
    app_logger.info("Website upload")
    try:
        extensiones_permitidas = ["pdf", "png", "jpg", "jpeg", "webp", "gif", "zip"]
        extension = file.filename.split(".")[-1].lower()
        if extension not in extensiones_permitidas:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Tipo de archivo no permitido: .{extension}. Solo se aceptan: "
                    f"{', '.join(extensiones_permitidas)}."
                ),
            )

        # `process_id` (si vino) es SOLO el placeholder reservado por
        # /website-upload/init para el camino de archivo suelto -- para
        # ZIP nunca se usa para construir ninguna ruta ni como process_id
        # final de ninguna factura (eso era el path traversal real,
        # hallazgo #1 de la revisión adversarial). Se conserva únicamente
        # como client_reference (trazabilidad en logs) y, si efectivamente
        # existe un placeholder "pending" con ese id, se limpia más abajo
        # apenas se detecta que el contenido real es un ZIP -- el backend
        # es la fuente de verdad de "esto es un ZIP", no el frontend (ver
        # docs/plan-fase1-zip-REDISEÑO.md sección 7): aunque el frontend
        # haya adivinado mal (nombre no terminaba en .zip) y por eso sí
        # llamó a /init, acá se corrige solo, sin dejar ningún row
        # "pending" huérfano para siempre.
        process_id_reservado = process_id
        process_id = process_id or f"website-{uuid.uuid4()}"

        os.makedirs("downloads", exist_ok=True)
        # Prefijo uuid4 único por-request -- antes esta ruta se armaba solo
        # con el basename de file.filename, sin ningún componente único:
        # dos uploads concurrentes con el mismo nombre de archivo (nombres
        # genéricos de cámara/scanner, o alguien forzándolo a propósito acá
        # en /website-upload, que es público) se pisaban en disco, mezclando
        # el contenido de dos facturas de dos process_id distintos.
        # Truncado a 200 BYTES reales (_truncar_nombre_a_bytes, no un
        # simple [:200] de caracteres -- ese truncado por code points
        # seguía permitiendo superar el límite de 255 bytes del filesystem
        # con nombres ricos en tildes/ñ/UTF-8 multi-byte, bug real
        # encontrado en la revisión adversarial). El prefijo uuid4.hex (33
        # bytes con el "-") ya reducía el margen disponible.
        file_location = (
            f"./downloads/{uuid.uuid4().hex}-"
            f"{_truncar_nombre_a_bytes(file.filename.split('/')[-1], 200)}"
        )
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        with open(file_location, "rb") as f:
            kind = filetype.guess(f.read(262))

        if kind is None:
            os.remove(file_location)
            raise HTTPException(status_code=400, detail="Tipo de archivo no permitido.")

        app_logger.info(f"Mime type: {kind.mime}")

        if kind.mime in ("application/zip", "application/x-zip-compressed"):
            if process_id_reservado:
                # Limpieza del placeholder huérfano: solo si REALMENTE
                # existe y sigue en "pending" (nunca tocar una factura real
                # en otro estado -- ver razonamiento completo en el
                # rediseño, sección 7. process_id es un uuid4 de 128 bits,
                # no adivinable, así que este chequeo es una salvaguarda
                # razonable contra un caller que reuse a propósito el
                # process_id de otra factura ajena).
                try:
                    placeholder = orchestrator._pb_client.get_invoice_by_process_id(
                        process_id_reservado
                    )
                    if placeholder and placeholder.get("status") == "pending":
                        orchestrator._pb_client.soft_delete_invoice(
                            process_id_reservado,
                            # deleted_by es relation -> users (ver migración
                            # 1783483896_add_soft_delete_fields.js) -- acá no
                            # hay ningún humano involucrado, es limpieza
                            # automática, así que va None (opcional,
                            # minSelect=0); la identidad del actor queda en
                            # `reason`. Bug real encontrado en producción
                            # (PocketBase 400 validation_missing_rel_records
                            # con un string acá) durante el smoke test
                            # post-deploy de la Fase 1 de ZIP.
                            deleted_by=None,
                            reason=(
                                "sistema:deteccion-zip -- Placeholder reservado "
                                "antes de conocer el contenido real -- resultó "
                                "ser un ZIP, no una factura individual."
                            ),
                        )
                except Exception as e:
                    app_logger.warning(
                        f"No se pudo limpiar el placeholder huérfano "
                        f"{process_id_reservado}: {e}"
                    )

            ingest_id = uuid.uuid4().hex
            zip_carpeta = _carpeta_ingest_zip(origen="website-upload", ingest_id=ingest_id)
            os.makedirs(zip_carpeta, exist_ok=True)
            zip_path = f"{zip_carpeta}/{os.path.basename(file_location)}"
            shutil.move(file_location, zip_path)

            validacion = _validar_zip_rapido(zip_path)
            if not validacion["ok"]:
                shutil.rmtree(zip_carpeta, ignore_errors=True)
                raise HTTPException(status_code=400, detail=validacion["error"])

            asyncio.create_task(
                _extraer_zip_y_despachar_individualmente(
                    zip_path=zip_path,
                    ingest_id=ingest_id,
                    origen="website-upload",
                    reservado_trabajo_en_vuelo=validacion["reservado"],
                    client_reference=process_id_reservado,
                )
            )
            return {
                "success": True,
                "message": (
                    f"ZIP recibido ({len(validacion['miembros'])} archivos) -- "
                    "cada factura se procesa de forma independiente."
                ),
                "status_code": 201,
            }

        if not (kind.mime.startswith("image") or kind.mime == "application/pdf"):
            os.remove(file_location)
            raise HTTPException(status_code=400, detail="Tipo de archivo no permitido.")

        asyncio.create_task(
            _procesar_en_background(
                file_location=file_location,
                file_name=file.filename,
                extension=extension,
                media_type=kind.mime,
                process_id=process_id,
            )
        )
        return {
            "success": True,
            "message": "La factura está siendo procesada.",
            "status_code": 201,
        }
    except HTTPException:
        raise
    except Exception as e:
        app_logger.info(f"Error: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error interno del servidor: {str(e)}"
        )


@router.get(
    "/queue",
    summary="Get current queue status",
    description="Returns information about currently active invoice processing tasks",
    response_description="A dictionary containing details about active comparisons in the processing queue",
)
async def get_queue_status():
    """
    Endpoint to check the current status of the invoice processing queue.

    Returns:
        dict: Contains 'queue_size' key with a dictionary of active processing tasks,
              where each key is a process ID and value contains file processing details

    Example response:
        {
            "queue_size": {
                "process_123": {
                    "file_name": "invoice.pdf",
                    "file_extension": ".pdf",
                    "file_path": "/tmp/invoice.pdf",
                    "media_type": "application/pdf",
                    "process_id": "process_123"
                }
            }
        }
    """
    return {"queue_size": orchestrator.active_comparisons}


@router.post(
    "/webhook",
    summary="Receive webhook notifications",
    description="Endpoints to receive webhook notifications from external services",
    response_description="A dictionary containing details about active comparisons in the processing queue",
)
async def webhook_endpoint(request: Request):
    app_logger.info(f"📨 Webhook recibido: {request.url}")
    try:
        data = await request.json()
        app_logger.info(f"📨 Webhook recibido: {data}")
        from_email = data.get("from_email")
        from_name = data.get("from_name")
        subject = data.get("subject")
        body = data.get("body")
        attachments = data.get("attachments")
        to_email = data.get("to_email")
        file_name = data.get("file_name")

        # to_thread -- mismo motivo que download_file_from_url un poco más
        # abajo (bug real: el primer fix cerró un vector de bloqueo del
        # event loop y dejó exactamente el mismo abierto acá, 2 líneas
        # antes, en el mismo flujo sin autenticación).
        file_type = await asyncio.to_thread(orchestrator.get_file_type_from_url, attachments)
        app_logger.info(f"📄 Tipo de archivo detectado: {file_type}")

        process_id = str(uuid.uuid4())
        temp_dir = f"./downloads/{process_id}"
        app_logger.info(f"🆔 Process ID generado: {process_id}")
        app_logger.info(f"📁 Creando directorio temporal: {temp_dir}")
        os.makedirs(temp_dir, exist_ok=True)

        # file_name viene crudo del body de este webhook -- que no tiene
        # NINGUNA autenticación (sin secret_key, sin X-Invoicy-Secret, sin
        # rate limiting). Antes de este fix se usaba directo para armar
        # file_location, sin sanear -- un file_name como "../../algo"
        # permitía escribir fuera de temp_dir con contenido descargado de
        # `attachments` (también controlado por el caller). Doble garantía,
        # no solo saneo de caracteres: (1) _sanear_nombre_para_process_id
        # reduce el nombre a un basename con charset seguro (mismo criterio
        # que ya se usa para nombres de miembro dentro de un ZIP), y (2) se
        # verifica que la ruta final resuelva DENTRO de temp_dir antes de
        # escribir nada -- mismo patrón que el chequeo de Zip Slip.
        nombre_archivo_seguro = _sanear_nombre_para_process_id(file_name or "archivo")
        file_location = os.path.join(temp_dir, nombre_archivo_seguro)
        if not os.path.realpath(file_location).startswith(
            os.path.realpath(temp_dir) + os.sep
        ):
            app_logger.error(f"❌ file_name inválido/inseguro: {file_name!r}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return {
                "success": False,
                "status": "error",
                "message": "Nombre de archivo inválido.",
                "id": process_id,
            }

        app_logger.info(f"⬇️ Descargando archivo desde: {attachments}")
        # to_thread -- requests.get es bloqueante (ver el timeout agregado
        # en download_file_from_url); antes corría directo en el event loop
        # de un endpoint sin ninguna autenticación, mismo problema que ya
        # se corrigió para fire_webhook/enviar_email.
        doc_saved = await asyncio.to_thread(
            orchestrator.download_file_from_url, attachments, file_location
        )
        if not doc_saved:
            app_logger.error(
                f"❌ Error al descargar archivo para process_id: {process_id}"
            )
            shutil.rmtree(temp_dir)
            return {
                "success": False,
                "status": "error",
                "message": "Error al descargar el archivo",
                "id": process_id,
            }
        app_logger.info(f"✅ Archivo descargado exitosamente: {file_location}")

        files_to_process = []
        files_skipped = []
        total_count = 0
        type_ = "unknown"

        if file_type in ["application/zip", "application/x-zip-compressed"]:
            app_logger.info(f"📦 Procesando archivo ZIP: {file_name}")
            # process_id ya es un uuid4 generado server-side más arriba
            # (línea "process_id = str(uuid.uuid4())") -- se reusa tal
            # cual como ingest_id, sin generar uno nuevo. Se mueve del
            # temp_dir genérico (compartido con el camino no-zip) a la
            # carpeta canónica de ingesta ZIP para que
            # _extraer_zip_y_despachar_individualmente limpie todo junto
            # al terminar (.zip crudo + miembros extraídos).
            ingest_id = process_id
            zip_carpeta = _carpeta_ingest_zip(origen="email", ingest_id=ingest_id)
            os.makedirs(zip_carpeta, exist_ok=True)
            zip_path = f"{zip_carpeta}/{os.path.basename(file_location)}"
            shutil.move(file_location, zip_path)
            shutil.rmtree(temp_dir, ignore_errors=True)

            validacion = _validar_zip_rapido(zip_path)
            if not validacion["ok"]:
                app_logger.error(f"❌ {validacion['error']} ({file_name})")
                shutil.rmtree(zip_carpeta, ignore_errors=True)
                # Notificación de rechazo total -- misma semántica que el
                # caso error_zip async (ver más abajo en
                # _extraer_zip_y_despachar_individualmente): worker() nunca
                # mandó un email de error, solo fire_webhook, así que acá se
                # replica exactamente eso, sin inventar un email de error
                # que no tiene precedente. asyncio.create_task (no await) --
                # el endpoint responde de inmediato, no espera al webhook.
                asyncio.create_task(
                    orchestrator.fire_webhook(
                        {
                            "process_id": process_id,
                            "file_name": file_name,
                            "error": validacion["error"],
                            "status": "error",
                            "success": False,
                        }
                    )
                )
                return {
                    "success": False,
                    "status": "error",
                    "message": validacion["error"],
                    "id": process_id,
                }
            app_logger.info(
                f"📊 ZIP contiene {len(validacion['miembros'])} archivos -- "
                "despachando cada uno al pipeline individual"
            )
            # No se espera (await) a propósito -- el endpoint responde de
            # inmediato y cada archivo se procesa en background por
            # _extraer_zip_y_despachar_individualmente, igual que un archivo
            # suelto vía _procesar_en_background.
            #
            # Compatibilidad con el pipeline viejo (worker()/job_queue, que
            # esta rama deja de usar): worker() disparaba, por cada
            # adjunto, un email de confirmación al remitente Y un
            # fire_webhook por factura (éxito o error) -- ver hallazgo #5
            # de la revisión adversarial. Acá se preservan los DOS
            # efectos, con la granularidad que corresponde a cada uno
            # (decisión confirmada, docs/plan-fase1-zip-REDISEÑO.md
            # sección 4): UN email de confirmación por ZIP (no uno por
            # factura -- evita mandarle 50 emails a quien mandó un ZIP de
            # 50 facturas), y fire_webhook por FACTURA (preserva la
            # granularidad que ya consumen integraciones externas).
            asyncio.create_task(
                _extraer_zip_y_despachar_individualmente(
                    zip_path=zip_path,
                    ingest_id=ingest_id,
                    origen="email",
                    reservado_trabajo_en_vuelo=validacion["reservado"],
                    enviar_resumen_por_email=True,
                    datos_email={"from_email": from_email, "subject": subject},
                    on_item_completado=orchestrator.fire_webhook,
                )
            )
            return {
                "success": True,
                "status": "processing",
                "process_id": process_id,
                "type": "zip",
                "total_count": len(validacion["miembros"]),
                "message": (
                    f"ZIP recibido ({len(validacion['miembros'])} archivos) -- "
                    "cada factura se procesa de forma independiente y va a "
                    "aparecer en la cola de revisión por separado."
                ),
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            }

        elif file_type == "application/pdf" or file_type.startswith("image/"):
            type_ = "pdf" if file_type == "application/pdf" else "image"
            total_count = 1
            app_logger.info(
                f"📄 Archivo individual detectado: {file_name} (tipo: {type_})"
            )
            files_to_process.append(
                {"name": file_name, "path": file_location, "mime": file_type}
            )
        else:
            app_logger.error(f"❌ Tipo de archivo no válido: {file_type}")
            os.remove(file_location)
            shutil.rmtree(temp_dir)
            return {
                "success": False,
                "status": "error",
                "message": f"Invalid file type: {file_type}",
                "id": process_id,
            }

        if not files_to_process:
            app_logger.error(
                f"❌ No se encontraron archivos válidos para procesar en {process_id}"
            )
            shutil.rmtree(temp_dir)
            return {
                "success": False,
                "status": "error",
                "message": "No files to process",
                "id": process_id,
            }

        app_logger.info(f"📋 Resumen de clasificación:")
        app_logger.info(f"   ✅ Archivos para procesar: {len(files_to_process)}")
        app_logger.info(f"   ⚠️ Archivos omitidos: {len(files_skipped)}")

        # Preparar items para el job
        items_to_process = []
        app_logger.info(f"🔧 Preparando items para el job...")
        for file_info in files_to_process:
            ext = os.path.splitext(file_info["name"])[1]
            item = {
                "file_name": file_info["name"],
                "file_extension": ext,
                "file_path": file_info["path"],
                "media_type": file_info["mime"],
                "process_id": process_id,
            }
            items_to_process.append(item)
            app_logger.info(
                f"   📄 Item preparado: {file_info['name']} ({file_info['mime']})"
            )

        # Encolar job
        job = {
            "process_id": process_id,
            "from_email": from_email,
            "subject": subject,
            "temp_dir": file_location,
            "items_to_process": items_to_process,
        }
        app_logger.info(
            f"📤 Encolando job {process_id} con {len(items_to_process)} items"
        )
        await orchestrator.job_queue.put(job)
        app_logger.info(f"✅ Job {process_id} encolado exitosamente")

        # Respuesta inmediata
        response_type = type_ if len(files_to_process) == 1 else "mixed"
        response = {
            "success": True,
            "status": "enqueued",
            "process_id": process_id,
            "type": response_type,
            "total_count": total_count,
            "to_process_count": len(files_to_process),
            "skipped_count": len(files_skipped),
            "files_to_process": files_to_process,
            "files_skipped": files_skipped,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        }

        app_logger.info(f"📤 Enviando respuesta inmediata para {process_id}:")
        app_logger.info(f"   🆔 Process ID: {process_id}")
        app_logger.info(f"   📊 Tipo: {response_type}")
        app_logger.info(f"   📈 Total archivos: {total_count}")
        app_logger.info(f"   ✅ Para procesar: {len(files_to_process)}")
        app_logger.info(f"   ⚠️ Omitidos: {len(files_skipped)}")

        return response

    except Exception as e:
        app_logger.error(f"❌ Error crítico procesando webhook: {str(e)}")
        if "temp_dir" in locals():
            app_logger.info(f"🧹 Limpiando directorio temporal por error: {temp_dir}")
            shutil.rmtree(temp_dir)
        return {
            "success": False,
            "status": "error",
            "message": "Error interno al procesar el webhook",
            "id": process_id if "process_id" in locals() else "unknown",
        }


@router.post(
    "/retry-op/{process_id}",
    summary="Reintentar la SIMULACIÓN (dry-run) de Orden de Pago del pipeline automático",
    tags=["Procesamiento de facturas"],
    response_description="Resultado del reintento de la simulación de orden de pago.",
)
async def retry_orden_pago(process_id: str):
    """
    ⚠️ ESTE ENDPOINT NUNCA ESCRIBE UNA ORDEN DE PAGO REAL EN BAS. Reintenta
    SOLO la simulación (dry_run=True, hardcodeado a propósito) del paso de
    Orden de Pago que corre automáticamente en InvoiceOrchestrator.worker()
    (o en /process-invoice) -- sirve para refrescar `bas_processing_status`
    cuando esa simulación falló por un motivo transitorio (BAS caído,
    timeout, etc.), NO para resolver una Orden de Pago real atascada.

    Para generar o reintentar un pago REAL, usar
    POST /payment-orders/{process_id}/create -- ese es el ÚNICO endpoint
    donde `dry_run` pasa a False, y ya es idempotente (no crea una segunda
    OP real si `payment_orders.status` ya es "success") y reporta
    explícitamente si una OP real quedó creada en BAS sin aplicar
    (`_op_sin_aplicar`, para reconciliar a mano -- NO reintentar ciegamente
    ahí tampoco).

    Por qué `dry_run` está hardcodeado en `True` y no se expone como
    parámetro: el payload que arma este endpoint es deliberadamente mínimo
    (no reconstruye Items/CentroApropiacion/etc., y el medio de pago viene
    fijo como "Efectivos") -- si se pusiera dry_run=False acá, cualquier
    factura que en realidad debía pagarse por transferencia/cheque/tarjeta
    terminaría con una Orden de Pago real registrada como pago en EFECTIVO,
    que es peor que el estado actual (simulación inerte). El fix correcto
    para "reintentar un pago real" es siempre por `/payment-orders/create`,
    que sí arma el payload completo y respeta el método de pago elegido.

    Idempotente: si `orden_pago_status` ya es "success" (de una simulación
    previa), NO se reintenta -- se devuelve 200 informando que ya estaba
    resuelta.

    No reconstruye el payload completo de ComprobantesCompra (solo el mínimo:
    proveedor_codigo, comprobante_prefijo/numero, total): asume que la factura
    ya está registrada en BAS y llama a crear_orden_de_pago_desde_factura con
    `registrar_si_no_existe=False`, así que si por algún motivo la factura NO
    está en BAS, esto falla explícito en vez de registrar una factura
    reconstruida a medias con datos incompletos.
    """
    status = orchestrator._pb_client.get_bas_processing_status(process_id)
    if status is None:
        raise HTTPException(
            status_code=404,
            detail=f"No se encontró bas_processing_status para process_id={process_id}.",
        )

    invoice = orchestrator._pb_client.get_invoice_by_process_id(process_id)
    if invoice is None:
        raise HTTPException(
            status_code=404,
            detail=f"No se encontró la factura (invoices) relacionada a process_id={process_id}.",
        )

    if status.get("orden_pago_status") == "success":
        return {
            "success": True,
            "process_id": process_id,
            "already_resolved": True,
            "dry_run": True,
            "message": (
                "La SIMULACIÓN de orden de pago ya estaba resuelta (success); no se "
                "reintenta. Esto no implica que exista una Orden de Pago real en BAS -- "
                "para eso usar /payment-orders/{process_id}/create."
            ),
            "bas_processing_status": status,
        }

    proveedor_codigo = status.get("proveedor_codigo")
    prefijo_externo = status.get("comprobante_prefijo")
    numero_externo = status.get("comprobante_numero")
    total = invoice.get("total")

    faltantes = [
        nombre
        for nombre, valor in (
            ("proveedor_codigo", proveedor_codigo),
            ("comprobante_prefijo", prefijo_externo),
            ("total", total),
        )
        if valor in (None, "")
    ]
    if faltantes:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No se puede reintentar la orden de pago para process_id={process_id}: "
                f"faltan datos mínimos en PocketBase: {', '.join(faltantes)}."
            ),
        )

    resultado = {"orden_pago": None, "error": None}
    try:
        with _lock_comprobante(proveedor_codigo, prefijo_externo, numero_externo):
            flujo = orchestrator._bas_client.crear_orden_de_pago_desde_factura(
                empresa=BAS_EMPRESA,
                sucursal=BAS_SUCURSAL,
                comprobante_factura="MA",
                prefijo_externo=prefijo_externo,
                numero_externo=numero_externo,
                importe=total,
                fecha_externo=invoice.get("fecha_emision"),
                prefijo_op=BAS_PREFIJO_TALONARIO_OP,
                caja_op=BAS_CAJA,
                prefijo_ctacte="P",
                codigo_ctacte=proveedor_codigo,
                pagos={"Efectivos": [{"MedioPago": "1", "Importe": total, "IngresooEgreso": "E"}]},
                comprobante_compra_payload=None,
                imputacion_contable=BAS_IMPUTACION_CONTABLE_PROVEEDORES,
                registrar_si_no_existe=False,
                dry_run=True,
            )
        resultado["orden_pago"] = flujo.get("orden_pago")
        if isinstance(resultado["orden_pago"], dict) and resultado["orden_pago"].get("_error"):
            _detalle = resultado["orden_pago"].get("detail")
            _sufijo = " -- requiere soporte de BAS, no reintentar" if resultado["orden_pago"].get("_requiere_soporte_bas") else ""
            resultado["error"] = f"Orden de pago falló{_sufijo}: {_detalle}"
    except BasApiError as e:
        resultado["error"] = f"BasApiError {e.status_code} en {e.path}: {e.detail}"
        app_logger.error(f"[{process_id}] retry-op: fallo BAS: {resultado['error']}")
    except Exception as e:
        resultado["error"] = str(e)
        app_logger.error(f"[{process_id}] retry-op: error inesperado: {e}")

    # Schema de bas_processing_status.orden_pago_status solo acepta
    # pending/success/failed (no "error").
    nuevo_status = "failed" if resultado["error"] else "success"
    retry_count_actual = status.get("retry_count") or 0
    actualizado = orchestrator._pb_client.upsert_bas_processing_status(
        process_id,
        orden_pago_status=nuevo_status,
        orden_pago_error=resultado["error"],
        retry_count=retry_count_actual + 1,
        last_attempt_at=datetime.datetime.utcnow().isoformat() + "Z",
    )

    return {
        "success": resultado["error"] is None,
        "process_id": process_id,
        "already_resolved": False,
        "dry_run": True,
        "nota": (
            "Esta es una simulación (dry_run) del pipeline automático, no una "
            "escritura real en BAS. Para generar el pago real, usar "
            "POST /payment-orders/{process_id}/create."
        ),
        "orden_pago": resultado["orden_pago"],
        "error": resultado["error"],
        "bas_processing_status": actualizado or status,
    }


def _verificar_secreto_invoicy(x_invoicy_secret: Optional[str]) -> None:
    """Mismo SECRET_KEY que ya protege /process-invoice, pero vía header en
    vez de Form: estos dos endpoints son JSON/streaming, no multipart
    upload. Pensados para ser llamados EXCLUSIVAMENTE server-side desde el
    dashboard Next.js (que ya valida la sesión de "users" antes de reenviar
    acá) -- nunca directo desde el navegador, a diferencia de /retry-op."""
    if not x_invoicy_secret or x_invoicy_secret != os.getenv("SECRET_KEY"):
        raise HTTPException(status_code=401, detail="Invalid secret key")


def _drive_credentials():
    """Mismas credenciales/scope que InvoiceOrchestrator.subir_archivo_a_drive
    -- drive.file alcanza para leer de vuelta un archivo que este mismo
    service account subió (el scope es "por-archivo creado/abierto por la
    app", no hace falta escalar a drive.readonly)."""
    client_email = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
    private_key = (os.getenv("GOOGLE_PRIVATE_KEY") or "").replace("\\n", "\n")
    if not client_email or not private_key:
        return None
    return service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "client_email": client_email,
            "private_key": private_key,
            "token_uri": "https://accounts.google.com/o/oauth2/token",
        },
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )


@router.get(
    "/invoices/{process_id}/file",
    summary="Proxy del archivo original de una factura (Drive o PocketBase)",
    tags=["Procesamiento de facturas"],
)
async def obtener_archivo_factura(
    process_id: str,
    x_invoicy_secret: Optional[str] = Header(default=None, alias="X-Invoicy-Secret"),
):
    """Streamea el archivo original (imagen/PDF) de una factura para el panel
    de revisión del dashboard. Dos orígenes posibles según cómo haya
    ingresado la factura:
      - drive_file_id (flujo viejo de email, worker()/job_queue): Google
        Drive, con scope drive.file y sin permissions().create() (privado al
        service account) -- por eso hace falta este proxy en vez de un
        iframe directo a drive.google.com.
      - documento_original (website-upload, /process-invoice, /upload del
        dashboard): campo "file" nativo de PocketBase, protected=true --
        hace falta un file token de corta duración (ver
        PocketBaseClient.obtener_file_token) para leerlo.
    """
    _verificar_secreto_invoicy(x_invoicy_secret)

    invoice = orchestrator._pb_client.get_invoice_by_process_id(process_id)
    if invoice is None:
        raise HTTPException(
            status_code=404, detail=f"No hay factura para process_id={process_id}."
        )

    if invoice.get("drive_file_id"):
        credentials = _drive_credentials()
        if credentials is None:
            raise HTTPException(
                status_code=500, detail="Faltan credenciales de Google Drive en el servidor."
            )
        credentials.refresh(google_auth_requests.Request())

        upstream = requests.get(
            f"https://www.googleapis.com/drive/v3/files/{invoice['drive_file_id']}",
            params={"alt": "media", "supportsAllDrives": "true"},
            headers={"Authorization": f"Bearer {credentials.token}"},
            stream=True,
            timeout=30,
        )
        if upstream.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Google Drive respondió {upstream.status_code} para {invoice['drive_file_id']}.",
            )
        return StreamingResponse(
            upstream.iter_content(chunk_size=65536),
            media_type=upstream.headers.get("Content-Type", "application/octet-stream"),
            headers={"Content-Disposition": "inline"},
        )

    if invoice.get("documento_original"):
        file_token = orchestrator._pb_client.obtener_file_token()
        if not file_token:
            raise HTTPException(
                status_code=502, detail="No se pudo obtener un token de archivo de PocketBase."
            )
        pb_base_url = (os.getenv("POCKETBASE_URL") or "").rstrip("/")
        file_url = (
            f"{pb_base_url}/api/files/invoices/{invoice['id']}/{invoice['documento_original']}"
        )
        upstream = requests.get(
            file_url, params={"token": file_token}, stream=True, timeout=30
        )
        if upstream.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"PocketBase respondió {upstream.status_code} al pedir el archivo.",
            )
        return StreamingResponse(
            upstream.iter_content(chunk_size=65536),
            media_type=upstream.headers.get("Content-Type", "application/octet-stream"),
            headers={"Content-Disposition": "inline"},
        )

    raise HTTPException(
        status_code=404, detail=f"No hay archivo original para process_id={process_id}."
    )


@router.post(
    "/invoices/{process_id}/retry-extraction",
    summary="Reintenta manualmente la extracción de una factura en status=error",
    tags=["Procesamiento de facturas"],
)
async def reintentar_extraccion(
    process_id: str,
    x_invoicy_secret: Optional[str] = Header(default=None, alias="X-Invoicy-Secret"),
):
    """Vuelve a correr el mismo pipeline de _procesar_en_background() para
    una factura que quedó en status="error" -- re-descarga el archivo
    original desde PocketBase (documento_original, adjuntado desde el
    arranque del procesamiento, ver _procesar_imagen_o_pdf) en vez de
    pedirle al usuario que lo suba de nuevo. Mismo process_id, así que el
    upsert_invoice de siempre actualiza el mismo registro en vez de crear
    uno nuevo -- el "Reintentar" del dashboard llama acá (ver
    ticket-ai-dashboard/app/api/invoices/[processId]/retry-extraction/route.ts).
    """
    _verificar_secreto_invoicy(x_invoicy_secret)

    invoice = orchestrator._pb_client.get_invoice_by_process_id(process_id)
    if invoice is None:
        raise HTTPException(
            status_code=404, detail=f"No hay factura para process_id={process_id}."
        )
    if invoice.get("status") != "error":
        raise HTTPException(
            status_code=409,
            detail=(
                "Solo se puede reintentar una factura en estado 'error' "
                f"(estado actual: {invoice.get('status')})."
            ),
        )
    if not invoice.get("documento_original"):
        raise HTTPException(
            status_code=422,
            detail=(
                "Esta factura no tiene el archivo original guardado -- "
                "subila de nuevo desde /subir-factura."
            ),
        )

    file_token = orchestrator._pb_client.obtener_file_token()
    if not file_token:
        raise HTTPException(
            status_code=502, detail="No se pudo obtener un token de archivo de PocketBase."
        )
    pb_base_url = (os.getenv("POCKETBASE_URL") or "").rstrip("/")
    file_url = (
        f"{pb_base_url}/api/files/invoices/{invoice['id']}/{invoice['documento_original']}"
    )
    upstream = requests.get(file_url, params={"token": file_token}, timeout=30)
    if upstream.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"PocketBase respondió {upstream.status_code} al pedir el archivo original.",
        )

    file_name = invoice["documento_original"]
    extension = file_name.split(".")[-1].lower()
    kind = filetype.guess(upstream.content[:262])
    media_type = kind.mime if kind else upstream.headers.get(
        "Content-Type", "application/octet-stream"
    )

    os.makedirs("downloads", exist_ok=True)
    # Prefijo "retry-" para no pisar un archivo que otro proceso pueda estar
    # escribiendo con el mismo nombre original en paralelo. `file_name`
    # viene de documento_original (un valor guardado en PocketBase, no
    # input directo de este request) -- pero mismo criterio que el fix H1
    # del webhook de email: nunca asumir que un nombre ya está saneado por
    # venir de "más atrás" en el flujo, sino verificar la ruta final
    # estructuralmente antes de escribir nada.
    nombre_archivo_seguro = _sanear_nombre_para_process_id(file_name or "archivo")
    file_location = f"./downloads/retry-{process_id}-{nombre_archivo_seguro}"
    if not os.path.realpath(file_location).startswith(
        os.path.realpath("./downloads") + os.sep
    ):
        raise HTTPException(status_code=422, detail="Nombre de archivo original inválido.")
    with open(file_location, "wb") as f:
        f.write(upstream.content)

    asyncio.create_task(
        _procesar_en_background(
            file_location=file_location,
            file_name=file_name,
            extension=extension,
            media_type=media_type,
            process_id=process_id,
        )
    )
    return {
        "success": True,
        "message": "Reintentando la extracción.",
        "status_code": 201,
    }


class CrearOrdenPagoBody(BaseModel):
    metodo_pago: str
    # gt=0: nunca 0 ni negativo -- ver validación adicional server-side más
    # abajo, que además exige que no supere el total de la factura (permite
    # pago parcial, nunca un sobre-pago).
    monto: Optional[float] = Field(default=None, gt=0)
    requested_by: str  # id de PocketBase (colección "users"), lo resuelve el dashboard Next.js
    # Solo aplica a metodo_pago="cheque": número del cheque de TERCEROS que se
    # está endosando para pagar (BAS lo exige como NumeroExterno en el array
    # Cheques -- confirmado en vivo 2026-07-21, ver METODO_PAGO_ARRAY_BAS).
    numero_cheque: Optional[str] = None
    # Solo aplica a metodo_pago="tarjeta": número de la tarjeta usada.
    numero_tarjeta: Optional[str] = None
    # Solo aplica a metodo_pago="transferencia": número/referencia de la
    # operación bancaria. Opcional -- si no viene, se usa el process_id como
    # referencia (BAS exige el campo igual, pero no depende de un dato real
    # para funcionar).
    numero_transferencia: Optional[str] = None


@router.post(
    "/payment-orders/{process_id}/create",
    summary="Crear la Orden de Pago real en BAS para una factura confirmada",
    tags=["Procesamiento de facturas"],
)
async def crear_orden_pago(
    process_id: str,
    body: CrearOrdenPagoBody,
    x_invoicy_secret: Optional[str] = Header(default=None, alias="X-Invoicy-Secret"),
):
    """
    ÚNICO lugar del sistema donde `dry_run` finalmente pasa a False. Exige
    invoices.review_status == "confirmed" -- se revalida server-side, no se
    confía en que el caller ya lo haya chequeado (pb_hooks/invoices.pb.js
    protege el dato, pero la decisión de negocio de "esto ya se puede pagar"
    se revalida acá también).

    NO reusa bas_processing_status.comprobante_registrado a ciegas: ese flag
    puede venir de un intento dry_run=True de la ingesta automática (que
    nunca escribió nada real en BAS -- procesar_factura_en_bas nunca pasa
    dry_run explícito, así que corre siempre con el default True). Se arma
    el comprobante_compra_payload completo igual que procesar_factura_en_bas
    y se deja que crear_orden_de_pago_desde_factura haga su propio chequeo
    real (GET) antes de decidir si hace falta registrar.

    Lee invoice_items DE POCKETBASE (no de la extracción original) para que
    correcciones hechas durante la revisión humana se reflejen en el pago.

    Sincrónico de punta a punta (mismo patrón que retry_orden_pago): escribe
    un row "processing" en payment_orders ANTES de llamar a BAS -- sobrevive
    a un crash/timeout de este proceso -- y lo actualiza a success/failed al
    terminar.
    """
    _verificar_secreto_invoicy(x_invoicy_secret)

    if body.metodo_pago not in METODO_PAGO_ARRAY_BAS:
        raise HTTPException(status_code=422, detail=f"metodo_pago inválido: {body.metodo_pago}")

    invoice = orchestrator._pb_client.get_invoice_by_process_id(process_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"No se encontró la factura para process_id={process_id}.")
    if invoice.get("review_status") != "confirmed":
        raise HTTPException(status_code=409, detail="La factura todavía no fue confirmada.")

    # Idempotencia PRIMERO, antes de cualquier re-derivación o validación:
    # un doble click, un reintento de red del dashboard, o dos requests
    # concurrentes no deben crear una SEGUNDA Orden de Pago real en BAS para
    # la misma factura (pagaría al proveedor dos veces). Se chequea acá
    # arriba de todo -- igual que ya hace retry_orden_pago con
    # bas_processing_status -- para que una factura YA pagada corte de
    # inmediato aunque sus datos hayan cambiado después (ej. quedó
    # soft-eliminada): no tiene sentido re-validar ni re-derivar nada si el
    # pago real ya existe y fue exitoso.
    existente = orchestrator._pb_client.get_payment_order(process_id)
    if existente and existente.get("status") == "success":
        return {
            "success": True,
            "process_id": process_id,
            "already_resolved": True,
            "message": "La orden de pago ya fue creada y aplicada exitosamente (success); no se genera un pago duplicado.",
            "payment_order": existente,
        }
    # Si hubo un intento real previo que llegó a crear una Orden de Pago en
    # BAS (bas_op_prefijo/bas_op_numero seteados, ver más abajo donde se
    # persisten) pero no se pudo aplicar, NO reintentar automáticamente: el
    # comprobante YA existe en BAS, registrado con lo que sea que tenía la
    # lógica vigente en ESE momento -- podría ser de antes de este fix
    # (neto puro, sin TotalIva/ImporteIva). La API de lectura de BAS
    # (ConsultaComprobantesExternos) no expone el Total/Vencimientos real
    # ya registrado (confirmado, ver investigación de causa raíz), así que
    # no hay forma de verificar desde acá si el `total_bruto` recalculado
    # localmente coincide con lo que BAS realmente tiene abierto para ese
    # comprobante. Aplicar un monto que no coincida vuelve a fallar con
    # "saldo del vencimiento no puede ser negativo" y crea OTRA OP
    # huérfana (confirmado real: factura MEDINA FLOR LUCIO DANIEL,
    # 00003-00000021, ya tiene dos -- 00001-00035009, 00001-00035010).
    if existente and existente.get("status") != "success" and existente.get("bas_op_prefijo"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Esta factura ya tiene un intento real fallido contra BAS (Orden de Pago "
                f"{existente.get('bas_op_prefijo')}-{existente.get('bas_op_numero')}, creada pero sin "
                "aplicar). No se puede reintentar automáticamente: el comprobante ya existe en BAS y no "
                "hay forma de confirmar desde acá con qué importe quedó registrado su vencimiento. "
                "Reconciliá manualmente en BAS antes de continuar."
            ),
        )

    status_bas = orchestrator._pb_client.get_bas_processing_status(process_id) or {}

    # Re-derivar proveedor_codigo DESDE la factura actual (invoices), no
    # desde bas_processing_status: ese registro se escribió durante el
    # procesamiento automático (dry_run), ANTES de cualquier corrección
    # humana hecha en la revisión. Si un revisor corrigió el CUIT del
    # emisor, reusar el proveedor_codigo viejo pagaría/aplicaría el
    # comprobante contra el proveedor equivocado. `_buscar_proveedor_bas` es
    # idempotente (cachea por CUIT, SOLO LECTURA -- ver P0-B) -- volver a
    # llamarlo acá es seguro. Ver docs/plan-validaciones-pre-bas.md, V5.2.
    #
    # BUG REAL corregido acá (P0-E, 2026-08-12): este call site todavía
    # llamaba a `_obtener_o_verificar_proveedor_bas`, el nombre viejo que
    # P0-B renombró/reemplazó por `_buscar_proveedor_bas` -- desde P0-B este
    # endpoint (el ÚNICO que todavía puede escribir de verdad en BAS)
    # crasheaba con AttributeError en cualquier invocación real. El
    # comportamiento de abajo (422 si no hay proveedor) ya era exactamente
    # el correcto para "no existe en BAS" -- no hacía falta ningún otro
    # cambio, solo apuntar al método que realmente existe.
    proveedor_actual = orchestrator._buscar_proveedor_bas(
        invoice.get("emisor_cuit"), invoice.get("emisor_nombre", "")
    )
    proveedor_codigo = proveedor_actual.get("Codigo") if proveedor_actual else None
    if not proveedor_codigo:
        raise HTTPException(
            status_code=422,
            detail="No se pudo resolver el proveedor en BAS (falta o es inválido el CUIT del emisor).",
        )
    if status_bas.get("proveedor_codigo") and status_bas.get("proveedor_codigo") != proveedor_codigo:
        app_logger.warning(
            f"[{process_id}] crear-orden-pago: proveedor_codigo re-derivado ({proveedor_codigo}) "
            f"difiere del guardado en bas_processing_status ({status_bas.get('proveedor_codigo')}) -- "
            "probablemente el CUIT del emisor fue corregido durante la revisión."
        )

    metodo_bas = orchestrator._pb_client.get_payment_method(body.metodo_pago)
    if metodo_bas is None or not metodo_bas.get("bas_medio_pago_codigo"):
        raise HTTPException(
            status_code=422,
            detail=f"No hay código BAS configurado para '{body.metodo_pago}' (bas_payment_methods).",
        )

    monto = body.monto if body.monto is not None else invoice.get("total")
    total_factura = invoice.get("total")
    # Tolerancia de 1 centavo por redondeo de floats -- nunca se permite un
    # sobre-pago real (pagar más de lo que la factura vale). Un pago parcial
    # SÍ es un caso de uso válido (monto < total_factura a propósito).
    if monto is not None and total_factura is not None and monto > float(total_factura) + 0.01:
        raise HTTPException(
            status_code=422,
            detail=(
                f"El monto a pagar (${monto}) supera el total de la factura (${total_factura}). "
                "Verificá el monto antes de generar la orden de pago."
            ),
        )
    # Huso ARGENTINO, no datetime.date.today() -- ver
    # utils/bas_config.py:ZONA_HORARIA_BAS.
    fecha_pago = fecha_hoy_bas().isoformat()
    medio_pago_codigo = metodo_bas["bas_medio_pago_codigo"]

    # Cada array de BAS exige campos runtime distintos más allá de lo que
    # marca el schema del Swagger como "required" (mismo patrón ya conocido
    # de ComprobantesCompra) -- confirmado en vivo 2026-07-21 probando cada
    # array con Total=1 contra la API real (ver docs/bas-orden-de-pago-research.md).
    if body.metodo_pago == "efectivo":
        # Efectivos NO tiene Fecha en su schema -- pasarla igual rompería
        # (additionalProperties: false).
        item_pago = {"MedioPago": medio_pago_codigo, "Importe": monto, "IngresooEgreso": "E"}
    elif body.metodo_pago == "cheque":
        if not body.numero_cheque:
            raise HTTPException(
                status_code=422,
                detail="Falta numero_cheque (el cheque de terceros que se va a endosar) para pagar con cheque.",
            )
        item_pago = {
            "MedioPago": medio_pago_codigo,
            "Importe": monto,
            "IngresooEgreso": "E",
            "Fecha": fecha_pago,
            "NumeroExterno": body.numero_cheque,
        }
    elif body.metodo_pago == "transferencia":
        if not metodo_bas.get("bas_cuenta_bancaria"):
            raise HTTPException(status_code=422, detail="Falta bas_cuenta_bancaria para transferencia.")
        item_pago = {
            "MedioPago": medio_pago_codigo,
            "Importe": monto,
            "IngresooEgreso": "E",
            "Fecha": fecha_pago,
            "Numero": (body.numero_transferencia or process_id)[:15],
            "CuentaBancaria": metodo_bas["bas_cuenta_bancaria"],
        }
    elif body.metodo_pago == "tarjeta":
        if not (metodo_bas.get("bas_plan_tarjeta") and metodo_bas.get("bas_codigo_tarjeta")):
            raise HTTPException(
                status_code=422,
                detail="Falta bas_plan_tarjeta/bas_codigo_tarjeta para tarjeta (bas_payment_methods).",
            )
        if not body.numero_tarjeta:
            raise HTTPException(status_code=422, detail="Falta numero_tarjeta para pagar con tarjeta.")
        item_pago = {
            "MedioPago": medio_pago_codigo,
            "Importe": monto,
            "IngresooEgreso": "E",
            "Fecha": fecha_pago,
            "Plan": metodo_bas["bas_plan_tarjeta"],
            "CodigoTarjeta": metodo_bas["bas_codigo_tarjeta"],
            "NumeroTarjeta": body.numero_tarjeta,
        }
    else:
        # No debería llegar acá: ya se validó metodo_pago in METODO_PAGO_ARRAY_BAS
        # más arriba -- este else cubre un método nuevo agregado a ese dict
        # sin su rama de payload correspondiente acá.
        raise HTTPException(status_code=422, detail=f"metodo_pago sin manejo de payload: {body.metodo_pago}")

    pagos = {METODO_PAGO_ARRAY_BAS[body.metodo_pago]: [item_pago]}

    # Mismo armado de Items/comprobante_compra_payload que
    # InvoiceOrchestrator.procesar_factura_en_bas, pero leyendo invoice_items
    # DE POCKETBASE en vez de la extracción original de Gemini.
    items = orchestrator._pb_client.get_invoice_items(invoice["id"])

    # Gate de validaciones críticas (ver docs/plan-validaciones-pre-bas.md,
    # Etapa 0) ANTES de escribir dinero real. Se corre acá y no en el flujo
    # automático porque procesar_factura_en_bas siempre corre en dry_run --
    # este es el único punto donde vale la pena bloquear.
    errores_validacion = validar_factura_antes_de_pago_real(invoice)
    if errores_validacion:
        raise HTTPException(
            status_code=422,
            detail={
                "mensaje": "La factura tiene datos que deben corregirse antes de generar el pago.",
                "validaciones": errores_validacion,
            },
        )

    # Alícuota real de IVA de la factura, extraída por Gemini y persistida en
    # invoices.iva_alicuota (ver _extraer_alicuota_iva) -- editable en la
    # revisión humana igual que cualquier otro campo. Conversión con guarda
    # de tipo (no confiar en que PocketBase devuelva siempre un number --
    # una edición manual corrupta no debe tirar un 500 sin manejar en el
    # único endpoint que mueve plata real, tiene que degradar al
    # comportamiento seguro de "alícuota desconocida" como cualquier otro
    # caso ambiguo).
    try:
        alicuota_iva = float(invoice.get("iva_alicuota")) if invoice.get("iva_alicuota") is not None else None
    except (TypeError, ValueError):
        alicuota_iva = None
    # Total/TotalGravado/TotalIva/CodigoItem anclados a invoice.total --
    # NUNCA a la suma de items_bas. Decisión de arquitectura 2026-08-19,
    # misma función que registrar_comprobante -- ver
    # utils/bas_payload.py para el detalle completo. `bas_codigo_item`
    # elegido a mano por un humano sigue mandando siempre que resuelva
    # válido (mismo criterio de prioridad que ya tenía este endpoint desde
    # P0-G, 2026-08-12 -- ahora centralizado en la función compartida).
    # validar_total (validar_factura_antes_de_pago_real, más arriba) ya
    # garantizó que invoice.total es válido antes de este punto -- el
    # ValueError de acá es defensa en profundidad, no el camino esperado.
    try:
        totales_e_items = construir_comprobante_totales_e_items(
            total_bruto=invoice.get("total"),
            alicuota_iva=alicuota_iva,
            items=items,
            pb_client=orchestrator._pb_client,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Deliberadamente SIN gate local que compare `monto` contra el Total
    # registrado (existió como validar_monto_aplicable_vs_neto hasta
    # 2026-08-04, ver docs/incidente-2026-08-04-pagos-solo-neto.md, y se
    # quitó por comparar contra un valor calculado acá mismo, no contra lo
    # realmente registrado en BAS). Ya no hace falta ese gate de todos
    # modos: desde 2026-08-19, `monto` (línea ~3662, default
    # invoice.get("total")) y `totales_e_items["Total"]` (arriba, también
    # anclado a invoice.total) son EL MISMO CAMPO por construcción -- no
    # pueden divergir salvo que se pase un `body.monto` explícito (pago
    # parcial, un caso de uso válido y separado, no un bug).

    # NO se toca `monto` (el importe de la orden de pago en sí) -- es un
    # concepto aparte, puede ser un pago parcial de esta factura, no
    # necesariamente igual al Total del comprobante.
    # Igual criterio que proveedor_codigo más arriba: re-parsear DESDE
    # invoice.numero_comprobante (el dato actual, post-revisión) en vez de
    # confiar en el prefijo/número cacheados en bas_processing_status, que
    # pueden haber quedado desactualizados si un revisor corrigió el número
    # de comprobante durante la revisión. Ya se validó arriba
    # (validar_factura_antes_de_pago_real) que numero_comprobante tiene un
    # formato parseable -- este parseo no debería caer nunca en el fallback.
    prefijo_externo, numero_externo = _extraer_prefijo_numero_comprobante_externo(
        {"numero": invoice.get("numero_comprobante")}
    )
    if (
        status_bas.get("comprobante_prefijo")
        and (
            status_bas.get("comprobante_prefijo") != prefijo_externo
            or status_bas.get("comprobante_numero") != numero_externo
        )
    ):
        app_logger.warning(
            f"[{process_id}] crear-orden-pago: numeración externa re-derivada "
            f"({prefijo_externo}-{numero_externo}) difiere de la guardada en "
            f"bas_processing_status ({status_bas.get('comprobante_prefijo')}-"
            f"{status_bas.get('comprobante_numero')}) -- probablemente el número de "
            "comprobante fue corregido durante la revisión."
        )
    comprobante_compra_payload = {
        "Comprobante": "MA",
        "Prefijo": BAS_PREFIJO_TALONARIO_MA,
        # Ver comentario equivalente en InvoiceOrchestrator.procesar_factura_en_bas:
        # "Fecha" es la fecha de asiento en BAS (subdiario), no la fecha del
        # documento -- usar fecha_emision real dispara 409 "fecha anterior al
        # cierre operativo del subdiario" (NUETRANSAC) para cualquier factura
        # registrada tarde. FechaComprobanteExterno (abajo) sí lleva la fecha
        # real del documento. Huso ARGENTINO (fecha_hoy_bas(), NO
        # datetime.date.today()) -- ver utils/bas_config.py:ZONA_HORARIA_BAS.
        "Fecha": fecha_hoy_bas().isoformat(),
        # Total/TotalGravado/TotalIva anclados a invoice.total (ver
        # utils/bas_payload.py) -- garantizan Total == TotalGravado +
        # TotalIva por construcción, la regla exacta de SP_VALIDA_TOTALES
        # que causó el incidente de MEDINA (2026-08-04, ver docs/incidente-
        # 2026-08-04-pagos-solo-neto.md): con TotalIva ausente, Total
        # quedaba forzado al neto sin importar qué se mandara en Items[].
        "Total": totales_e_items["Total"],
        "TotalGravado": totales_e_items["TotalGravado"],
        "TotalIva": totales_e_items["TotalIva"],
        "EmitidoPor": BAS_EMITIDO_POR_CAE,
        "Empresa": BAS_EMPRESA,
        "Sucursal": BAS_SUCURSAL,
        "Deposito": BAS_DEPOSITO,
        "Caja": BAS_CAJA,
        "MetodoPago": BAS_METODO_PAGO_CTA_CTE,
        "Proveedor": proveedor_codigo,
        "PrefijoComprobanteExterno": prefijo_externo,
        "NumeroComprobanteExterno": numero_externo,
        "FechaComprobanteExterno": invoice.get("fecha_emision"),
        "NumeroCAIoCAE": invoice.get("cae"),
        "VencimientoCAIoCAE": invoice.get("cae_vencimiento"),
        # Importe = Total -- tiene que coincidir con "Total" de la
        # cabecera (ver comentario ahí arriba); son literalmente el mismo
        # valor acá, nunca pueden divergir.
        "Vencimientos": [
            {"FechaVencimiento": invoice.get("fecha_emision"), "Importe": totales_e_items["Total"]}
        ],
        "Items": totales_e_items["Items"],
    }

    # `existente` ya se obtuvo arriba de todo (chequeo de idempotencia) --
    # no se vuelve a pedir acá para no correr el riesgo de una carrera entre
    # ambas lecturas; se reusa la misma referencia.
    retry_count = 0 if existente is None else (existente.get("retry_count") or 0) + 1
    ahora = datetime.datetime.utcnow().isoformat() + "Z"

    orchestrator._pb_client.upsert_payment_order(
        process_id,
        invoice=invoice["id"],
        metodo_pago=body.metodo_pago,
        monto=monto,
        status="processing",
        retry_count=retry_count,
        requested_by=body.requested_by,
        requested_at=ahora,
        last_attempt_at=ahora,
    )

    resultado = {"orden_pago": None, "error": None}
    try:
        with _lock_comprobante(proveedor_codigo, prefijo_externo, numero_externo):
            flujo = orchestrator._bas_client.crear_orden_de_pago_desde_factura(
                empresa=BAS_EMPRESA,
                sucursal=BAS_SUCURSAL,
                comprobante_factura="MA",
                prefijo_externo=prefijo_externo,
                numero_externo=numero_externo,
                importe=monto,
                fecha_externo=invoice.get("fecha_emision"),
                prefijo_op=BAS_PREFIJO_TALONARIO_OP,
                caja_op=BAS_CAJA,
                prefijo_ctacte="P",
                codigo_ctacte=proveedor_codigo,
                pagos=pagos,
                comprobante_compra_payload=comprobante_compra_payload,
                imputacion_contable=BAS_IMPUTACION_CONTABLE_PROVEEDORES,
                registrar_si_no_existe=True,
                dry_run=False,
            )
        resultado["orden_pago"] = flujo.get("orden_pago")
        if isinstance(resultado["orden_pago"], dict) and resultado["orden_pago"].get("_error"):
            detalle = resultado["orden_pago"].get("detail")
            op_huerfana = resultado["orden_pago"].get("_op_sin_aplicar")
            if op_huerfana:
                # La OP existe en BAS pero quedó sin aplicar (el flujo son dos
                # escrituras y falló la segunda -- ver la advertencia de no
                # atomicidad en crear_orden_de_pago_desde_factura). Se marca
                # explícito para que nadie reintente: un reintento crea una
                # SEGUNDA orden de pago por la misma factura. El número real
                # queda persistido más abajo para poder reconciliar.
                resultado["error"] = (
                    f"Orden de pago {op_huerfana.get('Prefijo')}-{op_huerfana.get('Numero')} "
                    "se creó en BAS pero NO se pudo aplicar a la factura. Queda "
                    "registrada sin aplicación: reconciliar a mano en BAS. "
                    f"NO reintentar (crearía otra OP). Detalle: {detalle}"
                )
            elif resultado["orden_pago"].get("_requiere_soporte_bas"):
                # Ver utils/bas.py:_es_error_no_resoluble_desde_cliente -- esta
                # firma de error ya se confirmó (~37 variantes de payload
                # probadas en total, incluido el endpoint alternativo
                # /api/AplicacionesComprobantes) como una limitación del lado
                # de BAS en esta instalación, no de esta factura ni de los
                # datos enviados. Reintentar desde acá no cambia el resultado.
                resultado["error"] = (
                    "Orden de pago falló: BAS no puede aplicar este comprobante "
                    "(SP_ICR_COMPROB_APL) -- limitación confirmada del lado de "
                    "BAS en esta instalación, no de los datos de la factura. "
                    f"No reintentar: escalar a soporte de BAS. Detalle: {detalle}"
                )
            else:
                resultado["error"] = f"Orden de pago falló: {detalle}"
    except BasApiError as e:
        resultado["error"] = f"BasApiError {e.status_code} en {e.path}: {e.detail}"
        app_logger.error(f"[{process_id}] crear-orden-pago: fallo BAS: {resultado['error']}")
    except Exception as e:
        resultado["error"] = str(e)
        app_logger.error(f"[{process_id}] crear-orden-pago: error inesperado: {e}")

    op = resultado["orden_pago"] if isinstance(resultado["orden_pago"], dict) else {}
    op_cmp = (op.get("Comprobantes") or [{}])[0] if op.get("Comprobantes") else {}
    # Si la OP se creó pero no se pudo aplicar, `orden_pago` es el dict de
    # error (no la respuesta de BAS) y no trae "Comprobantes" -- el número real
    # viene en `_op_sin_aplicar`. Persistirlo igual es lo único que después
    # permite encontrar esa OP en BAS para reconciliarla a mano.
    if not op_cmp and isinstance(op.get("_op_sin_aplicar"), dict):
        op_cmp = op["_op_sin_aplicar"]
    actualizado = orchestrator._pb_client.upsert_payment_order(
        process_id,
        status="failed" if resultado["error"] else "success",
        bas_op_prefijo=op_cmp.get("Prefijo"),
        bas_op_numero=op_cmp.get("Numero"),
        bas_error=resultado["error"],
        last_attempt_at=datetime.datetime.utcnow().isoformat() + "Z",
    )

    return {
        "success": resultado["error"] is None,
        "process_id": process_id,
        "orden_pago": resultado["orden_pago"],
        "error": resultado["error"],
        "payment_order": actualizado,
    }


@router.post(
    "/invoices/{process_id}/register-comprobante",
    summary="Registrar el ComprobanteCompra real en BAS (sin Orden de Pago) -- nuevo alcance",
    tags=["Procesamiento de facturas"],
)
async def registrar_comprobante(
    process_id: str,
    x_invoicy_secret: Optional[str] = Header(default=None, alias="X-Invoicy-Secret"),
):
    """
    P0-F (2026-08-12) -- segundo y, con el nuevo alcance, ÚLTIMO lugar del
    sistema donde `dry_run` pasa a False. Registra el ComprobanteCompra real
    en BAS y AHÍ TERMINA: sin Orden de Pago, sin método de pago, sin banco
    -- la cooperativa/contable crea la Orden de Pago directamente desde BAS
    (decisión de alcance confirmada 2026-08-10 por el cliente, ver
    docs/invoicy-bas-nuevo-alcance-plan-tecnico-FINAL.md). `crear_orden_pago`
    (arriba) queda como código legado del alcance viejo -- no se borra, pero
    ya no es el camino que usa el flujo nuevo.

    Mismo patrón de gates que `crear_orden_pago` (el otro endpoint con
    dry_run=False), MENOS todo lo específico de pago/OP (metodo_pago,
    banco, tarjeta, array de pagos):
      1. `review_status == "confirmed"` (revalidado server-side, no se
         confía en el caller).
      2. Idempotencia PRIMERO: si `invoices.bas_registration_status` ya es
         "registered", no reintenta. A diferencia de
         `bas_processing_status.comprobante_registrado` (que puede venir
         tainted por un intento dry_run=True de la ingesta automática, ver
         docstring de `crear_orden_pago`), `bas_registration_status` con
         valor "registered" SOLO lo escribe este mismo endpoint tras un
         éxito real (campo nuevo desde P0-A, nunca tocado por el flujo
         automático) -- es una señal confiable de verdad.
      3. Proveedor re-derivado FRESCO desde la factura actual, solo lectura
         (`_buscar_proveedor_bas`, P0-B) -- nunca se crea automáticamente.
      4. `validar_factura_antes_de_pago_real`: a pesar del nombre (heredado
         del endpoint de OP), sus 7 chequeos son de datos del COMPROBANTE
         -- CUIT, moneda, fecha de emisión, número de comprobante, CAE,
         ítems completos, alícuota de IVA -- ninguno es específico de pago.
         Se reusa tal cual, sin ninguna validación de monto/método.
      5. CodigoItem resuelto y VALIDADO por línea, fresco, vía
         `resolver_codigo_item` (P0-E) -- si el humano eligió un ítem a mano
         en el dashboard (`invoice_items.bas_codigo_item`), ese valor se
         pasa como `override_codigo_item` y manda (revalidado
         activo+elegible_compras; si ya no es válido, bloquea sin fallback a
         categoría); si no, se resuelve por categoría+alícuota como
         siempre. Nunca se construye un payload con un CodigoItem inventado
         o sin validar. Esto también es lo que permite llamar a este
         endpoint estando todavía en `bas_registration_status=
         "awaiting_service_selection"`: no hay ningún gate sobre el estado
         previo salvo el atajo de idempotencia de "registered" (punto 2) --
         la resolución de acá arriba es la única fuente de verdad de si la
         factura está realmente lista, sin depender de que
         `procesar_factura_en_bas` (el único lugar que escribe
         "ready_to_register", una sola vez, en la ingesta automática) haya
         corrido después de una corrección humana.
      6. Registro idempotente y verificado contra BAS
         (`BasClient.registrar_comprobante_compra_idempotente`, extraído en
         esta misma fase de `crear_orden_de_pago_desde_factura` -- mismas
         garantías: nunca un POST duplicado si el comprobante ya existe por
         número externo, y siempre verificado con un GET independiente
         antes de confirmar éxito, no se confía ciegamente en el 201).

    `MetodoPago="C"` (cuenta corriente) se mantiene en el payload SOLO
    porque BAS lo exige estructuralmente para poder registrar el
    comprobante contra la cuenta corriente del proveedor -- confirmado
    explícitamente ANTES de arrancar P0-D que esto no arrastra ningún flujo
    de pago (ver resumen de esa fase). No hay `pagos`, no hay
    `crear_orden_de_pago`, no hay `aplicar_comprobantes` en este endpoint.
    """
    _verificar_secreto_invoicy(x_invoicy_secret)

    invoice = orchestrator._pb_client.get_invoice_by_process_id(process_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"No se encontró la factura para process_id={process_id}.")
    if invoice.get("review_status") != "confirmed":
        raise HTTPException(status_code=409, detail="La factura todavía no fue confirmada.")

    if invoice.get("bas_registration_status") == "registered":
        return {
            "success": True,
            "process_id": process_id,
            "already_resolved": True,
            "message": "El comprobante ya fue registrado en BAS anteriormente; no se reintenta.",
            "bas_processing_status": orchestrator._pb_client.get_bas_processing_status(process_id),
        }

    proveedor_actual = orchestrator._buscar_proveedor_bas(
        invoice.get("emisor_cuit"), invoice.get("emisor_nombre", "")
    )
    proveedor_codigo = proveedor_actual.get("Codigo") if proveedor_actual else None
    if not proveedor_codigo:
        orchestrator._pb_client.upsert_invoice(
            {"process_id": process_id, "bas_registration_status": "awaiting_provider_match"}
        )
        raise HTTPException(
            status_code=422,
            detail="No se pudo resolver el proveedor en BAS (falta o es inválido el CUIT del emisor, "
            "o el proveedor no existe en BAS -- debe darse de alta manualmente y luego reintentar).",
        )

    items = orchestrator._pb_client.get_invoice_items(invoice["id"])
    errores_validacion = validar_factura_antes_de_pago_real(invoice)
    if errores_validacion:
        raise HTTPException(
            status_code=422,
            detail={
                "mensaje": "La factura tiene datos que deben corregirse antes de registrar el comprobante.",
                "validaciones": errores_validacion,
            },
        )

    try:
        alicuota_iva = float(invoice.get("iva_alicuota")) if invoice.get("iva_alicuota") is not None else None
    except (TypeError, ValueError):
        alicuota_iva = None

    # Total/TotalGravado/TotalIva/CodigoItem anclados a invoice.total --
    # NUNCA a la suma de items_bas. Decisión de arquitectura 2026-08-19:
    # un descuento, una bonificación, un precio_total negativo/cero/null o
    # un error de OCR en un ítem individual ya no pueden alterar el monto
    # que se registra en BAS -- ver utils/bas_payload.py para el detalle
    # completo. validar_total (validar_factura_antes_de_pago_real, más
    # arriba) ya garantizó que invoice.total es válido antes de este punto
    # -- el ValueError de acá es defensa en profundidad, no el camino
    # esperado.
    try:
        totales_e_items = construir_comprobante_totales_e_items(
            total_bruto=invoice.get("total"),
            alicuota_iva=alicuota_iva,
            items=items,
            pb_client=orchestrator._pb_client,
        )
    except ValueError as e:
        orchestrator._pb_client.upsert_invoice(
            {"process_id": process_id, "bas_registration_status": "awaiting_service_selection"}
        )
        raise HTTPException(status_code=422, detail=str(e))

    prefijo_externo, numero_externo = _extraer_prefijo_numero_comprobante_externo(
        {"numero": invoice.get("numero_comprobante")}
    )

    comprobante_compra_payload = {
        "Comprobante": "MA",
        "Prefijo": BAS_PREFIJO_TALONARIO_MA,
        "Fecha": fecha_hoy_bas().isoformat(),
        "Total": totales_e_items["Total"],
        "TotalGravado": totales_e_items["TotalGravado"],
        "TotalIva": totales_e_items["TotalIva"],
        "EmitidoPor": BAS_EMITIDO_POR_CAE,
        "Empresa": BAS_EMPRESA,
        "Sucursal": BAS_SUCURSAL,
        "Deposito": BAS_DEPOSITO,
        "Caja": BAS_CAJA,
        # Requerido estructuralmente por BAS para registrar contra la cuenta
        # corriente del proveedor -- NO implica ningún flujo de pago (ver
        # docstring del endpoint).
        "MetodoPago": BAS_METODO_PAGO_CTA_CTE,
        "Proveedor": proveedor_codigo,
        "PrefijoComprobanteExterno": prefijo_externo,
        "NumeroComprobanteExterno": numero_externo,
        "FechaComprobanteExterno": invoice.get("fecha_emision"),
        "NumeroCAIoCAE": invoice.get("cae"),
        "VencimientoCAIoCAE": invoice.get("cae_vencimiento"),
        "Vencimientos": [
            {"FechaVencimiento": invoice.get("fecha_emision"), "Importe": totales_e_items["Total"]}
        ],
        "Items": totales_e_items["Items"],
    }

    ahora = datetime.datetime.utcnow().isoformat() + "Z"
    resultado = {"comprobante": None, "error": None}
    registro = None
    try:
        with _lock_comprobante(proveedor_codigo, prefijo_externo, numero_externo):
            registro = orchestrator._bas_client.registrar_comprobante_compra_idempotente(
                empresa=BAS_EMPRESA,
                sucursal=BAS_SUCURSAL,
                comprobante="MA",
                prefijo_externo=prefijo_externo,
                numero_externo=numero_externo,
                fecha_externo=invoice.get("fecha_emision"),
                comprobante_compra_payload=comprobante_compra_payload,
                registrar_si_no_existe=True,
                dry_run=False,
            )
        resultado["comprobante"] = registro["comprobante"]
    except BasApiError as e:
        resultado["error"] = f"BasApiError {e.status_code} en {e.path}: {e.detail}"
        app_logger.error(f"[{process_id}] registrar-comprobante: fallo BAS: {resultado['error']}")
    except Exception as e:
        resultado["error"] = str(e)
        app_logger.error(f"[{process_id}] registrar-comprobante: error inesperado: {e}")

    cmp = resultado["comprobante"] or {}
    id_transaccion = registro.get("id_transaccion") if registro else None
    # comprobante_total_registrado: NO se puede leer de la respuesta de BAS
    # -- confirmado real (validación de P0-F, 2026-08-12) y contra el
    # swagger real de BAS: el schema de respuesta de ambas consultas
    # (RespuestaConsultaComprobante, la misma que usan
    # ConsultaComprobantes/ConsultaComprobantesExternos) es
    # `additionalProperties: false` y NO incluye Total/TotalGravado/TotalIva
    # en ninguna forma -- ni el 201 del POST ni ningún GET posterior lo
    # exponen. No hay ningún endpoint de BAS que permita reconfirmar
    # independientemente qué Total quedó registrado. Se persiste el Total
    # que efectivamente ENVIAMOS y que BAS aceptó con un 201 -- pero SOLO
    # cuando este intento fue el que realmente registró el comprobante
    # (`ya_existia=False`): si ya existía de un intento anterior, no
    # tenemos forma de saber con qué Total quedó esa vez, así que se deja
    # sin tocar en vez de asumir que coincide con el de ahora.
    total_confirmado = None
    if registro is not None and not registro.get("ya_existia"):
        total_confirmado = comprobante_compra_payload.get("Total")
    campos_status = dict(
        invoice=invoice["id"],
        proveedor_resuelto=True,
        proveedor_codigo=proveedor_codigo,
        comprobante_prefijo=cmp.get("Prefijo") or prefijo_externo,
        comprobante_numero=cmp.get("Numero"),
        comprobante_registrado=resultado["error"] is None,
        bas_id_transaccion=id_transaccion,
        comprobante_registrado_at=(ahora if resultado["error"] is None else None),
        comprobante_total_registrado=total_confirmado,
        bas_last_error=resultado["error"],
        last_attempt_at=ahora,
    )
    if orchestrator._pb_client.get_bas_processing_status(process_id) is None:
        # Caso raro (normalmente ya lo creó la ingesta automática):
        # orden_pago_status es 'required' en el schema de
        # bas_processing_status -- sin esto, el create fallaría la
        # validación de PocketBase y se perdería en silencio toda esta
        # auditoría (registro real exitoso/fallido en BAS sin dejar
        # rastro). "pending" porque no hubo ningún intento de OP -- ese eje
        # es legado, fuera de este alcance (ver migración
        # 1784500000_add_bas_traceability_and_state.js). Si el registro YA
        # existía, NO se toca este campo -- son 34+ filas históricas legado
        # que no hay que pisar.
        campos_status["orden_pago_status"] = "pending"
    status_processing = orchestrator._pb_client.upsert_bas_processing_status(process_id, **campos_status)
    orchestrator._pb_client.upsert_invoice(
        {
            "process_id": process_id,
            "bas_registration_status": "register_failed" if resultado["error"] else "registered",
        }
    )

    return {
        "success": resultado["error"] is None,
        "process_id": process_id,
        "comprobante": resultado["comprobante"],
        "error": resultado["error"],
        "bas_processing_status": status_processing,
    }


@router.post(
    "/invoices/{process_id}/recheck-provider",
    summary="Volver a buscar el proveedor en BAS tras darlo de alta manualmente -- P0-C",
    tags=["Procesamiento de facturas"],
)
async def recheck_provider(
    process_id: str,
    x_invoicy_secret: Optional[str] = Header(default=None, alias="X-Invoicy-Secret"),
):
    """
    P0-C (2026-08-12). Cuando una factura queda en
    bas_registration_status="awaiting_provider_match" (P0-B: el proveedor no
    existía en BAS y Invoicy nunca lo crea automáticamente), el flujo humano
    es: 1) alguien da de alta el proveedor A MANO en BAS, 2) presiona
    "Volver a buscar" en el dashboard -- eso llama a este endpoint.

    Paso crítico, por qué este endpoint no es simplemente "volver a llamar
    a _buscar_proveedor_bas": InvoiceOrchestrator es un singleton de vida
    larga (una sola instancia para todo el proceso) y
    `self._proveedores_bas_cache` cachea EN MEMORIA -- incluida la
    respuesta negativa (None) cuando el proveedor no existía (P0-B). Sin
    invalidar esa entrada acá, este endpoint devolvería SIEMPRE el mismo
    None viejo sin importar cuántas veces se llame, hasta que el proceso
    reinicie -- exactamente el caso que más le importa al negocio: alguien
    da de alta el proveedor y el sistema sigue sin verlo. El cache de
    PocketBase (`bas_providers`, get_provider_cache/set_provider_cache) NO
    hace falta invalidarlo: solo persiste resultados POSITIVOS (P0-B), así
    que nunca hay nada negativo ahí para limpiar.

    Solo lectura contra BAS -- mismo criterio P0-B, `_buscar_proveedor_bas`
    nunca crea ni modifica nada. Si encuentra el proveedor, guarda
    `bas_provider` (relation) en la factura. El nuevo
    `bas_registration_status` es "awaiting_service_selection", EXCEPTO si
    la factura ya estaba en "ready_to_register" (provider Y CodigoItems ya
    resueltos en un dry-run posterior) -- ahí no se downgradea: perder ese
    estado sería perder trabajo ya hecho, aunque nada de eso se toque
    directamente. Si todavía no existe, no se escribe nada -- la factura
    permanece en "awaiting_provider_match" tal cual estaba.

    Nunca toca `invoice_items` -- cualquier CodigoItem ya resuelto o
    seleccionado en las líneas de la factura queda intacto (regla 8 del
    alcance: re-buscar el proveedor no puede pisar el trabajo de selección
    de ítems ya hecho).
    """
    _verificar_secreto_invoicy(x_invoicy_secret)

    invoice = orchestrator._pb_client.get_invoice_by_process_id(process_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"No se encontró la factura para process_id={process_id}.")

    if invoice.get("bas_registration_status") == "registered":
        return {
            "success": True,
            "process_id": process_id,
            "encontrado": True,
            "already_resolved": True,
            "message": "Esta factura ya fue registrada en BAS; no hace falta volver a buscar el proveedor.",
        }

    cuit_emisor = invoice.get("emisor_cuit", "")
    cuit_normalizado = "".join(c for c in (cuit_emisor or "") if c.isdigit())

    # Invalidar el cache negativo EN MEMORIA antes de consultar -- ver
    # docstring de arriba, es el paso que hace que este endpoint sirva para
    # algo. No se pisa nada más (ni bas_providers en PocketBase, ni ningún
    # otro campo): solo se fuerza a que la próxima lectura vuelva a
    # consultar BAS en vez de confiar en lo que había en memoria.
    if cuit_normalizado:
        orchestrator._proveedores_bas_cache.pop(cuit_normalizado, None)

    proveedor = orchestrator._buscar_proveedor_bas(cuit_emisor, invoice.get("emisor_nombre", ""))

    if proveedor is None:
        return {
            "success": True,
            "process_id": process_id,
            "encontrado": False,
            "bas_registration_status": "awaiting_provider_match",
            "message": f"El proveedor con CUIT {cuit_emisor} todavía no existe en BAS.",
        }

    campos_actualizar = {
        "process_id": process_id,
        "bas_provider": proveedor.get("_pb_id") or "",
    }
    # No downgradear un "ready_to_register" ya alcanzado (ver docstring) --
    # en cualquier otro estado (incluido "awaiting_provider_match", el caso
    # normal), sí corresponde pasar a "awaiting_service_selection".
    if invoice.get("bas_registration_status") != "ready_to_register":
        campos_actualizar["bas_registration_status"] = "awaiting_service_selection"

    invoice_actualizada = orchestrator._pb_client.upsert_invoice(campos_actualizar)

    return {
        "success": True,
        "process_id": process_id,
        "encontrado": True,
        "bas_registration_status": campos_actualizar.get("bas_registration_status", invoice.get("bas_registration_status")),
        "proveedor": {"codigo": proveedor.get("Codigo"), "pb_id": proveedor.get("_pb_id")},
        "invoice": invoice_actualizada,
    }


class EliminarInvoiceBody(BaseModel):
    # id de PocketBase (colección "users"), resuelto por el dashboard Next.js
    # desde la sesión -- mismo criterio que CrearOrdenPagoBody.requested_by:
    # Invoicy no tiene noción propia de sesión de "users", así que confía en
    # que el caller (siempre el backend de Next.js, nunca el navegador
    # directo, protegido por _verificar_secreto_invoicy) ya la validó.
    deleted_by: str
    reason: Optional[str] = None


class EliminarPaymentOrderBody(BaseModel):
    deleted_by: str
    reason: Optional[str] = None


@router.delete(
    "/invoices/{process_id}",
    summary="Soft-delete de una factura (Cola de revisión o Facturas)",
    tags=["Procesamiento de facturas"],
)
async def eliminar_invoice(
    process_id: str,
    body: EliminarInvoiceBody,
    x_invoicy_secret: Optional[str] = Header(default=None, alias="X-Invoicy-Secret"),
):
    """
    Soft-delete SIEMPRE -- nunca un borrado físico (ver
    utils/pocketbase_client.py, migración 1783483896_add_soft_delete_fields.js:
    BAS nunca se entera de un borrado en PocketBase, perder el registro
    entero perdería toda la trazabilidad de auditoría sin revertir nada del
    lado de BAS). Sirve tanto para "Cola de revisión" como para "Facturas":
    ambas secciones del dashboard leen la misma colección `invoices`, solo
    con filtros de status/review_status distintos -- un solo endpoint
    alcanza para las dos.

    Bloquea con 409 si existe una payment_order asociada, activa (no
    soft-deleted), con status="success": BAS ya tiene esa Orden de Pago como
    real, y borrar la factura sin resolver eso primero perdería la
    trazabilidad de auditoría del lado de Invoicy sin revertir nada en BAS.
    El caller debe eliminar esa Orden de Pago primero (endpoint separado,
    con su propia fricción reforzada) -- no se cascadea automáticamente
    desde acá a propósito, para que esa fricción no se pueda esquivar
    borrando la factura en su lugar.

    Si la payment_order asociada está en processing/failed (nada real
    aplicado en BAS todavía), se soft-deletea junto con la factura -- bajo
    riesgo, no hace falta fricción extra ahí.

    Idempotente: si la factura ya estaba soft-deleted, responde 200 sin
    volver a escribir (evita un error confuso ante doble click o una
    carrera entre dos personas).
    """
    _verificar_secreto_invoicy(x_invoicy_secret)

    invoice = orchestrator._pb_client.get_invoice_by_process_id(process_id)
    if invoice is None:
        raise HTTPException(
            status_code=404, detail=f"No se encontró la factura para process_id={process_id}."
        )

    if invoice.get("deleted_at"):
        return {
            "success": True,
            "process_id": process_id,
            "already_deleted": True,
            "message": "La factura ya estaba eliminada.",
        }

    payment_order = orchestrator._pb_client.get_payment_order(process_id)
    if payment_order and not payment_order.get("deleted_at"):
        if payment_order.get("status") == "success":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Esta factura tiene una Orden de Pago ya exitosa en BAS. "
                    "Eliminá esa Orden de Pago primero (desde Órdenes de pago) "
                    "antes de borrar la factura."
                ),
            )
        # processing/failed: nada real en BAS todavía -- se soft-deletea
        # junto con la factura, sin fricción extra.
        orchestrator._pb_client.soft_delete_payment_order(
            process_id, deleted_by=body.deleted_by, reason=body.reason
        )

    actualizado = orchestrator._pb_client.soft_delete_invoice(
        process_id, deleted_by=body.deleted_by, reason=body.reason
    )
    if actualizado is None:
        raise HTTPException(status_code=502, detail="No se pudo eliminar la factura en PocketBase.")

    return {
        "success": True,
        "process_id": process_id,
        "already_deleted": False,
        "invoice": actualizado,
    }


@router.delete(
    "/payment-orders/{process_id}",
    summary="Soft-delete de una Orden de Pago",
    tags=["Procesamiento de facturas"],
)
async def eliminar_payment_order(
    process_id: str,
    body: EliminarPaymentOrderBody,
    x_invoicy_secret: Optional[str] = Header(default=None, alias="X-Invoicy-Secret"),
):
    """
    Soft-delete SIEMPRE. Si `status=="success"` (plata/contabilidad real ya
    aplicada en BAS -- utils/bas.py no expone ningún anular/cancelar vía
    esta API, la única forma de revertir es manual y directamente en BAS),
    exige `reason` no vacío: es la fricción reforzada decidida para este
    caso específico -- se permite, pero nunca sin que quede escrito el
    motivo. Nunca se confía en que el frontend ya validó esto (el frontend
    hace la misma validación solo por UX, no por seguridad).

    No toca la `invoice` asociada -- sigue existiendo, solo pierde su Orden
    de Pago activa (se podría crear otra más adelante si hiciera falta).

    Idempotente, mismo criterio que eliminar_invoice.
    """
    _verificar_secreto_invoicy(x_invoicy_secret)

    payment_order = orchestrator._pb_client.get_payment_order(process_id)
    if payment_order is None:
        raise HTTPException(
            status_code=404,
            detail=f"No se encontró la Orden de Pago para process_id={process_id}.",
        )

    if payment_order.get("deleted_at"):
        return {
            "success": True,
            "process_id": process_id,
            "already_deleted": True,
            "message": "La Orden de Pago ya estaba eliminada.",
        }

    if payment_order.get("status") == "success" and not (body.reason or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Se requiere un motivo para eliminar una Orden de Pago ya exitosa.",
        )

    actualizado = orchestrator._pb_client.soft_delete_payment_order(
        process_id, deleted_by=body.deleted_by, reason=body.reason
    )
    if actualizado is None:
        raise HTTPException(
            status_code=502, detail="No se pudo eliminar la Orden de Pago en PocketBase."
        )

    return {
        "success": True,
        "process_id": process_id,
        "already_deleted": False,
        "payment_order": actualizado,
    }
