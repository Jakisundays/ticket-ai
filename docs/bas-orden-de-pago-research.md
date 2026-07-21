# BAS CS WebAPI — Crear una Orden de Pago a partir de una factura

> Investigación del Swagger `http://190.210.77.103:32501/swagger/v1/swagger.json`
> (BAS CS WebAPI 3.0.0.18, OpenAPI 3.0.4, 168 endpoints). Auth `Bearer`.
> Fecha: 2026-06-19.

## Resumen ejecutivo

BAS es un ERP. Una **Orden de Pago (OP)** se emite para **cancelar una o más
facturas de compra de un proveedor** que están en su cuenta corriente. Por lo
tanto, "crear una orden de pago a partir de una factura" implica:

1. **Autenticarse** → `POST /auth/token` (Bearer).
2. **Validar / ubicar la factura de compra** en BAS → `GET /api/ConsultaComprobantesExternos`
   (por el número del proveedor, que es lo que extrae Invoicy) o
   `GET /api/ConsultaComprobantes` (por la numeración interna de BAS).
   - Si la factura **no existe** en BAS todavía, registrarla primero con
     `POST /api/ComprobantesCompra` (dependencia previa).
3. **Crear la OP** → `POST /api/OrdenesPago`, referenciando la factura dentro de
   `ComprobantesAplicados[]` y con al menos un medio de pago.
4. **Confirmar** → con la respuesta `201 RespuestaComprobantes` (trae IdTransaccion
   y el Prefijo/Número asignado a la OP) y, opcionalmente, re-consultando con
   `GET /api/ConsultaComprobantes` (Comprobante = `OP`).

El camino recomendado es **una sola llamada `POST /api/OrdenesPago` con las
facturas embebidas en `ComprobantesAplicados`** (atómico: registra el pago y lo
imputa a la factura en un solo paso).

---

## 0. Autenticación (dependencia transversal)

`POST /auth/token` — `multipart/form-data`:

| campo          | valor                               |
|----------------|-------------------------------------|
| `grant_type`   | `password`  (env `BAS_GRAND_TYPE`)  |
| `client_id`    | `api` (default del Swagger)         |
| `client_secret`| `secret` (default del Swagger)      |
| `username`     | env `BAS_USER` (`sa`)               |
| `password`     | env `BAS_PASSWORD`                  |

Respuesta `200` → `AccessTokens`:
```
{ access_token, token_type: "Bearer", expires_in: 6000, refresh_token }
```
- **Verificado OK** contra el servidor: token válido ~100 min, con `refresh_token`.
- Todas las demás llamadas usan `Authorization: Bearer <access_token>`.

> Falta en `.env`: la **URL base**. Conviene agregar `BAS_BASE_URL=http://190.210.77.103:32501`
> en vez de hardcodearla.

---

## 1. Buscar / validar la factura

La factura a pagar es una **Factura de Compra** (origen "compra"). Códigos de
tipo de comprobante (de `GET /api/TiposComprobantes`, campo `Comprobante`):

| Código | Nombre                       |
|--------|------------------------------|
| `MA`   | Factura de Compra A          |
| `MB`   | Factura de Compra B          |
| `MC`   | Factura de Compra C          |
| `MI`   | Factura de Compra I          |
| `MM`   | Factura de Compra M          |
| `RA/RB/XC` | Factura-Recibo de Compra A/B/C |
| `TA/TB/TC/TI/TM` | Notas de Crédito (compras) |
| `BA/BB/BC/BI/BM` | Notas de Débito (compras)  |
| `OP`   | **Orden de pago**            |
| `AV`   | Aplicac. Ctas. Ctes. Proveedores |

### Opción A — la factura ya existe en BAS
- `GET /api/ConsultaComprobantes` (params **requeridos**: `Empresa`, `Sucursal`,
  `Comprobante`, `Prefijo`, `Numero`; opcional `Fecha`). Para cuando ya guardamos
  la numeración **interna** de BAS.
- `GET /api/ConsultaComprobantesExternos` — además acepta
  `FechaComprobanteExterno`, `PrefijoComprobanteExterno`, `NumeroComprobanteExterno`.
  **Es la indicada para Invoicy**, porque Invoicy extrae el número de factura del
  **proveedor** (externo), no el de BAS.
- Respuesta `200` → `RespuestaConsultaComprobante`
  (`Empresa, Sucursal, Comprobante, Prefijo, Numero, NumeroComprobanteExterno,
  CodigoCuentaCorriente, Anulado, ...`). **`204`** = no existe.

> **Verificado contra el servidor (2026-06-19):**
> - `ConsultaComprobantesExternos` exige **una de dos** combinaciones de parámetros:
>   `Prefijo`+`Numero` (interno) **o** `FechaComprobanteExterno`+`PrefijoComprobanteExterno`+`NumeroComprobanteExterno`.
>   Si falta, devuelve `400` con: *"You must provide the Prefix and Number or Date, Prefix and External Number."*
> - Cuando **no hay coincidencia** devuelve `200` con un **array vacío `[]`** (no `204`).
>   El cliente normaliza `[]`/`{}`/`204` → `None` (helper `_normalizar_comprobante`).

### Opción B — la factura NO existe aún en BAS (dependencia previa)
Registrarla con `POST /api/ComprobantesCompra?IgnoraAdvertencias=false`.
Body `ComprobanteCompra` (campos clave; `*` = requerido):

- `*Comprobante` (`MA`/`MB`/...), `*Fecha`, `*Total`, `*EmitidoPor`
- `Proveedor`, `Empresa`, `Sucursal`
- `PrefijoComprobanteExterno`, `NumeroComprobanteExterno`, `FechaComprobanteExterno`
  ← mapean directo desde lo que extrae Invoicy (`comprobante.numero`, `fecha_emision`)
- Totales: `TotalGravado`, `TotalIva`, `TotalPercepcion*`, `Total`
- `NumeroCAIoCAE`, `VencimientoCAIoCAE` ← `otros.CAE` / `otros.vencimiento_CAE`
- `Items[]`, `Vencimientos[]`, e incluso medios de pago si es contado.

Respuesta `201` → `RespuestaComprobantes` con el `Prefijo`/`Numero` interno
asignado, que luego se usa en `ComprobantesAplicados` de la OP.

> **Este es el flujo real del usuario:** registran facturas a partir de **fotos**
> que les mandan; la factura **nunca** preexiste en BAS → siempre se va por esta rama.

#### Requisitos de registro validados con POST de prueba (2026-06-19)

- **Schema-required:** `Comprobante` (MA/MB/MC…), `Fecha`, `Total`, `EmitidoPor`
  (`0`CAI `1`ControladorFiscal `2`CAE `3`FCE → factura electrónica con CAE = **`2`**).
- **Runtime también exige `Prefijo`** (talonario del comprobante de compra) — igual que la OP.
- `Proveedor` = código del maestro, **máx. 8 caracteres** (no es obligatorio en el
  schema; hay además un bloque `Ocasional` + campos inline `Nombre`/`NumeroImpositivoTipo`/
  `NumeroImpositivo` para proveedor no registrado — **a validar si evita el maestro**).
