"""
Test OFFLINE (sin red, sin BAS, sin PocketBase) para la fórmula de
Total/TotalGravado/TotalIva/ImporteTotal del payload de BAS.

Reescrito 2026-08-19 para la arquitectura nueva (ver utils/bas_payload.py):
antes de este cambio, el cálculo vivía DUPLICADO e inline en 2 call sites de
routes/process_invoice_google_2.py (InvoiceOrchestrator.procesar_factura_en_bas
y crear_orden_pago), sumando ImporteGravado/ImporteIva de cada línea de
items_bas -- por eso este test original leía el código fuente como texto
(regex) y reimplementaba el cálculo a mano. Ahora existe un ÚNICO lugar
real (utils/bas_payload.py:construir_comprobante_totales_e_items), sin
efectos secundarios de import (a diferencia de
routes/process_invoice_google_2.py, que instancia InvoiceOrchestrator a
nivel de módulo y dispara asyncio.create_task + llamadas reales a
PocketBase/BAS solo con importarlo) -- así que este test ahora importa y
ejecuta la función REAL directamente, en vez de reimplementarla.

Dos partes:

1. REGRESIÓN ESTRUCTURAL: confirma, sobre el texto fuente real, que
   `ImporteTotal` de la línea sigue siendo `= <gravado>` (nunca
   gravado+iva -- la regla de línea real de SP_VALIDA_TOTALES, ver Parte 2),
   y que los 3 call sites reales de BAS (procesar_factura_en_bas,
   crear_orden_pago, registrar_comprobante) delegan TODOS a la misma
   función compartida -- ninguno reimplementa su propia copia del cálculo.

2. VALIDACIÓN CONTRA LA REGLA REAL DE BAS: corre la función real contra dos
   casos reales de producción y valida el payload resultante contra un
   validador que replica PLATINUM_TEST.dbo.SP_VALIDA_TOTALES (leído fresco
   de PLATINUM_TEST.sys.sql_modules, texto citado en cada regla):
     - OESTEREICHER HUGO (comprobante externo 00010-00001854, subtotal
       850000.00, total 1028500.00, iva_alicuota=21) -- el caso real que
       originalmente disparó el 409 de ImporteTotal mal calculado.
     - Telefónica Móviles (Movistar Movil 2GB $17431.80 + "Bonificacion
       Movistar" -$12598.74, invoice.total=4850.00) -- el caso real que
       disparó todo el rediseño de arquitectura (2026-08-18): confirma que
       el ítem NEGATIVO no altera el Total registrado, que sigue siendo
       exactamente invoice.total pase lo que pase con los ítems.

Uso: python3 scripts/test_offline_importe_total_linea.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import re
import sys
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import utils.bas_config as bas_config  # noqa: E402
from utils.bas_payload import construir_comprobante_totales_e_items  # noqa: E402 -- real

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE_PAYLOAD = REPO_ROOT / "utils" / "bas_payload.py"
TARGET_FILE_ROUTES = REPO_ROOT / "routes" / "process_invoice_google_2.py"

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


# Cache determinístico de bas_config -- mismo criterio que los demás tests
# offline de BAS: resolver_codigo_item real no debe intentar red.
bas_config._cache_categoria_map["datos"] = {
    "Gastos Generales": {21: "Gs Gs 21%"},
}
bas_config._cache_categoria_map["actualizado_en"] = time.time()


class FakePbClient:
    def __init__(self, bas_items_validos):
        self._bas_items = bas_items_validos

    def get_bas_item(self, codigo):
        return self._bas_items.get(codigo)


PB_CLIENT = FakePbClient({"Gs Gs 21%": {"activo": True, "elegible_compras": True}})


def r2(x):
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ============================================================================
# Parte 1 -- Regresión estructural sobre el código fuente real
# ============================================================================
print("=" * 78)
print("Parte 1 -- ImporteTotal = ImporteGravado, en un ÚNICO lugar real")
print("=" * 78)

codigo_payload = TARGET_FILE_PAYLOAD.read_text(encoding="utf-8")
codigo_routes = TARGET_FILE_ROUTES.read_text(encoding="utf-8")

check(
    "utils/bas_payload.py: 'ImporteTotal' se asigna desde total_gravado (regla de línea real de SP_VALIDA_TOTALES)",
    bool(re.search(r'"ImporteTotal":\s*total_gravado\s*,', codigo_payload)),
)
check(
    "utils/bas_payload.py: la fórmula vieja (gravado+iva) NO está presente para ImporteTotal",
    not re.search(r'"ImporteTotal":\s*(round\(\s*)?total_gravado\s*\+\s*total_iva', codigo_payload),
)

apariciones_call_sites = len(re.findall(r"construir_comprobante_totales_e_items\(", codigo_routes))
check(
    f"Los 3 call sites reales (procesar_factura_en_bas, crear_orden_pago, registrar_comprobante) "
    f"delegan a la función compartida (encontrados: {apariciones_call_sites})",
    apariciones_call_sites == 3,
)

# Verificación negativa: ningún call site debería tener su propia copia de
# un loop sumando ImporteGravado/ImporteIva de items_bas -- si aparece,
# alguien reintrodujo la duplicación de lógica que este módulo eliminó.
check(
    "routes/process_invoice_google_2.py NO reimplementa una suma de líneas para el Total de cabecera",
    "sum(i[\"ImporteGravado\"]" not in codigo_routes and "sum(item[\"ImporteGravado\"]" not in codigo_routes,
)


# ============================================================================
# Parte 2 -- Validación contra la regla real de BAS (SP_VALIDA_TOTALES)
# ============================================================================
def linea_pasa_regla(importe_total, importe_gravado, item_prefi="S", imponible=0, iva_flag="I"):
    """Regla de línea real de SP_VALIDA_TOTALES (leída de
    PLATINUM_TEST.sys.sql_modules): con TRANSAC.IVA='I',
    ABS(ImporteTotal - ImporteGravado) debe ser <= 1. Solo aplica cuando
    ItemPrefi<>'T' y Imponible=0."""
    if item_prefi == "T" or imponible != 0:
        return True  # fuera del alcance de la regla
    if iva_flag != "I":
        return True  # otras ramas del SP no aplican a este caso (IVA='I' confirmado real)
    diff = abs(Decimal(str(importe_total)) - Decimal(str(importe_gravado)))
    return not (importe_total != importe_gravado and diff > 1)


def validar_payload(payload):
    """Replica las 2 reglas de cabecera reales de SP_VALIDA_TOTALES
    (líneas 115-119 y 121-125, TotalGravado/TotalIva contra SUM(líneas)) +
    Total == TotalGravado + TotalIva (regla del header real)."""
    items = payload["Items"]
    suma_gravado = r2(sum(Decimal(str(i["ImporteGravado"])) for i in items))
    suma_iva = r2(sum(Decimal(str(i["ImporteIva"])) for i in items))
    return {
        "linea_pasa": all(linea_pasa_regla(i["ImporteTotal"], i["ImporteGravado"]) for i in items),
        "total_gravado_ok": r2(payload["TotalGravado"]) == suma_gravado,
        "total_iva_ok": r2(payload["TotalIva"]) == suma_iva,
        "total_ok": r2(payload["Total"]) == r2(Decimal(str(payload["TotalGravado"])) + Decimal(str(payload["TotalIva"]))),
    }


print()
print("=" * 78)
print("Parte 2a -- Caso real OESTEREICHER HUGO (total=1028500.00, alicuota=21)")
print("=" * 78)

payload_hugo = construir_comprobante_totales_e_items(
    total_bruto=1028500.00,
    alicuota_iva=21,
    items=[{"categoria": "Gastos Generales", "precio_total": 850000}],
    pb_client=PB_CLIENT,
)
print(f"\n  Total={payload_hugo['Total']}  TotalGravado={payload_hugo['TotalGravado']}  TotalIva={payload_hugo['TotalIva']}")
print(f"  Línea: ImporteGravado={payload_hugo['Items'][0]['ImporteGravado']}  ImporteTotal={payload_hugo['Items'][0]['ImporteTotal']}")

check("Total == 1028500.00 (invoice.total, sin cambios)", payload_hugo["Total"] == 1028500.00)
check("TotalGravado == 850000.00 (mismo valor histórico real)", payload_hugo["TotalGravado"] == 850000.00)
check("TotalIva == 178500.00 (mismo valor histórico real)", payload_hugo["TotalIva"] == 178500.00)

validacion_hugo = validar_payload(payload_hugo)
check("SP_VALIDA_TOTALES -- la línea pasa la regla (ImporteTotal == ImporteGravado)", validacion_hugo["linea_pasa"])
check("SP_VALIDA_TOTALES -- TotalGravado(cabecera) == SUM(líneas.ImporteGravado)", validacion_hugo["total_gravado_ok"])
check("SP_VALIDA_TOTALES -- TotalIva(cabecera) == SUM(líneas.ImporteIva)", validacion_hugo["total_iva_ok"])
check("SP_VALIDA_TOTALES -- Total == TotalGravado + TotalIva", validacion_hugo["total_ok"])


print()
print("=" * 78)
print("Parte 2b -- Caso real Telefónica Móviles (ítem NEGATIVO, total=4850.00)")
print("=" * 78)

# Movistar Movil 2GB (+17431.80) + Bonificacion Movistar (-12598.74) --
# el caso real que bloqueaba la factura antes del rediseño de arquitectura
# (2026-08-18): con la suma vieja de ítems, un precio_total negativo podía
# alterar/bloquear el Total. Acá invoice.total (4850.00) NO tiene ninguna
# relación aritmética con la suma de los ítems (4833.06) -- a propósito,
# para probar que el Total registrado es 100% independiente de los ítems.
payload_telefonica = construir_comprobante_totales_e_items(
    total_bruto=4850.00,
    alicuota_iva=21,
    items=[
        {"categoria": "Gastos Generales", "precio_total": 17431.80},
        {"categoria": "Gastos Generales", "precio_total": -12598.74},
    ],
    pb_client=PB_CLIENT,
)
print(f"\n  Suma de ítems (NUNCA usada para el Total): {17431.80 + -12598.74}")
print(f"  Total={payload_telefonica['Total']}  TotalGravado={payload_telefonica['TotalGravado']}  TotalIva={payload_telefonica['TotalIva']}")
print(f"  Líneas en el payload: {len(payload_telefonica['Items'])} (una sola, nunca 2)")

check("Total == 4850.00 (invoice.total) -- NO 4833.06 (lo que daría sumar los ítems)", payload_telefonica["Total"] == 4850.00)
check("El payload tiene UNA sola línea (no una por ítem, no reparto proporcional)", len(payload_telefonica["Items"]) == 1)

validacion_telefonica = validar_payload(payload_telefonica)
check("SP_VALIDA_TOTALES -- la línea pasa la regla (ImporteTotal == ImporteGravado)", validacion_telefonica["linea_pasa"])
check("SP_VALIDA_TOTALES -- TotalGravado(cabecera) == SUM(líneas.ImporteGravado)", validacion_telefonica["total_gravado_ok"])
check("SP_VALIDA_TOTALES -- TotalIva(cabecera) == SUM(líneas.ImporteIva)", validacion_telefonica["total_iva_ok"])
check("SP_VALIDA_TOTALES -- Total == TotalGravado + TotalIva", validacion_telefonica["total_ok"])


print()
print("=" * 78)
if FALLOS:
    print(f"RESULTADO: {len(FALLOS)} chequeo(s) fallaron:")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("RESULTADO: todos los chequeos pasaron.")
    sys.exit(0)
