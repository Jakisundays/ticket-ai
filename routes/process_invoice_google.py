# FastAPI imports
from fastapi import Form, APIRouter, HTTPException, UploadFile, File
import logging

app_logger = logging.getLogger("app_logger")

# Standard library imports
import os
import shutil
import json
import base64
import asyncio
import ssl
import mimetypes
from pathlib import Path
from typing import Dict, Optional, Union, TypedDict
import fitz  # PyMuPDF
from PIL import Image
import io

# Third-party imports
import aiohttp
import certifi
import filetype
import requests
import zipfile
from jsonschema import validate, ValidationError
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Local imports
from tools import tools
from tools_standard import tools as tools_standard

# Crea una instancia del router de FastAPI
router = APIRouter(prefix="/gemini")


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
        texto = (
            f"Retención #{i}:\n"
            f"  - Tipo: {r['tipo']}\n"
            f"  - Descripción: {r.get('description', 'No especificada')}\n"
            f"  - Base Imponible: ${r['base_imponible']:.2f}\n"
        )
        resultado.append(texto)
    return "\n".join(resultado)


# Formatea la información de impuestos en texto legible, incluyendo campos opcionales
def formatear_impuestos(impuestos):
    if not impuestos:
        return ""
    resultado = []
    for i, imp in enumerate(impuestos, start=1):
        # Construye el texto base con campos requeridos
        texto = (
            f"Impuesto #{i}:\n"
            f"  - Tipo: {imp['tipo']}\n"
            f"  - Base Imponible: ${imp['base_imponible']:.2f}\n"
            f"  - Importe: ${imp['importe']:.2f}\n"
        )

        # Agrega descripción si está presente
        if "descripcion" in imp:
            texto = texto.replace(
                f"  - Tipo: {imp['tipo']}\n",
                f"  - Tipo: {imp['tipo']}\n" f"  - Descripción: {imp['descripcion']}\n",
            )

        # Agrega alícuota si está presente
        if "alicuota" in imp and imp["alicuota"] is not None:
            texto = texto.replace(
                f"  - Base Imponible: ${imp['base_imponible']:.2f}\n",
                f"  - Base Imponible: ${imp['base_imponible']:.2f}\n"
                f"  - Alícuota: {imp['alicuota']:.2f}%\n",
            )

        resultado.append(texto)
    return "\n".join(resultado)


# Definición de tipo para elementos en cola que contienen información del archivo
class QueueItem(TypedDict):
    file_name: str
    file_extension: str
    file_path: str
    media_type: str
    process_id: str