- `Items` **no** es obligatorio, y cada ítem exige `CodigoItem` (código del maestro de
  BAS) + `TipoEntrega`. Las fotos no traen códigos → **registrar solo cabecera** (sin ítems).
- Con `Prefijo` placeholder → `409` (conflicto: el talonario/proveedor debe existir de verdad).

**Mapeo Invoicy `factura["data"]` → ComprobanteCompra:**

| ComprobanteCompra | Origen en Invoicy |
|---|---|
| `Comprobante` (MA/MB/MC) | `comprobante.tipo` + letra según condición IVA del receptor |
| `PrefijoComprobanteExterno` / `NumeroComprobanteExterno` | `comprobante.numero` (`05062-0000024802` → `05062` / `24802`) |
| `FechaComprobanteExterno` / `Fecha` | `comprobante.fecha_emision` (convertir serial→ISO) |
| `Nombre` | `emisor.nombre` |
| `NumeroImpositivo` (+ `NumeroImpositivoTipo`="80") | `emisor.id_fiscal` (sin guiones) |
| `Total` / `TotalIva` / `TotalGravado` | `items.total` / `impuestos[].importe` / `items.subtotal` |
| `NumeroCAIoCAE` / `VencimientoCAIoCAE` / `EmitidoPor=2` | `otros.CAE` / `otros.vencimiento_CAE` |

**Falta (config de BAS, no de la foto):** `Proveedor` (≤8, del maestro — bloqueado
por el 500) y, según el caso, `TratImpositivo`/`TratImpositivoProv`.

#### Talonarios / Prefijos (Empresa 1, verificado `GET /api/Talonarios/1`)

> El `{id}` de `/api/Talonarios/{id}` es el **código de Empresa** (por eso `2`/`10`
> devuelven `[]`). El cliente los resuelve con `buscar_prefijo_talonario(empresa, comprobante)`.

| Comprobante | Talonario | Prefijo |
|---|---|---|
| `MA` Factura de Compra A | 1 | **`00001`** |
| `MC` Factura de Compra C | 2 | `00001` |
| `MM` Factura de Compra M | 38 | `00001` |
| `MI` Factura Proveedor Exterior | 55 | `00001` |
| `OP` Orden de Pago | 3 | **`00001`** |
| `AV` Aplicaciones | 4 | `00001` |
| `MB` Factura de Compra B | — | **no hay talonario regular** (solo "SALDO INICIAL") |

> ⚠️ **No existe talonario regular para Factura de Compra B (`MB`).** Tiene sentido:
> PLATINUM HOMES es Responsable Inscripto, así que los proveedores le emiten
> **Factura A** (`MA`), no B. La factura de prueba de SUPERCOOP es **a "Consumidor
> Final" (B)** → no es una compra registrable de la empresa, es un ticket de consumidor.
> El mapper debe enviar la mayoría de las compras como `MA`.

#### Estado de los POST de prueba con el Prefijo REAL (2026-06-19)

Con `Prefijo=00001`, **ambos flujos pasan toda la validación de campos y llegan a
`409`** (conflicto de negocio: el proveedor/cuenta de prueba no existe). Es decir, los
payloads ya están **estructuralmente completos**:
- **Registro:** `MA` + `Prefijo 00001` + Empresa/Sucursal 1 + `EmitidoPor 2` + Proveedor + nro externo.
- **OP:** `Prefijo 00001` + `PrefijoCuentaCorriente "P"` + `CodigoCuentaCorriente` + medio de pago con `IngresooEgreso "E"`.

**Estado (2026-07-01):** el `500` de `GET /api/Proveedores` **ya está resuelto**. Se
confirmó que SUPERCOOP no estaba en el maestro (1000+ proveedores) y **se dio de alta
como proveedor real** vía `POST /api/Proveedores`:

```json
{"Codigo":"SUPERCOO","RazonSocial":"SUPERCOOP","EmpresaAlta":1,
 "TratImpositivo":"2","TratImpositivoProv":"1",
 "NumeroImpositivoTipo":"80","NumeroImpositivo1":"30525705931"}
```
→ `201` creado, verificado con `GET /api/Proveedores/SUPERCOO`. `TratImpositivo`/
`TratImpositivoProv` salen de los catálogos reales `GET /api/TratamientosImpositivos(Provinciales)/{empresa}`
(nota: estas dos rutas requieren `{empresa}` en el path — sin él dan `405`, no `200`
vacío). Implementado en `BasClient.construir_payload_proveedor()` + `crear_proveedor()`.

**⚠️ NUEVO HALLAZGO — el proveedor NO era la única causa del `409`:** con SUPERCOOP ya
real, el registro de la factura de compra (`POST /api/ComprobantesCompra`) **sigue
devolviendo `409` sin body**, igual que antes de crear el proveedor. Se probaron 4
variantes reales (ninguna creó nada): proveedor inexistente → proveedor real; fecha
2025-05-02 → fecha actual (para descartar período contable cerrado); `EmitidoPor="2"`
(CAE) → `EmitidoPor="0"` (CAI). **Los cuatro intentos dieron el mismo `409` sin detalle.**
Conclusión: la existencia del proveedor **no era (o no era la única) causa** del `409`
en el registro de la factura — hay otra condición de negocio no identificable desde
afuera de la API, porque el `409` no trae cuerpo. **Se necesita que alguien con acceso
a los logs del servidor de BAS revise el detalle real del conflicto** para esa
transacción; la API no lo expone. No se debe seguir iterando POSTs reales a ciegas
contra producción sin esa información.

**Confirmación adicional (2026-07-01, a pedido del usuario):** se repitió el registro
con un proveedor **preexistente y con historial real** en BAS (`Proveedor="00001"`,
Thymbra Latinoamericana SA — no creado por esta integración), mismo talonario (`MA`,
Prefijo `00001`), `Total=1`, comprobante externo distinto (`TEST-1`) para no chocar con
los intentos previos. **Resultado: el mismo `409` sin body.** Esto **descarta
definitivamente** que el `409` tenga relación con el proveedor (nuevo vs. establecido,
da igual) — la causa está en otro lado del payload/talonario de `ComprobantesCompra`,
ajeno al proveedor. Refuerza que el paso siguiente es exclusivamente **revisar los logs
del servidor de BAS**, no seguir variando campos del lado cliente.

---

## DIAGNÓSTICO DEFINITIVO del `409` (2026-07-01)

### Corrección previa importante

Los `409` **siempre tuvieron body** (`application/problem+json` con `{"title": ..., "status": 409}`).
El "409 sin detalle" reportado antes era un **bug de nuestros scripts de diagnóstico**:
imprimían `e.detail.get("errors")` (la forma de los `400` de validación), y este tipo de
respuesta no tiene clave `errors` → imprimía `null`. `BasClient._detalle()` siempre lo
capturó bien. **Lección: imprimir siempre `e.detail` completo.** No hacía falta acceso a
logs del servidor: el mensaje estaba disponible del lado cliente desde el principio.

