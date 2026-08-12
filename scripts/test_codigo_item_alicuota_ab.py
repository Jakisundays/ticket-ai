"""
Test real aislado A/B: confirma si un CodigoItem catalogado a una tasa
distinta de la TasaIva/ImporteIva real declarada es lo que BAS rechaza con
409 "no son consistentes(SP_GENEROASI)(SP_ICR_COMPROB_COMPRA)".

Reutiliza BasClient (utils/bas.py), mismo patrón que
scripts/test_crear_comprobante_compra.py. Total=1 real (Test A: 0.99,
Test B: 0.99 -- regla de seguridad ya establecida, ver
docs/bas-orden-de-pago-research.md), cada uno con un NumeroComprobanteExterno
distinto (no hay anulación posible para MA, ver docs/incidente-2026-08-04-
pagos-solo-neto.md seccion 12).

Test A: CodigoItem="Gs Gs 21%" (catalogado a 21%) + TasaIva=10.5 real
        -- reproduce el mismatch real de la factura MEDINA d28axals9ayatdj.
Test B: CodigoItem="Gs Gs 10, 5" (catalogado a 10.5%) + TasaIva=10.5 real
        -- mismo payload, unica variable que cambia es el CodigoItem.

Uso (desde la raiz de Invoicy, con el venv):
    venv/bin/python scripts/test_codigo_item_alicuota_ab.py --dry-run
    venv/bin/python scripts/test_codigo_item_alicuota_ab.py
"""

