"""
Prueba end-to-end del flujo completo: ComprobanteCompra + OrdenPago aplicada
contra él, vía `BasClient.crear_orden_de_pago_desde_factura` (la misma
función que usa `crear_orden_pago` en producción, ver
routes/process_invoice_google_2.py).

Prueba puntual del fix en `ComprobantesAplicados` (ver utils/bas.py,
`crear_orden_de_pago_desde_factura`): ese bloque va con el Prefijo/Numero
del comprobante EXTERNO (el del proveedor), no con la numeración interna
que asigna BAS -- confirmado por soporte de BAS tras el 409 "no se pudo
establecer la moneda correspondiente a la cuenta 0".

SIEMPRE Total=1 (regla de seguridad ya establecida para pruebas reales
contra BAS). Reusa el proveedor y el ítem ya verificados en
scripts/test_crear_comprobante_compra.py.

Uso (desde la raíz de Invoicy, con el venv):
    venv/bin/python scripts/test_crear_orden_pago_desde_factura.py --dry-run
        # arma los dos payloads (ComprobanteCompra + OrdenPago) y los loguea,
        # NO escribe nada en BAS

    venv/bin/python scripts/test_crear_orden_pago_desde_factura.py
        # POST real (Total=1) de ambos pasos + verificación con GET

Requiere BAS_BASE_URL / BAS_USER / BAS_PASSWORD en Invoicy/.env.
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
    BAS_IMPUTACION_CONTABLE_PROVEEDORES,
    BAS_METODO_PAGO_CTA_CTE,
    BAS_PREFIJO_TALONARIO_MA,
    BAS_PREFIJO_TALONARIO_OP,
    BAS_SUCURSAL,
    BAS_TIPO_ENTREGA_SIN_STOCK,
    fecha_hoy_bas,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("test_crear_orden_pago_desde_factura")

DEFAULT_PROVEEDOR_CODIGO = "SUPERCOO"
ITEM_CODIGO_VERIFICADO = "Gs Gs 21%"


def _pretty(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def _numero_externo_de_prueba() -> tuple[str, int]:
    """Mismo esquema que test_crear_comprobante_compra.py -- ver ese archivo
    para el detalle de por qué día-del-año+hora+minuto (7 dígitos, entra en
    el límite de 8 dígitos que exige BAS para NumeroComprobanteExterno)."""
    ahora = datetime.datetime.now(datetime.timezone.utc)
    prefijo = "0001"
    numero = int(ahora.strftime("%j%H%M"))
    return prefijo, numero


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proveedor-codigo", default=DEFAULT_PROVEEDOR_CODIGO)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo arma y loguea los payloads -- NO hace ningún POST real.",
    )
    args = parser.parse_args()

    load_dotenv()
    # 90s y no el default de 30: un POST a ComprobantesCompra ya se pasó de 30
    # una vez, y el timeout del cliente deja la duda de si BAS lo procesó igual
    # (ese caso quedó confirmado: se había creado). Mejor esperar.
    cliente = BasClient(timeout=90)

    log.info("=" * 78)
    log.info("PASO 0 — Autenticación")
    log.info("=" * 78)
    try:
        token = cliente.get_token()
    except BasApiError as e:
        log.error("❌ Falló la autenticación: %s %s en %s", e.status_code, e.detail, e.path)
        sys.exit(1)
    log.info("✅ Token obtenido (len=%d)", len(token))

    log.info("=" * 78)
    log.info("PASO 1 — Resolver proveedor '%s'", args.proveedor_codigo)
    log.info("=" * 78)
    proveedor = cliente.obtener_proveedor(args.proveedor_codigo)
    if proveedor is None:
        log.error("❌ El proveedor '%s' no existe en BAS.", args.proveedor_codigo)
        sys.exit(1)
    codigo_ctacte = cliente.codigo_cuenta_corriente_proveedor(proveedor)
    log.info(
        "✅ Proveedor encontrado: Codigo=%s RazonSocial=%s (CodigoCuentaCorriente=%s)",
        proveedor.get("Codigo"),
        proveedor.get("RazonSocial"),
        codigo_ctacte,
    )

    prefijo_externo, numero_externo = _numero_externo_de_prueba()
    fecha_hoy = fecha_hoy_bas().isoformat()  # huso argentino, ver utils/bas_config.py

    log.info("=" * 78)
    log.info("PASO 2 — Armar payload de ComprobanteCompra (Total=1)")
    log.info("=" * 78)
    comprobante_compra_payload = {
        "Comprobante": "MA",
        "Prefijo": BAS_PREFIJO_TALONARIO_MA,
        "Fecha": fecha_hoy,
        # Sin IVA a propósito -- ver comentario equivalente en
        # scripts/test_crear_comprobante_compra.py.
        "Total": 1,
        "TotalGravado": 1,
        "TotalIva": 0,
        "MonedaComprobante": "L",
        "EmitidoPor": BAS_EMITIDO_POR_CAE,
        "Empresa": BAS_EMPRESA,
        "Sucursal": BAS_SUCURSAL,
        "Deposito": BAS_DEPOSITO,
        "Caja": BAS_CAJA,
        "MetodoPago": BAS_METODO_PAGO_CTA_CTE,
        "Proveedor": proveedor.get("Codigo"),
        "PrefijoComprobanteExterno": prefijo_externo,
        "NumeroComprobanteExterno": numero_externo,
        "FechaComprobanteExterno": fecha_hoy,
        "NumeroCAIoCAE": "12345678901234",
        "VencimientoCAIoCAE": fecha_hoy,
        "Vencimientos": [{"FechaVencimiento": fecha_hoy, "Importe": 1}],
        "Items": [
            {
                "CodigoItem": ITEM_CODIGO_VERIFICADO,
                "TipoEntrega": BAS_TIPO_ENTREGA_SIN_STOCK,
                "NumeroUnidadMedida": "1",
                "CantidadPrimeraUnidad": 1,
                "PrecioUnitario": 1,
                "ImporteGravado": 1,
                "ImporteIva": 0,
                "ImporteTotal": 1,
                "TasaIva": 0,
                "CentroApropiacionA": BAS_CENTRO_APROPIACION_SD,
                "CentroApropiacionB": BAS_CENTRO_APROPIACION_SD,
            }
        ],
    }
    log.info("Payload ComprobanteCompra:\n%s", _pretty(comprobante_compra_payload))

    log.info("=" * 78)
    log.info("PASO 3 — crear_orden_de_pago_desde_factura (dry_run=%s)", args.dry_run)
    log.info("=" * 78)
    log.info(
        "ComprobantesAplicados va a usar Prefijo=%s Numero=%s (comprobante EXTERNO -- "
        "ver fix en utils/bas.py)",
        prefijo_externo,
        numero_externo,
    )
    try:
        flujo = cliente.crear_orden_de_pago_desde_factura(
            empresa=BAS_EMPRESA,
            sucursal=BAS_SUCURSAL,
            comprobante_factura="MA",
            prefijo_externo=prefijo_externo,
            numero_externo=numero_externo,
            importe=1,
            fecha=fecha_hoy,
            fecha_externo=fecha_hoy,
            prefijo_op=BAS_PREFIJO_TALONARIO_OP,
            caja_op=BAS_CAJA,
            prefijo_ctacte="P",
            codigo_ctacte=codigo_ctacte,
            pagos={"Efectivos": [{"MedioPago": "1", "Importe": 1, "IngresooEgreso": "E"}]},
            comprobante_compra_payload=comprobante_compra_payload,
            imputacion_contable=BAS_IMPUTACION_CONTABLE_PROVEEDORES,
            registrar_si_no_existe=True,
            dry_run=args.dry_run,
        )
    except BasApiError as e:
        log.error("❌ BasApiError %s en %s:\n%s", e.status_code, e.path, _pretty(e.detail))
        sys.exit(1)

    log.info("Resultado:\n%s", _pretty(flujo))

    orden_pago = flujo.get("orden_pago") if isinstance(flujo, dict) else None
    if isinstance(orden_pago, dict) and orden_pago.get("_error"):
        log.error("❌ La factura se registró pero la Orden de Pago falló:\n%s", _pretty(orden_pago))
        sys.exit(1)

    if args.dry_run:
        log.info("=" * 78)
        log.info("dry_run=True -> no se escribió nada real en BAS. Payloads arriba para revisar.")
        log.info("Corré sin --dry-run para hacer los POST reales (Total=1).")
        log.info("=" * 78)
        return

    log.info("=" * 78)
    log.info("🎉 Listo -- ComprobanteCompra + OrdenPago creados (Total=1).")
    log.info("=" * 78)


if __name__ == "__main__":
    main()
