# Incidente: pagos automáticos a BAS quedaban registrados solo por el neto (sin IVA)

**Fecha:** 2026-08-03/04
**Severidad:** Alta (dinero real, ERP de producción) -- sin pérdida de dinero (el sistema nunca pagó de más ni de menos: cuando fallaba, cortaba antes de aplicar mal el pago), pero bloqueaba el pago automático de cualquier factura real con IVA y generó Órdenes de Pago huérfanas en BAS que requieren reconciliación manual.
**Estado:** Resuelto y desplegado a producción. Verificado de punta a punta con una factura real de prueba (Total=\$0.99, IVA 10,5%) pagada automáticamente por el bruto completo.

---

## Resumen ejecutivo

`crear_orden_pago` (Invoicy) es el único endpoint del sistema que efectivamente mueve dinero real contra BAS (el ERP). Al intentar pagar automáticamente una factura real con IVA (MEDINA FLOR LUCIO DANIEL, 00003-00000021), BAS rechazó la aplicación del pago con *"el saldo del vencimiento no puede ser negativo"*, dejando una Orden de Pago creada en BAS pero sin aplicar (huérfana). La investigación reveló dos causas raíz independientes, ambas confirmadas con pruebas reales contra la API de BAS (siempre con Total=1 peso, respetando la regla de seguridad del proyecto):

1. **Nunca se mandaban los campos `TotalIva`/`ImporteIva`** del schema real de BAS -- sin ellos, el comprobante quedaba registrado siempre por el neto, sin importar cuánto fuera el IVA real de la factura.
2. **Las fechas que se le mandan a BAS se calculaban en UTC**, no en el huso horario de Argentina donde corre BAS -- durante una ventana de ~3 horas por día esto rompía la aplicación del pago con un error de fecha, sin relación con el monto.

Ambas causas están corregidas y desplegadas a producción (Invoicy, ticket-ai-infra y ticket-ai-dashboard).

---

## 1. Causa raíz original

### 1.1 — Falta de `TotalIva` / `ImporteIva`

El schema real de BAS (`/swagger/v1/swagger.json`, endpoint `POST /api/ComprobantesCompra`) define, además de `Total` y `TotalGravado` (que sí se usaban), dos campos que **nunca se habían usado en este código**:

- `ComprobanteCompra.TotalIva` -- "Importe total Iva" (cabecera).
- `Item.ImporteIva` -- "Importe del IVA" (por línea).

BAS valida `Total` contra la suma de sus propios totales parciales de cabecera (`TotalGravado + TotalIva + ...`), **no** contra una suma re-derivada de los ítems. Como `TotalIva` nunca se mandaba (quedaba en 0/null), `Total` quedaba matemáticamente forzado a ser igual a `TotalGravado` -- es decir, siempre el neto, sin importar qué se mandara en `Items[].ImporteTotal` o en cualquier otro campo.

Consecuencia: el comprobante (`ComprobanteCompra`) se registraba correctamente, pero el **vencimiento** asociado quedaba con saldo igual al neto de la factura. Cuando después se intentaba **aplicar** el pago por el monto bruto real (con IVA), BAS rechazaba la aplicación (`AplicacionesComprobantes`) con:

```
"El saldo del vencimiento del [fecha] del comprobante [...] no puede ser negativo."
(SP_GENEROASI)(SP_VALIDA_APLICACIONES)(SP_ICR_APLICACIONES)
```

En ese punto la Orden de Pago **ya existía en BAS** (primera escritura, exitosa) pero sin aplicar (segunda escritura, fallida) -- el flujo de dos pasos no es atómico, así que queda una OP real, huérfana, que hay que reconciliar a mano.

### 1.2 — Fechas calculadas en UTC, no en huso argentino

`datetime.date.today()` / `datetime.datetime.utcnow()`, sin especificar zona horaria, toman la del sistema del contenedor -- que corre en **UTC**, no en la hora de Argentina donde efectivamente corre el servidor de BAS. Al intentar aplicar un pago, BAS compara **su propio reloj de servidor** (en hora argentina) contra la `Fecha` del comprobante que se le mandó. Si el comprobante se registró con una fecha que, para BAS, todavía es "el día siguiente", la aplicación se rechaza con:

