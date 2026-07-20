# Plan de implementación: frontend del flujo "Crear Orden de Pago"

**Fecha:** 2026-07-20. **Basado en:** `Invoicy/docs/flujo-crear-orden-pago-documentacion-completa.md`
(2026-07-18). **Alcance:** solo planificación — sin código todavía.

**Estado de partida importante:** el frontend actual (`PaymentOrderPanel.tsx`,
`PaymentOrderMission.tsx`, `use-mission-choreography.ts`, `lib/payment-order-mission.ts`,
`PaymentOrdersTable.tsx`) **ya implementa correctamente** la mayor parte del flujo de 6
pasos, incluyendo el paso 4 nuevo ("verificación posterior") y el manejo de `payment_orders`
como colección separada de `bas_processing_status`. Esto NO es una reconstrucción desde
cero — es una lista de gaps concretos contra el documento fuente. Los enumero por sección
del documento para trazabilidad.

---

## 0. Resumen de gaps encontrados (lectura completa del código actual vs. el doc)

| # | Gap | Sección del doc | Severidad |
|---|---|---|---|
| G1 | No existe endpoint de retry parcial en el frontend — "Reintentar" llama al mismo `create`, no a `/retry-op/{process_id}` | §8, §9 | Alta (cuando se resuelva el bloqueo 409, retry desde cero re-ejecutará TODO el flujo incluyendo re-registrar el comprobante, cuando la factura ya quedó registrada) |
| G2 | `inferMissionOutcome` mapea errores por **substring matching** frágil, sin cubrir todos los formatos documentados (ej. el bug de trazabilidad del `path` mal etiquetado en §5/§7, ni el caso "conflicto de negocio" 409 con mensajes no-"cuenta 0" en `ComprobantesCompra`) | §6, §7, §9 | Alta |
| G3 | El estado `payment_orders.status` no distingue "bloqueado permanentemente" (caso §8 actual: BAS rechaza estructuralmente, no es un error transitorio) de "falló, reintentable" — la UI trata todo `failed` igual | §8, §13 | Media |
| G4 | No hay manejo explícito del caso "200 siempre, pero excepción no capturada perdió `status_code`/`path`" (§9, "Limitaciones") — cuando `error` no matchea ningún patrón conocido, cae silenciosamente al paso 2 | §9 | Media |
| G5 | El botón de pago no valida client-side `bas_cuenta_bancaria` antes de enviar si `metodo_pago==="transferencia"` — se descubre recién en el 422 del backend | §9 (paso 7) | Baja (UX, no funcional) |
| G6 | `PaymentOrdersTable` no tiene refresh/polling — una orden en `processing` que se resuelve del lado del backend después de cargar la tabla no se refleja sin recargar manualmente | §11 | Baja/Media (aumenta con volumen) |
| G7 | No hay superficie de UI para el hallazgo de PocketBase "creación sin `invoice` pierde estado silenciosamente" (§11) — no es bug de frontend, pero el frontend no tiene forma de detectar/alertar si una orden "desapareció" | §11 | Baja (monitoreo, no bloqueante) |
| G8 | La demo (`PaymentOrderMissionDemo.tsx`) tiene 6 escenarios pero le falta el escenario "bloqueo estructural §8" (409 "no existe para aplicarlo") como caso demostrable distinto de `ordenFallo` genérico | §8 | Baja |

Ninguno de estos gaps es un blocker para que la UI "funcione" hoy — el flujo end-to-end ya
corre y muestra bien los 6 pasos. Son gaps de **robustez, fidelidad de mensajes y manejo del
día en que se resuelva el bloqueo de §8**, que es la pieza que más va a cambiar el
comportamiento esperado del frontend a futuro.

---

## 1. Pantallas, componentes y flujos a modificar

### 1.1 `lib/payment-order-mission.ts` (núcleo de lógica, sin JSX)
- Reescribir `inferMissionOutcome` como **tabla de reglas ordenada y explícita** en vez de
  cadena de `if` con substrings sueltos, para que cubrir un caso nuevo sea agregar una fila,
  no rastrear la función. Cada regla: `{match: (status, data) => boolean, failedStepIndex,
  reason}`.