class InvoiceOrchestrator:
    # Inicializa la clase con las credenciales y configs necesarias para procesar facturas
    def __init__(
        self,
        secret: str,  # Clave secreta para autenticar
        webhook_url: str,  # URL para notificar resultados
        api_key: str,  # API key de Anthropic
        recharge_cooldown: int,  # Tiempo entre recargas
        queue_check_cooldown: int,  # Tiempo entre checks de cola
        model: str,  # Modelo de Claude a usar
        semaphore: int,  # Límite de procesos paralelos
    ):
        self.SECRET = secret
        self.WEBHOOK_URL = webhook_url
        self.api_key = api_key
        self.active_comparisons = {}

        self.task_queue = asyncio.Queue()  # Cola async para procesar facturas
        self.semaphore = asyncio.Semaphore(semaphore)  # Control de concurrencia

        self.model = model
        self.tool_with_prompts = tools  # Herramientas para procesar facturas

    # Worker que procesa items de la cola continuamente
    async def worker(self):
        while True:
            item = await self.task_queue.get()
            app_logger.info(f"Procesando item {item}")
            try:
                self.active_comparisons[item["process_id"]] = item
                app_logger.info(f"Procesando item: {item['process_id']}")
                # Procesa la factura y notifica resultado
                factura = await self.process_item(item)
                app_logger.info("Factura procesada")
                webhook_response = await self.fire_webhook(factura)
                if webhook_response:
                    app_logger.info("Webhook delivered successfully")
                else:
                    app_logger.error("Error sending webhook")
            except Exception as e:
                # Si hay error, notifica con webhook
                app_logger.error(f"An error occurred: {e}")
                item["error"] = e
                item["saved_sheet"] = False
                webhook_response = await self.fire_webhook(item)
                if webhook_response:
                    app_logger.info("Webhook delivered successfully")
                else:
                    app_logger.error("Error sending webhook")
            finally:
                if item["process_id"] in self.active_comparisons:
                    del self.active_comparisons[item["process_id"]]
                # Limpia archivos temporales
                try:
                    os.remove(item["file_path"])
                except OSError as e:
                    app_logger.error(f"Error deleting file {item['file_path']}: {e}")
                self.task_queue.task_done()

    # Envía resultados vía webhook
    async def fire_webhook(self, data):
        try:
            res = requests.post(
                self.WEBHOOK_URL,
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

                app_logger.info("Tenemos las respuestas")
                # Guarda en sheets y formatea respuesta
                saved_sheet = orchestrator.guardar_factura_completa_en_sheets(
                    respuestas["data"]
                )
                app_logger.info(
                    "Guardamos la factura"
                    if saved_sheet
                    else "No guardamos la factura, error"
                )
                factura = orchestrator.formatear_factura(respuestas["data"])
                factura["id"] = item["process_id"]
                factura["saved_sheet"] = bool(saved_sheet)
                factura["error"] = ""

                return factura
        except Exception as e:
            app_logger.error(f"An error occurred while processing item: {e}")
            raise ValueError(f"Error processing item: {e}")

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
                            app_logger.warning(f"API request failed with status {response.status}. Retrying in {sleep_time} seconds...")
                            await asyncio.sleep(sleep_time)
                        else:
                            app_logger.error(f"API request failed with status {response.status} - {await response.text()}")
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
        model: str = "gemini-2.5-flash-preview-05-20",
        max_retries: int = 6,
    ):
        url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('GEMINI_API_KEY')}",
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
                error_message = {
                    "tool_name": tool_name,
                    "tool_output": tool_output,
                    "tool": next(
                        (tool for tool in tools if tool["name"] == tool_name), None
                    ),
                    "error": e.message,
                }
                error_response = requests.post(
                    self.WEBHOOK_URL,
                    json=error_message,
                    timeout=10,
                )
                app_logger.error(f"Webhook Status Code: {error_response.status_code}")
                if attempt < max_retries:
                    app_logger.warning("🔄 Retrying...")
                    continue
                else:
                    app_logger.error("❌ Max retries exceeded.")
                    raise ValueError(
                        f"Max retries exceeded for '{tool_name}'. Last error: {e.message}"
                    )
            except Exception as e:
                # Notifica error general
                app_logger.error(f"❌ Unexpected error: {e}")
                error_message = {
                    "tool_name": tool_name,
                    "tool_output": tool_output,
                    "tool": next(
                        (tool for tool in tools if tool["name"] == tool_name), None
                    ),
                    "error": e.message,
                }
                error_response = requests.post(
                    self.WEBHOOK_URL,
                    json=error_message,
                    timeout=10,
                )
                app_logger.error(f"Webhook Status Code: {error_response.status_code}")
                if attempt < max_retries:
                    app_logger.warning("🔄 Retrying...")
                    continue
                else:
                    app_logger.error("❌ Max retries exceeded.")
                    raise ValueError(
                        f"Max retries exceeded for '{tool_name}'. Last error: {e.message}"
                    )

    # Procesa imágenes con Claude Vision
    async def run_image_toolchain(
        self,
        item: QueueItem,
    ):
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

            sheet_id = os.getenv("SHEET_ID")
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
    api_key=os.getenv("GEMINI_API_KEY"),
    recharge_cooldown=45,
    queue_check_cooldown=20,
    model="gemini-2.5-flash-preview-05-20",
    semaphore=3,
)