### Cadena de errores destrabada (uno por uno, todos con `Total=1`)

| # | Error del servidor (textual) | Causa | Solución | ¿Nuestra? |
|---|------------------------------|-------|----------|-----------|
| 1 | `El depósito 0 no pertenece a la empresa...` | No mandábamos `Deposito`; default 0 no existe | `"Deposito": 1` (DEPOSITO PLATINUM, de `GET /api/Depositos/{empresa}` — el `{id}` es la empresa) | ✅ |
| 2 | `La imputación contable indicada X es distinta a la definida en parámetros para compras contado` | Imputación explícita ≠ parámetro | `"ImputacionContable": 211003` (= `ParametrosGenerales.CuentaComprasContado`) | ✅ |
| 3 | `Incorrect syntax near the keyword 'FROM' (SP_ICR_COMPROB_COMPRA_ITEMS)` | **SQL crudo roto en el SP de BAS cuando la factura va SIN ítems** (cabecera sola) | Workaround: mandar ≥1 línea de ítem real | ⚠️ workaround nuestro; el crash es bug de BAS |
| 4 | `El talonario del comprobante NO tiene un talonario asociado para ingresos/egresos de mercadería` | `TipoEntrega="O"` mueve mercadería y el talonario 1 no tiene remito asociado | `TipoEntrega="E"` (entrega pendiente, no mueve stock) | ✅ |
| 5 | `La caja (null) no está definida en la empresa (SP_ICR_COMPROB_PAGOS)` | Sucursal sin `Caja` default | `"Caja": "1"` (de `Sucursal.Cajas[]`) | ✅ |
| 6 | `No se indicaron líneas de ítems (SP_GENEROASI)` | La línea iba con campos inexistentes (`Cantidad` no existe) → cantidades/importes en 0 | Campos correctos: `CantidadPrimeraUnidad`, `PrecioUnitario`, `ImporteGravado`, `ImporteTotal`, `NumeroUnidadMedida="1"` | ✅ |
| 7 | `La posición contable del ítem 006 Insumos no está definida para el concepto COM (ACTUREF)` | **NINGÚN ítem del maestro tiene posición contable para compras** | — | ❌ **BLOQUEO FINAL — configuración contable de BAS** |

### Causa raíz (verificada con evidencia)

**El circuito de COMPRAS de esta instalación de BAS nunca fue configurado.** Evidencia:
- **0 de 255 ítems** (253 servicios + 2 bienes, maestro completo paginado + detalle por id)
  tienen `PosicionesContables`; las 30 posiciones existentes son todas de **VENTAS**
  (`VENTAS-Insumos`, `VENTAS-Bar`, ...). Sin posición contable de compras, `SP_GENEROASI`
  no puede armar el asiento de NINGUNA factura de compra con ítems.
- La vía "cabecera sin ítems" (nuestro diseño preferido) **crashea el SP de BAS** con un
  error de sintaxis SQL — bug de robustez del stored procedure, no configurable de afuera.
- Config faltante en cascada, todo consistente: Sucursal sin `Deposito`/`Caja`/imputaciones
  default, talonario de compras sin remito asociado, y el `500` histórico por tabla faltante
  (`CTACTESTARJETAS`). El módulo de ventas está configurado; el de compras, no.

### Veredicto: escalar (con dos pedidos concretos)

Ya **no queda nada accionable de nuestro lado**: los 6 primeros obstáculos están resueltos
y codificados; el 7º exige decisiones contables dentro del ERP (qué posición contable de
compras corresponde — hoy no existe ninguna) y/o corregir el SP. Pedidos al equipo/admin BAS:

1. **Habilitar compras vía API**, en cualquiera de sus dos formas:
   a) que soporte factura de compra **sin ítems** (hoy `SP_ICR_COMPROB_COMPRA_ITEMS`
      revienta con `Incorrect syntax near 'FROM'` — reportar como bug), **o**
   b) definir **posiciones contables de compras** (concepto `COM`) al menos para un ítem
      genérico (p.ej. crear servicio "GASTOS VARIOS" con su posición) que usaremos en las
      líneas.
2. **Código(s) de `MedioPago`** (Texto 3) para la orden de pago (efectivo/transferencia)
   — sin endpoint que los exponga.

*(Opcional, calidad de vida: setear defaults de `Deposito`, `Caja` e imputaciones de
compra en la Sucursal — mientras tanto los mandamos explícitos sin problema.)*

**Sobre la hipótesis "¿es una cuenta/ambiente de pruebas?":** parcialmente confirmada en
su versión fuerte — no son permisos (el usuario `sa` escribe: creó a SUPERCOO), sino una
**instalación con el módulo de compras sin configurar** (posiblemente instancia de prueba
levantada en mar-2026: fechas `Fechareg` 2026-03-16 en Depósito/talonarios nuevos).

### Verificación de integridad (2026-07-01)

Ningún intento de factura persistió (todos `409` = rechazo limpio; confirmado con
`ConsultaComprobantesExternos` → no existen ni `05062-24802` ni `TEST-*`). Lo único
creado en el ERP es el **proveedor SUPERCOO** (intencional y aprobado).

### 🎉 ACTUALIZACIÓN (2026-07-01): registro de factura de compra LOGRADO — `201`

El diagnóstico anterior (posición contable "nunca configurada") estaba **parcialmente
equivocado** por un error metodológico: se buscaba el campo `PosicionesContables`
(plural) en los ítems, que no existe — el campo real es **`PosicionContable`**
(singular, string con el código). Con el campo correcto: **255/255 ítems tienen
posición asignada**, y **193/255 (76%) tienen el concepto `COM` (Compras) configurado**
(las 30 posiciones contables del maestro: 117 con COM, resto solo VEN/otros). El ítem
usado en la primera prueba (`006 - Insumos`) resultó ser una de las excepciones sin COM.

**Payload que logró el `201` con proveedor preexistente (Thymbra, `00001`):**

```json
{
  "Comprobante": "MA", "Prefijo": "00001", "Fecha": "2026-07-01",
  "Total": 1, "TotalGravado": 1,
  "EmitidoPor": "2", "Empresa": 1, "Sucursal": 1,
  "Deposito": 1, "Caja": "1",
  "MetodoPago": "C",
  "Proveedor": "00001",
  "PrefijoComprobanteExterno": "TEST", "NumeroComprobanteExterno": 9,
  "FechaComprobanteExterno": "2026-07-01",
  "NumeroCAIoCAE": "75083266482093", "VencimientoCAIoCAE": "2026-07-11",
  "Vencimientos": [{"FechaVencimiento": "2026-07-31", "Importe": 1}],
  "Items": [{
    "CodigoItem": "Gs Gs 21%",
    "TipoEntrega": "E",
    "NumeroUnidadMedida": "1",
    "CantidadPrimeraUnidad": 1,
    "PrecioUnitario": 1,
    "ImporteGravado": 1,
    "ImporteTotal": 1,
    "TasaIva": 21,
    "CentroApropiacionA": "SD",
    "CentroApropiacionB": "SD"
  }]
}
```
→ `201`: `{"IdTransaccion": 274435, "Comprobantes": [{"Comprobante": "FAC A", "Prefijo": "00001", "Numero": "00021876", ...}]}`
→ Verificado con `GET /api/ConsultaComprobantes`: `Anulado: false`, persiste correctamente.

