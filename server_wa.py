"""
Entrypoint dedicado al flujo acoplado a ticket-wa (WhatsApp).

Monta únicamente `process_invoice_google_router` (prefix /gemini). Ver plan de
arquitectura en docs/ (o el plan de sesión) para el razonamiento de por qué este
flujo vive en su propio contenedor, separado del resto de Invoicy.

Correr con: uvicorn server_wa:app --host 0.0.0.0 --port 8000
"""

from app_factory import create_app
from routes.process_invoice_google import orchestrator as google_orchestrator
from routes.process_invoice_google import router as process_invoice_google_router

app = create_app(
    title="Invoicy — WA API",
    description=(
        "Flujo Gemini de extracción de facturas acoplado a ticket-wa (WhatsApp). "
        "Único router montado: process_invoice_google (prefix /gemini)."
    ),
    routers=[process_invoice_google_router],
    extra_workers=[google_orchestrator],
)