```
"La fecha de la aplicación debe ser igual o superior a la de los comprobantes que se están aplicando (DD/MM/YYYY)."
(SP_GENEROASI)(SP_VALIDA_APLICACIONES)(SP_ICR_APLICACIONES)
```

---

## 2. Por qué el problema solo aparecía en determinadas condiciones

### 2.1 — El bug de `TotalIva` no se notaba con facturas sin IVA real

Mientras `Total` y `TotalGravado` se mandaran iguales (ambos al neto), el comprobante se registraba sin ningún error -- BAS lo acepta perfectamente, porque desde su punto de vista es un comprobante 100% consistente (0 IVA declarado). El problema **solo se manifestaba al intentar aplicar un pago por un monto mayor al neto** (es decir, el bruto real, con IVA) contra un vencimiento que solo podía registrarse en neto. Con facturas de prueba donde `monto` nunca superó el neto (por casualidad, o porque las pruebas anteriores no involucraban IVA real), el bug quedó invisible durante semanas.

Además, el diagnóstico inicial fue engañoso: en la primera prueba real con un monto "bruto" artificial, el error obtenido (*"la suma de los vencimientos no coincide con el total del comprobante"*) parecía indicar que `Total` se validaba contra `Vencimientos`, no contra `TotalIva`. Recién con una prueba que usaba una alícuota real distinta de la asumida (10,5% real vs. la hipótesis de que alcanzaba con ajustar `Vencimientos`) apareció el error real (*"no coincide con la suma de los totales parciales"*), que llevó a inspeccionar el schema completo de BAS y encontrar `TotalIva`/`ImporteIva`.

### 2.2 — El bug de huso horario solo aparecía ~3 horas por día

Argentina es UTC-3. Entre las **00:00 y las 03:00 UTC** (equivalentes a las 21:00-23:59 del día anterior en Argentina), el contenedor -- que calcula "hoy" en UTC -- ya considera que es un día que, para el reloj real de BAS, todavía no llegó. Fuera de esa ventana (el otro ~87.5% del día), `datetime.date.today()` y la fecha real de Argentina coinciden, y el bug no se manifiesta. Por eso nunca apareció en las pruebas anteriores de esta integración (todas corrieron en horario diurno/vespertino de Argentina) y recién se detectó en una prueba que, por casualidad, corrió pasada la medianoche UTC.

---

## 3. Cambios realizados

1. **`ImporteIva` por línea + `TotalIva` de cabecera**, calculados con la alícuota real de IVA de la factura (`invoices.iva_alicuota`, extraída por Gemini -- antes se calculaba pero se descartaba). `Total = TotalGravado + TotalIva`. Sin alícuota conocida (factura sin IVA, exenta, o con múltiples tasas que no se pueden resolver a una sola), cae a 0 -- mismo comportamiento histórico (neto puro), no se inventa una tasa.
2. **`CodigoItem` sigue sin depender de la alícuota** -- se confirmó real que BAS no cruza `ImporteIva`/`TasaIva` contra la tasa configurada del catálogo para el `CodigoItem` elegido. Se probó explícitamente un `CodigoItem` de 21% con `TasaIva`/`ImporteIva` de 10,5% real y BAS lo aceptó sin problema.
3. **`fecha_hoy_bas()`** (`utils/bas_config.py`, usa `zoneinfo.ZoneInfo("America/Argentina/Buenos_Aires")`) reemplaza todo `datetime.date.today()`/`datetime.datetime.utcnow()` que alimenta un campo `Fecha` que viaja a BAS. Los timestamps internos propios (`deleted_at`, `requested_at`, logs) siguen en UTC a propósito -- no se tocaron.
4. **Gate financiero actualizado**: `validar_monto_aplicable_vs_neto` ahora compara contra `total_bruto` (neto + IVA real) en vez de siempre el neto -- colapsa al mismo comportamiento de antes cuando no hay alícuota resuelta.
5. **Protección contra una tercera OP huérfana**: si una factura ya tiene un intento real fallido contra BAS (una OP creada pero sin aplicar), `crear_orden_pago` ahora bloquea con 409 y exige reconciliación manual en vez de reintentar a ciegas -- porque no hay forma de verificar desde Invoicy con qué importe quedó realmente registrado el vencimiento de un comprobante ya existente en BAS (la API de lectura de BAS no lo expone).
6. **`validar_alicuota_iva`** (rango 0-27%) agregada al gate de validaciones -- antes nada acotaba ese valor, que se escribe directo en el registro contable real de BAS.
7. **Guardas de tipo** en `crear_orden_pago` para `iva_alicuota` (evita un 500 sin manejar si el dato viene corrupto) y en `PocketBaseClient.obtener_categoria_map` (una fila con dato corrupto ya no invalida toda la colección).
8. **Migraciones de PocketBase**: `bas_category_map.alicuota` (con las variantes de tasa reales del catálogo de BAS: 10,5%/5%/exento donde existen) e `invoices.iva_alicuota`.
9. **Dashboard**: campo "Alícuota IVA" visible/editable en la revisión de facturas.
10. Limpieza: import muerto, comentarios desactualizados/contradictorios, scripts de referencia actualizados al patrón nuevo.

