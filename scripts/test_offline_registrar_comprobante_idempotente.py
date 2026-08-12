"""
Test OFFLINE (sin red, sin BAS real) para P0-F: `registrar_comprobante_compra_idempotente`
(utils/bas.py), extraído de los pasos 1-2 de `crear_orden_de_pago_desde_factura`
para poder reusarse también en el registro real sin Orden de Pago.

BasClient.__init__ no hace ninguna llamada de red (la auth es perezosa, ver
BasClient.get_token) -- se puede instanciar un cliente real sin credenciales
y parchear a mano los métodos públicos que este método usa
(consultar_comprobante_externo / crear_comprobante_compra / consultar_comprobante),
en vez de un doble de prueba aparte -- más simple y prueba el método real.

Cubre:
  1. Ya existe -> idempotente, NO llama a crear_comprobante_compra.
  2. No existe + registrar_si_no_existe=False -> BasApiError 404.
  3. No existe + falta comprobante_compra_payload -> ValueError.
  4. No existe + dry_run=True -> registra en dry_run, NUNCA verifica (no
     tiene sentido: no hay nada real que consultar).
  5. No existe + dry_run=False, POST 201 sin Prefijo/Numero -> BasApiError 500.
  6. No existe + dry_run=False, POST OK pero la verificación (GET) no lo
     encuentra -> BasApiError 409.
  7. No existe + dry_run=False, POST OK + verificación OK -> éxito,
     `id_transaccion` viene del POST (no de la verificación, que no lo trae).
  8. Regresión de comportamiento: `crear_orden_de_pago_desde_factura` sigue
     funcionando igual después de la extracción (test de integración liviano,
     un escenario representativo con todos los métodos de escritura
     parcheados).

Uso: python3 scripts/test_offline_registrar_comprobante_idempotente.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.bas import BasApiError, BasClient  # noqa: E402

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


def cliente_con_parches(*, encontrado=None, respuesta_post=None, verificacion=None, explota_post=None):
    """BasClient real (sin red -- __init__ no autentica) con los 3 métodos
    públicos que usa registrar_comprobante_compra_idempotente reemplazados
    por dobles controlados. Cuenta las llamadas para las aserciones de
    "nunca se llama a X"."""
    cliente = BasClient(base_url="http://test.invalido", user="x", password="x")
    llamadas = {"consultar_externo": 0, "crear_comprobante": 0, "consultar_interno": 0}

    def _consultar_comprobante_externo(*a, **kw):
        llamadas["consultar_externo"] += 1
        return encontrado

    def _crear_comprobante_compra(payload, *, dry_run=False, ignora_advertencias=False):
        llamadas["crear_comprobante"] += 1
        if explota_post is not None:
            raise explota_post
        if dry_run:
            return {"dry_run": True, "endpoint": "/api/ComprobantesCompra", "payload": payload}
        return respuesta_post

    def _consultar_comprobante(*a, **kw):
        llamadas["consultar_interno"] += 1
        return verificacion

    cliente.consultar_comprobante_externo = _consultar_comprobante_externo
    cliente.crear_comprobante_compra = _crear_comprobante_compra
    cliente.consultar_comprobante = _consultar_comprobante
    cliente._llamadas = llamadas
    return cliente


PAYLOAD_BASE = {"Comprobante": "MA", "Total": 1}


print("=" * 78)
print("1 -- Ya existe -> idempotente")
print("=" * 78)

existente = {"Prefijo": "00010", "Numero": 555, "Total": 1, "Anulado": False}
cliente = cliente_con_parches(encontrado=existente)
r = cliente.registrar_comprobante_compra_idempotente(
    empresa=1, sucursal=1, comprobante="MA",
    prefijo_externo="00001", numero_externo=123,
    comprobante_compra_payload=PAYLOAD_BASE, dry_run=False,
)
check("Ya existe -> ya_existia=True", r["ya_existia"] is True)
check("Ya existe -> devuelve el comprobante encontrado tal cual", r["comprobante"] == existente)
check("Ya existe -> id_transaccion=None (no hubo POST)", r["id_transaccion"] is None)
check("Ya existe -> NUNCA se llama a crear_comprobante_compra (no hay POST duplicado)", cliente._llamadas["crear_comprobante"] == 0)
check("Ya existe -> NUNCA se llama a consultar_comprobante (nada que verificar)", cliente._llamadas["consultar_interno"] == 0)


print()
print("=" * 78)
print("2 -- No existe + registrar_si_no_existe=False -> 404, sin POST")
print("=" * 78)

cliente2 = cliente_con_parches(encontrado=None)
try:
    cliente2.registrar_comprobante_compra_idempotente(
        empresa=1, sucursal=1, comprobante="MA",
        prefijo_externo="00001", numero_externo=123,
        comprobante_compra_payload=PAYLOAD_BASE,
        registrar_si_no_existe=False, dry_run=False,
    )
    check("registrar_si_no_existe=False -> debería haber lanzado BasApiError", False)
except BasApiError as e:
    check("registrar_si_no_existe=False -> BasApiError 404", e.status_code == 404)
    check("registrar_si_no_existe=False -> NUNCA llama a crear_comprobante_compra", cliente2._llamadas["crear_comprobante"] == 0)


print()
print("=" * 78)
print("3 -- No existe + falta comprobante_compra_payload -> ValueError")
print("=" * 78)

cliente3 = cliente_con_parches(encontrado=None)
try:
    cliente3.registrar_comprobante_compra_idempotente(
        empresa=1, sucursal=1, comprobante="MA",
        prefijo_externo="00001", numero_externo=123,
        comprobante_compra_payload=None, dry_run=False,
    )
    check("Sin payload -> debería haber lanzado ValueError", False)
except ValueError:
    check("Sin payload -> ValueError (no explota como BasApiError ni sigue de largo)", True)


print()
print("=" * 78)
print("4 -- No existe + dry_run=True -> registra en dry_run, nunca verifica")
print("=" * 78)

cliente4 = cliente_con_parches(encontrado=None)
r4 = cliente4.registrar_comprobante_compra_idempotente(
    empresa=1, sucursal=1, comprobante="MA",
    prefijo_externo="00001", numero_externo=123,
    comprobante_compra_payload=PAYLOAD_BASE, dry_run=True,
)
check("dry_run=True -> ya_existia=False", r4["ya_existia"] is False)
check("dry_run=True -> id_transaccion=None (no hay POST real)", r4["id_transaccion"] is None)
check("dry_run=True -> comprobante es el eco del payload", r4["comprobante"].get("dry_run") is True)
check("dry_run=True -> SÍ llamó a crear_comprobante_compra (para armar el eco)", cliente4._llamadas["crear_comprobante"] == 1)
check("dry_run=True -> NUNCA verifica con GET (no hay nada real que consultar)", cliente4._llamadas["consultar_interno"] == 0)


print()
print("=" * 78)
print("5 -- No existe + POST 201 sin Prefijo/Numero -> BasApiError 500")
print("=" * 78)

cliente5 = cliente_con_parches(encontrado=None, respuesta_post={"IdTransaccion": 999})  # sin "Comprobantes"
try:
    cliente5.registrar_comprobante_compra_idempotente(
        empresa=1, sucursal=1, comprobante="MA",
        prefijo_externo="00001", numero_externo=123,
        comprobante_compra_payload=PAYLOAD_BASE, dry_run=False,
    )
    check("POST sin Prefijo/Numero -> debería haber lanzado BasApiError", False)
except BasApiError as e:
    check("POST sin Prefijo/Numero -> BasApiError 500", e.status_code == 500)
    check("POST sin Prefijo/Numero -> NUNCA intenta verificar (no hay con qué)", cliente5._llamadas["consultar_interno"] == 0)


print()
print("=" * 78)
print("6 -- No existe + POST OK pero la verificación no lo encuentra -> 409")
print("=" * 78)

respuesta_post_ok = {"IdTransaccion": 12345, "Comprobantes": [{"Prefijo": "00010", "Numero": 777}]}
cliente6 = cliente_con_parches(encontrado=None, respuesta_post=respuesta_post_ok, verificacion=None)
try:
    cliente6.registrar_comprobante_compra_idempotente(
        empresa=1, sucursal=1, comprobante="MA",
        prefijo_externo="00001", numero_externo=123,
        comprobante_compra_payload=PAYLOAD_BASE, dry_run=False,
    )
    check("Verificación no encuentra el comprobante -> debería haber lanzado BasApiError", False)
except BasApiError as e:
    check("Verificación no encuentra el comprobante -> BasApiError 409", e.status_code == 409)
    check("El 409 SÍ intentó verificar antes de fallar", cliente6._llamadas["consultar_interno"] == 1)


print()
print("=" * 78)
print("7 -- No existe + POST OK + verificación OK -> éxito completo")
print("=" * 78)

verificacion_ok = {"Prefijo": "00010", "Numero": 777, "Total": 1, "Anulado": False}
cliente7 = cliente_con_parches(encontrado=None, respuesta_post=respuesta_post_ok, verificacion=verificacion_ok)
r7 = cliente7.registrar_comprobante_compra_idempotente(
    empresa=1, sucursal=1, comprobante="MA",
    prefijo_externo="00001", numero_externo=123,
    comprobante_compra_payload=PAYLOAD_BASE, dry_run=False,
)
check("Registro real exitoso -> ya_existia=False", r7["ya_existia"] is False)
check("Registro real exitoso -> comprobante es el VERIFICADO (GET), no el eco del POST", r7["comprobante"] == verificacion_ok)
check("Registro real exitoso -> id_transaccion viene del POST (la verificación no lo trae)", r7["id_transaccion"] == 12345)
check("Registro real exitoso -> se llamó exactamente 1 vez a crear_comprobante_compra (sin duplicar)", cliente7._llamadas["crear_comprobante"] == 1)
check("Registro real exitoso -> se llamó exactamente 1 vez a consultar_comprobante (verificación)", cliente7._llamadas["consultar_interno"] == 1)


print()
print("=" * 78)
print("8 -- Regresión: crear_orden_de_pago_desde_factura sigue igual tras la extracción")
print("=" * 78)

# Escenario representativo: dry_run=True, comprobante no existe -- ejercita
# la rama que ANTES construía prefijo_int/numero_int desde
# _primer_comprobante(factura_resultado) (el eco del payload en dry_run).
cliente8 = cliente_con_parches(encontrado=None)
llamadas_op = {"crear_op": 0, "aplicar": 0}


def _crear_orden_de_pago(payload, *, dry_run=False):
    llamadas_op["crear_op"] += 1
    return {"dry_run": True, "endpoint": "/api/OrdenesPago", "payload": payload}


def _aplicar_comprobantes(**kw):
    llamadas_op["aplicar"] += 1
    return {"dry_run": True, "endpoint": "/api/AplicacionesComprobantes", "payload": kw}


cliente8.crear_orden_de_pago = _crear_orden_de_pago
cliente8.aplicar_comprobantes = _aplicar_comprobantes

resultado8 = cliente8.crear_orden_de_pago_desde_factura(
    empresa=1, sucursal=1, comprobante_factura="MA",
    prefijo_externo="00001", numero_externo=123, importe=1,
    prefijo_op="00001", caja_op="1",
    comprobante_compra_payload=PAYLOAD_BASE,
    imputacion_contable=211001,
    dry_run=True,
)
check("crear_orden_de_pago_desde_factura sigue devolviendo 'factura'/'orden_pago'/'aplicacion'", set(resultado8.keys()) == {"factura", "orden_pago", "aplicacion"})
check("crear_orden_de_pago_desde_factura -- 'factura' es el eco del payload (dry_run, no existía)", resultado8["factura"].get("dry_run") is True)
check("crear_orden_de_pago_desde_factura -- SÍ llegó a intentar crear la OP (no se cortó por el refactor)", llamadas_op["crear_op"] == 1)
check("crear_orden_de_pago_desde_factura -- SÍ llegó a aplicar (flujo completo, sin cortes)", llamadas_op["aplicar"] == 1)


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