import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from utils.bas import BasApiError, BasClient  # noqa: E402
from utils.bas_config import (  # noqa: E402
    BAS_CAJA,
    BAS_CENTRO_APROPIACION_SD,
    BAS_DEPOSITO,
    BAS_EMITIDO_POR_CAE,
    BAS_EMPRESA,
    BAS_METODO_PAGO_CTA_CTE,
    BAS_PREFIJO_TALONARIO_MA,
    BAS_SUCURSAL,
    BAS_TIPO_ENTREGA_SIN_STOCK,
    fecha_hoy_bas,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("test_codigo_item_alicuota_ab")

DEFAULT_PROVEEDOR_CODIGO = "SUPERCOO"

IMPORTE_GRAVADO = 0.90
TASA_IVA_REAL = 10.5
IMPORTE_IVA = round(IMPORTE_GRAVADO * TASA_IVA_REAL / 100, 2)
IMPORTE_TOTAL = round(IMPORTE_GRAVADO + IMPORTE_IVA, 2)


def _pretty(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def _numero_externo_de_prueba(offset: int) -> tuple[str, int]:
    """Mismo esquema que test_crear_comprobante_compra.py (dia-del-anio +
    hora+minuto, 7 digitos, entra holgado en el limite de 8 de BAS) + offset
    para que Test A y Test B no choquen entre si en la misma corrida."""
    ahora = datetime.datetime.now(datetime.timezone.utc)
    prefijo = "0001"
    numero = int(ahora.strftime("%j%H%M")) * 10 + offset
    return prefijo, numero


def _verificar_catalogo(cliente: BasClient, codigo_item: str) -> None:
    log.info("Verificando catalogo real de BAS para CodigoItem=%r", codigo_item)
    resp = cliente._request("GET", f"/api/Servicios/{codigo_item}")
    if resp.status_code != 200:
        log.warning(
            "GET /api/Servicios/%s devolvio %s (no se pudo verificar el catalogo, "
            "se sigue igual)", codigo_item, resp.status_code
        )
        return
    servicio = resp.json()
    impuesto_codigo = servicio.get("Impuesto") if isinstance(servicio, dict) else None
    if not impuesto_codigo:
        log.warning("Servicio %r sin campo Impuesto -- no se pudo verificar la tasa", codigo_item)
        return
    resp2 = cliente._request("GET", f"/api/Impuestos/{BAS_EMPRESA}/{impuesto_codigo}")
    if resp2.status_code == 200:
        tasa = resp2.json().get("TasaIvaCompras")
        log.info("Catalogo confirma: %r -> Impuesto %s -> TasaIvaCompras=%s", codigo_item, impuesto_codigo, tasa)


def _armar_payload(codigo_item: str, prefijo_externo: str, numero_externo: int, proveedor_codigo: str, fecha_hoy: str) -> dict:
    return {
        "Comprobante": "MA",
        "Prefijo": BAS_PREFIJO_TALONARIO_MA,
        "Fecha": fecha_hoy,
        "Total": IMPORTE_TOTAL,
        "TotalGravado": IMPORTE_GRAVADO,
        "TotalIva": IMPORTE_IVA,
        "MonedaComprobante": "L",
        "EmitidoPor": BAS_EMITIDO_POR_CAE,
        "Empresa": BAS_EMPRESA,
        "Sucursal": BAS_SUCURSAL,
        "Deposito": BAS_DEPOSITO,
        "Caja": BAS_CAJA,
        "MetodoPago": BAS_METODO_PAGO_CTA_CTE,
        "Proveedor": proveedor_codigo,
        "PrefijoComprobanteExterno": prefijo_externo,
        "NumeroComprobanteExterno": numero_externo,
        "FechaComprobanteExterno": fecha_hoy,
        "NumeroCAIoCAE": "12345678901234",
        "VencimientoCAIoCAE": fecha_hoy,
        "Vencimientos": [{"FechaVencimiento": fecha_hoy, "Importe": IMPORTE_TOTAL}],
        "Items": [
            {
                "CodigoItem": codigo_item,
                "TipoEntrega": BAS_TIPO_ENTREGA_SIN_STOCK,
                "NumeroUnidadMedida": "1",
                "CantidadPrimeraUnidad": 1,
                "PrecioUnitario": IMPORTE_GRAVADO,
                "ImporteGravado": IMPORTE_GRAVADO,
                "ImporteIva": IMPORTE_IVA,
                "ImporteTotal": IMPORTE_TOTAL,
                "TasaIva": TASA_IVA_REAL,
                "CentroApropiacionA": BAS_CENTRO_APROPIACION_SD,
                "CentroApropiacionB": BAS_CENTRO_APROPIACION_SD,
            }
        ],
    }


def _correr_test(nombre: str, cliente: BasClient, codigo_item: str, offset: int, proveedor_codigo: str, dry_run: bool) -> None:
    log.info("=" * 78)
    log.info("%s -- CodigoItem=%r, TasaIva=%s, Total=%s", nombre, codigo_item, TASA_IVA_REAL, IMPORTE_TOTAL)
    log.info("=" * 78)
    prefijo_externo, numero_externo = _numero_externo_de_prueba(offset)
    fecha_hoy = fecha_hoy_bas().isoformat()
    payload = _armar_payload(codigo_item, prefijo_externo, numero_externo, proveedor_codigo, fecha_hoy)
    log.info("Payload:\n%s", _pretty(payload))
    try:
        resultado = cliente.crear_comprobante_compra(payload, dry_run=dry_run)
        log.info("%s -- RESULTADO OK:\n%s", nombre, _pretty(resultado))
    except BasApiError as e:
        log.error("%s -- RESULTADO ERROR %s en %s:\n%s", nombre, e.status_code, e.path, _pretty(e.detail))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proveedor-codigo", default=DEFAULT_PROVEEDOR_CODIGO)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    cliente = BasClient()
    cliente.get_token()

    _verificar_catalogo(cliente, "Gs Gs 21%")
    _verificar_catalogo(cliente, "Gs Gs 10, 5")

    _correr_test("TEST A (mismatch, como MEDINA)", cliente, "Gs Gs 21%", 1, args.proveedor_codigo, args.dry_run)
    _correr_test("TEST B (matched)", cliente, "Gs Gs 10, 5", 2, args.proveedor_codigo, args.dry_run)


if __name__ == "__main__":
    main()