---

## 4. Archivos modificados

**Invoicy** (commit `b2f30ae`, sobre `328331a` y `f168d2a` de la misma investigación):
- `routes/process_invoice_google_2.py` -- `_extraer_alicuota_iva` (nueva), `items_bas` en `procesar_factura_en_bas` y `crear_orden_pago`, protección de 3ra OP huérfana, `fecha_hoy_bas()`.
- `utils/bas.py` -- default de `fecha` en `crear_orden_de_pago_desde_factura`.
- `utils/bas_config.py` -- `ZONA_HORARIA_BAS`, `fecha_hoy_bas()`.
- `utils/validaciones_pre_bas.py` -- `validar_alicuota_iva`, tolerancia/mensajes de `validar_monto_aplicable_vs_neto`, `validar_fecha_emision` con huso argentino.
- `utils/pocketbase_client.py` -- `obtener_categoria_map` (alícuota por fila, aislamiento de errores por fila).
- `scripts/test_crear_comprobante_compra.py`, `scripts/test_crear_orden_pago_desde_factura.py` -- actualizados al patrón nuevo (`ImporteIva`/`TotalIva` explícitos, `fecha_hoy_bas()`).

**ticket-ai-infra**:
- `pocketbase/pb_migrations/1783483945_add_alicuota_to_bas_category_map.js` (commit `5264849`).
- `pocketbase/pb_migrations/1783483955_add_iva_alicuota_to_invoices.js` (commit `5264849`).
- 5 migraciones sincronizadas desde producción, sin relación con este fix pero que no estaban comiteadas (commit `0a65d9f`).

**ticket-ai-dashboard** (commit `21b9ced`):
- `lib/pocketbase-types.ts` -- `InvoicesRecord.iva_alicuota`, `BasCategoryMapRecord.alicuota`.
- `app/(dashboard)/invoices/[id]/InvoiceReviewForm.tsx` -- campo editable.
- `app/(dashboard)/invoices/[id]/page.tsx` -- campo visible en la vista de factura confirmada.

Commits relacionados, previos a este fix pero parte de la misma investigación/incidente: `9898216`, `341a0ab`, `c3127ad` (bloqueo de "tiene IVA" en cola de revisión: se agregó y se sacó el mismo día, ver sección de lecciones aprendidas) y `89dda25`, `61ba4ff` (equivalentes en dashboard).

---

## 5. Cómo reproducir el problema anterior

Sobre el código previo a `b2f30ae`/`5264849`/`21b9ced` (o revirtiendo estos commits):

1. Crear una factura de prueba con un ítem real y una alícuota de IVA real distinta de 0 (ej. subtotal=0.90, IVA 10,5%=0.09, total=0.99 -- respetar la regla de Total=1 peso para cualquier prueba real).
2. Confirmarla y llamar a `crear_orden_pago` sin pasar `monto` explícito (usa el default, `invoice.total`, el bruto).
3. **Resultado esperado del bug**: el comprobante se registra bien (201 real), pero la aplicación del pago falla con 409 *"el saldo del vencimiento no puede ser negativo"* -- queda una Orden de Pago real en BAS, creada pero sin aplicar.
4. Para el bug de fecha específicamente: repetir el mismo flujo (con el fix de `TotalIva` ya aplicado, o mandando el comprobante+aplicación en un solo script como los de `scripts/`) durante la ventana 00:00-03:00 UTC. Sin `fecha_hoy_bas()`, la aplicación falla con 409 *"la fecha de la aplicación debe ser igual o superior a la de los comprobantes que se están aplicando"*, sin relación con el monto.

