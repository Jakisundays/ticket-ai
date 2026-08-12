"""
Test real aislado: reproduce el patron exacto de redondeo de ImporteIva de
la factura real MEDINA d28axals9ayatdj (94745.70*10.5/100 = 9948.2985,
redondea HACIA ARRIBA a 9948.30) a escala de $1, para descartar/confirmar
que la direccion del redondeo (no el CodigoItem, ya descartado por
test_codigo_item_alicuota_ab.py: ambos casos dieron 201 real) es la causa
del 409 "no son consistentes(SP_GENEROASI)(SP_ICR_COMPROB_COMPRA)".

ImporteGravado=0.95, TasaIva=10.5 -> raw=0.09975 -> round()=0.10 (redondea
HACIA ARRIBA, mismo patron que MEDINA). El test anterior (Test A) usaba
ImporteGravado=0.90 -> raw=0.0945 -> round()=0.09 (redondea HACIA ABAJO) y
dio 201 real -- si este nuevo caso (redondeo hacia arriba) tambien da 201,
la direccion del redondeo tampoco es la causa.
"""

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("test_redondeo_iva")

PROVEEDOR_CODIGO = "SUPERCOO"
IMPORTE_GRAVADO = 0.95
TASA_IVA_REAL = 10.5
IMPORTE_IVA = round(IMPORTE_GRAVADO * TASA_IVA_REAL / 100, 2)
IMPORTE_TOTAL = round(IMPORTE_GRAVADO + IMPORTE_IVA, 2)
CODIGO_ITEM = "Gs Gs 21%"


def _pretty(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def main() -> None:
    load_dotenv()
    cliente = BasClient()
    cliente.get_token()

    ahora = datetime.datetime.now(datetime.timezone.utc)
    prefijo_externo = "0001"
    numero_externo = int(ahora.strftime("%j%H%M")) * 10 + 3
    fecha_hoy = fecha_hoy_bas().isoformat()

    log.info(
        "raw ImporteIva=%s -> redondeado=%s (direccion: %s)",
        IMPORTE_GRAVADO * TASA_IVA_REAL / 100,
        IMPORTE_IVA,
        "ARRIBA" if IMPORTE_IVA > IMPORTE_GRAVADO * TASA_IVA_REAL / 100 else "ABAJO/IGUAL",
    )

    payload = {
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
        "Proveedor": PROVEEDOR_CODIGO,
        "PrefijoComprobanteExterno": prefijo_externo,
        "NumeroComprobanteExterno": numero_externo,
        "FechaComprobanteExterno": fecha_hoy,
        "NumeroCAIoCAE": "12345678901234",
        "VencimientoCAIoCAE": fecha_hoy,
        "Vencimientos": [{"FechaVencimiento": fecha_hoy, "Importe": IMPORTE_TOTAL}],
        "Items": [
            {
                "CodigoItem": CODIGO_ITEM,
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
    log.info("Payload:\n%s", _pretty(payload))
    try:
        resultado = cliente.crear_comprobante_compra(payload, dry_run=False)
        log.info("RESULTADO OK:\n%s", _pretty(resultado))
    except BasApiError as e:
        log.error("RESULTADO ERROR %s en %s:\n%s", e.status_code, e.path, _pretty(e.detail))


if __name__ == "__main__":
    main()
