# P0-F — validación real contra BAS (2026-08-12)

Registro de los datos de prueba reales que quedaron en BAS al validar el
endpoint `POST /invoices/{process_id}/register-comprobante` (P0-F, registro
real de `ComprobanteCompra` sin Orden de Pago). Ver
[scripts/test_p0f_validacion_real.py](../scripts/test_p0f_validacion_real.py).

**No se borran ni se anulan** (instrucción explícita del usuario) -- quedan
documentados acá como evidencia de que la validación pasó.

## Comprobantes reales creados (Total=1, proveedor SUPERCOOP)

| # | IdTransaccion | Prefijo/Numero interno | NumeroComprobanteExterno | Total | Resultado |
|---|---|---|---|---|---|
| 1 | 274654 | 00001-21915 | 0090-2241619 | 1 | Registro exitoso (primer intento) |
| 2 | 274655 | 00001-21916 | 0090-2241629 | 1 | Registro exitoso (segundo intento, tras corregir `comprobante_total_registrado`) |

Ambos verificados con un GET independiente (`ConsultaComprobantesExternos`)
antes de darlos por buenos -- ver el script para el detalle completo.

## Intento fallido (deliberado, no dejó nada real en BAS)

`NumeroComprobanteExterno=999999999` (9 dígitos) -- rechazado por BAS con
400 real (*"must be between 0 and 99999999"*) antes de crear nada. Usado
para probar el camino de error del endpoint (`register_failed` +
`bas_last_error`).

## Hallazgo real de esta validación

`GET /api/ConsultaComprobantes` y `GET /api/ConsultaComprobantesExternos`
(mismo schema de respuesta, `RespuestaConsultaComprobante`,
`additionalProperties: false`) **no exponen el Total del comprobante en
ningún campo** -- confirmado contra el swagger real de BAS
(`/swagger/v1/swagger.json`) y empíricamente con los 2 comprobantes de
arriba. `comprobante_total_registrado` en `bas_processing_status` se
persiste desde el payload enviado (que BAS aceptó con el 201), no desde la
respuesta -- ver el fix en `routes/process_invoice_google_2.py`
(`registrar_comprobante`) y el test actualizado en
`scripts/test_offline_registrar_comprobante_endpoint.py`.

## Entorno usado

- BAS: instancia real de producción (no hay sandbox) -- mismo criterio de
  siempre, Total=1 en toda prueba real.
- PocketBase: instancia LOCAL de Docker (`ticket-ai-infra-pocketbase-1`,
  `localhost:8090`), no producción. Se creó un service account local
  (`invoicy-backend@invoicy.local`, colección `service_accounts`) para
  poder correr el backend Python contra ella -- el `.env` de Invoicy no
  tenía credenciales de PocketBase configuradas. El superuser temporal
  usado para crear ese service account ya se borró; el service account
  local queda disponible para futuras validaciones de este tipo.
- Las 3 invoices de prueba creadas en PocketBase local (`p0f-validacion-*`)
  se borraron tras la validación -- no forman parte del catálogo real.
