import streamlit as st
import os
import io
from PIL import Image
import ssl
from typing import Dict, Optional, Union
import aiohttp
import certifi
import asyncio
import base64
from pathlib import Path
import pandas as pd
import fitz
from google.oauth2 import service_account
from googleapiclient.discovery import build


def formatear_retenciones(retenciones):
    if not retenciones:
        return "No hay retenciones."
    resultado = []
    for i, r in enumerate(retenciones, start=1):
        texto = (
            f"Retención #{i}:\n"
            f"  - Tipo: {r['tipo']}\n"
            f"  - Descripción: {r['description'] or 'No especificada'}\n"
            f"  - Base Imponible: ${r['base_imponible']:.2f}\n"
        )
        resultado.append(texto)
    return "\n".join(resultado)


def formatear_impuestos(impuestos):
    if not impuestos:
        return "No hay impuestos."
    resultado = []
    for i, imp in enumerate(impuestos, start=1):
        texto = (
            f"Impuesto #{i}:\n"
            f"  - Tipo: {imp['tipo']}\n"
            f"  - Descripción: {imp['descripcion'] or 'No especificada'}\n"
            f"  - Base Imponible: ${imp['base_imponible']:.2f}\n"
            f"  - Alícuota: {f'{imp["alicuota"]:.2f}%' if imp['alicuota'] is not None else 'No especificada'}\n"
            f"  - Importe: ${imp['importe']:.2f}\n"
        )
        resultado.append(texto)
    return "\n".join(resultado)


def guardar_factura_completa_en_sheets(
    tool_messages: list,
    client_email: str,
    private_key: str,
    sheet_id: str,
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
                        [d.get("descripcion", "") for d in detalles]
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

    credentials = service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "client_email": client_email,
            "private_key": private_key,
            "token_uri": "https://accounts.google.com/o/oauth2/token",
        },
        scopes=scopes,
    )

    print(credentials)
    # credentials = service_account.Credentials.from_service_account_file(
    #     cred_path, scopes=scopes
    # )
    service = build("sheets", "v4", credentials=credentials)

    print(service)
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

    return response


def convert_pdf_to_images(pdf_bytes):
    images = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        pix = page.get_pixmap()
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        images.append(img)
    return images


# Mostrar datos extraídos
def mostrar_datos(respuestas):
    # Extracción de datos de las respuestas
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
            elif tool_content.get("name") == "impuestos_y_retenciones_de_la_factura":
                datos_factura["impuestos"] = tool_content.get("input", {})

    # Mostrar datos del comprobante, emisor y receptor
    if "emisor_receptor" in datos_factura:
        mostrar_datos_comprobante(datos_factura["emisor_receptor"])

    # Mostrar detalles de facturación
    if "items" in datos_factura:
        mostrar_items_facturados(datos_factura["items"])

    # Mostrar impuestos y retenciones
    if "impuestos" in datos_factura:
        mostrar_impuestos(datos_factura["impuestos"])

    # Mostrar el uso de tokens
    mostrar_uso_tokens(total_tokens)

    client_email = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
    private_key = os.getenv("GOOGLE_PRIVATE_KEY")
    sheet_id = os.getenv("SHEET_ID")

    guardar_factura_completa_en_sheets(
        tool_messages=respuestas,
        client_email=client_email,
        private_key=private_key,
        sheet_id=sheet_id,
    )

    # Show link to Google Sheet with extracted data
    st.markdown(
        """
        ### Ver datos en Google Sheets
        Los datos extraídos se pueden visualizar en tiempo real en esta [hoja de cálculo](https://docs.google.com/spreadsheets/d/1hUUm3OKJGn2JAGatl2HsykhvYadAWxASJ9DOTQVyrCg/edit?gid=0#gid=0).
    """
    )