**Errores adicionales destrabados hoy** (más allá de los 7 documentados antes):

| # | Error | Causa | Fix |
|---|---|---|---|
| 8 | `El talonario NO tiene talonario asociado para ingresos/egresos de mercadería` | `TipoEntrega:"O"` mueve stock | `TipoEntrega:"E"` (entrega pendiente) |
| 9 | `INSERT conflicted with FOREIGN KEY "FK_MVSITEMS_CENTROSAP"` en DB `PLATINUM_TEST` | Sin `CentroApropiacionA/B`, apunta a un registro que no existe | `CentroApropiacionA/B: "SD"` (de `GET /api/CentrosApropiacion/{tipo}`) |
| 10 | `Debe ingresar el número de C.A.E. y/o la fecha de vencimiento` | `EmitidoPor:"2"` exige CAE | `NumeroCAIoCAE` + `VencimientoCAIoCAE` |
| 11 | `Si el método de pago es Contado, el comprobante debe tener líneas de pago` | Default de `MetodoPago` es `"D"` (Contado) | `MetodoPago:"C"` (Cuenta Corriente) — regex `[DCR]` |
| 12 | `El total gravado del comprobante (0.00) no coincide con la suma de los totales gravados de las líneas (1.00)` | Faltaba `TotalGravado` en cabecera | `TotalGravado:1` |

**⚠️ Confirmación directa de la hipótesis "ambiente de pruebas":** el error #9 reveló el
nombre real de la base de datos: **`"PLATINUM_TEST"`**. Ya no es hipótesis.

### ⚠️ Nuevo obstáculo (distinto): la Orden de Pago no reconoce la factura recién creada