No reproducir esto contra el BAS real de producción sin la regla de Total=1 peso -- cada intento fallido dentro de este flujo dos-pasos deja una Orden de Pago huérfana real que hay que reconciliar a mano.

---

## 6. Cómo validar que quedó solucionado

Validación mínima (una sola llamada real, Total=1 peso, con el código actual):

1. Crear una factura de prueba confirmada, con `iva_alicuota` seteada a una tasa real distinta de 0 (ej. 10.5), y `total` = neto × (1 + alícuota/100).
2. Llamar `POST /gemini2/payment-orders/{process_id}/create` (mismo endpoint que usa el dashboard).
3. **Resultado esperado**: HTTP 200, `payment_order.status == "success"`, `payment_order.monto` == el bruto completo de la factura, sin `bas_error`.
4. Verificar que no quedó ninguna Orden de Pago huérfana asociada (`payment_orders` con `status != "success"` y `bas_op_prefijo` seteado para ese `process_id`).

Esto se ejecutó y confirmó **tres veces** durante el desarrollo de este fix: una vez contra el entorno local, y una vez contra producción real (proveedor de prueba, Total=\$0.99, `status: "success"`, sin errores). Los datos de prueba de las tres corridas ya fueron borrados.

Validación adicional recomendada la primera vez que se reprocese una factura real con IVA (no de prueba): confirmar en el dashboard que el campo "Alícuota IVA" tiene el valor correcto antes de generar el pago -- si Gemini no lo extrajo bien, corregirlo a mano ahí mismo.

---

## 7. Riesgos conocidos y limitaciones

- **Tres Órdenes de Pago huérfanas reales, sin reconciliar**, todas de antes de este fix:
  - `00001-00035009` y `00001-00035010` -- factura real MEDINA FLOR LUCIO DANIEL (00003-00000021).
  - `00001-00035011` -- de una prueba propia durante la investigación (comprobante de prueba, Total=\$1, proveedor SUPERCOOP).
  Ninguna se reconcilió automáticamente ni se va a reconciliar sola -- requieren intervención manual directa en BAS por alguien con acceso al ERP.
- **`crear_orden_pago` ahora bloquea el reintento** sobre cualquier factura con un intento real previo fallido (incluida MEDINA) -- es la protección nueva contra una cuarta OP huérfana, pero significa que **esas facturas específicas no se pueden volver a intentar desde el dashboard** hasta que alguien reconcilie la OP huérfana correspondiente en BAS.
- **La extracción de la alícuota depende de un match exacto** (`tipo == "IVA"`, sin variantes) contra lo que devuelve Gemini. Si Gemini alguna vez extrae `"IVA 21%"`, `"I.V.A."` u otra variante en el campo `tipo`, la alícuota no se detecta y la factura cae al comportamiento histórico (neto) sin ningún error visible más que un log informativo (`_extraer_alicuota_iva: N impuesto(s)... cae a neto`). No se verificó contra logs reales de producción qué strings devuelve Gemini en la práctica.
- **La selección de `CodigoItem` no usa la alícuota real** -- toda la infraestructura para hacerlo (`resolver_item_bas`, la columna `alicuota` en `bas_category_map`) está construida pero no conectada a ningún llamador de producción, a propósito (confirmado que no hace falta para que el monto/IVA salga bien, pero puede importar para la clasificación contable interna dentro de BAS).
- **La función de importación masiva de facturas** (`import_batches`/`import_batch_items`, commits previos en Invoicy/ticket-ai-infra) sigue sin desplegar a producción -- sin relación con este fix, ya identificado en una sesión anterior.
- **Una migración de producción con una contraseña real en texto plano** (`1783483885_reset_equipo_password.js`) quedó deliberadamente fuera del repo -- no afecta este fix, pero sigue siendo deuda técnica de seguridad (debería reescribirse para leer de una variable de entorno si algún día hace falta trackearla).