- Agregar un `outcomeKind` adicional además de `success`/`failedStepIndex`/`detailText`:
  `"blocked"` — para el caso §8 (409 "no existe para aplicarlo"), que semánticamente NO es
  "fallo transitorio reintentable" sino "requiere intervención humana/backend". Se distingue
  por substring `"no existe para aplicarlo"` o, mejor, por un campo estructurado si se logra
  que el backend lo exponga (ver §3 más abajo, dependencia de backend).
- Mantener el fallback a `failedStepIndex: null` ("conexión perdida") solo para errores
  realmente no clasificables (502, excepción cruda, formato de respuesta no reconocido).

### 1.2 `hooks/use-mission-choreography.ts`
- Sin cambios estructurales grandes — el patrón de coreografía optimista es correcto para
  un endpoint 100% síncrono (confirmado en §9 del doc: "no hay streaming de progreso").
- Agregar soporte opcional para **dos modos de `run()`**: `create` (flujo actual) y `retry`
  (cuando exista G1) — mismo mecanismo de timers, pero permitiendo que el caller le pase el
  `liveStepIndex` inicial (para que un retry parcial arranque visualmente desde el paso que
  falló, no desde 0). Ver detalle en fase 3.

### 1.3 `PaymentOrderPanel.tsx`
- Agregar validación client-side de `bas_cuenta_bancaria` cuando `metodoPago==="transferencia"`
  antes de habilitar el botón (G5) — deshabilitar + mensaje inline en vez de esperar el 422.
- Cuando `outcomeKind==="blocked"` (nuevo), cambiar el copy del botón de "Reintentar" a algo
  como "Bloqueado — requiere revisión" y deshabilitarlo (o dejarlo pero con confirmación
  extra), para no invitar a reintentos infinitos contra un bloqueo estructural conocido
  (§8, "SIN RESOLVER").
- Cuando exista el endpoint de retry (G1, depende de backend), cambiar la llamada de
  "Reintentar" para usar `POST /api/payment-orders/{processId}/retry` en vez de reusar
  `create`. Mientras el backend no lo exponga, el frontend sigue como está (retry = create).

### 1.4 `PaymentOrderMission.tsx`
- Nuevo estado visual para pasos con `outcomeKind==="blocked"`: hoy solo hay
  `pending|active|done|error|skipped`. Agregar `blocked` con un color/ícono distinto de
  `error` (ej. ámbar/candado en vez de rojo/X) — comunica "esto no es un fallo tuyo, es un
  límite conocido del sistema", coherente con el copy que ya usa `ConnectionLostBanner` para
  un caso similar (conexión perdida).

### 1.5 `PaymentOrdersTable.tsx` / `page.tsx`
- Agregar refresh manual (botón) como mínimo viable para G6; polling automático solo si el
  volumen de "processing" concurrentes lo justifica (evaluar con datos reales, no
  premature).
- Exponer visualmente `retry_count` de forma más prominente cuando `status==="failed"` y
  `retry_count` es alto (ej. ≥3) — señal de que puede ser el bloqueo estructural de §8 y no
  un error transitorio.

### 1.6 `PaymentOrderMissionDemo.tsx`
- Agregar 7mo escenario: `ordenBloqueada` — 200 con `error` conteniendo
  `"no existe para aplicarlo"`, mapeado a `outcomeKind: "blocked"`, `failedStepIndex: 4`.
  Sirve como demo y como fixture de test.

---

## 2. Estados que debe manejar el frontend en cada paso

Usando los 6 pasos visuales (`MISSION_STEPS`) como eje, el estado real que el frontend debe
sostener (más allá de lo puramente visual) es:

| Paso | Estado de negocio que representa | De dónde sale (ya existe / falta) |
|---|---|---|
| 1. Validar factura | `review_status==="confirmed"` ya validado antes de mostrar el botón (el backend igual revalida) | Ya existe: se lee de `invoice` server-side antes de renderizar el panel |
| 2. Proveedor/medio de pago | `proveedor_codigo` y `bas_medio_pago_codigo` ya resueltos (lectura, no escritura, en este momento) | Ya existe parcialmente — falta la validación client-side de `bas_cuenta_bancaria` para transferencia (G5) |
| 3. Comprobante | Resultado del POST `ComprobantesCompra` (creado o ya existía) | Ya existe vía `outcome` |
| 4. Verificación post-registro | Resultado del GET `ConsultaComprobantes` | Ya existe vía matching de `"verificación posterior"` — frágil (G2) |
| 5. Orden de pago | Resultado del POST `OrdenesPago` — puede ser éxito, fallo transitorio, o **bloqueo estructural conocido** | Falta distinguir bloqueo estructural (G3) |
| 6. Confirmación | Derivado del mismo 201 del paso 5, no hay llamada propia | Ya existe (no requiere estado nuevo) |

Estado transversal a nivel de orden completa (no por paso):
- `order.status`: `processing | success | failed` — **ya existe**, ya modelado en
  `PaymentOrdersRecord`. Nota: PocketBase no tiene `pending` (confirmado en §11 del doc) —
  el frontend no debe intentar mostrar un estado "pending" separado de "processing".
- `isStaleProcessing`: **ya existe** en `PaymentOrderPanel` — cubre el caso de un
  `processing` que quedó colgado (crash a mitad de camino, §9 "Limitaciones": el endpoint es
  síncrono sin timeout propio).
- `retry_count`: **ya existe** en el tipo y se muestra en la tabla — falta usarlo para
  distinguir visualmente bloqueos recurrentes (1.5).
- Nuevo: `outcomeKind: "success" | "transient_error" | "blocked" | "connection_lost"` —
  reemplaza el binario implícito actual (`success` + `failedStepIndex===null` como único
  proxy de "conexión perdida"). Este es el cambio de modelo más importante del plan.

---

## 3. Integración de cada endpoint en el flujo (frontend)

El frontend **nunca habla directo con Invoicy ni con BAS** — todo pasa por el proxy Next.js
(§10 del doc). Esto ya está bien y no debe cambiar (el secreto `X-Invoicy-Secret` no debe
vivir en el cliente, regla explícita del doc). Los puntos de integración reales son:

1. **`POST /api/payment-orders/[processId]`** (ya implementado) — único punto de entrada
   hoy, usado tanto para "crear" como para "reintentar". Sin cambios estructurales salvo
   los de abajo.
2. **`POST /api/payment-orders/[processId]/retry`** (NUEVO, depende de que Invoicy exponga
   `/retry-op/{process_id}` como ruta real y no solo mencionada como posible en el doc §8 —
   **hay que confirmar con el equipo de backend si ese endpoint existe hoy o es aspiracional**,
   el doc dice *"`/retry-op/{process_id}` permite reintentar solo este paso"* dándolo por
   existente). Si existe: el frontend debe agregar esta ruta proxy (mismo patrón que la
   actual: validar sesión, agregar secreto, reenviar). Payload mínimo: `process_id`
   (de la URL), sin `metodo_pago`/`monto` (esos ya están persistidos en el `payment_order`
   existente, salvo que el backend permita cambiarlos en el retry — **a confirmar**).
3. **Lectura de `payment_orders`** (`PaymentOrdersTable`, `PaymentOrderPanel` vía
   `existingOrder`) — ya usa la colección correcta (§11 del doc, ya corregido). Sin cambios.
4. **Lectura de `bas_payment_methods`** para poblar el selector de método de pago y mostrar
   el aviso de "no confirmado" — ya existe.

No hay ningún endpoint de BAS (§3 a §8 del doc) que el frontend deba llamar directamente —
son 100% responsabilidad del backend Invoicy. El frontend solo necesita **interpretar
correctamente las formas de respuesta** que ese orquestador devuelve (la parte que cubre
G2).

