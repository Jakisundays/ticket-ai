from fastapi import FastAPI, HTTPException, UploadFile, File
import os
import shutil
import filetype
from pathlib import Path
import base64
from utils.ai import call_claude_vision, call_claude_pdf
from utils.sheets import guardar_factura_completa_en_sheets
from utils.file_encoders import pdf_to_base64
from tools import tools
import asyncio

app = FastAPI(
    title="API de Invoicy",
    description="API para procesar facturas electrónicas e imágenes de comprobantes utilizando Claude AI y otras utilidades. Documentación completa en español para facilitar la integración y el uso.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {
            "name": "General",
            "description": "Endpoints generales para chequeo de salud y bienvenida.",
        },
        {
            "name": "Procesamiento de facturas",
            "description": "Procesamiento inteligente de facturas electrónicas e imágenes de comprobantes.",
        },
    ],
)


# API Endpoints
@app.get(
    "/",
    summary="Chequeo de salud",
    tags=["General"],
    response_description="Mensaje de bienvenida y estado de la API.",
)
async def read_root():
    """
    Endpoint raíz para chequeo de salud de la API.

    Devuelve un mensaje de bienvenida y confirma que la API está operativa.

    **Ejemplo de respuesta:**
    {
        "message": "Bienvenido a la API de Invoicy. Documentación disponible en /docs y /redoc."
    }
    """
    return {
        "message": "Bienvenido a la API de Invoicy. Documentación disponible en /docs y /redoc."
    }


@app.post(
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
                    }
                }
            },
        },
        400: {"description": "Tipo de archivo no permitido."},
        500: {"description": "Error interno del servidor."},
    },
)
async def process_invoice(
    file: UploadFile = File(
        ...,
        description="Archivo de la factura a procesar. Puede ser PDF o imagen (png, jpg, jpeg, webp, gif).",
    )
):
    """
    Procesa una factura electrónica o imagen de comprobante y extrae los datos clave utilizando IA.

    **Parámetros:**
    - **file**: Archivo de la factura a procesar. Debe ser PDF o imagen (png, jpg, jpeg, webp, gif).

    **Respuesta exitosa:**
    - **data**: Diccionario con los datos extraídos de la factura, incluyendo emisor, receptor, items e impuestos.
    - **tokens**: Estadísticas de uso de tokens durante el procesamiento.

    **Ejemplo de respuesta:**
    {
        "data": {
            "emisor_receptor": {"nombre": "Empresa S.A.", ...},
            "items": {"detalles": [{"descripcion": "Producto A", ...}], ...},
            "impuestos": {"impuestos": [{"tipo": "IVA", ...}], ...}
        },
        "tokens": {
            "input_tokens": 1234,
            "output_tokens": 567,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0
        }
    }

    **Errores posibles:**
    - 400: Tipo de archivo no permitido.
    - 500: Error interno del servidor.
    """
    try:
        extensiones_permitidas = ["pdf", "png", "jpg", "jpeg", "webp", "gif"]
        extension = file.filename.split(".")[-1].lower()
        if extension not in extensiones_permitidas:
            raise HTTPException(
                status_code=400,
                detail=f"Tipo de archivo no permitido: .{extension}. Solo se aceptan: {', '.join(extensiones_permitidas)}",
            )
        os.makedirs("downloads", exist_ok=True)
        file_location = f"./downloads/{file.filename.split('/')[-1]}"
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        with open(file_location, "rb") as f:
            kind = filetype.guess(f.read(262))
        if kind.mime.startswith("image"):
            image_file = Path(file_location)
            base64_string = base64.b64encode(image_file.read_bytes()).decode()
            response = await call_claude_vision(
                tools=[tool["data"] for tool in tools],
                encoded_img=base64_string,
                type=kind.mime,
                prompt=tools[0]["prompt"],
                tool_name=tools[0]["data"]["name"],
                process_id="process_id",
            )
            tasks = []
            for tool in tools[1:]:
                tool_res = call_claude_vision(
                    tools=[tool["data"] for tool in tools],
                    encoded_img=base64_string,
                    type=kind.mime,
                    prompt=tool["prompt"],
                    tool_name=tool["data"]["name"],
                    process_id="process_id",
                )
                tasks.append(tool_res)
            results = await asyncio.gather(*tasks)
            respuestas = [response] + results
            guardar_factura_completa_en_sheets(
                tool_messages=respuestas,
            )
            datos_factura = {}
            total_tokens = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
            for respuesta in respuestas:
                # Acumular tokens
                for token_type, value in respuesta.get("usage", {}).items():
                    total_tokens[token_type] += value
                # Extraer datos según el tipo de herramienta
                if respuesta.get("content") and len(respuesta["content"]) > 0:
                    tool_content = respuesta["content"][0]
                    if tool_content.get("name") == "datos_del_emisor_y_receptor":
                        datos_factura["emisor_receptor"] = tool_content.get("input", {})
                    elif tool_content.get("name") == "detalle_de_items_facturados":
                        datos_factura["items"] = tool_content.get("input", {})
                    elif (
                        tool_content.get("name")
                        == "impuestos_y_retenciones_de_la_factura"
                    ):
                        datos_factura["impuestos"] = tool_content.get("input", {})
            return {
                "data": datos_factura,
                "tokens": total_tokens,
            }
        elif kind.mime == "application/pdf":
            static_content = pdf_to_base64(file_location)
            response = await call_claude_pdf(
                tools=[tool["data"] for tool in tools],
                static_content=static_content,
                prompt=tools[0]["prompt"],
                tool_name=tools[0]["data"]["name"],
                process_id="process_id",
            )
            tasks = []
            for tool in tools[1:]:
                tool_res = call_claude_pdf(
                    tools=[tool["data"] for tool in tools],
                    static_content=static_content,
                    prompt=tool["prompt"],
                    tool_name=tool["data"]["name"],
                    process_id="process_id",
                )
                tasks.append(tool_res)
            results = await asyncio.gather(*tasks)
            respuestas = [response] + results
            guardar_factura_completa_en_sheets(
                tool_messages=respuestas,
            )
            datos_factura = {}
            total_tokens = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
            for respuesta in respuestas:
                # Acumular tokens
                for token_type, value in respuesta.get("usage", {}).items():
                    total_tokens[token_type] += value
                # Extraer datos según el tipo de herramienta
                if respuesta.get("content") and len(respuesta["content"]) > 0:
                    tool_content = respuesta["content"][0]
                    if tool_content.get("name") == "datos_del_emisor_y_receptor":
                        datos_factura["emisor_receptor"] = tool_content.get("input", {})
                    elif tool_content.get("name") == "detalle_de_items_facturados":
                        datos_factura["items"] = tool_content.get("input", {})
                    elif (
                        tool_content.get("name")
                        == "impuestos_y_retenciones_de_la_factura"
                    ):
                        datos_factura["impuestos"] = tool_content.get("input", {})
            return {
                "data": datos_factura,
                "tokens": total_tokens,
            }
        else:
            raise HTTPException(status_code=400, detail="Tipo de archivo no permitido.")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error interno del servidor: {str(e)}"
        )
    finally:
        if os.path.exists(file_location):
            os.remove(file_location)