@router.post(
    "/process-invoice",
    summary="Procesar factura - GEMINI",
    tags=["Procesamiento de facturas"],
    response_description="Datos extraídos de la factura y uso de tokens.",
    response_model=dict,
    responses={
        200: {
            "description": "Respuesta exitosa con los datos extraídos de la factura y uso de tokens.",
            "content": {
                "application/json": {
                    "example": {
                        "data": {
                            "emisor_receptor": {
                                "comprobante": {
                                    "tipo": "Factura",
                                    "subtipo": "A",
                                    "jurisdiccion_fiscal": "AR",
                                    "numero": "0001-00001234",
                                    "fecha_emision": "2024-06-01",
                                    "moneda": "ARS",
                                },
                                "emisor": {
                                    "nombre": "Empresa S.A.",
                                    "id_fiscal": "30-12345678-9",
                                    "direccion": "Av. Siempre Viva 123",
                                    "condicion_iva": "Responsable Inscripto",
                                },
                                "receptor": {
                                    "nombre": "Juan Pérez",
                                    "id_fiscal": "20-98765432-1",
                                    "direccion": "Calle Falsa 456",
                                    "condicion_iva": "Consumidor Final",
                                },
                                "otros": {
                                    "forma_pago": "Transferencia",
                                    "CAE": "12345678901234",
                                    "vencimiento_CAE": "2024-06-15",
                                },
                            },
                            "items": {
                                "detalles": [
                                    {
                                        "descripcion": "Producto A",
                                        "cantidad": 2,
                                        "precio_unitario": 1500.0,
                                        "precio_total": 3000.0,
                                    }
                                ],
                                "subtotal": 3000.0,
                                "total": 3630.0,
                                "observaciones": "Pago contado.",
                            },
                            "impuestos": {
                                "impuestos": [
                                    {
                                        "tipo": "IVA",
                                        "descripcion": "IVA 21%",
                                        "base_imponible": 3000.0,
                                        "alicuota": 21.0,
                                        "importe": 630.0,
                                    }
                                ],
                                "retenciones": [
                                    {
                                        "tipo": "Ganancias",
                                        "descripcion": "Retención de Ganancias",
                                        "base_imponible": 1000.0,
                                    }
                                ],
                            },
                        },
                        "tokens": {
                            "input_tokens": 1234,
                            "output_tokens": 567,
                            "cache_creation_input_tokens": 1230,
                            "cache_read_input_tokens": 2130,
                        },
                        "id": "id_value",
                        "saved_sheet": True,
                        "status_code": 200,
                    }
                }
            },
        },
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
        if secret_key != orchestrator.SECRET:
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
        file_location = f"./downloads/{file.filename.split('/')[-1]}"
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        with open(file_location, "rb") as f:
            kind = filetype.guess(f.read(262))

        # Procesa imagen o PDF
        if kind.mime.startswith("image") or kind.mime == "application/pdf":
            # Prepara info del archivo
            item = {
                "file_name": file.filename,
                "file_extension": extension,
                "file_path": file_location,
                "media_type": kind.mime,
                "process_id": id,
            }

            # Procesa según tipo
            if kind.mime.startswith("image"):
                app_logger.info("Tenemos una imagen")
                respuestas = await orchestrator.run_image_toolchain(item)
            elif kind.mime == "application/pdf":
                app_logger.info("Tenemos un PDF")
                respuestas = await orchestrator.run_pdf_toolchain(item)

            factura = orchestrator.formatear_factura(respuestas["data"])
            saved_sheet = orchestrator.guardar_factura_completa_en_sheets(
                factura["data"]
            )
            factura["id"] = id
            factura["saved_sheet"] = bool(saved_sheet)
            factura["status_code"] = 200

            os.remove(file_location)

            return factura

        # Procesa ZIP
        elif kind.mime == "application/zip":
            app_logger.info("Tenemos un ZIP")
            supported_extensions = [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"]

            # Extrae y procesa cada archivo
            with zipfile.ZipFile(file_location, "r") as zip_ref:
                for member_name in zip_ref.namelist():
                    if member_name.endswith("/"):
                        continue  # Skip dirs

                    # Info del archivo
                    file_name_in_zip = os.path.basename(member_name)
                    file_extension_in_zip = os.path.splitext(file_name_in_zip)[
                        1
                    ].lower()

                    # Extrae archivo
                    downloads_folder = "downloads"
                    zip_ref.extract(member_name, downloads_folder)
                    extracted_file_path = os.path.join(downloads_folder, member_name)
                    media_type, _ = mimetypes.guess_type(extracted_file_path)

                    # Procesa si es compatible
                    if file_extension_in_zip in supported_extensions:
                        item = {
                            "file_name": file_name_in_zip,
                            "file_extension": file_extension_in_zip,
                            "file_path": extracted_file_path,
                            "media_type": media_type,
                            "process_id": f"{id}/{file_name_in_zip}",
                        }
                        await orchestrator.task_queue.put(item)

                    # Notifica y elimina si no es compatible
                    else:
                        await orchestrator.fire_webhook(
                            {
                                "file_name": file_name_in_zip,
                                "file_extension": file_extension_in_zip,
                                "file_path": extracted_file_path,
                                "media_type": kind.media_type,
                                "process_id": f"{id}/{file_name_in_zip}",
                                "error": "Tipo de archivo no permitido.",
                            }
                        )
                        os.remove(extracted_file_path)

        else:
            raise HTTPException(status_code=400, detail="Tipo de archivo no permitido.")

        return {
            "success": True,
            "message": "La factura está siendo procesada.",
            "status_code": 201,
        }

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