---

## 4. Validaciones cliente vs. backend

| Validación | Dónde debe vivir | Motivo |
|---|---|---|
| `review_status==="confirmed"` | Backend (ya, revalida siempre — §9 regla de negocio explícita) + Frontend solo para UX (ocultar/deshabilitar el botón antes de tiempo) | El doc es explícito: "Revalida server-side — no confía en que el caller ya lo haya chequeado" |
| `metodo_pago` es uno de los valores permitidos | Backend (fuente de verdad, `METODO_PAGO_ARRAY_BAS`) + Frontend (el selector ya solo ofrece opciones válidas, así que es indirecto) | El proxy Next.js **no valida el valor**, solo el tipo (doc §10) — el frontend debe seguir sin duplicar esa lista, usar el `<select>` como guardrail natural |
| `bas_cuenta_bancaria` presente si `metodo_pago==="transferencia"` | **Frontend (nuevo, G5)** + Backend (ya lo hace, 422) | Hoy solo backend — agregar client-side es puro ahorro de un roundtrip, no reemplaza el backend |
| `monto` es número válido / no excede el total | Backend decide si acepta parciales (doc: "diseño que en teoría soporta pagos parciales... nunca se probó en vivo") — Frontend: mantener validación básica (número positivo, no mayor a `invoiceTotal` a menos que el negocio confirme que se permite pago parcial) | **No implementar UI de pago parcial todavía** — el doc marca esa capacidad como no verificada en producción por el bloqueo de §8. Evitar construir sobre una hipótesis no confirmada |
| Proveedor/medio de pago resuelto (`proveedor_codigo`, `bas_medio_pago_codigo`) | 100% Backend — el frontend no tiene forma de verificarlo de antemano, solo lo descubre en la respuesta (422) | Es estado interno de PocketBase que el panel no carga proactivamente hoy |
| Clasificación de errores devueltos (paso fallido, bloqueado, conexión perdida) | 100% Frontend (`inferMissionOutcome`) | El backend no devuelve un código de paso estructurado — el frontend hace best-effort sobre texto libre. **Esto es la mayor deuda técnica del sistema y excede el frontend** (ver recomendación en §6) |

---

## 5. Manejo de errores, loading y casos límite

### 5.1 Loading
- Ya cubierto: `loading` booleano + coreografía optimista de 750ms/paso + `slowHint` a los
  4s. No requiere cambios — es la solución correcta para un endpoint síncrono sin streaming
  (confirmado explícitamente por el doc: "No hay streaming de progreso del servidor").
- Único ajuste: si se agrega el endpoint de retry parcial (G1), la coreografía de un retry
  debería arrancar el `liveStepIndex` en el paso que falló la vez anterior, no en 0 — dar
  la sensación correcta de "continuar", no "repetir todo".

### 5.2 Errores
- **Error de red / proxy caído (502)** → ya cubierto por `ConnectionLostBanner`. Mantener.
- **Error de validación pre-BAS (404/409/422)** → ya cubierto, mapea a paso 0 o 1. Mantener,
  pero robustecer el matching de 422 (G2) para no depender de substrings de mensajes en
  español que el backend puede reformular sin aviso (`"proveedor_codigo"`,
  `"código BAS configurado"`, `"cuenta_bancaria"` — confirmar con backend si se puede pasar
  un campo estructurado, ej. `{detail, code: "PROVIDER_NOT_RESOLVED"}`, para dejar de
  parsear texto libre).
- **Error de negocio post-BAS (200 con `error` string)** → ya cubierto para 3 patrones
  conocidos. Agregar el 4to (bloqueo estructural §8) y dejar la función abierta a agregar
  más sin reescritura (tabla de reglas, 1.1).
- **Caso "excepción no capturada" del backend (G4)** — cuando el string de `error` no
  matchea ningún patrón conocido, hoy cae silenciosamente al paso 2. Cambiar el fallback:
  si no matchea nada, no asumir paso 2 — usar `failedStepIndex: null` (conexión
  perdida/error no clasificado) para no atribuir un fallo al paso equivocado y confundir al
  usuario. Es más honesto mostrar "no pudimos identificar en qué paso falló" que adivinar mal.
