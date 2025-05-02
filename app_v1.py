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

        # Aquí se implementaría la lógica para procesar la factura
        st.success("¡Archivo cargado con éxito! Listo para procesar.")

        # Botón para procesar la factura (funcionalidad a implementar en el futuro)
        if st.button("Procesar Factura"):
            tools = [
                {
                    "name": "invoice",
                    "description": "Manage and analyze structured invoice details including origin, recipient, subtotal, taxes, and final total.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "structured_invoice": {
                                "type": "object",
                                "description": "Structured invoice details capturing all essential components of an invoice.",
                                "properties": {
                                    "origin": {
                                        "type": "string",
                                        "description": "Origin of the invoice.",
                                    },
                                    "invoice_number": {
                                        "type": "string",
                                        "description": "Unique identifier assigned to the invoice.",
                                    },
                                    "invoice_date": {
                                        "type": "string",
                                        "format": "date",
                                        "description": "The date on which the invoice was issued.",
                                    },
                                    "data": {
                                        "type": "object",
                                        "description": "General data related to the invoice. This can be any dictionary containing additional details.",
                                        "additionalProperties": True,
                                    },
                                    "recipient": {
                                        "type": "string",
                                        "description": "The entity receiving the invoice.",
                                    },
                                    "subtotal": {
                                        "type": "number",
                                        "description": "Subtotal amount before taxes.",
                                    },
                                    "total": {
                                        "type": "number",
                                        "description": "Final total amount including taxes.",
                                    },
                                },
                                "required": [
                                    "origin",
                                    # "data",
                                    "recipient",
                                    "subtotal",
                                    "total",
                                    "invoice_date",
                                    "invoice_number",
                                ],
                                "additionalProperties": False,
                            }
                        },
                        "required": ["structured_invoice"],
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "taxes_and_charges",
                    "description": "Manage and analyze all tax details and additional charges (e.g., fees, surcharges) that are added to the invoice subtotal.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "taxes_and_charges": {
                                "type": "object",
                                "description": "Extract and organize all tax-related details and extra charges from the invoice, structuring them separately as taxes and additional charges.",
                                "properties": {
                                    "taxes": {
                                        "type": "array",
                                        "description": "Analyze the invoice and list every tax applied, providing both the category and the exact amount.",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "tax_type": {
                                                    "type": "string",
                                                    "description": "The category or type of the tax applied.",
                                                },
                                                "amount": {
                                                    "type": "number",
                                                    "description": "The amount of tax applied.",
                                                },
                                            },
                                            "required": ["tax_type", "amount"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    "additional_charges": {
                                        "type": "array",
                                        "description": "List of additional charges (e.g., fees, surcharges) added to the invoice subtotal.",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "description": {
                                                    "type": "string",
                                                    "description": "A brief description of the charge.",
                                                },
                                                "amount": {
                                                    "type": "number",
                                                    "description": "The amount of the additional charge.",
                                                },
                                            },
                                            "required": ["description", "amount"],
                                            "additionalProperties": False,
                                        },
                                    },
                                },
                                "required": ["taxes", "additional_charges"],
                                "additionalProperties": False,
                            }
                        },
                        "required": ["taxes_and_charges"],
                        "additionalProperties": False,
                    },
                    "cache_control": {"type": "ephemeral"},
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