---

## 8. Qué monitorear durante los próximos días

- **Logs de `invoice-api-bas`** buscando:
  - `_extraer_alicuota_iva: N impuesto(s) extraído(s), ninguno con tipo='IVA' exacto` -- indica una factura real donde la alícuota no se pudo detectar pese a haber impuestos extraídos (posible variante de string de Gemini no cubierta).
  - Cualquier 409 de BAS en `crear_orden_pago` (`grep "crear-orden-pago"` / `"fallo BAS"`) -- si vuelve a aparecer *"saldo del vencimiento no puede ser negativo"* o *"la fecha de la aplicación..."*, el fix tiene un caso no cubierto.
  - El mensaje nuevo *"ya tiene un intento real fallido contra BAS"* -- confirma que la protección contra 3ra OP huérfana se está disparando (esperado para MEDINA si alguien intenta reprocesarla sin reconciliar antes).
- **La colección `payment_orders`** en PocketBase: cualquier registro nuevo con `status = "failed"` y `bas_op_prefijo` seteado es una OP huérfana nueva -- requiere la misma reconciliación manual que las tres ya conocidas.
- **Facturas reales que se confirmen esta semana**: revisar en el dashboard que "Alícuota IVA" tenga un valor razonable (no vacío para facturas que claramente tienen IVA, no un número absurdo) antes de que alguien genere el pago.
- **El campo `bas_category_map`** en el dashboard (`/category-map`): si alguien agrega una categoría nueva ahí, hoy el formulario no expone el campo `alicuota` (gap conocido, no bloqueante porque `CodigoItem` no depende de la alícuota en producción) -- una fila nueva sin alícuota explícita puede quedar en 0 por default, lo cual está bien mientras nadie dependa de ese valor para otra cosa.

---

## Lecciones aprendidas

**¿Por qué existen `TotalIva` e `ImporteIva`, y por qué no alcanza con `Total`/`TotalGravado`?**

BAS modela el IVA de una compra como una imputación contable separada del gasto neto (cuenta "IVA Crédito Fiscal", código 112101) -- pero para que la cabecera del comprobante (`Total`) refleje el monto bruto real que efectivamente se le debe al proveedor, hay que declararle a BAS explícitamente cuánto de ese total es IVA (`TotalIva`, y su desglose por línea, `ImporteIva`). Sin esos dos campos, `Total` queda matemáticamente atado a `TotalGravado` (el neto) -- no es una limitación de BAS, es que **el dato nunca se le mandaba**. Si en el futuro alguien "simplifica" el payload sacando `TotalIva`/`ImporteIva` porque "total_gravado ya alcanza" o porque parecen redundantes con `Total`, va a reproducir exactamente este incidente: el comprobante se va a seguir registrando sin error (BAS no exige estos campos), pero cualquier intento de aplicar un pago por el monto bruto real va a volver a fallar con "saldo del vencimiento no puede ser negativo", y cada intento fallido deja una Orden de Pago huérfana real en BAS.

**¿Por qué el huso horario de Argentina es explícito (`fecha_hoy_bas()`) en vez de usar `datetime.date.today()`?**

Porque el servidor de BAS valida ciertas reglas de negocio (como la fecha de aplicación de un pago) contra **su propio reloj**, en hora de Argentina -- sin importar qué `Fecha` le mandemos nosotros en el payload. Un contenedor Docker corre en UTC salvo que se le diga lo contrario explícitamente, así que `datetime.date.today()` da un resultado *distinto* al de Argentina durante ~3 horas todos los días (00:00-03:00 UTC). Este bug es especialmente peligroso porque es **intermitente y depende de la hora exacta en que corre el código** -- una prueba manual hecha de tarde o de noche temprano en Argentina nunca lo va a reproducir, dando una falsa sensación de que "ya está probado y funciona". Si en el futuro alguien reemplaza `fecha_hoy_bas()` por `datetime.date.today()` "para simplificar" o porque en sus pruebas locales no nota diferencia, el bug vuelve a aparecer -- silencioso, intermitente, y solo visible en producción en el peor momento (justo cuando alguien intenta pagar una factura real de madrugada, hora Argentina).

