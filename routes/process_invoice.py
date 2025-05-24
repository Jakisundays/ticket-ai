# FastAPI imports
from fastapi import Form, APIRouter, HTTPException, UploadFile, File

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

# Third-party imports
import aiohttp
import certifi
import filetype
import requests
import zipfile
from jsonschema import validate, ValidationError

# Local imports
from tools import tools

# Crea una instancia del router de FastAPI
router = APIRouter()


# Convierte un archivo PDF a string base64
def pdf_to_base64(file_path: str) -> Union[str, None]:
    try:
        with open(file_path, "rb") as pdf_file:
            binary_data = pdf_file.read()
            base_64_encoded_data = base64.b64encode(binary_data)
            base64_string = base_64_encoded_data.decode("utf-8")
        return base64_string
    except Exception as e:
        print(f"An error occurred: {e}")
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
            print(f"Procesando item {item}")
            try:
                # Procesa la factura y notifica resultado
                factura = await self.process_item(item)
                print("Factura procesada")
                webhook_response = await self.fire_webhook(factura)
                if webhook_response:
                    print("Webhook delivered successfully")
                else:
                    print("Error sending webhook")
            except Exception as e:
                # Si hay error, notifica con webhook
                print(f"An error occurred: {e}")
                item["error"] = e
                item["saved_sheet"] = False
                webhook_response = await self.fire_webhook(item)
                if webhook_response:
                    print("Webhook delivered successfully")
                else:
                    print("Error sending webhook")
            finally:
                # Limpia archivos temporales
                try:
                    os.remove(item["file_path"])
                except OSError as e:
                    print(f"Error deleting file {file_path}: {e}")
                self.task_queue.task_done()

    # Envía resultados vía webhook
    async def fire_webhook(self, data):
        try:
            res = requests.post(
                self.WEBHOOK_URL,
                json=data,
                timeout=10,
            )
            print(res.status_code)
            return True
        except Exception as e:
            print(f"An error occurred: {e}")
            return False

    # Procesa un item según su tipo (imagen o PDF)
    async def process_item(self, item: QueueItem):
        try:
            async with self.semaphore:
                print(f"Procesando item {item}")
                if item["media_type"].startswith("image"):
                    respuestas = await self.run_image_toolchain(item)
                elif item["media_type"] == "application/pdf":
                    respuestas = await self.run_pdf_toolchain(item)

                print("Tenemos las respuestas")
                # Guarda en sheets y formatea respuesta
                saved_sheet = orchestrator.guardar_factura_completa_en_sheets(
                    respuestas
                )
                print(
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
            print(f"An error occurred: {e}")
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
                            await asyncio.sleep(sleep_time)
                        else:
                            print(f"Error: {response.status} - {await response.text()}")
                            raise ValueError(
                                f"Request failed with status {response.status}"
                            )
                except aiohttp.ClientError as e:
                    raise ValueError(f"Request error: {str(e)}")
        raise ValueError("Max retries exceeded.")

    # Procesa imágenes con Claude Vision
    async def run_image_toolchain(
        self,
        item: QueueItem,
    ):
        # Convierte imagen a base64
        image_file = Path(item["file_path"])
        base64_string = base64.b64encode(image_file.read_bytes()).decode()

        # Procesa con primera herramienta
        response = await self.vision_tool_handler(
            tools=[tool["data"] for tool in self.tool_with_prompts],
            encoded_img=base64_string,
            type=item["media_type"],
            prompt=self.tool_with_prompts[0]["prompt"],
            tool_name=self.tool_with_prompts[0]["data"]["name"],
            process_id=item["process_id"],
        )

        # Procesa con resto de herramientas en paralelo
        tasks = []
        for tool in self.tool_with_prompts[1:]:
            tool_res = self.vision_tool_handler(
                tools=[tool["data"] for tool in self.tool_with_prompts],
                encoded_img=base64_string,
                type=item["media_type"],
                prompt=tool["prompt"],
                tool_name=tool["data"]["name"],
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
        # Convierte PDF a base64
        static_content = pdf_to_base64(item["file_path"])

        # Procesa con primera herramienta
        response = await self.pdf_tool_handler(
            tools=[tool["data"] for tool in self.tool_with_prompts],
            static_content=static_content,
            prompt=self.tool_with_prompts[0]["prompt"],
            tool_name=self.tool_with_prompts[0]["data"]["name"],
            process_id=item["process_id"],
        )

        # Procesa con resto de herramientas en paralelo
        tasks = []
        for tool in self.tool_with_prompts[1:]:
            tool_res = self.pdf_tool_handler(
                tools=[tool["data"] for tool in self.tool_with_prompts],
                static_content=static_content,
                prompt=tool["prompt"],
                tool_name=tool["data"]["name"],
                process_id=item["process_id"],
            )
            tasks.append(tool_res)
        results = await asyncio.gather(*tasks)
        respuestas = [response] + results
        item["data"] = respuestas
        return item

    # Maneja el procesamiento de imágenes con Claude Vision
    async def vision_tool_handler(
        self,
        tools: list,
        encoded_img: str,
        type: str,
        prompt: str,
        tool_name: str,
        process_id: str,
        model: str = "claude-sonnet-4-20250514",
        max_retries: int = 6,
    ):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        schema = next(
            (tool["input_schema"] for tool in tools if tool["name"] == tool_name), None
        )
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }

        # Intenta procesar con reintentos
        for attempt in range(0, max_retries):
            try:
                data = {
                    "model": model if attempt < 3 else "claude-3-7-sonnet-20250219",
                    "tools": tools,
                    "max_tokens": 8192,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": type,
                                        "data": encoded_img,
                                    },
                                    "cache_control": {"type": "ephemeral"},
                                },
                                {"type": "text", "text": prompt},
                            ],
                        },
                    ],
                    "tool_choice": {
                        "type": "tool",
                        "name": tool_name,
                        "disable_parallel_tool_use": True,
                    },
                }

                response = await self.make_api_request(
                    url="https://api.anthropic.com/v1/messages",
                    headers=headers,
                    data=data,
                    process_id=process_id,
                )
                content = response.get("content", [])
                tool_msg = next(
                    (c for c in content if c.get("type") == "tool_use"), None
                )
                tool_output = tool_msg["input"]
                validate(instance=tool_output, schema=schema)
                print("✅ Validation passed.")
                return response
            except ValidationError as e:
                # Notifica error de validación
                print(f"❌ Validation error for '{tool_name}': {e.message}")
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
                print(f"Webhook Status Code: {error_response.status_code}")
                if attempt < max_retries:
                    print("🔄 Retrying...")
                    continue
                else:
                    print("❌ Max retries exceeded.")
                    raise ValueError(
                        f"Max retries exceeded for '{tool_name}'. Last error: {e.message}"
                    )
            except Exception as e:
                # Notifica error general
                print(f"❌ Unexpected error: {e}")
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
                print(f"Webhook Status Code: {error_response.status_code}")
                if attempt < max_retries:
                    print("🔄 Retrying...")
                    continue
                else:
                    print("❌ Max retries exceeded.")
                    raise ValueError(
                        f"Max retries exceeded for '{tool_name}'. Last error: {e.message}"
                    )

    # Maneja el procesamiento de PDFs con Claude
    async def pdf_tool_handler(
        self,
        tools: list,
        static_content: str,
        prompt: str,
        tool_name: str,
        process_id: str,
        model: str = "claude-sonnet-4-20250514",
        max_retries: int = 6,
    ):
        api_key = os.getenv("ANTHROPIC_API_KEY")

        if api_key is None:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set in the environment variables."
            )

        schema = next(
            (tool["input_schema"] for tool in tools if tool["name"] == tool_name), None
        )

        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }

        # Intenta procesar con reintentos
        for attempt in range(0, max_retries):
            try:
                data = {
                    "model": model if attempt < 3 else "claude-3-7-sonnet-20250219",
                    "tools": tools,
                    "max_tokens": 8192,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "document",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "application/pdf",
                                        "data": static_content,
                                    },
                                    "cache_control": {"type": "ephemeral"},
                                },
                                {"type": "text", "text": prompt},
                            ],
                        },
                    ],
                    "tool_choice": {
                        "type": "tool",
                        "name": tool_name,
                        "disable_parallel_tool_use": True,
                    },
                }

                response = await self.make_api_request(
                    url="https://api.anthropic.com/v1/messages",
                    headers=headers,
                    data=data,
                    process_id=process_id,
                )
                content = response.get("content", [])
                tool_msg = next(
                    (c for c in content if c.get("type") == "tool_use"), None
                )
                tool_output = tool_msg["input"]
                validate(instance=tool_output, schema=schema)
                return response

            except ValidationError as e:
                # Notifica error de validación
                print(f"❌ Validation error for '{tool_name}': {e.message}")
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
                print(f"Webhook Status Code: {error_response.status_code}")
                if attempt < max_retries:
                    print("🔄 Retrying...")
                    continue
                else:
                    print("❌ Max retries exceeded.")
                    raise ValueError(
                        f"Max retries exceeded for '{tool_name}'. Last error: {e.message}"
                    )
            except Exception as e:
                # Notifica error general
                print(f"❌ Unexpected error: {e}")
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
                print(f"Webhook Status Code: {error_response.status_code}")
                if attempt < max_retries:
                    print("🔄 Retrying...")
                    continue
                else:
                    print("❌ Max retries exceeded.")
                    raise ValueError(
                        f"Max retries exceeded for '{tool_name}'. Last error: {e.message}"
                    )

    # Guarda los datos de la factura en Google Sheets
    def guardar_factura_completa_en_sheets(
        self,
        tool_messages: list,
        range_: str = "A2:M2",
    ):
        """
        Extrae los datos clave de un array de tool_use y los guarda como una fila en Google Sheets.
        """

        # Inicializar variables
        emisor = receptor = otros = comprobante = {}
        subtotal = total = observaciones = ""
        descripcion_items = ""
        impuestos = []

        try:

            # Buscar los bloques relevantes
            for msg in tool_messages:
                for content in msg.get("content", []):
                    if content.get("type") == "tool_use":
                        name = content.get("name")
                        input_data = content.get("input", {})

                        if name == "datos_del_emisor_y_receptor":
                            comprobante = input_data.get("comprobante", {})
                            emisor = input_data.get("emisor", {})
                            receptor = input_data.get("receptor", {})
                            otros = input_data.get("otros", {})

                        elif name == "detalle_de_items_facturados":
                            detalles = input_data.get("detalles", [])

                            descripcion_items = "; ".join(
                                [
                                    f"Descripción: {d.get('descripcion', '')}, Cantidad: {d.get('cantidad', '')}, Precio Unitario: ${d.get('precio_unitario', '')}, Precio Total: ${d.get('precio_total', '')}"
                                    for d in detalles
                                ]
                            )
                            subtotal = input_data.get("subtotal", "")
                            total = input_data.get("total", "")
                            observaciones = input_data.get("observaciones", "")

                        elif name == "impuestos_y_retenciones_de_la_factura":
                            impuestos = input_data.get("impuestos", [])
                            retenciones = input_data.get("retenciones", [])

            # total_impuestos = sum([imp.get("importe", 0) for imp in impuestos])

            # Preparar los datos para una fila
            fila = [
                [
                    comprobante.get("tipo", ""),
                    comprobante.get("subtipo", ""),
                    comprobante.get("jurisdiccion_fiscal", ""),
                    comprobante.get("numero", ""),
                    comprobante.get("fecha_emision", ""),
                    comprobante.get("moneda", ""),
                    emisor.get("nombre", ""),
                    emisor.get("id_fiscal", ""),
                    emisor.get("condicion_iva", ""),
                    emisor.get("direccion", ""),
                    receptor.get("nombre", ""),
                    receptor.get("id_fiscal", ""),
                    receptor.get("condicion_iva", ""),
                    receptor.get("direccion", ""),
                    descripcion_items,
                    subtotal,
                    formatear_impuestos(impuestos),
                    formatear_retenciones(retenciones),
                    total,
                    observaciones,
                    otros.get("CAE", ""),
                    otros.get("vencimiento_CAE", ""),
                    otros.get("forma_pago", ""),
                ]
            ]

            # Cargar credenciales y conectar con Sheets
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]

            client_email = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
            if not client_email:
                print("No se encontró el correo electrónico del servicio.")
                return None
            private_key = os.getenv("GOOGLE_PRIVATE_KEY")
            if not private_key:
                print("No se encontró la clave privada.")
                return None
            private_key = private_key.replace("\\n", "\n")

            sheet_id = os.getenv("SHEET_ID")
            if not sheet_id:
                print("No se encontró el ID de la hoja de cálculo.")
                return None

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

            # Escribir en la hoja
            body = {"values": fila}
            response = (
                sheet.values()
                .append(
                    spreadsheetId=sheet_id,
                    range=range_,
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body=body,
                )
                .execute()
            )
            return True
        except Exception as e:
            print(f"Error al guardar la factura en Google Sheets: {e}")
            return False

    # Formatea los datos de la factura para la respuesta
    def formatear_factura(self, factura_completa):
        print("Formateando factura")
        datos_factura = {}
        total_tokens = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        for respuesta in factura_completa:
            # Acumular tokens
            for token_type, value in respuesta.get("usage", {}).items():
                if token_type == "service_tier":
                    continue
                total_tokens[token_type] += value
            # Extraer datos según el tipo de herramienta
            if respuesta.get("content") and len(respuesta["content"]) > 0:
                tool_content = respuesta["content"][0]
                if tool_content.get("name") == "datos_del_emisor_y_receptor":
                    datos_factura["emisor_receptor"] = tool_content.get("input", {})
                elif tool_content.get("name") == "detalle_de_items_facturados":
                    datos_factura["items"] = tool_content.get("input", {})
                elif (
                    tool_content.get("name") == "impuestos_y_retenciones_de_la_factura"
                ):
                    datos_factura["impuestos"] = tool_content.get("input", {})
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
    secret="1234",
    webhook_url=os.getenv("WEBHOOK_URL"),
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    recharge_cooldown=45,
    queue_check_cooldown=20,
    model="claude-sonnet-4-20250514",
    semaphore=3,
)


@router.post(
    "/process-invoice",
    summary="Procesar factura",
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
                respuestas = await orchestrator.run_image_toolchain(item)
            elif kind.mime == "application/pdf":
                respuestas = await orchestrator.run_pdf_toolchain(item)

            # Guarda resultados y formatea respuesta
            saved_sheet = orchestrator.guardar_factura_completa_en_sheets(respuestas)
            factura = orchestrator.formatear_factura(respuestas["data"])
            factura["id"] = id
            factura["saved_sheet"] = bool(saved_sheet)
            factura["status_code"] = 200

            return factura

        # Procesa ZIP
        elif kind.mime == "application/zip":
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
                        error_response = await orchestrator.fire_webhook(
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
        print(e)
        raise HTTPException(
            status_code=500, detail=f"Error interno del servidor: {str(e)}"
        )