- **Orden colgada en "processing"** (`isStaleProcessing`) → ya cubierto. Confirmar copy: dado
  que el endpoint es síncrono, un `processing` persistente casi siempre significa que el
  proceso Python murió a mitad de camino (doc §9, "Limitaciones") — el mensaje debería
  sugerir reintentar directamente en vez de "esperar".

### 5.3 Casos límite
- **Doble click / múltiples requests concurrentes al mismo `processId`** — verificar que el
  botón se deshabilita mientras `loading===true` (revisar en implementación, no confirmado
  en la lectura de código si ya existe ese guard).
- **Navegación fuera de la página mientras la request está en vuelo** — como es un solo
  `fetch` sin AbortController visto en el reporte, confirmar si al desmontar el componente
  se cancela o se ignora la respuesta tardía (evitar `setState` en componente desmontado).
- **Pago parcial (`monto !== invoiceTotal`)** — no construir UI dedicada todavía (ver §4),
  dejar el campo como está (editable, default a `invoiceTotal`) sin prometer más de lo que
  el backend garantiza hoy.
- **Reintentos indefinidos contra el bloqueo §8** — con el estado `outcomeKind==="blocked"`
  nuevo, el frontend puede cortar el ciclo de "reintentar → mismo 409 → reintentar" que hoy
  es indistinguible de un error transitorio real.

---

## 6. Cambios estructurales/arquitectura recomendados

1. **Tipar el contrato de error del backend.** La causa raíz de casi todos los gaps (G2, G3,
   G4) es que Invoicy devuelve errores como texto libre en español y el frontend hace
   pattern-matching sobre ese texto. Esto es frágil por diseño y ya causó el bug de
   trazabilidad documentado en el doc (§5/§7: el campo `path` queda mal etiquetado). La
   recomendación de mayor apalancamiento, aunque **excede el scope de este repo frontend**,
   es pedir al backend un campo estructurado adicional en la respuesta 200 con error
   (ej. `{success: false, error: "...", failed_step: "comprobante" | "verificacion" |
   "orden_pago" | null, error_kind: "validation" | "business_conflict" | "blocked" |
   "unknown"}`). Mientras eso no exista, el frontend debe centralizar todo el parsing en
   `inferMissionOutcome` (ya es así) con la tabla de reglas explícita de 1.1, para que el
   día que el backend structure la respuesta, sea un cambio de una función, no de todo el
   árbol de componentes.
2. **Separar `outcomeKind` de `failedStepIndex`.** Hoy un solo campo (`failedStepIndex`,
   con `null` como comodín de "no sé qué pasó") carga dos señales distintas: *dónde* falló y
   *qué tipo* de fallo es (transitorio vs. estructural vs. no clasificado). Separarlos hace
   que la UI pueda tomar decisiones distintas (deshabilitar retry vs. invitarlo) sin overload
   semántico de un solo campo.
3. **No introducir polling/websockets todavía.** El doc es explícito en que el backend es
   síncrono de punta a punta — cualquier mecanismo de progreso en tiempo real del lado
   frontend sería cosmético, no reflejaría estado real del servidor, y agregaría complejidad
   (reconexión, cleanup) sin beneficio funcional. La coreografía optimista actual es la
   arquitectura correcta mientras el backend no cambie ese modelo.
4. **Mantener `lib/payment-order-mission.ts` libre de JSX/React** (ya es así) — permite
   testear `inferMissionOutcome`/`computeMissionSteps` con los 6+ escenarios enlatados de la
   demo como tests unitarios reales (hoy son solo demo visual; conviene promoverlos a
   fixtures de test, reduce el costo de robustecer G2 con confianza).
5. **Confirmar la existencia real de `/retry-op/{process_id}`** en Invoicy antes de
   construir G1 — es la única pieza del plan que depende de una confirmación externa
   (backend) que no pude verificar leyendo solo el frontend.

