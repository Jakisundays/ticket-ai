from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
from utils.formatters import formatear_impuestos, formatear_retenciones


def guardar_factura_completa_en_sheets(
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
    private_key = os.getenv("GOOGLE_PRIVATE_KEY").replace("\\n", "\n")

    if not private_key:
        st.error("No se encontró la clave privada.")
        return

    sheet_id = os.getenv("SHEET_ID")

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

    return response
