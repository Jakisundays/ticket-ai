"""
Entrypoint dedicado al flujo Gemini + integración BAS ERP.

Monta únicamente `process_invoice_google_router_2` (prefix /gemini2). Sin caller
interno confirmado hoy (ver auditoría) — candidato a futuro reemplazo de
server_wa.py una vez que BAS destrabe el bloqueo de Órdenes de Pago documentado
en docs/bas-orden-de-pago-research.md.

No se pasa `extra_workers`: a diferencia de los flujos Claude y Gemini/wa, el
orquestador de process_invoice_google_2 ya se autoarranca con un único worker en
su propio __init__ (mismo comportamiento que tiene hoy dentro de server.py, que
nunca lo incluye en el loop de 5 workers extra del startup_event).

Correr con: uvicorn server_bas:app --host 0.0.0.0 --port 8000
"""

from app_factory import create_app
from routes.process_invoice_google_2 import router as process_invoice_google_router_2

app = create_app(
    title="Invoicy — BAS API",
    description=(
        "Flujo Gemini + integración BAS ERP (proveedores, facturas de compra, "
        "Drive). Único router montado: process_invoice_google_2 (prefix /gemini2)."
    ),
    routers=[process_invoice_google_router_2],
)
