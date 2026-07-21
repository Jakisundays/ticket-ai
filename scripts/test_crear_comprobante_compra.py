"""
Prueba end-to-end: POST /api/ComprobantesCompra en BAS + verificación con
GET /api/ConsultaComprobantesExternos.

Reutiliza BasClient (utils/bas.py) -- mismo cliente que ya usa
process_invoice_google_2.py, no reinventa nada. SIEMPRE Total=1 (regla de
seguridad ya establecida para pruebas reales contra BAS, ver
docs/bas-orden-de-pago-research.md) y usa un ítem con posición contable de
compras (COM) ya verificado con un 201 real ("Gs Gs 21%").

Uso (desde la raíz de Invoicy, con el venv):
    venv/bin/python scripts/test_crear_comprobante_compra.py --dry-run
        # solo arma y loguea el payload, NO escribe nada en BAS

    venv/bin/python scripts/test_crear_comprobante_compra.py
        # POST real (Total=1) + verificación con GET

    venv/bin/python scripts/test_crear_comprobante_compra.py --proveedor-codigo THYMBRA

Requiere BAS_BASE_URL / BAS_USER / BAS_PASSWORD en Invoicy/.env (las mismas
que ya usa BasClient para todo lo demás).
"""

import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

# Permite correr el script tal cual ("python scripts/archivo.py") sin
# necesidad de invocarlo como módulo -- agrega la raíz de Invoicy a sys.path.
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
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("test_crear_comprobante_compra")

# SUPERCOOP, confirmado real (dado de alta con aprobación explícita del
# usuario -- ver memoria del proyecto). Cambiar con --proveedor-codigo si se
# prefiere probar con otro (ej. "THYMBRA", también usado en pruebas previas).
DEFAULT_PROVEEDOR_CODIGO = "SUPERCOO"

# Único CodigoItem confirmado con un 201 real contra BAS -- tiene posición
# contable de compras (concepto COM) configurada. No usar otro sin verificar
# antes contra el catálogo real (ver utils/bas_config.py).
ITEM_CODIGO_VERIFICADO = "Gs Gs 21%"


def _pretty(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def _numero_externo_de_prueba() -> tuple[str, int]:
    """
    Número de comprobante externo único por corrida (basado en la hora
    actual), para no chocar con pruebas anteriores si el script se
    re-ejecuta más de una vez.

    BAS valida NumeroComprobanteExterno con dos reglas distintas, ambas
    confirmadas en runtime contra el 400 real que devuelve la API:
      1. Debe convertir a Int32 (un timestamp de 12 dígitos rompe el parseo
         JSON con "could not be converted to Nullable<Int32>").
      2. Además, el propio validador de BAS exige "between 0 and 99999999"
         (8 dígitos) -- más chico que el rango de Int32, así que hay que
         respetar el límite de BAS, no el de Int32.
    Día-del-año (3 dígitos) + hora+minuto (4 dígitos) entra holgado en 7.
    """
    ahora = datetime.datetime.now(datetime.timezone.utc)
    prefijo = "0001"
    numero = int(ahora.strftime("%j%H%M"))
    return prefijo, numero


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--proveedor-codigo",
        default=DEFAULT_PROVEEDOR_CODIGO,
        help=f"Código de proveedor ya existente en BAS (default: {DEFAULT_PROVEEDOR_CODIGO})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo arma y loguea el payload -- NO hace el POST real ni la verificación.",
    )
    args = parser.parse_args()

    load_dotenv()
    cliente = BasClient()

    log.info("=" * 78)
    log.info("PASO 0 — Autenticación")
    log.info("=" * 78)
    log.info("base_url = %s", cliente.base_url)
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
        log.error(
            "❌ El proveedor '%s' no existe en BAS (GET /api/Proveedores/%s devolvió 204). "
            "Pasá un código válido con --proveedor-codigo.",
            args.proveedor_codigo,
            args.proveedor_codigo,
        )
        sys.exit(1)
    log.info(
        "✅ Proveedor encontrado: Codigo=%s RazonSocial=%s",
        proveedor.get("Codigo"),
        proveedor.get("RazonSocial"),
    )

    prefijo_externo, numero_externo = _numero_externo_de_prueba()
    fecha_hoy = datetime.date.today().isoformat()

    log.info("=" * 78)
    log.info("PASO 2 — Armar payload de ComprobanteCompra (Total=1)")
    log.info("=" * 78)
    payload = {
        "Comprobante": "MA",
        "Prefijo": BAS_PREFIJO_TALONARIO_MA,
        "Fecha": fecha_hoy,
        "Total": 1,
        "TotalGravado": 1,
        "MonedaComprobante": "L",  # probando explícito -- 409 previo: "no se pudo establecer la moneda correspondiente a la cuenta 0"
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
                "ImporteTotal": 1,
                "TasaIva": 21,
                "CentroApropiacionA": BAS_CENTRO_APROPIACION_SD,
                "CentroApropiacionB": BAS_CENTRO_APROPIACION_SD,
            }
        ],
    }
    log.info("Payload a enviar:\n%s", _pretty(payload))

    log.info("=" * 78)
    log.info("PASO 3 — POST /api/ComprobantesCompra (dry_run=%s)", args.dry_run)
    log.info("=" * 78)
    try:
        resultado = cliente.crear_comprobante_compra(payload, dry_run=args.dry_run)
    except BasApiError as e:
        log.error("❌ BasApiError %s en %s:\n%s", e.status_code, e.path, _pretty(e.detail))
        sys.exit(1)
    log.info("Respuesta:\n%s", _pretty(resultado))

    if args.dry_run:
        log.info("=" * 78)
        log.info("dry_run=True -> no se escribió nada real en BAS. Nada que verificar.")
        log.info("Corré sin --dry-run para hacer el POST real (Total=1) y verificarlo.")
        log.info("=" * 78)
        return

    log.info("=" * 78)
    log.info("PASO 4 — Verificar con GET /api/ConsultaComprobantesExternos")
    log.info("=" * 78)
    log.info(
        "Buscando Empresa=%s Sucursal=%s Comprobante=MA PrefijoExterno=%s NumeroExterno=%s Fecha=%s",
        BAS_EMPRESA,
        BAS_SUCURSAL,
        prefijo_externo,
        numero_externo,
        fecha_hoy,
    )
    try:
        encontrado = cliente.consultar_comprobante_externo(
            BAS_EMPRESA,
            BAS_SUCURSAL,
            "MA",
            prefijo_externo=prefijo_externo,
            numero_externo=numero_externo,
            fecha_externo=fecha_hoy,
        )
    except BasApiError as e:
        log.error("❌ BasApiError %s en %s:\n%s", e.status_code, e.path, _pretty(e.detail))
        sys.exit(1)

    if encontrado is None:
        log.error(
            "❌ NO se encontró el comprobante (204, sin coincidencia). "
            "El POST del paso 3 dijo que se creó, pero la consulta no lo confirma."
        )
        sys.exit(1)

    log.info("✅ Comprobante VERIFICADO en BAS:\n%s", _pretty(encontrado))
    log.info(
        "Numeración interna: Prefijo=%s Numero=%s Anulado=%s",
        encontrado.get("Prefijo"),
        encontrado.get("Numero"),
        encontrado.get("Anulado"),
    )
    log.info("=" * 78)
    log.info("🎉 Listo -- factura creada Y verificada de forma independiente.")
    log.info("=" * 78)


if __name__ == "__main__":
    main()