# Mostrar datos del comprobante
def mostrar_datos_comprobante(datos):
    st.header("Datos del Comprobante")

    # Comprobante
    if "comprobante" in datos:
        comprobante = datos["comprobante"]
        st.subheader("Comprobante")

        # Crear dos columnas para los datos del comprobante
        col1, col2 = st.columns(2)

        with col1:
            st.info(f"**Tipo:** {comprobante.get('tipo', 'N/A')}")
            st.info(f"**Número:** {comprobante.get('numero', 'N/A')}")
            st.info(f"**Moneda:** {comprobante.get('moneda', 'N/A')}")

        with col2:
            st.info(f"**Fecha de emisión:** {comprobante.get('fecha_emision', 'N/A')}")
            st.info(
                f"**Jurisdiccion fiscal:** {comprobante.get('jurisdiccion_fiscal', 'N/A')}"
            )
            if "subtipo" in comprobante:
                st.info(f"**Subtipo:** {comprobante.get('subtipo', 'N/A')}")

    # Emisor
    if "emisor" in datos:
        emisor = datos["emisor"]
        st.subheader("Datos del Emisor")

        # Mostrar datos del emisor sin columnas anidadas
        st.info(f"**Nombre:** {emisor.get('nombre', 'N/A')}")
        st.info(f"**ID Fiscal:** {emisor.get('id_fiscal', 'N/A')}")
        if "condicion_iva" in emisor:
            st.info(f"**Condición IVA:** {emisor.get('condicion_iva', 'N/A')}")
        if "direccion" in emisor:
            st.info(f"**Dirección:** {emisor.get('direccion', 'N/A')}")

    # Receptor
    if "receptor" in datos:
        receptor = datos["receptor"]
        st.subheader("Datos del Receptor")

        # Mostrar datos del receptor sin columnas anidadas
        st.info(f"**Nombre:** {receptor.get('nombre', 'N/A')}")
        if "id_fiscal" in receptor:
            st.info(f"**ID Fiscal:** {receptor.get('id_fiscal', 'N/A')}")
        if "condicion_iva" in receptor:
            st.info(f"**Condición IVA:** {receptor.get('condicion_iva', 'N/A')}")
        if "direccion" in receptor:
            st.info(f"**Dirección:** {receptor.get('direccion', 'N/A')}")

    # Otros datos
    if "otros" in datos:
        otros = datos["otros"]
        st.subheader("Información Adicional")

        # Mostrar datos adicionales sin columnas anidadas
        if "forma_pago" in otros:
            st.info(f"**Forma de pago:** {otros.get('forma_pago', 'N/A')}")
        if "CAE" in otros:
            st.info(f"**CAE:** {otros.get('CAE', 'N/A')}")
        if "vencimiento_CAE" in otros:
            st.info(f"**Vencimiento CAE:** {otros.get('vencimiento_CAE', 'N/A')}")


# Mostrar detalles de facturación
def mostrar_items_facturados(datos):
    st.header("Detalles de Facturación")

    # Tabla de detalles
    if (
        "detalles" in datos
        and isinstance(datos["detalles"], list)
        and datos["detalles"]
    ):
        st.subheader("Ítems Facturados")
        try:
            df = pd.DataFrame(datos["detalles"])

            # Formatear datos numéricos
            if "precio_unitario" in df:
                df["precio_unitario"] = df["precio_unitario"].apply(
                    lambda x: f"${x:,.2f}" if pd.notna(x) else "N/A"
                )
            if "precio_total" in df:
                df["precio_total"] = df["precio_total"].apply(
                    lambda x: f"${x:,.2f}" if pd.notna(x) else "N/A"
                )

            st.dataframe(df, use_container_width=True)
        except ValueError as e:
            with st.expander(
                "No fue posible formatear correctamente los detalles de facturación. A continuación, se muestra el contenido en formato JSON:"
            ):
                st.write(datos["detalles"])
    else:
        with st.expander(
            "No fue posible formatear correctamente los detalles de facturación. A continuación, se muestra el contenido en formato JSON:"
        ):
            st.write(datos["detalles"])

    # Totales
    st.subheader("Totales")
    if "subtotal" in datos and pd.notna(datos["subtotal"]):
        try:
            # Convert subtotal to float if it's a string
            subtotal = (
                float(datos["subtotal"])
                if isinstance(datos["subtotal"], str)
                else datos["subtotal"]
            )
            st.success(f"**Subtotal:** ${subtotal:,.2f}")
        except (ValueError, TypeError):
            # Handle case where conversion fails
            st.success(f"**Subtotal:** ${datos['subtotal']}")
    else:
        st.info("Subtotal no disponible.")

    if "total" in datos and pd.notna(datos["total"]):
        try:
            # Convert total to float if it's a string
            total = (
                float(datos["total"])
                if isinstance(datos["total"], str)
                else datos["total"]
            )
            st.success(f"**Total:** ${total:,.2f}")
        except (ValueError, TypeError):
            # Handle case where conversion fails
            st.success(f"**Total:** ${datos['total']}")
    else:
        st.info("Total no disponible.")

    # Observaciones
    if "observaciones" in datos and datos["observaciones"]:
        st.subheader("Observaciones")
        st.info(datos["observaciones"])


