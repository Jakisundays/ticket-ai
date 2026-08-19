"""
Test OFFLINE (sin red, sin BAS, sin PocketBase) dedicado a
utils/validaciones_pre_bas.py:validar_total -- el gate primario que exige
`invoice.total` obligatorio y > 0 (agregado 2026-08-19, junto con el
cambio de arquitectura que ancla Total/TotalGravado/TotalIva de BAS a este
campo -- ver utils/bas_payload.py).

Por qué existe un archivo dedicado: hasta ahora `validar_total` solo se
ejercitaba indirectamente --
  - scripts/test_offline_registrar_comprobante_endpoint.py y
    scripts/test_offline_crear_orden_pago_codigo_item.py stubbean
    `validar_factura_antes_de_pago_real` por completo (no llaman a la
    función real), así que nunca ejercitan validar_total.
  - scripts/test_p0f_validacion_real.py SÍ usa la función real, pero
    requiere infraestructura local (PocketBase + BAS reales) para poder
    correr.
Este archivo importa `utils.validaciones_pre_bas` directo (módulo puro,
sin efectos secundarios de import) y prueba `validar_total` de forma
aislada -- así queda al menos un test que corre siempre, sin infra, y que
de verdad ejercita el gate primario.

También confirma que `validar_total` sigue conectado dentro de
`validar_factura_antes_de_pago_real` (regresión estructural: si alguien
lo saca de la tupla de validaciones, este test lo pesca).

Uso: python3 scripts/test_offline_validar_total.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.validaciones_pre_bas import validar_factura_antes_de_pago_real, validar_total  # noqa: E402 -- real

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


print("=" * 78)
print("validar_total -- casos inválidos (deben devolver un mensaje, no None)")
print("=" * 78)

for total_invalido, etiqueta in [
    (None, "None (ausente)"),
    (0, "cero"),
    (-1, "negativo"),
    (-1000.50, "negativo con decimales"),
    ("no-es-un-numero", "string no numérico"),
    ([], "tipo inesperado (lista)"),
]:
    mensaje = validar_total(total_invalido)
    check(f"total={etiqueta} -> devuelve mensaje de error (no None)", mensaje is not None)

print()
print("=" * 78)
print("validar_total -- casos válidos (deben devolver None)")
print("=" * 78)

for total_valido, etiqueta in [
    (1, "1 (mínimo entero positivo)"),
    (0.01, "0.01 (mínimo positivo con decimales)"),
    (1028500.00, "caso real OESTEREICHER HUGO"),
    ("4850.00", "string numérico válido (PocketBase puede devolver string)"),
]:
    mensaje = validar_total(total_valido)
    check(f"total={etiqueta} -> None (pasa)", mensaje is None)


print()
print("=" * 78)
print("validar_factura_antes_de_pago_real -- validar_total sigue conectado en la tupla")
print("=" * 78)

INVOICE_VALIDA = {
    "emisor_cuit": "20111111111",
    "moneda": "ARS",
    "fecha_emision": "2026-08-01",
    "numero_comprobante": "00010-00001854",
    "cae": "12345678901234",
    "cae_vencimiento": "2026-09-01",
    "total": 1000.00,
    "iva_alicuota": 21,
}

errores_ok = validar_factura_antes_de_pago_real(dict(INVOICE_VALIDA))
check("Factura completa y válida (incluye total=1000.00) -> sin errores", errores_ok == [])

invoice_sin_total = dict(INVOICE_VALIDA)
invoice_sin_total["total"] = None
errores_sin_total = validar_factura_antes_de_pago_real(invoice_sin_total)
check(
    "Factura sin total -> validar_factura_antes_de_pago_real SÍ reporta el error (validar_total sigue conectado)",
    any("total" in e.lower() for e in errores_sin_total),
)

invoice_total_cero = dict(INVOICE_VALIDA)
invoice_total_cero["total"] = 0
errores_total_cero = validar_factura_antes_de_pago_real(invoice_total_cero)
check(
    "Factura con total=0 -> también bloquea (> 0 estricto, no >= 0)",
    any("total" in e.lower() for e in errores_total_cero),
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