---

## 7. Orden de implementación por fases

**Fase 0 — Confirmación con backend (bloqueante para Fase 3, no bloquea el resto).**
- Confirmar si `/retry-op/{process_id}` existe hoy en Invoicy o es aspiracional.
- Confirmar si vale la pena pedir un campo `failed_step`/`error_kind` estructurado en la
  respuesta del orquestador (§6.1) — no bloqueante, pero determina cuánto vale la pena
  invertir en el parsing de texto vs. esperar el campo estructurado.

**Fase 1 — Robustecer clasificación de errores (sin cambios visuales grandes).**
- Reescribir `inferMissionOutcome` como tabla de reglas explícita (1.1).
- Agregar `outcomeKind` (`success | transient_error | blocked | connection_lost`).
- Cambiar el fallback de "no matchea nada" de paso-2-por-defecto a `connection_lost` (5.2,
  G4).
- Agregar el escenario `ordenBloqueada` a la demo (G8) — sirve de fixture visual para probar
  el cambio antes de tocar el panel real.
- Promover los escenarios de la demo a tests unitarios de `inferMissionOutcome`.

**Fase 2 — UI de bloqueo estructural (depende de Fase 1).**
- Nuevo estado visual `blocked` en `PaymentOrderMission.tsx` (1.4).
- Cambios de copy/deshabilitado de botón en `PaymentOrderPanel.tsx` cuando
  `outcomeKind==="blocked"` (1.3).
- Uso de `retry_count` en `PaymentOrdersTable` para resaltar posibles bloqueos recurrentes
  (1.5).

**Fase 3 — Validaciones client-side de bajo costo (independiente, puede ir en paralelo con
Fase 1/2).**
- Validación de `bas_cuenta_bancaria` para transferencia antes de habilitar el botón (G5).
- Confirmar/agregar guard de doble-click y cleanup en desmontaje (5.3).

**Fase 4 — Retry parcial real (depende de Fase 0 confirmando el endpoint).**
- Solo si Invoicy confirma `/retry-op/{process_id}`: agregar la ruta proxy
  `app/api/payment-orders/[processId]/retry/route.ts` (mismo patrón que `create`).
- Cambiar `PaymentOrderPanel` para usar `retry` en vez de `create` cuando
  `order?.status==="failed"`.
- Ajustar `useMissionChoreography` para arrancar la coreografía desde el paso fallido
  anterior en vez de 0 (1.2).

**Fase 5 — Refresh de tabla (menor prioridad, evaluar necesidad real primero).**
- Botón de refresh manual en `PaymentOrdersTable`/`page.tsx` (G6).
- Evaluar con datos reales de volumen si vale la pena polling automático — no implementarlo
  especulativamente.

**Explícitamente fuera de este plan (dependen de que se resuelva el bloqueo de negocio en
§8 del doc, que es un problema de backend/BAS, no de frontend):**
- UI de pago parcial (monto ≠ total de factura) — el doc marca esa capacidad como no
  verificada en producción.
- Cualquier variante de UI que asuma que `POST /api/OrdenesPago` puede devolver 201 — hoy
  "nunca logrado en vivo" según el doc (§8). El frontend debe seguir preparado para el 201
  (ya lo está, es el caso `success` existente) pero no se puede verificar en producción
  hasta que el bloqueo se resuelva del lado de BAS/Invoicy.

---

## 8. Resumen para decidir por dónde empezar

Si el objetivo es robustecer lo que ya existe sin esperar a backend: **Fase 1 → Fase 3 →
Fase 2**, en ese orden, son 100% ejecutables hoy mismo sin ninguna dependencia externa.

Fase 4 (retry parcial real) es la única pieza que no se puede arrancar sin antes confirmar
con el equipo de Invoicy si `/retry-op/{process_id}` existe — vale la pena resolver esa
pregunta primero (Fase 0) aunque sea en paralelo con las Fases 1-3.