Con la factura real (`MA`, Prefijo `00001`, Numero `21876`) ya registrada y verificada
(`Anulado: false`), se intentó `POST /api/OrdenesPago` aplicándola. Se encontró primero
que **no hay ningún endpoint que exponga el catálogo de `MedioPago`** — probando códigos
candidatos, `"1"` pasó todas las validaciones de formato (a diferencia de `"EF"`, `"001"`,
`"EFE"`, `"CAJ"`, `"100"`, que dieron *"El medio de pago X de la sección EFECTIVO es
inexistente"*), sugiriendo que **`"1"` es válido** para efectivo.

Con `MedioPago="1"` + `Caja="1"` + la factura real referenciada, el error persistente es:

```
"El comprobante MA 00001-00021876 no existe para aplicarlo."
(SP_ICR_COMPROB_APL)(SP_ICR_COMPROB_CAJA)
```

A pesar de que `GET /api/ConsultaComprobantes` confirma que el comprobante existe y no
está anulado. Se probaron y descartaron como causa: formato de `Numero` (int/string/
padding), `CodigoCuentaCorriente` con padding a 8 chars, agregar `Fecha`/
`FechaVencimiento` dentro de `ComprobantesAplicados`, agregar `ImputacionContable`,
reintento por timing. Con `Importe` **negativo** el mensaje cambia a uno de validación
de signo (*"contiene aplicaciones con importe negativo o igual a cero"*) — indica que esa
validación de signo ocurre *antes* en el código, no que el importe negativo "encuentre"
el comprobante. **Causa aún no identificada.** Hipótesis no probadas: la moneda del
comprobante (`MonedaComprobante` no se especificó al crear la factura), o que
`MetodoPago="C"` requiera un paso de "confirmación"/cierre adicional (vía la app de
escritorio o un endpoint no identificado) antes de que la factura quede disponible para
aplicarse a un pago — a confirmar con el admin de BAS.

#### Investigación adicional (2026-07-01, continuación) — causas descartadas con evidencia

Se probaron 9 hipótesis más, cada una descartando una causa concreta (ninguna resolvió
el problema; ninguna creó una OP real — todos `409`, `Total=1`):

| Hipótesis probada | Resultado |
|---|---|
| Reintento simple (timing) | Mismo error — no es timing |
| `ImputacionContable` dentro de `ComprobanteAplicado` | Mismo error |
| `Comprobante:"FAC A"` (nombre completo en vez de código `"MA"`) | `400` de validación — el campo espera el código corto, `"MA"` es correcto |
| `Importe` negativo | Mensaje **distinto**: *"contiene aplicaciones con importe negativo o igual a cero"* — indica que la validación de signo ocurre *antes* en el código, no que el negativo "encuentre" el comprobante |
| `MedioPago` candidatos (`EF`,`001`,`EFE`,`CAJ`,`100`) | Todos: *"medio de pago X de la sección EFECTIVO es inexistente"* — validados contra una tabla real |
| `MedioPago="1"` | **Pasa** esa validación (no da el error de "inexistente") — parece ser el código válido |
| Factura nueva con `MonedaComprobante="L"` explícito | Mismo error de aplicación — no es la moneda del comprobante |
| `MonedaCtaCte="L"` en la **factura** | Rompe algo distinto: `FK_TRANSAC_CUENTAS` en tabla `CUENTAS` — no es el camino, requiere config de cuentas bancarias que no existe |
| `MonedaCtaCte="L"` en la **OP** | Mismo error de aplicación — no es esto |
| `ImputacionContable` en la **cabecera de la OP** | Error nuevo: *"No puede indicar Aplicaciones para una cuenta que no tiene subcuentas clientes o proveedores"* — confirma que `ImputacionContable` de cabecera es un mecanismo alternativo (aplicar contra cuenta contable directa) incompatible con `PrefijoCuentaCorriente`/`CodigoCuentaCorriente` (aplicar contra cta cte de un tercero); no deben combinarse |

**Verificado también:** no existe ningún endpoint de la API para consultar el estado de
cuenta corriente de un **proveedor** (`EstadoCtaCteCliente`, `VencimientosCtaCteCliente`,
`SaldoDisponible` — los tres exigen `CodCliente`/`Cliente`, son exclusivos de Clientes).
Sin esa visibilidad, no se puede confirmar desde la API si el movimiento de cuenta
corriente de la factura se generó correctamente al registrarla.

**Estado:** causa raíz del *"no existe para aplicarlo"* aún no identificada tras ~13
variantes en total. Se agotaron las hipótesis razonables verificables desde la API.
**Recomendación:** este es un caso para el admin de BAS — los SPs involucrados
(`SP_ICR_COMPROB_APL`, prefijo `ICR`) podrían ser específicos de la capa de integración
REST (distintos de los que usa la interfaz de escritorio clásica), y podría tratarse de
un caso no cubierto por esos SPs en el ambiente `PLATINUM_TEST`, más que de un dato de
configuración faltante como en el primer obstáculo.

### Confirmación final: el bloqueo del PRIMER obstáculo era independiente del proveedor

Se repitió la cadena completa de fixes (Deposito=1, Caja="1", Vencimientos, Item con
campos correctos) usando **Thymbra Latinoamericana SA (Proveedor="00001")** — proveedor
preexistente en BAS, no creado por esta integración. **Resultado idéntico:**
`"La posición contable del ítem 006 Insumos no está definida para el concepto COM"`.
Esto confirma de forma definitiva que el bloqueo del punto 7 no depende de qué proveedor
se use (nuevo o preexistente) — es puramente una falta de configuración del catálogo de
ítems para el concepto `COM`, afecta a cualquier factura de compra con ítems.

Sigue faltando, además, el **código de `MedioPago`**:
> **Este valor NO está en la documentación (es dato del ERP):**
> Código de `MedioPago` (Texto 3) → **no hay endpoint ni enum**. Verificado en vivo:
> `GET /api/Cuentas` = plan de cuentas contable (6 dígitos, no es MedioPago),
> `GET /api/Conceptos` = COM/VEN, `GET /api/ParametrosCuentaCorriente` = flags.
> Lo provee el admin de BAS / la app de Tesorería.

---

## 2. Crear la Orden de Pago

`POST /api/OrdenesPago` — body `OrdenDePago`:

> **Importante — qué exige la doc vs. qué es inferencia de negocio.**
> El único `required` del schema `OrdenDePago` es: **`["Empresa", "Fecha", "Sucursal", "Total"]`**.
> El medio de pago y la cuenta corriente **NO** figuran como requeridos en el Swagger;
> los listamos abajo como *probables* requisitos prácticos por lógica de ERP (un pago
> necesita un medio y un proveedor al que imputarse), pero eso es una **regla de
> negocio del backend, no del contrato OpenAPI**. Se confirma solo con una prueba real
> (`dry_run=False`): el ERP responde `400`/`409` si falta algo que valida en runtime.

**Campos de cabecera** (`*` = requerido **por el schema**):
- `*Fecha` (date), `*Total` (double), `*Empresa` (int), `*Sucursal` (int)
- `Prefijo`, `Numero` (nullable → lo asigna el talonario de `OP`)
- `PrefijoCuentaCorriente` + `CodigoCuentaCorriente` (opcionales en el schema) →
  atan la OP al proveedor; **posible** requisito de negocio (a confirmar; quizá BAS lo
  deriva del comprobante aplicado)
- `MonedaCtaCte`, `CotizacionMonedaComprobante`, `ObservacionComprobante`, `Usuario`

**Facturas que paga** → `ComprobantesAplicados[]` (este es el enlace OP ↔ factura):
- `*Comprobante` (ej. `MA`), `*Prefijo`, `*Numero`, `*Importe`
- opcionales: `Fecha`, `FechaVencimiento`, `ImputacionContable`

**Medios de pago** (arrays opcionales en el schema; por lógica de negocio se espera
≥1 que sume `Total` − retenciones — **a confirmar contra el backend**):
`Efectivos[]`, `PagosPorBanco[]`, `Cheques[]`, `ChequesPropios[]`, `Pagares[]`,
`PagaresPropios[]`, `Tarjetas[]`, `CobrosPorBanco[]`. Cada ítem requiere `*MedioPago`
(código del medio) + `Importe`. El más simple es `Efectivos` o `PagosPorBanco`
(transferencia, requiere `CuentaBancaria`).

**Retenciones** → `Retenciones[]` (`*TipoDeRetencion`, importes, provincia, régimen).

Respuesta `201` → `RespuestaComprobantes`:
```
{ IdTransaccion, Comprobantes: [ { Comprobante: "OP", Prefijo, Numero, Fecha, ... } ] }
```
Otros: `400` (validación), `409` (conflicto, p.ej. factura ya pagada o saldo
insuficiente), `401` (token), `500` (error interno).

### Requisitos REALES validados con POST de prueba (2026-06-19, Total=1)

Encadenando intentos reales, el backend exige (más allá del `required` del Swagger):

| Campo | Regla (verificada en runtime) |
|-------|-------------------------------|
| `Prefijo` | **Requerido** — prefijo/talonario de la propia OP (Texto ≤8) |
| `CodigoCuentaCorriente` | **Requerido** — código de cta cte del **proveedor** (Texto 8) |
| `PrefijoCuentaCorriente` | Debe matchear `[CPA]` → **`C`** Clientes / **`P`** Proveedores / **`A`** Agentes. Para una OP → **`P`** |
| `<medio>[].IngresooEgreso` | Requerido, `[IE]` → un pago es **`E`** (Egreso) |
| `<medio>[].MedioPago` | Código del medio de pago (Texto 3) — de la config de BAS |

Secuencia de errores observada:
1. Sin cta cte → `400` `{Prefijo: required, CodigoCuentaCorriente: required}`.
2. Con cta cte placeholder → `409` (conflicto de negocio: la cuenta/factura no existe).
3. Con `PrefijoCuentaCorriente` inválido + efectivo → `400`
   `{PrefijoCuentaCorriente: regex [CPA], Efectivos[0].IngresooEgreso: required}`.

**Conclusión:** confirma que para pagar a un proveedor hace falta su cuenta corriente
(`PrefijoCuentaCorriente="P"` + `CodigoCuentaCorriente=<código del proveedor>`), que
sale del **registro de proveedores** — hoy bloqueado por el `500` de `GET /api/Proveedores`.
Además, la factura debe existir en BAS (la de prueba `05062-24802` NO está cargada).

---

## 3. Confirmar la creación

1. **Principal:** la respuesta `201 RespuestaComprobantes` ya confirma el alta y
   trae el **Prefijo/Número asignado a la OP** + `IdTransaccion`.
2. **Verificación opcional (doble check):** `GET /api/ConsultaComprobantes` con
   `Comprobante="OP"`, `Prefijo`, `Numero` de la respuesta → `200` y `Anulado=false`
   confirma que quedó registrada.

---

## 4. Dependencias y pasos intermedios

| Dependencia | Endpoint | Notas |
|-------------|----------|-------|
| Token Bearer | `POST /auth/token` | expira en 6000 s; refrescar con `refresh_token` |
| Empresa válida | `GET /api/Empresas` | `1`=PLATINUM HOMES S.A.; `2`=FIRST PARKING S.A. |
| Sucursal válida | `GET /api/Sucursales` | Empresa 1/Suc 1; Empresa 2/Suc 1 |
| Tipos de comprobante | `GET /api/TiposComprobantes` | mapear `MA/MB/...`, `OP`, `AV` |
| Proveedor + cuenta corriente | `GET /api/Proveedores` / `POST` | ✅ endpoint OK; mapear por CUIT (`NumeroImpositivo1`) desde `emisor.id_fiscal` de Invoicy. `CodigoCuentaCorriente` = `Proveedor.Codigo` |
| Código de MedioPago | — | ❓ sin endpoint; lo provee el admin de BAS |
| Factura en cta cte | `ConsultaComprobantes(Externos)` / `ComprobantesCompra` | debe existir antes de aplicarla |

> ✅ **RESUELTO (2026-06-20):** el equipo de BAS corrigió el `500` de
> `GET /api/Proveedores` en el entorno de pruebas. Verificado en vivo: devuelve el
> maestro completo, paginado (`pageSize`/`pageNumber`). Confirmado **más de 1000
> proveedores** (2 páginas de 500 ya traen 1000). No hay filtro por CUIT en la API,
> así que `BasClient.buscar_proveedor_por_cuit()` pagina el maestro y compara
> `NumeroImpositivo1` (solo dígitos) hasta encontrar coincidencia o agotar páginas.

### `Empresa` vs `Proveedor` — no confundir

- **`Empresa`** (Código de Empresa, entero 0–99) = la **empresa pagadora** del ERP
  multiempresa. Sale de `GET /api/Empresas` (NO del proveedor). Instancia actual:
  `1`=PLATINUM HOMES S.A. (CUIT 30714004758), `2`=FIRST PARKING S.A.
- El esquema **`Proveedor` no tiene campo `Empresa`**. Identificadores:
  `Codigo` (texto 8, obligatorio), `RazonSocial` (texto 60, obligatorio),
  `NumeroImpositivo1` (CUIT/RUC, texto 15).
- El `Empresa` de la OP se **elige** (qué empresa paga); del proveedor se obtiene su
  **cuenta corriente**.

**Endpoints de proveedores:** `GET /api/Proveedores` (paginado),
`GET /api/Proveedores/{id}` (id=Codigo), `GET /api/Proveedores/razonsocial={x}`,
`POST/PUT/DELETE /api/Proveedores/{id}`. **No hay búsqueda por CUIT** → listar y
filtrar por `NumeroImpositivo1` del lado del cliente.

> ⚠️ **Corrección (2026-06-20):** `Proveedor.CuentasCorrientes[]` **NO** contiene el
> `CodigoCuentaCorriente` que exige la OP. Un proveedor real trae, por ejemplo,
> `"CuentasCorrientes":[{"ImputacionContable":211001,"PorDefecto":true}]` — eso es la
> **cuenta contable de imputación** (plan de cuentas), no el vínculo con la OP.
> El `CodigoCuentaCorriente` que pide `OrdenDePago`/`ComprobanteCompra` **es el propio
> `Codigo` del proveedor** (con `PrefijoCuentaCorriente="P"` indicando "cuenta de tipo
> Proveedor"). Implementado en `BasClient.codigo_cuenta_corriente_proveedor()`.
> Confianza alta pero no 100% confirmada con un `201` real — el `409` del servidor no
> distingue "cuenta inválida" de "factura inválida" en el mismo intento, así que no se
> pudo aislar con una sola prueba sin arriesgar crear una OP real sobre un proveedor
> existente. Se confirma en la primera prueba de punta a punta con una factura real.

---

## Alternativas (varios caminos al mismo objetivo)

1. **OP con aplicaciones embebidas (RECOMENDADO).** `POST /api/OrdenesPago` con
   `ComprobantesAplicados`. Atómico, una sola llamada, menor superficie de error.
2. **Aplicación separada.** Crear la OP y luego imputar con
   `POST /api/AplicacionesComprobantes` (comprobante `AV`). Más flexible pero dos
   llamadas; útil sólo si hay que imputar pagos/créditos preexistentes a facturas
   a posteriori.
3. **Validación interna vs externa.** `ConsultaComprobantesExternos` cuando sólo
   se tiene el número del proveedor (caso Invoicy); `ConsultaComprobantes` cuando
   ya se guardó la numeración interna de BAS.
4. **Alta de factura directa vs preparación.** `ComprobantesCompra` registra de
   una; `ComprobantesCompraPreparacion` es una etapa de preparación/revisión previa.

---

## Diseño recomendado (robusto y mantenible)

- **Cliente BAS aislado** (módulo nuevo, p.ej. `routes/bas_client.py` o
  `utils/bas.py`): URL base y credenciales desde `.env`; cache del token con
  refresh automático; métodos tipados:
  `consultar_comprobante_externo()`, `crear_comprobante_compra()`,
  `crear_orden_de_pago()`, `consultar_comprobante()`.
- **Idempotencia:** antes de crear la factura/OP, consultar por número externo
  para no duplicar; persistir `IdTransaccion` + `Prefijo/Numero` devueltos.
- **Configuración, no hardcode:** Empresa, Sucursal, prefijo de cta cte, códigos
  de comprobante y medio de pago por defecto en `.env`/config.
- **Manejo de errores:** mapear `400/409` (negocio) vs `401` (re-auth) vs `500`
  (infra), con logs claros y reintento sólo donde tenga sentido (auth).

## Decisiones de negocio

| # | Decisión | Estado |
|---|----------|--------|
| 1 | Origen de la factura | **DEFINIDO — ambos casos (robusto).** Validar con `ConsultaComprobantesExternos`; si `204` (no existe), registrar con `ComprobantesCompra` y recién después crear la OP. Idempotente. |
| 2 | Medio de pago por defecto | **DIFERIDO.** El cliente BAS se deja agnóstico: soporta `Efectivos` / `PagosPorBanco` / `Cheques` / etc. vía un parámetro `medio_pago`. La elección concreta se resuelve más adelante. |
| 3 | Retenciones automáticas | Pendiente (Ganancias/IVA/IIBB). El campo `Retenciones[]` queda soportado pero opcional. |
| 4 | Empresa/Sucursal y prefijo de cta cte | Pendiente de confirmar con el admin de BAS. |

### Secuencia recomendada (flujo robusto, decisión #1)

```
1. token = POST /auth/token                      (cache + refresh)
2. r = GET /api/ConsultaComprobantesExternos      (Empresa, Sucursal,
        Comprobante=MA/MB/..., Num/Pref externos de Invoicy)
   ├─ 200 → la factura existe; tomo Prefijo/Numero internos
   └─ 204 → POST /api/ComprobantesCompra           (alta desde datos de Invoicy)
            → tomo Prefijo/Numero del 201
3. POST /api/OrdenesPago
     ComprobantesAplicados = [{Comprobante, Prefijo, Numero, Importe}]
     <medio_pago> = [{ MedioPago, Importe }]        (parametrizable, decisión #2)
   → 201 RespuestaComprobantes (IdTransaccion + OP Prefijo/Numero)
4. (opcional) GET /api/ConsultaComprobantes(Comprobante="OP", Prefijo, Numero)
```

### Cliente implementado — `utils/bas.py` (✅ hecho)

`BasClient` ya está implementado y verificado (auth real + consultas de lectura +
armado de payload en `dry_run`; **sin** escribir en el ERP). API pública:

```python
class BasClient:
    # config desde .env: BAS_BASE_URL, BAS_GRAND_TYPE, BAS_USER, BAS_PASSWORD,
    #                    BAS_CLIENT_ID (api), BAS_CLIENT_SECRET (secret)
    def get_token(self) -> str: ...                       # cache + refresh + re-login
    def consultar_comprobante(self, empresa, sucursal, comprobante, prefijo, numero, fecha=None)
    def consultar_comprobante_externo(self, empresa, sucursal, comprobante, *,
        prefijo_externo, numero_externo, fecha_externo, prefijo=None, numero=None)
    def consultar_talonarios(self, empresa) -> list
    def buscar_prefijo_talonario(self, empresa, comprobante) -> str | None
    def obtener_proveedor(self, codigo) -> dict | None            # GET /api/Proveedores/{id}
    def buscar_proveedor_por_razon_social(self, razon_social) -> dict | None
    def buscar_proveedor_por_cuit(self, cuit) -> dict | None       # pagina el maestro, corta al match
    def codigo_cuenta_corriente_proveedor(self, proveedor) -> str | None  # = Proveedor.Codigo
    def construir_payload_orden_pago(self, *, empresa, sucursal, fecha, total,
        comprobantes_aplicados, pagos: dict, prefijo_ctacte=None, codigo_ctacte=None,
        retenciones=None, ...)                            # `pagos` = {arrayMedio: [items]}
    def crear_comprobante_compra(self, payload, *, ignora_advertencias=False, dry_run=False)
    def crear_orden_de_pago(self, payload, *, dry_run=False)
    def crear_orden_de_pago_desde_factura(self, *, empresa, sucursal, comprobante_factura,
        prefijo_externo, numero_externo, importe, fecha_externo=None,
        prefijo_ctacte=None, codigo_ctacte=None, pagos=None, retenciones=None,
        comprobante_compra_payload=None, registrar_si_no_existe=True,
        dry_run=True)                                     # orquesta el flujo completo
```

- **Seguridad:** los métodos de escritura aceptan `dry_run`; `crear_orden_de_pago_desde_factura`
  usa `dry_run=True` por defecto (arma y devuelve el payload sin impactar el ERP).
- **Agnóstico al medio de pago:** se pasa `pagos={"PagosPorBanco": [...]}` / `{"Efectivos": [...]}` / etc.
- **Smoke test seguro:** `python -m utils.bas` (auth + consulta + payload, no escribe).

> Pendiente de configuración: agregar `BAS_BASE_URL` (y opcionalmente
> `BAS_CLIENT_ID`/`BAS_CLIENT_SECRET`) al `.env`. Hoy el cliente cae al default
> `http://190.210.77.103:32501` si no está seteada.

---

## 5. Catálogo real de `MedioPago` (confirmado en vivo, 2026-07-21)

**No existe ningún endpoint que exponga este catálogo** (confirmado probando
`/api/CONSULTAGRAL/{NameConsulta}` con varios nombres candidatos —
`MediosPago`, `MedioPago`, `MediosDePago`, `TablasGenerales`,
`CodigosMedioPago` — todos responden *"La consulta X no está habilitada"*).
La única forma de descubrirlo fue probar en vivo contra `/api/OrdenesPago`
con `Total=1` (regla de seguridad del proyecto para pruebas reales), leyendo
el mensaje de validación de BAS, que sí devuelve el tipo real de cada código:

*"El medio de pago N corresponde al tipo X y fue informado en la sección Y"*

| Código | Tipo real | Array de `OrdenDePago` correcto |
|---|---|---|
| 1, 2, 10, 11, 12 | Efectivo | `Efectivos` |
| 3, 5 | Cheque **propio** (existen en el maestro de BAS, pero **ninguno habilitado en Caja 1** — un admin de BAS tiene que agregarlos ahí antes de poder usarlos) | `ChequesPropios` |
| 4, 6 | Cheque **recibido** (de terceros, se paga endosándolo) | `Cheques` |
| 7 | Cobro por banco (dinero **entrante** — no sirve para pagar a un proveedor) | `CobrosPorBanco` |
| **8** | **Pago por banco** | `PagosPorBanco` — esto es "transferencia" |
| 9 | Tarjeta | `Tarjetas` |
| 13-20 | No existen (mensaje distinto: "es inexistente", no "no tiene imputación contable en la caja") | — |

**Confirmado end-to-end (llegó al mismo bloqueo estructural ya documentado
en §DIAGNÓSTICO DEFINITIVO, no a un error de medio de pago):**
- Transferencia: `MedioPago="8"` en `PagosPorBanco`, con `CuentaBancaria`
  (código real obtenido de `GET /api/CuentasBancarias/{empresa}`, ej. `"1"`
  = Banco Patagonia) + `Fecha` + `Numero` (bug real encontrado: el código de
  producción anterior no mandaba `Fecha` ni `Numero` en absoluto para
  `PagosPorBanco`, hubiera fallado con 400 aunque el `MedioPago` fuera
  correcto).
- Cheque (recibido): `MedioPago="4"` en `Cheques`, con `Fecha` +
  `NumeroExterno` (el número del cheque de terceros que se está endosando —
  dato que el flujo humano tiene que aportar, BAS lo valida contra un cheque
  ya existente).
- Tarjeta: `MedioPago="9"` en `Tarjetas`, con `Fecha` + `Plan` +
  `CodigoTarjeta` + `NumeroTarjeta` (los primeros dos son códigos del
  maestro de tarjetas de BAS — mismo problema que `MedioPago`, no hay
  catálogo consultable; se guardan en `bas_payment_methods` una vez que se
  confirmen con el admin de BAS o probando en vivo).

**Requisitos runtime NO declarados como `required` en el schema del Swagger**
(mismo patrón ya visto en `ComprobantesCompra`, §DIAGNÓSTICO):
- `Efectivos`: ninguno extra (no tiene `Fecha` en su schema — pasarla rompe
  por `additionalProperties: false`).
- `Cheques`/`ChequesPropios`: `Fecha`, `NumeroExterno`.
- `PagosPorBanco`: `Fecha`, `Numero`, `CuentaBancaria`.
- `Tarjetas`: `Fecha`, `Plan`, `CodigoTarjeta`, `NumeroTarjeta`.
- Cabecera de la OP: `ImputacionContable` (distinta de
  `CuentasCorrientes[].ImputacionContable` del proveedor — esta es la cuenta
  contable de la propia línea de pago) — sin ella, error genérico *"Debe
  ingresar la imputación contable"* que **bloquea la validación de
  `MedioPago` por completo** (no se llega a saber si el código es válido
  hasta resolver esto primero).

**Proveedores de prueba usados:** `LITORALG` tiene `CuentasCorrientes: []`
(proveedor viejo, de antes del fix "cuenta 0") — inútil para probar
`OrdenesPago` real. `SUPERCOO` sí tiene `CuentasCorrientes: [{ImputacionContable: 211001, PorDefecto: true}]`
confirmado — usado para todas las pruebas de este catálogo.

---

## ⛔ ACTUALIZACIÓN 2026-07-21 — "no existe para aplicarlo" CONFIRMADO como limitación de BAS, no de Invoicy (~37 variantes probadas en total, ninguna resuelve)

Nota: la nota de arriba sobre `LITORALG` ya quedó obsoleta -- el fix de
"cuenta 0" del mismo día (`utils/bas.py:asegurar_cuenta_corriente_proveedor`)
reparó su `CuentasCorrientes` automáticamente al re-procesar una factura real
de ese proveedor. `LITORALG` es hoy un proveedor sano, usado en las pruebas
de esta sección.

**Contexto:** con el bug de "cuenta 0" (proveedor sin cuenta contable) y el
bug de "fecha anterior al cierre del subdiario" (`Fecha` = fecha del
documento en vez de fecha de registración) ya resueltos ese mismo día, la
factura de Litoral Gas se registró con éxito (`MA 00001-00021885`, verificado
con `GET /api/ConsultaComprobantes`, `Anulado: false`) pero **crear la Orden
de Pago sigue fallando** con el mismo error ya documentado arriba (2026-07-01):

```
"El comprobante MA 00001-00021885 no existe para aplicarlo.
(SP_ICR_COMPROB_APL)(SP_ICR_COMPROB_CAJA)"
```

Esta sección agrega **evidencia nueva, no probada en la investigación de
2026-07-01**, que cierra el caso: **esto no es resoluble desde el payload de
Invoicy.**

### Experimento 1 — Fecha dentro de `ComprobantesAplicados`

Se probó explícitamente lo que la investigación de julio dejó "no probado con
el valor correcto": agregar `Fecha` al ítem de `ComprobantesAplicados`, pero
usando el valor **real y verificado** que devuelve `GET /api/ConsultaComprobantes`
para ese comprobante (no un valor inventado). Mismo resultado exacto, con y
sin el campo:

```
FALLO: 409 {'title': 'El comprobante MA 00001-00021885 no existe para
aplicarlo.(SP_ICR_COMPROB_APL)(SP_ICR_COMPROB_CAJA)', 'status': 409}
```

**Descubrimiento colateral:** `GET /api/ConsultaComprobantesExternos` y
`GET /api/ConsultaComprobantes` no necesariamente devuelven el mismo valor de
`Fecha` para el mismo comprobante -- la primera parece influenciada por el
parámetro `fecha_externo` de la consulta, no por el valor realmente
persistido. `ConsultaComprobantes` (sin parámetro de fecha) es la fuente de
verdad. No cambia la conclusión de esta sección, pero es una trampa a evitar
en cualquier verificación futura.

### Experimento 2 — Endpoint dedicado `POST /api/AplicacionesComprobantes`

El Swagger expone un endpoint separado, nunca antes probado contra BAS real,
pensado específicamente para "aplicar" un comprobante ya existente contra
otro (schema `Entidadesv2.Varias.AplicacionComprobante`) -- listado como
"Alternativa 2" en la sección de arriba pero nunca ejecutado. Se probó de
punta a punta:

1. Se creó una Orden de Pago **suelta**, sin `ComprobantesAplicados` (un
   comprobante de pago sin aplicar todavía) → **201 real, sin error**:
   `IdTransaccion 274510`, `Comprobante: "OPG"`, `Prefijo 00001`, `Numero
   00034994`. Esto **confirma que el mecanismo de creación de OP en sí
   funciona perfectamente** -- el problema es específicamente la aplicación
   contra un comprobante existente, no la OP como tal.
2. Se intentó aplicar esa OP contra `MA 00001-00021885` vía
   `POST /api/AplicacionesComprobantes` (top-level `Comprobante: "OP"` —
   nota: la respuesta de creación usa el nombre largo `"OPG"`, pero el
   *código* de 2 caracteres que exige este endpoint es `"OP"`, mismo patrón
   ya visto con `"FAC A"` vs `"MA"`). Primero pidió `ImputacionContable`
   (`409 "Debe indicar la imputación contable.(SP_ICR_APLICACIONES)"` —
   resuelto agregando `211001`, la misma cuenta ya usada para proveedores).
   Con eso resuelto, el resultado final fue:

```
409 {'title': 'El comprobante MA 00001-00021885 no existe para
aplicarlo.(SP_ICR_COMPROB_APL)(SP_ICR_APLICACIONES)', 'status': 409}
```

**Esto es la prueba decisiva:** `SP_ICR_COMPROB_APL` es el mismo
procedimiento interno que falla sin importar si se llega a él desde
`OrdenesPago` (con `ComprobantesAplicados` embebido) o desde
`AplicacionesComprobantes` (endpoint dedicado). No es un problema del
endpoint elegido ni de cómo arma el payload Invoicy -- es el propio SP el que
no puede resolver la aplicación en esta instalación.

### Experimento 3 — Control con un comprobante 100% limpio

Para descartar cualquier particularidad de Litoral Gas (fecha vieja, CAE
real, proveedor recién reparado), se repitió el intento de aplicación contra
`MA 00001-00021884` -- un comprobante creado ESE MISMO DÍA, con `Fecha` de
hoy, CAE de prueba (no real), proveedor `SUPERCOO` con cuenta sana desde
hace 3 días. **Mismo error, letra por letra.** Esto descarta con evidencia
directa que el problema dependa del proveedor, la fecha del documento, o si
el CAE es real o de prueba.

### Conclusión y recomendación

Con dos investigaciones independientes (2026-07-01 y 2026-07-21) y ~37
variantes de payload distintas probadas en total -- formatos de número,
`ImputacionContable` en 3 lugares distintos, `Fecha`/sin `Fecha`, ambos
endpoints de aplicación, `Importe` negativo, monedas, medios de pago, un
comprobante recién creado y limpio vs. uno real -- **ninguna cambia el
resultado.** `SP_ICR_COMPROB_APL` (y su variante `SP_ICR_APLICACIONES`)
rechaza sistemáticamente la aplicación de cualquier comprobante de compra en
esta instalación de BAS (`PLATINUM_TEST`), consistente con la hipótesis ya
planteada en julio: es un caso no cubierto por esos SPs específicos de la
capa de integración REST/ICR (podrían no reflejar la misma lógica que usa la
interfaz de escritorio clásica de BAS).

**Esto ya no es investigable más a fondo desde el lado de Invoicy.**
Recomendación: escalar al equipo/soporte de BAS con la evidencia exacta de
esta sección (en particular el Experimento 2, que aísla el problema al SP
mismo, independiente del endpoint). Mientras tanto, `utils/bas.py` detecta
esta firma de error (`_es_error_no_resoluble_desde_cliente`) y la marca
como `_requiere_soporte_bas` para que el mensaje mostrado al usuario diga
explícitamente "no reintentar, escalar a BAS" en vez de invitar a reintentos
que no van a cambiar el resultado.

**Efecto colateral de estas pruebas (documentado, no limpiado):** quedó una
Orden de Pago suelta y sin aplicar en BAS real (`IdTransaccion 274510`,
`OPG 00001-00034994`, Total=1, proveedor `LITORALG`, sin aplicar contra
ninguna factura). No se intentó anular/revertir -- requiere que el usuario
decida (misma política que el resto de las escrituras reales de esta
investigación).
