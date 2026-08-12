"""
Test OFFLINE (sin red, sin BAS, sin PocketBase) para el fix de P0-1: la
fórmula de ImporteTotal por línea en el payload de BAS.

Por qué es offline y no importa routes/process_invoice_google_2.py directo:
ese módulo instancia `orchestrator = InvoiceOrchestrator(...)` a nivel de
módulo (línea ~2042), cuyo __init__ llama `asyncio.create_task(self.worker())`
-- requiere un event loop corriendo solo para poder importarlo, y además
intenta hablar con PocketBase/BAS reales al arrancar. Importarlo en un test
unitario dispararía efectos secundarios reales (justo lo que este test debe
evitar). Ese acoplamiento es un problema de testabilidad preexistente del
módulo, fuera del alcance de este cambio puntual (P0-1).

Esta prueba en cambio hace dos cosas independientes y se queda con la
intersección de ambas como garantía:

1. REGRESIÓN DE FÓRMULA: lee el código FUENTE real (como texto) de los dos
   call sites que arman `items_bas` y confirma que la línea de
   `ImporteTotal` sigue siendo `= <gravado>` (el fix), no
   `round(<gravado> + <iva>, 2)` (el bug). Si alguien revierte el fix en
   cualquiera de los dos lugares, esta parte falla.

2. VALIDACIÓN CONTRA LA REGLA REAL DE BAS: reconstruye el payload que ESE
   código produce (antes y después del fix) para el caso real
   OESTEREICHER HUGO (comprobante externo 00010-00001854, invoices de
   PocketBase 'of333b3x0byexp1'/'ddrv9u73b0ku9r3', ambas con
   subtotal=850000.00, total=1028500.00, iva_alicuota=21 -- 2 fallos reales
   en producción, 2026-08-05) y lo corre contra un validador que replica
   PLATINUM_TEST.dbo.SP_VALIDA_TOTALES (leído fresco de
   PLATINUM_TEST.sys.sql_modules, texto citado en cada regla). Confirma que
   el payload ANTES es rechazado exactamente por la regla de línea que
   generó el 409 real, y que el payload DESPUÉS pasa todas las reglas
   evaluables offline, con Total/TotalGravado/TotalIva sin cambios.

Uso: python3 scripts/test_offline_importe_total_linea.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE = REPO_ROOT / "routes" / "process_invoice_google_2.py"

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


# ============================================================================
# Parte 1 -- Regresión de fórmula: el código fuente real sigue teniendo el fix
# ============================================================================
print("=" * 78)
print("Parte 1 -- El código fuente real usa ImporteTotal = ImporteGravado")
print("=" * 78)

codigo = TARGET_FILE.read_text(encoding="utf-8")

# Call site 1: InvoiceOrchestrator.procesar_factura_en_bas (dry_run,
# ingesta automática). Debe existir la línea `importe_total = importe_gravado`
# y NO debe existir la fórmula vieja `round(importe_gravado + importe_iva, 2)`
# asignada a importe_total.
check(
    "Call site 1 (procesar_factura_en_bas): 'importe_total = importe_gravado' presente",
    bool(re.search(r"^\s*importe_total\s*=\s*importe_gravado\s*$", codigo, re.M)),
)
check(
    "Call site 1: la fórmula vieja (gravado+iva) NO está presente para importe_total",
    not re.search(r"importe_total\s*=\s*round\(\s*importe_gravado\s*\+\s*importe_iva", codigo),
)

# Call site 2: crear_orden_pago (endpoint real, dry_run=False). Mismo criterio
# con el prefijo `_` de las variables locales de esa función.
check(
    "Call site 2 (crear_orden_pago): '_importe_total = _importe_gravado' presente",
    bool(re.search(r"^\s*_importe_total\s*=\s*_importe_gravado\s*$", codigo, re.M)),
)
check(
    "Call site 2: la fórmula vieja (gravado+iva) NO está presente para _importe_total",
    not re.search(r"_importe_total\s*=\s*round\(\s*_importe_gravado\s*\+\s*_importe_iva", codigo),
)

# Verificación negativa: confirmar que efectivamente hay solo estos 2 call
# sites que arman "ImporteTotal" para items_bas (si aparece un tercero en el
# futuro, este test no lo cubre y hay que extenderlo).
apariciones_importetotal_key = len(re.findall(r'"ImporteTotal":\s*(importe_total|_importe_total)', codigo))
check(
    f"Exactamente 2 call sites arman 'ImporteTotal' desde una variable calculada (encontrados: {apariciones_importetotal_key})",
    apariciones_importetotal_key == 2,
)


# ============================================================================
# Parte 2 -- Validación contra la regla real de BAS (SP_VALIDA_TOTALES)
# ============================================================================
print()
print("=" * 78)
print("Parte 2 -- Caso real OESTEREICHER HUGO vs. SP_VALIDA_TOTALES")
print("=" * 78)


def r2(x):
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def construir_items_bas(detalles, alicuota_iva, formula_importe_total):
    """Reproduce el bucle real de items_bas (mismo cálculo que los 2 call
    sites verificados en la Parte 1) -- `formula_importe_total` es el único
    parámetro que varía entre ANTES (bug) y DESPUÉS (fix)."""
    items = []
    for item in detalles:
        importe_gravado = r2(item["precio_total"])
        tasa_iva = alicuota_iva if alicuota_iva is not None else 0
        importe_iva = r2(importe_gravado * Decimal(tasa_iva) / 100)
        importe_total = formula_importe_total(importe_gravado, importe_iva)
        items.append(
            {
                "CodigoItem": item["codigo_item"],
                "ImporteGravado": importe_gravado,
                "ImporteIva": importe_iva,
                "ImporteTotal": importe_total,
                "TasaIva": Decimal(tasa_iva),
                "ItemPrefi": item.get("itemprefi", "S"),
                "Imponible": Decimal("0"),  # default real confirmado en MVSITEMS
            }
        )
    return items


def armar_payload(items, iva_flag="I"):
    total_gravado = sum(i["ImporteGravado"] for i in items)
    total_iva = sum(i["ImporteIva"] for i in items)
    total = r2(total_gravado + total_iva)
    return {
        "Items": items,
        "TotalGravado": r2(total_gravado),
        "TotalIva": r2(total_iva),
        "Total": total,
        "IvaFlag": iva_flag,  # TRANSAC.IVA -- 'I' confirmado en el 100% de las filas
    }


def linea_pasa_regla(it, iva_flag):
    """Regla de línea real de SP_VALIDA_TOTALES (leída de
    PLATINUM_TEST.sys.sql_modules): con TRANSAC.IVA='I',
    ABS(ImporteTotal - ImporteGravado) debe ser <= 1. Solo aplica cuando
    ItemPrefi<>'T' y Imponible=0 (confirmado con datos reales que este ítem
    cumple ambas condiciones)."""
    if it["ItemPrefi"] == "T" or it["Imponible"] != 0:
        return True  # fuera del alcance de la regla
    importe, gravado = it["ImporteTotal"], it["ImporteGravado"]
    if iva_flag == "I":
        return not (importe != gravado and abs(importe - gravado) > 1)
    return True  # otras ramas del SP no aplican a este caso (IVA='I' confirmado)


# Caso real: OESTEREICHER HUGO, 00010-00001854 (invoices 'of333b3x0byexp1' /
# 'ddrv9u73b0ku9r3'), subtotal=850000.00, total=1028500.00, iva_alicuota=21.
detalles = [
    {
        "precio_total": Decimal("850000"),
        "codigo_item": "Gs Gs 21%",
        "itemprefi": "S",  # confirmado real: ITEMS.ITEMPREFI de 'Gs Gs 21%'
    }
]
alicuota_iva = 21

items_antes = construir_items_bas(detalles, alicuota_iva, lambda gravado, iva: r2(gravado + iva))
payload_antes = armar_payload(items_antes)

items_despues = construir_items_bas(detalles, alicuota_iva, lambda gravado, iva: gravado)
payload_despues = armar_payload(items_despues)

print(f"\n  ANTES : ImporteGravado={items_antes[0]['ImporteGravado']}  ImporteIva={items_antes[0]['ImporteIva']}  ImporteTotal={items_antes[0]['ImporteTotal']}")
print(f"  DESPUÉS: ImporteGravado={items_despues[0]['ImporteGravado']}  ImporteIva={items_despues[0]['ImporteIva']}  ImporteTotal={items_despues[0]['ImporteTotal']}")
print()

check(
    "ANTES: la línea NO pasa la regla de SP_VALIDA_TOTALES (reproduce el 409 real)",
    not linea_pasa_regla(items_antes[0], payload_antes["IvaFlag"]),
)
check(
    "DESPUÉS: la línea SÍ pasa la regla de SP_VALIDA_TOTALES",
    linea_pasa_regla(items_despues[0], payload_despues["IvaFlag"]),
)
check(
    f"Total de cabecera sin cambios ({payload_antes['Total']} == {payload_despues['Total']})",
    payload_antes["Total"] == payload_despues["Total"] == Decimal("1028500.00"),
)
check(
    f"TotalGravado de cabecera sin cambios ({payload_antes['TotalGravado']} == {payload_despues['TotalGravado']})",
    payload_antes["TotalGravado"] == payload_despues["TotalGravado"] == Decimal("850000.00"),
)
check(
    f"TotalIva de cabecera sin cambios ({payload_antes['TotalIva']} == {payload_despues['TotalIva']})",
    payload_antes["TotalIva"] == payload_despues["TotalIva"] == Decimal("178500.00"),
)
check(
    "TotalGravado(cabecera) == SUM(líneas.ImporteGravado) en ambos escenarios (regla de SP_VALIDA_TOTALES l.115-119)",
    payload_antes["TotalGravado"] == sum(i["ImporteGravado"] for i in items_antes)
    and payload_despues["TotalGravado"] == sum(i["ImporteGravado"] for i in items_despues),
)
check(
    "TotalIva(cabecera) == SUM(líneas.ImporteIva) en ambos escenarios (regla de SP_VALIDA_TOTALES l.121-125)",
    payload_antes["TotalIva"] == sum(i["ImporteIva"] for i in items_antes)
    and payload_despues["TotalIva"] == sum(i["ImporteIva"] for i in items_despues),
)

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
