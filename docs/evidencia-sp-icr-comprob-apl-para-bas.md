# Evidencia: `SP_ICR_COMPROB_APL` rechaza la aplicación de un comprobante que BAS acaba de confirmar que existe

**Fecha:** 2026-07-21
**Instalación:** `PLATINUM_TEST` (Empresa 1 = PLATINUM HOMES S.A.)
**Comprobante afectado:** `MA 00001-00021885` (Litoral Gas S.A., proveedor `LITORALG`)

## Resumen

El flujo de creación de Orden de Pago registra correctamente la factura de
compra en BAS, y una consulta independiente confirma que el comprobante
existe (`Anulado: false`). Al intentar crear la Orden de Pago aplicando ese
mismo comprobante (mismo `Prefijo`/`Numero` ya confirmados), BAS responde que
el comprobante **no existe para aplicarlo**.

Se probaron múltiples variantes de payload (fecha del comprobante en el
detalle de aplicación, el endpoint alternativo `/api/AplicacionesComprobantes`
en vez de `/api/OrdenesPago`, un comprobante recién creado sin ninguna
particularidad) y todas fallan con la misma firma de error interna
(`SP_ICR_COMPROB_APL`), lo que confirma que el problema está en ese
procedimiento, no en los datos enviados.

---

## 1) Registro del comprobante — `POST /api/ComprobantesCompra` (ÉXITO, 201)

Payload enviado:

```json
{
  "Comprobante": "MA",
  "Prefijo": "00001",
  "Fecha": "2026-07-21",
  "Total": 11488.18,
  "TotalGravado": 11488.18,
  "EmitidoPor": "2",
  "Empresa": 1,
  "Sucursal": 1,
  "Deposito": 1,
  "Caja": "1",
  "MetodoPago": "C",
  "Proveedor": "LITORALG",
  "PrefijoComprobanteExterno": "0081",
  "NumeroComprobanteExterno": 50240726,
  "FechaComprobanteExterno": "2025-05-21",
  "NumeroCAIoCAE": "36190005763288",
  "VencimientoCAIoCAE": "2025-05-21",
  "Vencimientos": [{ "FechaVencimiento": "2025-05-21", "Importe": 11488.18 }],
  "Items": [
    {
      "CodigoItem": "Gs Gs 21%",
      "TipoEntrega": "E",
      "NumeroUnidadMedida": "1",
      "CantidadPrimeraUnidad": 1,
      "PrecioUnitario": 6133.68,
      "ImporteGravado": 6133.68,
      "ImporteTotal": 6133.68,
      "TasaIva": 21,
      "CentroApropiacionA": "SD",
      "CentroApropiacionB": "SD"
    }
    // ... 15 ítems en total, uno por línea real de la factura
    // (Cargo fijo, Gas Consumido, impuestos, IVA, etc.)
  ]
}
```

BAS respondió `201` (confirmado por logs de la aplicación:
`"BAS: factura registrada; orden de pago creada"` / `"factura registrada OK"`).

---

## 2) Verificación independiente — `GET /api/ConsultaComprobantes` (ÉXITO)

Antes de intentar la Orden de Pago, se vuelve a consultar el comprobante por
separado (no se confía únicamente en la respuesta del `POST`). Respuesta real
de BAS:

```json
{
  "IdTransaccion": 274505,
  "Empresa": "1",
  "Sucursal": "1",
  "Fecha": "2025-05-21",
  "Comprobante": "MA",
  "Prefijo": "00001",
  "Numero": 21885,
  "HastaNumero": 21885,
  "FechaComprobanteExterno": "2025-05-21",
  "PrefijoComprobanteExterno": "0081",
  "NumeroComprobanteExterno": 50240726,
  "CodigoCuentaCorriente": "LITORALG",
  "Usuario": "sa",
  "Anulado": false,
  "Observacion": null
}
```

Esto confirma: el comprobante existe (`Anulado: false`), con
`Prefijo: "00001"` y `Numero: 21885`.

---

## 3) Crear Orden de Pago — `POST /api/OrdenesPago` (FALLA, 409)

Payload enviado, referenciando exactamente el mismo comprobante que el paso 2
acaba de confirmar que existe:

```json
{
  "Fecha": "2026-07-21",
  "Total": 1,
  "Empresa": 1,
  "Sucursal": 1,
  "CotizacionMonedaComprobante": 1.0,
  "ComprobantesAplicados": [
    { "Comprobante": "MA", "Prefijo": "00001", "Numero": 21885, "Importe": 1 }
  ],
  "PrefijoCuentaCorriente": "P",
  "CodigoCuentaCorriente": "LITORALG",
  "Prefijo": "00001",
  "Caja": "1",
  "Efectivos": [
    { "MedioPago": "1", "Importe": 1, "IngresooEgreso": "E" }
  ]
}
```

Respuesta real de BAS:

```json
{
  "title": "El comprobante MA 00001-00021885 no existe para aplicarlo.(SP_ICR_COMPROB_APL)(SP_ICR_COMPROB_CAJA)",
  "status": 409
}
```

**El contraste que importa:** el JSON del punto 2 (`Anulado: false`, con
`Prefijo: "00001"` / `Numero: 21885`) es la prueba de que el comprobante
existe. El JSON del punto 3 usa exactamente esos mismos valores y BAS
igual responde que no existe.

---

## 4) Otras variantes probadas (todas fallan igual)

| Variante | Resultado |
|---|---|
| Agregar `Fecha` real y verificada (la del punto 2) dentro de `ComprobantesAplicados` | Mismo error |
| Usar el endpoint alternativo `POST /api/AplicacionesComprobantes` en vez de `OrdenesPago` (crear una Orden de Pago suelta primero, sin aplicaciones — eso sí funciona, `201` real — y aplicar después por separado) | Mismo error interno: `"...(SP_ICR_COMPROB_APL)(SP_ICR_APLICACIONES)"` |
| Repetir con un comprobante recién creado el mismo día, sin factura electrónica real de por medio, con proveedor sano | Mismo error exacto |

La constante en los tres casos es la firma `SP_ICR_COMPROB_APL` — el
procedimiento interno de BAS que busca el comprobante para aplicarlo. No
depende del proveedor, la fecha, el endpoint usado, ni de si el comprobante es
real o de prueba.

