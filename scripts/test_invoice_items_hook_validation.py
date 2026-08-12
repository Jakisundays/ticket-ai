"""
Validación REAL (no mockeada) del hook `invoice_items.pb.js`
(ticket-ai-infra/pocketbase/pb_hooks/invoice_items.pb.js) -- P0-G,
2026-08-12.

Este repo no tiene convención de tests para hooks de PocketBase (son código
JS que corre en el runtime Goja del propio servidor, no algo importable
desde Python) -- ticket-ai-infra tampoco tiene una. La verificación
establecida en este proyecto para ese tipo de código es empírica: contra
una PocketBase REAL corriendo (local, Docker), con aserciones sobre la
respuesta HTTP real. Esto deja esa verificación como un script reusable en
vez de perderse en el historial de una terminal.

Qué prueba: el hook ya NO recalcula `bas_codigo_item` a partir de
`categoria` (comportamiento viejo, inseguro -- ver P0-E/P0-G). En cambio,
valida cualquier `bas_codigo_item` no vacío contra `bas_items`
(activo=true + elegible_compras=true) antes de aceptar el update.

Prerequisitos:
  - PocketBase local corriendo (docker compose up -d pocketbase en
    ticket-ai-infra) con el hook de P0-G ya bakeado en la imagen
    (docker compose build pocketbase && docker compose up -d pocketbase).
  - Un service account en la colección "service_accounts" (ver
    ticket-ai-infra/pocketbase/README.md).
  - Al menos un ítem real en "bas_items" con activo=true+elegible_compras=true
    (ver scripts/sync_bas_items.py) -- se autodetecta el primero disponible.

Uso:
    POCKETBASE_URL=http://localhost:8090 \\
    POCKETBASE_SERVICE_EMAIL=<...> \\
    POCKETBASE_SERVICE_PASSWORD=<...> \\
    venv/bin/python scripts/test_invoice_items_hook_validation.py

Sale con código 0 si todo pasa, 1 si algo falla. Limpia sus propios datos
de prueba al final (éxito o fallo).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.pocketbase_client import PocketBaseClient  # noqa: E402

FALLOS = []
PROCESS_ID_TEST = "hook-validation-p0g"


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


if not os.getenv("POCKETBASE_URL"):
    print("Falta POCKETBASE_URL/POCKETBASE_SERVICE_EMAIL/POCKETBASE_SERVICE_PASSWORD en el entorno.")
    sys.exit(1)

pb = PocketBaseClient()

print("=" * 78)
print("PASO 0 -- Prerequisitos")
print("=" * 78)

items_validos = pb.list_bas_items(solo_elegibles=True)
check("Hay al menos 1 ítem activo+elegible en bas_items", len(items_validos) > 0)
if not items_validos:
    print("Corré primero scripts/sync_bas_items.py -- no hay nada contra qué validar.")
    sys.exit(1)
codigo_valido = items_validos[0]["codigo"]
print(f"  Usando '{codigo_valido}' como código válido de prueba.")

invoice = None
item_id = None
try:
    print()
    print("=" * 78)
    print("PASO 1 -- Crear factura + ítem de prueba")
    print("=" * 78)
    invoice = pb.upsert_invoice(
        {
            "process_id": PROCESS_ID_TEST,
            "status": "completed",
            "review_status": "needs_review",
            "emisor_cuit": "20111111111",
            "total": 1,
        }
    )
    check("Factura de prueba creada", bool(invoice and invoice.get("id")))
    ok = pb.bulk_create_invoice_items(
        invoice["id"],
        [
            {
                "process_id": PROCESS_ID_TEST,
                "linea": 1,
                "descripcion": "validación hook P0-G",
                "cantidad": 1,
                "precio_unitario": 1,
                "precio_total": 1,
                "categoria": "Gastos Generales",
            }
        ],
    )
    check("Ítem de prueba creado", ok)
    items = pb.get_invoice_items(invoice["id"])
    item_id = items[0]["id"]

    print()
    print("=" * 78)
    print("PASO 2 -- Código VÁLIDO (existe en bas_items, activo+elegible) -> aceptado")
    print("=" * 78)
    r1 = pb._request(
        "PATCH",
        f"/api/collections/invoice_items/records/{item_id}",
        json_body={"bas_codigo_item": codigo_valido},
    )
    check(f"PATCH con '{codigo_valido}' -> 200", r1.status_code == 200)

    print()
    print("=" * 78)
    print("PASO 3 -- Código INVENTADO (no existe en bas_items) -> rechazado")
    print("=" * 78)
    r2 = pb._request(
        "PATCH",
        f"/api/collections/invoice_items/records/{item_id}",
        json_body={"bas_codigo_item": "CODIGO-QUE-NO-EXISTE-EN-BAS-XYZ"},
    )
    check("PATCH con código inventado -> 400 (rechazado)", r2.status_code == 400)
    check(
        "El mensaje de error menciona el código rechazado",
        "CODIGO-QUE-NO-EXISTE-EN-BAS-XYZ" in (r2.text or ""),
    )

    item_tras_rechazo = pb._request(
        "GET", f"/api/collections/invoice_items/records/{item_id}"
    ).json()
    check(
        "El valor rechazado NUNCA se persistió -- sigue el último válido",
        item_tras_rechazo.get("bas_codigo_item") == codigo_valido,
    )

    print()
    print("=" * 78)
    print("PASO 4 -- Vaciar el código (estado 'todavía sin elegir') -> permitido")
    print("=" * 78)
    r3 = pb._request(
        "PATCH",
        f"/api/collections/invoice_items/records/{item_id}",
        json_body={"bas_codigo_item": ""},
    )
    check("PATCH con bas_codigo_item='' -> 200 (vacío es válido)", r3.status_code == 200)

finally:
    print()
    print("=" * 78)
    print("Limpieza")
    print("=" * 78)
    if invoice and invoice.get("id"):
        r = pb._request("DELETE", f"/api/collections/invoices/records/{invoice['id']}")
        print(f"  Factura de prueba borrada (invoice_items cascadea): status {r.status_code}")

print()
print("=" * 78)
if FALLOS:
    print(f"RESULTADO: {len(FALLOS)} chequeo(s) fallaron:")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("RESULTADO: todos los chequeos pasaron -- hook de P0-G validado contra PocketBase real.")
    sys.exit(0)
