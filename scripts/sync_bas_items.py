"""
Sincroniza el catálogo real de Servicios/Bienes de BAS a PocketBase
(colección "bas_items"). Ver utils/bas_items_sync.py para la lógica.

Corre en modo solo-lectura contra BAS (nada de POST/PUT/DELETE) -- lee
/api/Servicios, /api/Bienes, /api/Impuestos y /api/PosicionesContables, y
escribe únicamente en PocketBase.

Uso (desde la raíz de Invoicy, con el venv):
    venv/bin/python scripts/sync_bas_items.py
        # sincronización real

    venv/bin/python scripts/sync_bas_items.py --dry-run
        # trae el catálogo real de BAS y lo procesa, pero NO escribe en
        # PocketBase -- imprime lo que habría hecho

Requiere BAS_BASE_URL/BAS_USER/BAS_PASSWORD y POCKETBASE_URL/
POCKETBASE_SERVICE_EMAIL/POCKETBASE_SERVICE_PASSWORD en Invoicy/.env (las
mismas variables que ya usa el resto de la app).

Pensado para correrse manual al principio (antes de habilitar el nuevo
flujo de registro) y luego vía cron diario (P1-B del plan técnico) -- ver
docs/invoicy-bas-nuevo-alcance-plan-tecnico-FINAL.md.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from utils.bas import BasClient  # noqa: E402
from utils.bas_config import BAS_EMPRESA  # noqa: E402
from utils.bas_items_sync import sincronizar_bas_items  # noqa: E402
from utils.pocketbase_client import PocketBaseClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("sync_bas_items")


class _PbClientDryRun:
    """Envoltorio de solo-lectura sobre PocketBaseClient para --dry-run --
    delega get_bas_item/list_bas_items (lectura real, útil para ver qué
    cambiaría) pero intercepta upsert_bas_item y solo lo loguea."""

    def __init__(self, real: PocketBaseClient):
        self._real = real
        self.escrituras = []

    def get_bas_item(self, codigo):
        return self._real.get_bas_item(codigo)

    def list_bas_items(self, solo_elegibles=True):
        return self._real.list_bas_items(solo_elegibles=solo_elegibles)

    def upsert_bas_item(self, codigo, **campos):
        self.escrituras.append({"codigo": codigo, **campos})
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Trae el catálogo real de BAS pero no escribe en PocketBase; imprime lo que habría hecho.",
    )
    args = parser.parse_args()

    load_dotenv()

    bas_client = BasClient()
    log.info("BAS: base_url=%s", bas_client.base_url)
    log.info("BAS: autenticando...")
    bas_client.get_token()
    log.info("BAS: auth OK")

    pb_real = PocketBaseClient()
    pb_client = _PbClientDryRun(pb_real) if args.dry_run else pb_real

    log.info("Sincronizando catálogo (dry_run=%s)...", args.dry_run)
    resumen = sincronizar_bas_items(bas_client, pb_client, empresa=BAS_EMPRESA)

    print(json.dumps(resumen, indent=2, ensure_ascii=False))

    if args.dry_run:
        print(f"\n--dry-run: {len(pb_client.escrituras)} upserts que se HABRÍAN hecho (primeros 10):")
        for w in pb_client.escrituras[:10]:
            print(f"  {w['codigo']}: elegible_compras={w.get('elegible_compras')} tasa_iva_compras={w.get('tasa_iva_compras')}")


if __name__ == "__main__":
    main()
