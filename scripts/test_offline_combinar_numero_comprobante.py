"""
Test OFFLINE (sin red, sin Gemini, sin BAS) de la normalización real del
"número de comprobante" -- utils/validaciones_pre_bas.py:
combinar_numero_comprobante y normalizar_numero_comprobante.

Bug real que motiva este archivo (2026-08-14, factura LEON LUGO BLANCA
ELENA): el schema de extracción (tools_standard.py) ya le pide a Gemini
"numero" (Comp. Nro) y "punto_de_venta" como campos SEPARADOS -- el header
AFIP estándar de un comprobante electrónico argentino los imprime así,
"Punto de Venta: 00001" / "Comp. Nro: 00000066", en la enorme mayoría de
los documentos, no algo específico de un layout puntual. Pero nada en el
pipeline los combinaba: solo "numero" llegaba a `numero_comprobante`, así
que la factura quedaba guardada como "00000066" a secas y las tres copias
de la validación (Invoicy, dashboard, hook de PocketBase) la rechazaban
por no tener el separador "-" que BAS necesita (PrefijoComprobanteExterno
/ NumeroComprobanteExterno). `combinar_numero_comprobante` cierra ese
hueco combinando los dos valores que Gemini YA extrae, sin inventar
ningún dato -- ver su docstring para las reglas exactas.

De paso, este archivo también prueba `normalizar_numero_comprobante`
(el fix ya escrito pero nunca shippeado para cuando Gemini incluye la
letra de tipo AFIP como tercer segmento, ej. "A-0064-00671710" -- visto
real en MetroGAS, 2026-08-07) porque `combinar_numero_comprobante` lo usa
como primer paso y ambos se shippean juntos (misma familia de problema:
normalizar numero_comprobante antes de que llegue a las capas de
validación).

Cubre, en orden:
  A. Caso real que motiva el fix (LEON LUGO): numero + punto_de_venta
     separados -> combinados.
  B. Ya viene combinado (Gemini lo unió él mismo en esta corrida) -> NO se
     vuelve a anteponer punto_de_venta (sin doble prefijo).
  C. Letra AFIP + punto de venta juntos ("A-00001-00000066") -> se
     normaliza correctamente a "00001-00000066".
  D. Sin punto_de_venta -> no se inventa nada, se devuelve tal cual (mismo
     comportamiento de hoy).
  E. Ceros a la izquierda: se preservan en ambos lados de la combinación.
  F. Idempotencia: aplicar la función dos veces con el mismo
     punto_de_venta da exactamente el mismo resultado que aplicarla una
     vez, para cada uno de los casos de arriba.
  G. Valores reales ya guardados en producción (los 6 que existen hoy) --
     ninguno debe cambiar: prueba de no-regresión explícita.
  H. normalizar_numero_comprobante en aislado (el fix de la letra AFIP,
     shippeado junto a este).

Uso: python3 scripts/test_offline_combinar_numero_comprobante.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.validaciones_pre_bas import (  # noqa: E402
    combinar_numero_comprobante,
    normalizar_numero_comprobante,
)

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


print("=" * 78)
print("A -- caso real LEON LUGO: numero + punto_de_venta separados -> combinados")
print("=" * 78)

resultado_a = combinar_numero_comprobante("00000066", "00001")
check(
    "'00000066' + P.V. '00001' -> '00001-00000066'",
    resultado_a == "00001-00000066",
)


print()
print("=" * 78)
print("B -- ya viene combinado -> NO se vuelve a anteponer punto_de_venta")
print("=" * 78)

resultado_b = combinar_numero_comprobante("00001-00000066", "00001")
check(
    "'00001-00000066' + P.V. '00001' -> queda igual (sin doble prefijo)",
    resultado_b == "00001-00000066",
)

# Caso más revelador todavía: punto_de_venta DISTINTO al prefijo real ya
# presente -- si la función combinara sin mirar la forma actual, esto
# rompería un valor que ya estaba bien. Prueba directa de la regla 2.
resultado_b2 = combinar_numero_comprobante("00001-00000066", "99999")
check(
    "'00001-00000066' + P.V. '99999' (distinto) -> sigue igual, no se pisa el prefijo real",
    resultado_b2 == "00001-00000066",
)


print()
print("=" * 78)
print("C -- letra AFIP + punto de venta juntos -> normaliza a 'PV-numero'")
print("=" * 78)

resultado_c = combinar_numero_comprobante("A-00001-00000066", "00001")
check(
    "'A-00001-00000066' -> '00001-00000066' (letra AFIP descartada)",
    resultado_c == "00001-00000066",
)

# Mismo caso, pero sin punto_de_venta -- el segundo segmento YA es el
# punto de venta real (así vino impreso el número compuesto), no debería
# hacer falta punto_de_venta para este caso.
resultado_c2 = combinar_numero_comprobante("A-0064-00671710", None)
check(
    "'A-0064-00671710' sin P.V. -> '0064-00671710' (caso real MetroGAS, 2026-08-07)",
    resultado_c2 == "0064-00671710",
)


print()
print("=" * 78)
print("D -- sin punto_de_venta -> no se inventa nada, se devuelve tal cual")
print("=" * 78)

resultado_d1 = combinar_numero_comprobante("00000066", None)
check("'00000066' sin P.V. (None) -> se devuelve tal cual", resultado_d1 == "00000066")

resultado_d2 = combinar_numero_comprobante("00000066", "")
check("'00000066' con P.V. vacío ('') -> se devuelve tal cual", resultado_d2 == "00000066")

resultado_d3 = combinar_numero_comprobante("00000066", "   ")
check(
    "'00000066' con P.V. solo espacios -> se devuelve tal cual (strip lo deja vacío)",
    resultado_d3 == "00000066",
)


print()
print("=" * 78)
print("E -- ceros a la izquierda: se preservan en ambos lados")
print("=" * 78)

resultado_e = combinar_numero_comprobante("00000001", "00099")
check(
    "'00000001' + P.V. '00099' -> '00099-00000001' (ceros intactos, nunca se castea a int)",
    resultado_e == "00099-00000001",
)


print()
print("=" * 78)
print("F -- idempotencia: aplicar dos veces == aplicar una vez")
print("=" * 78)

CASOS_IDEMPOTENCIA = [
    ("00000066", "00001"),
    ("00001-00000066", "00001"),
    ("00001-00000066", "99999"),
    ("A-00001-00000066", "00001"),
    ("A-0064-00671710", None),
    ("00000066", None),
    ("00000066", ""),
    ("00000001", "00099"),
]
for numero, pv in CASOS_IDEMPOTENCIA:
    primera = combinar_numero_comprobante(numero, pv)
    segunda = combinar_numero_comprobante(primera, pv)
    check(
        f"combinar('{numero}', {pv!r}) aplicado 2 veces da el mismo resultado ('{primera}')",
        primera == segunda,
    )


print()
print("=" * 78)
print("G -- valores reales YA guardados en producción -- no-regresión")
print("=" * 78)

# Los 6 valores reales de invoices.numero_comprobante en producción al
# momento de este fix (2026-08-14). 5 de 6 no tienen relación con ninguno
# de los dos bugs que arregla este cambio -- tienen que salir IDÉNTICOS.
# MetroGAS es la excepción a propósito: quedó guardado con la letra AFIP
# todavía pegada ("A-0064-00671710") porque el fix de
# normalizar_numero_comprobante nunca se había shippeado hasta este mismo
# commit -- para ESE valor puntual, el cambio de comportamiento es
# exactamente lo que se pidió arreglar, no una regresión. No se toca el
# dato ya guardado en PocketBase (fuera de alcance de este fix, que es
# sobre el pipeline de ingesta hacia adelante) -- esto solo prueba qué
# haría la función si se le pasara ese valor.
VALORES_REALES_PRODUCCION = [
    ("Litoral Gas S.A.", "0081-50240726", "0081-50240726"),
    ("AUTOSERVICIO MAYORISTA DIARCO S.A.", "2033-00094753", "2033-00094753"),
    ("MetroGAS S.A.", "A-0064-00671710", "0064-00671710"),  # cambia a propósito
    ("FRIO INTERLOGISTICA SUR S.A.", "A00005-00261570", "A00005-00261570"),
    ("PLATINUM HOMES S.A.", "00001-00035739", "00001-00035739"),
    ("LEON LUGO BLANCA ELENA", "00001-00000066", "00001-00000066"),
]
for emisor, valor_guardado, esperado in VALORES_REALES_PRODUCCION:
    # Sin punto_de_venta a mano (no se persiste en PocketBase, ver
    # docstring de combinar_numero_comprobante) -- exactamente lo que
    # pasaría si se re-normalizara un valor ya guardado sin volver a
    # llamar a Gemini.
    resultado = combinar_numero_comprobante(valor_guardado, None)
    etiqueta = "sin cambios" if esperado == valor_guardado else "CAMBIA A PROPÓSITO (letra AFIP)"
    check(
        f"{emisor}: '{valor_guardado}' -> '{resultado}' ({etiqueta})",
        resultado == esperado,
    )


print()
print("=" * 78)
print("H -- normalizar_numero_comprobante en aislado (fix de la letra AFIP)")
print("=" * 78)

check(
    "'A-0064-00671710' -> '0064-00671710'",
    normalizar_numero_comprobante("A-0064-00671710") == "0064-00671710",
)
check(
    "'C-00001-00000066' -> '00001-00000066' (cualquier letra AFIP válida, no solo 'A')",
    normalizar_numero_comprobante("C-00001-00000066") == "00001-00000066",
)
check(
    "'00001-00000066' (ya bien formado, 2 partes) -> intacto",
    normalizar_numero_comprobante("00001-00000066") == "00001-00000066",
)
check(
    "'00000066' (1 parte, sin punto de venta) -> intacto, no es trabajo de esta función",
    normalizar_numero_comprobante("00000066") == "00000066",
)
check(
    "'Z-00001-00000066' (letra NO reconocida) -> intacto, no se inventa una regla nueva",
    normalizar_numero_comprobante("Z-00001-00000066") == "Z-00001-00000066",
)
check("None -> None", normalizar_numero_comprobante(None) is None)
check("'' -> ''", normalizar_numero_comprobante("") == "")


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
