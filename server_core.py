"""
Entrypoint dedicado al resto de los procesos de Invoicy: flujo Claude
(process_invoice.py) + logger de webhooks genérico (webhook.py).

Correr con: uvicorn server_core:app --host 0.0.0.0 --port 8000
"""

from app_factory import create_app
from routes.process_invoice import orchestrator as invoice_orchestrator
from routes.process_invoice import router as process_invoice_router
from routes.webhook import router as webhook_router

app = create_app(
    title="Invoicy — Core API",
    description=(
        "Flujo de extracción de facturas con Claude, más el logger de webhooks "
        "genérico. Separado del flujo de WhatsApp (server_wa.py) y del flujo "
        "BAS (server_bas.py)."
    ),
    routers=[process_invoice_router, webhook_router],
    extra_workers=[invoice_orchestrator],
)
