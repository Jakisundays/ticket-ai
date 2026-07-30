# Consulta del comprobante MA 00001-00021885 — para soporte BAS

## La consulta (request)

```
GET http://190.210.77.103:32501/api/ConsultaComprobantes?Empresa=1&Sucursal=1&Comprobante=MA&Prefijo=00001&Numero=21885
```

## Lo que devuelve la consola (response, 200 OK)

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

Esto confirma que el comprobante existe (`Anulado: false`).

---

## El problema: al intentar crear la Orden de Pago con esos mismos datos, BAS dice que no existe

### La consulta que falla (request)

```
POST http://190.210.77.103:32501/api/OrdenesPago
```

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

### Lo que devuelve la consola (409 Conflict)

```json
{
  "title": "El comprobante MA 00001-00021885 no existe para aplicarlo.(SP_ICR_COMPROB_APL)(SP_ICR_COMPROB_CAJA)",
  "status": 409
}
```

---

**Resumen:** la consulta (`GET /api/ConsultaComprobantes`) confirma que el comprobante `MA 00001-00021885` existe y no está anulado. El mismo `Prefijo`/`Numero` (`00001`/`21885`), usados en `POST /api/OrdenesPago` para aplicarlo, hacen que BAS responda que ese comprobante no existe.