def mostrar_impuestos(datos):
    st.header("Impuestos y Retenciones")

    # Tabla de impuestos
    if "impuestos" in datos and datos["impuestos"]:
        st.subheader("Impuestos")

        # Crear una tabla para mostrar los impuestos
        datos_impuestos = []
        for impuesto in datos["impuestos"]:
            tipo = impuesto.get("tipo", "N/A")
            if "descripcion" in impuesto and impuesto["descripcion"]:
                tipo += f" - {impuesto['descripcion']}"

            alicuota = (
                f"{impuesto.get('alicuota', 'N/A')}%"
                if "alicuota" in impuesto and impuesto["alicuota"] is not None
                else "N/A"
            )
            importe = f"${impuesto.get('importe', 0):,.2f}"

            datos_impuestos.append(
                {"Tipo": tipo, "Alícuota": alicuota, "Importe": importe}
            )

        # Mostrar como dataframe si hay datos
        if datos_impuestos:
            st.dataframe(
                pd.DataFrame(datos_impuestos), use_container_width=True, hide_index=True
            )
    else:
        st.info("No hay impuestos registrados")

    # Tabla de retenciones
    if "retenciones" in datos and datos["retenciones"]:
        st.subheader("Retenciones")

        # Crear una tabla para mostrar las retenciones
        datos_retenciones = []
        for retencion in datos["retenciones"]:
            tipo = retencion.get("tipo", "N/A")
            if "descripcion" in retencion and retencion["descripcion"]:
                tipo += f" - {retencion['descripcion']}"

            base = f"${retencion.get('base_imponible', 0):,.2f}"

            datos_retenciones.append({"Tipo": tipo, "Base Imponible": base})

        # Mostrar como dataframe si hay datos
        if datos_retenciones:
            st.dataframe(
                pd.DataFrame(datos_retenciones),
                use_container_width=True,
                hide_index=True,
            )
    else:
        st.info("No hay retenciones registradas")


