import streamlit as st
import os
import io
import PyPDF2
import fitz
from PIL import Image
import ssl
from typing import List, Dict, Any, Optional, Union
import aiohttp
import certifi
import asyncio
import base64
from pathlib import Path
import json


def convert_image_to_base64(img: Image.Image) -> str:
    """
    Converts a PIL Image to a base64-encoded string.

    Args:
    img (Image.Image): The PIL image to be converted.

    Returns:
    str: The base64-encoded string representation of the image.
    """
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    base64_encoded_data = base64.b64encode(buffered.getvalue())
    base64_string = base64_encoded_data.decode("utf-8")
    return base64_string


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


async def make_api_request(
    url: str, headers: Dict, data: Dict, process_id: str, retries: int = 5
) -> Optional[Dict]:
    """Alternative helper function to make API requests with retries asynchronously."""
    # Create an SSL context using the certifi CA bundle
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    # Create a connector with the custom SSL context
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    connector = aiohttp.TCPConnector(verify_ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        for i in range(retries):
            try:
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status in [429, 529, 503]:
                        retry_after = response.headers.get("retry-after")
                        print(f"retry_after: {retry_after}")
                        print(
                            f"Service unavailable. Waiting {retry_after} seconds before retrying..."
                        )
                        await asyncio.sleep(retry_after)
                        # print(
                        #     f"Service unavailable. Waiting {WAIT_TIMES[i]} seconds before retrying..."
                        # )
                        # await asyncio.sleep(WAIT_TIMES[i])
                    else:
                        print(f"Error: {response.status} - {await response.text()}")
                        raise ValueError(
                            f"Request failed with status {response.status}"
                        )
            except aiohttp.ClientError as e:
                raise ValueError(f"Request error: {str(e)}")
    raise ValueError("Max retries exceeded.")


async def call_claude_vision(
    tools: list,
    encoded_img: str,
    type: str,
    prompt: str,
    tool_name: str,
    process_id: str,
    model: str = "claude-3-7-sonnet-20250219",
):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key is None:
        raise ValueError("ANTHROPIC_API_KEY is not set in the environment variables.")
    data = {
        "model": model,
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

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    response = await make_api_request(
        url="https://api.anthropic.com/v1/messages",
        headers=headers,
        data=data,
        process_id=process_id,
    )
    return response


async def call_claude_pdf(
    tools: list,
    static_content: str,
    prompt: str,
    tool_name: str,
    process_id: str,
    model: str = "claude-3-7-sonnet-20250219",
):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key is None:
        raise ValueError("ANTHROPIC_API_KEY is not set in the environment variables.")

    data = {
        "model": model,
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

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    response = await make_api_request(
        url="https://api.anthropic.com/v1/messages",
        headers=headers,
        data=data,
        process_id=process_id,
    )
    return response


def save_uploaded_file(uploaded_file, save_path):
    # Create the full file path
    file_path = os.path.join(save_path, uploaded_file.name)

    # Ensure the directory exists
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    # Save the file
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return file_path


async def main():
    # Configuración de la página
    st.set_page_config(page_title="Invoicy", page_icon="📄")

    # Título y descripción
    st.title("Invoicy 📄")
    st.write(
        "Invoicy es una aplicación diseñada para extraer información relevante de facturas."
    )

    # Separador
    st.markdown("---")

    # Sección de carga de archivos
    st.subheader("Cargar Factura")
    st.write(
        "Por favor, cargue su factura en formato PDF o imagen (PNG, JPEG, WEBP, GIF no animado)."
    )

    # Componente para cargar archivos
    archivo_cargado = st.file_uploader(
        "Seleccione un archivo",
        type=["pdf", "png", "jpg", "jpeg", "webp", "gif"],
        help="Formatos soportados: PDF, PNG, JPEG, WEBP, GIF no animado",
    )

    # Mostrar el archivo cargado
    if archivo_cargado is not None:
        # Mostrar información del archivo
        file_details = {
            "Nombre del archivo": archivo_cargado.name,
            "Tipo de archivo": archivo_cargado.type,
            "Tamaño": f"{archivo_cargado.size} bytes",
        }

        st.write("### Detalles del archivo:")
        for key, value in file_details.items():
            st.write(f"**{key}:** {value}")

        # Mostrar vista previa según el tipo de archivo
        if archivo_cargado.type.startswith("image"):
            st.image(archivo_cargado, caption=f"Imagen cargada: {archivo_cargado.name}")
            st.write(archivo_cargado.type)
        elif archivo_cargado.type == "application/pdf":
            st.write("El archivo PDF ha sido cargado correctamente.")

        # Botón para procesar la factura (funcionalidad a implementar en el futuro)
        if st.button("Procesar Factura"):
            tools = [
                {
                    "name": "extraer_datos_basicos_comprobante", # Updated name
                    "description": "Extrae y gestiona los datos clave de un comprobante fiscal como tipo, número, jurisdicción, fecha y moneda.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "comprobante": {
                                "type": "object",
                                "description": "Detalles estructurados del comprobante fiscal.",
                                "properties": {
                                    "tipo_comprobante": {
                                        "type": "string",
                                        "description": "Tipo de comprobante fiscal, como 'Factura' o 'Nota de Crédito'.",
                                    },
                                    "subtipo_comprobante": {
                                        "type": "string",
                                        "enum": [
                                            "Para operaciones entre responsables inscriptos",
                                            "Para consumidores finales y exentos",
                                            "Emitida por monotributistas",
                                        ],
                                        "description": "Subtipo del comprobante basado en el régimen fiscal del receptor.",
                                    },
                                    "jurisdiccion_fiscal": {
                                        "type": "string",
                                        "description": "País o jurisdicción fiscal del comprobante, como 'Argentina'.",
                                    },
                                    "numero_comprobante": {
                                        "type": "string",
                                        "description": "Número del comprobante, por ejemplo '0001-00000001'.",
                                    },
                                    "fecha_emision": {
                                        "type": "string",
                                        "format": "date",
                                        "description": "Fecha de emisión del comprobante (YYYY-MM-DD).",
                                    },
                                    "moneda": {
                                        "type": "string",
                                        "description": "Moneda del comprobante, por ejemplo 'ARS', 'USD'.",
                                    },
                                },
                                "required": [
                                    "tipo_comprobante",
                                    "jurisdiccion_fiscal",
                                    "numero_comprobante",
                                    "fecha_emision",
                                    "moneda",
                                ],
                                "additionalProperties": False,
                            }
                        },
                        "required": ["comprobante"],
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "extraer_datos_completos_comprobante", # Updated name
                    "description": "Extrae y gestiona los datos clave de un comprobante fiscal, incluyendo datos del emisor y del receptor con validación de enums.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "comprobante": {
                                "type": "object",
                                "description": "Datos generales del comprobante.",
                                "properties": {
                                    "tipo": {
                                        "type": "string",
                                        "enum": [
                                            "Factura",
                                            "Nota de Crédito",
                                            "Nota de Débito",
                                        ],
                                        "description": "Tipo de comprobante fiscal.",
                                    },
                                    "subtipo": {
                                        "type": "string",
                                        "enum": ["A", "B", "C"],
                                        "description": "Letra del comprobante según régimen fiscal.",
                                    },
                                    "numero": {
                                        "type": "string",
                                        "description": "Número del comprobante, por ejemplo: '0001-00001234'.",
                                    },
                                    "fecha_emision": {
                                        "type": "string",
                                        "format": "date",
                                        "description": "Fecha de emisión del comprobante (YYYY-MM-DD).",
                                    },
                                    "moneda": {
                                        "type": "string",
                                        "description": "Moneda en la que se emite el comprobante, por ejemplo 'ARS', 'USD'.",
                                    },
                                },
                                "required": [
                                    "tipo",
                                    "subtipo",
                                    "numero",
                                    "fecha_emision",
                                    "moneda",
                                ],
                                "additionalProperties": False,
                            },
                            "emisor": {
                                "type": "object",
                                "description": "Datos del emisor del comprobante.",
                                "properties": {
                                    "nombre": {
                                        "type": "string",
                                        "description": "Nombre o razón social del emisor.",
                                    },
                                    "id_fiscal": {
                                        "type": "string",
                                        "description": "CUIT o CUIL del emisor.",
                                    },
                                    "direccion": {
                                        "type": "string",
                                        "description": "Domicilio fiscal del emisor.",
                                    },
                                    "condicion_iva": {
                                        "type": "string",
                                        "enum": [
                                            "Responsable Inscripto",
                                            "Monotributo",
                                            "Exento",
                                            "Desconocido",
                                        ],
                                        "description": "Condición frente al IVA del emisor.",
                                    },
                                },
                                "required": ["nombre", "id_fiscal"],
                                "additionalProperties": False,
                            },
                            "receptor": {
                                "type": "object",
                                "description": "Datos del receptor del comprobante.",
                                "properties": {
                                    "nombre": {
                                        "type": "string",
                                        "description": "Nombre o razón social del receptor.",
                                    },
                                    "id_fiscal": {
                                        "type": "string",
                                        "description": "CUIT o CUIL del receptor.",
                                    },
                                    "direccion": {
                                        "type": "string",
                                        "description": "Domicilio del receptor.",
                                    },
                                    "condicion_iva": {
                                        "type": "string",
                                        "enum": [
                                            "Responsable Inscripto",
                                            "Consumidor Final",
                                            "Desconocido",
                                        ],
                                        "description": "Condición frente al IVA del receptor.",
                                    },
                                },
                                "required": ["nombre"],
                                "additionalProperties": False,
                            },
                        },
                        "required": ["comprobante", "emisor", "receptor"],
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "extraer_detalle_items_factura", # Updated name
                    "description": "Extrae y valida los detalles de ítems facturados junto con totales e impuestos.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "detalles": {
                                "type": "array",
                                "description": "Lista de ítems facturados en la factura.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "descripcion": {
                                            "type": "string",
                                            "description": "Descripción del producto o servicio facturado.",
                                        },
                                        "cantidad": {
                                            "type": "number",
                                            "description": "Cantidad facturada.",
                                        },
                                        "precio_unitario": {
                                            "type": "number",
                                            "description": "Precio por unidad antes de impuestos.",
                                        },
                                        "precio_total": {
                                            "type": "number",
                                            "description": "Total de la línea (cantidad x precio unitario).",
                                        },
                                    },
                                    "required": [
                                        "descripcion",
                                        "cantidad",
                                        "precio_unitario",
                                        "precio_total",
                                    ],
                                    "additionalProperties": False,
                                },
                            },
                            "subtotal": {
                                "type": "number",
                                "description": "Suma de importes sin incluir impuestos.",
                            },
                        },
                        "required": ["detalles", "subtotal"],
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "extraer_impuestos_retenciones_factura", # Updated name
                    "description": "Extrae, valida y estructura la información de impuestos y retenciones de una factura.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "impuestos": {
                                "type": "array",
                                "description": "Lista de impuestos aplicados en la factura.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "tipo": {
                                            "type": "string",
                                            "description": "Tipo de impuesto aplicado (e.g., IVA, Percepción, Impuesto Municipal).",
                                        },
                                        "descripcion": {
                                            "type": "string",
                                            "description": "Descripción detallada del impuesto.",
                                            "nullable": True,
                                        },
                                        "base_imponible": {
                                            "type": "number",
                                            "description": "Monto base sobre el que se calcula el impuesto.",
                                        },
                                        "alicuota": {
                                            "type": "number",
                                            "description": "Porcentaje de alícuota aplicada.",
                                            "nullable": True,
                                        },
                                        "importe": {
                                            "type": "number",
                                            "description": "Importe del impuesto calculado.",
                                        },
                                    },
                                    "required": ["tipo", "base_imponible", "importe"],
                                    "additionalProperties": False,
                                },
                            },
                            "retenciones": {
                                "type": "array",
                                "description": "Lista de retenciones aplicadas.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "tipo": {
                                            "type": "string",
                                            "description": "Tipo de retención (e.g., Ganancias, IVA, IIBB).",
                                        },
                                        "description": {
                                            "type": "string",
                                            "description": "Detalle adicional de la retención.",
                                            "nullable": True,
                                        },
                                        "base_imponible": {
                                            "type": "number",
                                            "description": "Monto base sobre el que se aplica la retención.",
                                        },
                                    },
                                    "required": ["tipo", "base_imponible"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["impuestos", "retenciones"],
                        "additionalProperties": False,
                    },
                },
            ]

            # Mostrar vista previa según el tipo de archivo
            if archivo_cargado.type.startswith("image"):
                file_path = save_uploaded_file(archivo_cargado, "./uploaded_pdfs")

                image_file = Path(file_path)
                assert image_file.is_file(), "The provided image path does not exist."

                # Read and encode the image file
                base64_string = base64.b64encode(image_file.read_bytes()).decode()

                tool_answer = await call_claude_vision(
                    tools=tools,
                    encoded_img=base64_string,
                    type=archivo_cargado.type,
                    prompt="Extract structured invoice details from the provided image.",
                    tool_name="invoice",
                    process_id="process_id",
                )

                st.write(tool_answer)

                taxes_answer = await call_claude_vision(
                    tools=tools,
                    encoded_img=base64_string,
                    type=archivo_cargado.type,
                    prompt="Extract and categorize all invoice charges beyond the subtotal. Identify each tax type (such as VAT, sales tax) with its specific amount, and list any additional fees or surcharges along with a brief description and their respective amounts.",
                    tool_name="taxes_and_charges",
                    process_id="process_id",
                )
                st.write(taxes_answer)

            elif archivo_cargado.type == "application/pdf":

                file_path = save_uploaded_file(archivo_cargado, "./uploaded_pdfs")
                static_content = pdf_to_base64(file_path)

                with open(file_path, "rb") as saved_file:
                    pdf_data = saved_file.read()
                    pdf_reader = PyPDF2.PdfReader(io.BytesIO(pdf_data))
                    num_pages = len(pdf_reader.pages)
                    st.write(f"Number of pages: {num_pages}")
                    # hacer proceso con claude pdf
                    static_content = pdf_to_base64(file_path)
                    tool_answer = await call_claude_pdf(
                        tools=tools,
                        static_content=static_content,
                        prompt="Extract structured invoice details from the provided PDF.",
                        tool_name="invoice",
                        process_id="process_id",
                    )
                    st.write(tool_answer)
                    taxes_answer = await call_claude_pdf(
                        tools=tools,
                        static_content=static_content,
                        prompt="Extract and categorize all invoice charges beyond the subtotal. Identify each tax type (such as VAT, sales tax) with its specific amount, and list any additional fees or surcharges along with a brief description and their respective amounts.",
                        tool_name="taxes_and_charges",
                        process_id="process_id",
                    )
                    st.write(taxes_answer)


if __name__ == "__main__":
    asyncio.run(main())
