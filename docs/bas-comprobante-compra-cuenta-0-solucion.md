# Solución: 409 "cuenta 0" al crear un Comprobante de Compra en BAS

Resumen accionable. Para la investigación completa (evidencia, alternativas descartadas,
diagramas), ver `docs/bas-comprobante-compra-cuenta-0-diagnostico.md`.

**Estado: RESUELTO y verificado con un `201` real (2026-07-18). Este documento explica
qué se hizo y qué falta para cerrarlo del todo (commitear).**

---

## 1. El problema

`POST /api/ComprobantesCompra` fallaba con:

```
409 Conflict
No se pudo establecer la moneda correspondiente a la cuenta 0.
(SP_ICR_VALIDA_CODTAB)(SP_ICR_COMPROB_COMPRA)
```

## 2. La causa

El proveedor usado en la prueba (`SUPERCOO`) fue dado de alta sin `CuentasCorrientes`.
Con `MetodoPago="C"` (cuenta corriente), BAS necesita esa cuenta para resolver en qué
moneda está — sin ella, cae a una cuenta "0" inexistente y rechaza el comprobante.

Esto **no es exclusivo de SUPERCOO**: el código que da de alta proveedores nuevos
automáticamente (`construir_payload_proveedor`, `utils/bas.py`) nunca seteaba
`CuentasCorrientes` para ningún proveedor.

## 3. Los 2 pasos para resolverlo

### Paso A — Arreglar el proveedor ya existente (escritura puntual, ya ejecutada)

```
PUT /api/Proveedores/SUPERCOO
```

Body: el proveedor completo (GET primero, no mandar un objeto parcial) + agregar:

```json
"CuentasCorrientes": [
  { "ImputacionContable": 211001, "PorDefecto": true }
]
```

`211001` es la cuenta contable **"Proveedores"** del plan de cuentas real
(`GET /api/Cuentas`) — genérica, no inventada, presente en el 100% de una muestra de 25
proveedores reales y activos del maestro.

**Verificación:** `GET /api/Proveedores/SUPERCOO` → `CuentasCorrientes` ya no está vacío.

### Paso B — Evitar que se repita con el próximo proveedor nuevo (fix de código, ya aplicado)

Cualquier proveedor que se da de alta automáticamente por CUIT (flujo de producción real,
`verificar_o_dar_de_alta_proveedor`) debe nacer con esa misma cuenta:

| Archivo | Cambio |
|---|---|
| `utils/bas_config.py` | + `BAS_IMPUTACION_CONTABLE_PROVEEDORES = 211001` |
| `utils/bas.py` | `construir_payload_proveedor()` y `verificar_o_dar_de_alta_proveedor()` reciben un parámetro opcional `imputacion_contable`; si se pasa, arma `CuentasCorrientes` automáticamente |
| `routes/process_invoice_google_2.py` | el único call site real (`_obtener_o_verificar_proveedor_bas`) pasa `BAS_IMPUTACION_CONTABLE_PROVEEDORES` |

## 4. Verificación de que funciona

```bash
cd Invoicy
venv/bin/python scripts/test_crear_comprobante_compra.py --proveedor-codigo SUPERCOO
```

Resultado esperado: `201`, y el propio script lo confirma con un `GET` independiente
(`ConsultaComprobantesExternos`). Ya se corrió y dio `IdTransaccion 274465`,
`MA 00001-00021883`, `Anulado: false`.

## 5. Qué queda pendiente

- [ ] **Commitear los cambios.** Hoy están sin commitear:
  - `utils/bas.py`
  - `utils/bas_config.py`
  - `routes/process_invoice_google_2.py`
  - `docs/bas-comprobante-compra-cuenta-0-diagnostico.md` (y este archivo) — sin trackear
  - `scripts/` — sin trackear (nunca se commiteó, incluye el script de prueba)
- [ ] Si en el futuro se necesita dar de alta un proveedor para una **empresa distinta de
      Empresa 1 (PLATINUM HOMES)**, `211001` puede no ser la cuenta correcta para esa
      empresa — confirmar contra `GET /api/Cuentas` de esa empresa antes de reusar el
      valor a ciegas.
- [ ] Las categorías `"Bebidas y Bar"` / `"Insumos"` / `"Limpieza"` en
      `utils/bas_config.py` siguen siendo placeholders (`"<codigo a confirmar>"`) — deuda
      pendiente sin relación con este bug, documentada ahí mismo.

## 6. Qué NO resuelve esto (bloqueo distinto, no relacionado)

El paso siguiente del flujo, `POST /api/OrdenesPago` (aplicar la factura ya registrada),
sigue fallando con `"El comprobante ... no existe para aplicarlo"` — es un bloqueo
**distinto**, ya investigado a fondo y sin causa identificada del lado de Invoicy (ver
`docs/bas-orden-de-pago-research.md`). No se toca en este documento.