**Regla general para el futuro:** cualquier campo que se le mande a BAS y dependa de "ahora" (sea fecha, o cualquier otro dato que BAS pueda validar contra su propio estado interno) hay que tratarlo con la misma sospecha con la que se trató este incidente -- no asumir que el campo es cosmético solo porque el schema no lo marca como `required`. Los tres bugs reales de esta integración hasta la fecha (el de `TotalGravado`, el de `TotalIva`, y el de fecha) fueron los tres del mismo tipo: un campo que el schema no exige explícitamente, pero que BAS sí valida por dentro contra otro dato que nunca se le mandó.

---

## 9. Actualización 2026-08-04 (posterior): se quitó el gate `validar_monto_aplicable_vs_neto`

Horas después del fix, una factura real (proveedor distinto de MEDINA, monto solicitado \$64.999,35) volvió a mostrar el mensaje de este gate ("BAS solo admite aplicar... \$53.718,47"). Investigación con evidencia de código (no de suposición) encontró que **el gate nunca consultaba a BAS**: `total_registrado` era `total_bruto`, calculado por Invoicy mismo en `crear_orden_pago` (`routes/process_invoice_google_2.py`, a partir de `invoice_items` + `iva_alicuota` de PocketBase) **antes** de que el flujo llamara por primera vez a BAS (`crear_orden_de_pago_desde_factura`, ~100 líneas más abajo). El texto del mensaje ("el importe con el que se registró") afirmaba algo que el código nunca había verificado.

**Decisión:** se quitó `validar_monto_aplicable_vs_neto` por completo (definición en `utils/validaciones_pre_bas.py`, import y llamada en `routes/process_invoice_google_2.py`). El cálculo de `total_bruto`/`total_gravado`/`total_iva` **no se tocó** -- sigue siendo necesario para armar `comprobante_compra_payload` (Total/TotalGravado/TotalIva/Vencimientos) que se manda a BAS.

**Qué cambia en el comportamiento:**
- Antes: si `monto` (bruto) superaba el `total_bruto` que Invoicy calculaba localmente, `crear_orden_pago` cortaba con 422 **sin llamar a BAS**.
- Ahora: `crear_orden_pago` llama siempre a BAS. Si el comprobante ya existía (registrado con un importe distinto al que Invoicy hubiera calculado hoy) o si por cualquier motivo el monto no cierra contra el vencimiento real, **BAS crea la Orden de Pago real y recién en el paso de aplicación la rechaza** (típicamente con "saldo del vencimiento no puede ser negativo"). Ese caso ya estaba manejado antes de este gate y sigue manejado igual: se detecta vía `_op_sin_aplicar` (ver `crear_orden_de_pago_desde_factura` en `utils/bas.py`), se reporta como error explícito, y **queda una OP huérfana real en BAS que requiere reconciliación manual** -- este gate era, en la práctica, el único mecanismo que evitaba llegar a ese punto para este caso específico.
- La protección que sí sigue activa: el bloqueo de reintento sobre un `process_id` que ya tiene un intento real fallido con `bas_op_prefijo` (mensaje "ya tiene un intento real fallido contra BAS") -- esa validación no se tocó, porque no es una suposición: se basa en un `payment_order` propio que registra una OP real que efectivamente falló.

**Riesgo que queda abierto:** cualquier factura cuyo comprobante en BAS ya esté registrado por un importe menor al que se intenta aplicar (por ejemplo, comprobantes registrados antes de este fix, o con alícuota mal extraída) va a generar una OP huérfana real nueva en el primer intento, no en el segundo -- ya no hay nada del lado de Invoicy que lo prevenga antes de escribir en BAS. La forma de saberlo sin adivinar es la misma que hubiera resuelto esto desde el principio: consultar `consultar_comprobante_externo` (GET real a BAS) antes de aplicar, y comparar `monto` contra el `Total`/`Vencimientos` que BAS devuelve -- eso no está implementado; por ahora BAS es la única fuente de verdad sobre el importe registrado, y su rechazo (con OP huérfana) es la señal.