# Mostrar uso de tokens
def mostrar_uso_tokens(tokens):
    st.header("Uso de Tokens")

    # Crear una tabla para el uso de tokens
    tokens_data = {
        "Tipo": [
            "Input Tokens",
            "Output Tokens",
            "Cache Creation",
            "Cache Read",
        ],
        "Cantidad": [
            tokens["input_tokens"],
            tokens["output_tokens"],
            tokens["cache_creation_input_tokens"],
            tokens["cache_read_input_tokens"],
        ],
    }

    # Mostrar como dataframe
    df_tokens = pd.DataFrame(tokens_data)
    st.dataframe(df_tokens, use_container_width=True, hide_index=True)

    # Mostrar el total como un métrico destacado
    # st.metric("Total Tokens Utilizados", sum(tokens.values()))


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
    st.set_page_config(page_title="Ticket AI", page_icon="📄", layout="wide")

    # Título y descripción
    st.sidebar.title("Ticket AI 📄")
    st.sidebar.write(
        "Ticket AI es una aplicación diseñada para extraer información relevante de facturas."
    )

    # Separador
    st.sidebar.markdown("---")

    # Sección de carga de archivos
    st.sidebar.subheader("Cargar Factura")
    st.sidebar.write(
        "Por favor, cargue su factura en formato PDF o imagen (PNG, JPEG, WEBP, GIF no animado)."
    )

    # Componente para cargar archivos
    archivo_cargado = st.sidebar.file_uploader(
        "Seleccione un archivo",
        type=["pdf", "png", "jpg", "jpeg", "webp", "gif"],
        help="Formatos soportados: PDF, PNG, JPEG, WEBP, GIF no animado",
    )

    # Mostrar el archivo cargado
    if archivo_cargado is not None:
        if st.sidebar.button("Procesar Factura", use_container_width=True):
            tools = [
                {
                    "prompt": (
                        "Extrae los detalles estructurados de la factura. "
                        "Incluye el domicilio comercial del emisor y del receptor, "
                        "priorizando siempre el Domicilio Comercial sobre el Legal o Fiscal. "
                    ),
                    "data": {
                        "name": "datos_del_emisor_y_receptor",  # Updated name
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
                                        "jurisdiccion_fiscal",
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
                                            "description": "Extrae el domicilio comercial del emisor. Prioriza siempre el Domicilio Comercial sobre el Legal o Fiscal.",
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
                                            "description": "Extrae el domicilio comercial del receptor. Prioriza siempre el Domicilio Comercial sobre el Legal o Fiscal.",
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
                                "otros": {
                                    "type": "object",
                                    "description": "Otros datos relevantes del comprobante.",
                                    "properties": {
                                        "forma_pago": {
                                            "type": "string",
                                            "description": "Método de pago utilizado. Ejemplos: 'Efectivo', 'Transferencia', 'Tarjeta de crédito'.",
                                            "nullable": True,
                                        },
                                        "CAE": {
                                            "type": "string",
                                            "description": "Código de Autorización Electrónico emitido por AFIP.",
                                            "nullable": True,
                                        },
                                        "vencimiento_CAE": {
                                            "type": "string",
                                            "format": "date",
                                            "description": "Fecha de vencimiento del CAE en formato YYYY-MM-DD.",
                                            "nullable": True,
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["comprobante", "emisor", "receptor"],
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "prompt": "Extrae los detalles estructurados de la factura",
                    "data": {
                        "name": "detalle_de_items_facturados",  # Updated name
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
                                "total": {
                                    "type": "number",
                                    "description": "Importe total del comprobante, calculado como subtotal + impuestos - retenciones. Ejemplo: 4130.00",
                                },
                                "observaciones": {
                                    "type": "string",
                                    "description": "Notas o comentarios adicionales sobre el comprobante.",
                                },
                            },
                            "required": ["detalles", "subtotal", "total"],
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "prompt": (
                        "Extrae y clasifica todos los cargos que aparezcan en la factura por encima del subtotal. "
                        "Incluye impuestos como IVA o impuestos a las ventas, y detalla cualquier cargo adicional o recargo con su descripción y monto. "
                        "Las retenciones deben corresponder exclusivamente a conceptos fiscales o tributarios (como Ganancias, IVA, IIBB). "
                        "No incluyas descuentos comerciales, promociones ni bonificaciones bajo la categoría de retenciones. "
                        "Estos deben clasificarse por separado o ser ignorados si no corresponden a un cargo sobre el subtotal."
                    ),
                    "data": {
                        "name": "impuestos_y_retenciones_de_la_factura",  # Updated name
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
                                        "required": [
                                            "tipo",
                                            "base_imponible",
                                            "importe",
                                        ],
                                        "additionalProperties": False,
                                    },
                                },
                                "retenciones": {
                                    "type": "array",
                                    "description": "Lista de retenciones aplicadas. No incluir descuentos ni promociones; estos deben clasificarse por separado.",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "tipo": {
                                                "type": "string",
                                                "description": "Tipo de retención (e.g., Ganancias, IVA, IIBB). No usar para descuentos o promociones.",
                                            },
                                            "description": {
                                                "type": "string",
                                                "description": "Detalle adicional de la retención. No incluir información de descuentos ni promociones.",
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
                },
            ]

            # Mostrar vista previa según el tipo de archivo
            if archivo_cargado.type.startswith("image"):
                col1, col2 = st.columns([1, 1])

                with col1:
                    st.write("## Factura")
                    st.image(
                        archivo_cargado,
                        caption=f"Imagen cargada: {archivo_cargado.name}",
                        use_container_width=True,
                    )

                file_path = save_uploaded_file(archivo_cargado, "./uploaded_pdfs")

                image_file = Path(file_path)
                assert image_file.is_file(), "The provided image path does not exist."

                # Read and encode the image file
                base64_string = base64.b64encode(image_file.read_bytes()).decode()

                response = await call_claude_vision(
                    tools=[tool["data"] for tool in tools],
                    encoded_img=base64_string,
                    type=archivo_cargado.type,
                    prompt=tools[0]["prompt"],
                    tool_name=tools[0]["data"]["name"],
                    process_id="process_id",
                )

                # with st.expander("Respuesta"):
                #     st.write(response)

                tasks = []
                for tool in tools[1:]:
                    tool_res = call_claude_vision(
                        tools=[tool["data"] for tool in tools],
                        encoded_img=base64_string,
                        type=archivo_cargado.type,
                        prompt=tool["prompt"],
                        tool_name=tool["data"]["name"],
                        process_id="process_id",
                    )
                    tasks.append(tool_res)

                # Wait for all tasks to complete
                results = await asyncio.gather(*tasks)
                # with st.expander("Respuestas"):
                #     st.write(results)

                all_results = [response] + results

                with col2:
                    st.write("## Respuestas")
                    with st.expander("Respuestas - No formateadas"):
                        st.write(all_results)
                    mostrar_datos(all_results)

                # Delete the temporary PDF file after processing
                try:
                    os.remove(file_path)
                except OSError as e:
                    print(f"Error deleting file {file_path}: {e}")

            elif archivo_cargado.type == "application/pdf":

                file_path = save_uploaded_file(archivo_cargado, "./uploaded_pdfs")
                static_content = pdf_to_base64(file_path)

                with open(file_path, "rb") as saved_file:
                    images = convert_pdf_to_images(saved_file.read())

                    col1, col2 = st.columns([1, 1])  # Puedes ajustar las proporciones

                    with col1:
                        st.write("## Páginas del PDF")
                        for i, img in enumerate(images):
                            st.image(img, caption=f"Página {i + 1}")

                    static_content = pdf_to_base64(file_path)

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

                    all_results = [response] + results

                    with col2:
                        st.write("## Respuestas")
                        with st.expander("Respuestas - No formateadas"):
                            st.write(all_results)
                        mostrar_datos(all_results)

                try:
                    os.remove(file_path)
                except OSError as e:
                    print(f"Error deleting file {file_path}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
