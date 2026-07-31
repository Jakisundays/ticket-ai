"""
Entrypoint dedicado al flujo Gemini + integración BAS ERP.

Monta `process_invoice_google_router_2` (prefix /gemini2) y
`batch_import_router` (prefix /admin/batch-import, ver
docs/plan-importacion-masiva-facturas.md) -- este último reusa el orchestrator
singleton y _procesar_imagen_o_pdf de process_invoice_google_2, así que
importarlo por separado no crea una segunda instancia del orchestrator.

No se pasa `extra_workers`: a diferencia de los flujos Claude y Gemini/wa, el
orquestador de process_invoice_google_2 ya se autoarranca con un único worker en
su propio __init__ (mismo comportamiento que tiene hoy dentro de server.py, que
nunca lo incluye en el loop de 5 workers extra del startup_event).

Correr con: uvicorn server_bas:app --host 0.0.0.0 --port 8000
"""

from app_factory import create_app
from routes.process_invoice_google_2 import router as process_invoice_google_router_2
from routes.batch_import import router as batch_import_router

app = create_app(
    title="Invoicy — BAS API",
    description=(
        "Flujo Gemini + integración BAS ERP (proveedores, facturas de compra, "
        "Drive) + importación masiva. Routers montados: process_invoice_google_2 "
        "(prefix /gemini2), batch_import (prefix /admin/batch-import)."
    ),
    routers=[process_invoice_google_router_2, batch_import_router],
)
