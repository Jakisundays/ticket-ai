# Documentación completa: flujo "Crear Orden de Pago" (Invoicy ↔ BAS ERP)

**Fecha:** 2026-07-18. **Alcance:** documentación exhaustiva, endpoint por endpoint, de
todo lo que corre por detrás del checklist gamificado de 6 pasos (`PaymentOrderMission.tsx`,
`ticket-ai-dashboard`) y de su demo (`/demo`). No es un diagnóstico de un bug puntual —
es el mapa completo del sistema, para que cualquier desarrollador entienda de punta a punta
cómo viaja la información, qué reglas de negocio aplica cada pieza, y dónde están sus
límites reales.

**Documentos relacionados (no duplicados acá, referenciados):**
- `docs/bas-orden-de-pago-research.md` — investigación original del Swagger + cadena
  histórica completa de errores (~12 de `ComprobantesCompra`, ~13 de `OrdenesPago`).
- `docs/bas-comprobante-compra-cuenta-0-diagnostico.md` — diagnóstico y alternativas del
  bug "cuenta 0" (RESUELTO 2026-07-18).
- `docs/bas-comprobante-compra-cuenta-0-solucion.md` — resumen accionable del fix.

**Metodología:** 8 de los 9 endpoints se investigaron con agentes en paralelo (lectura
completa de código + Swagger real de BAS descargado + verificaciones en vivo de solo
lectura ya ejecutadas en sesiones anteriores). El noveno (`invoicy_orchestrator`, el más
central) se documentó a mano leyendo el código fuente actual directamente, tras un límite
de sesión que interrumpió su investigación automática.

---

## 1. Resumen ejecutivo

El flujo completo tiene **un solo disparador humano** (el botón "Crear orden de pago" en
el dashboard) que, en una **única llamada HTTP síncrona**, hace correr hasta 7 sub-pasos
reales contra BAS y PocketBase. No hay streaming de progreso del servidor — los "6 pasos"
que ve el usuario son una **coreografía optimista puramente client-side**
(`useMissionChoreography`, `ticket-ai-dashboard/hooks/use-mission-choreography.ts`) que se
resuelve retroactivamente contra el ÚNICO resultado que devuelve el backend.

```text
Usuario (dashboard)
  │
  ▼
PaymentOrderPanel.tsx — botón "Crear orden de pago"
  │  fetch POST
  ▼
[Next.js] POST /api/payment-orders/[processId]           ← proxy de confianza (§8)
  │  fetch POST + X-Invoicy-Secret + requested_by (server-side)
  ▼
[Invoicy] POST /gemini2/payment-orders/{process_id}/create  ← orquestador central (§7)
  │
  ├─ PocketBase: invoices, bas_processing_status, bas_payment_methods,      (§9)
  │              invoice_items, payment_orders
  │
  └─ BasClient.crear_orden_de_pago_desde_factura(dry_run=False)
       │
       ├─ POST /auth/token                          (transversal, §2)
       ├─ [ya resuelto antes] proveedor en BAS       (§3, normalmente NO se llama acá)
       ├─ GET  /api/ConsultaComprobantesExternos     (§4 — paso 3a)
       ├─ POST /api/ComprobantesCompra               (§5 — paso 3b)
       ├─ GET  /api/ConsultaComprobantes             (§6 — paso 4, NUEVO)
       └─ POST /api/OrdenesPago                      (§7... es decir §7bis; ver §7)
```

---

## 2. Mapa: los 6 pasos visuales vs. las llamadas reales

| # | Paso visual (`MISSION_STEPS`) | Qué pasa técnicamente | Endpoint(s) | Falla con... |
|---|---|---|---|---|
| 1 | Validando factura y método de pago | Valida `metodo_pago`, existencia de la factura y `review_status=="confirmed"` | PocketBase (`invoices`) | 422 / 404 / 409 |
| 2 | Verificando proveedor y medio de pago en BAS | Lee `proveedor_codigo` y `bas_medio_pago_codigo` YA resueltos antes (no vuelve a tocar BAS en este momento) | PocketBase (`bas_processing_status`, `bas_payment_methods`) | 422 |
| 3 | Registrando comprobante de compra | Busca por número externo; si no existe, lo crea | `GET ConsultaComprobantesExternos` + `POST ComprobantesCompra` | 409 "cuenta 0" (RESUELTO) y otros 12 históricos |
| 4 | Confirmando que la factura quedó registrada | GET independiente post-escritura, no confía en el 201 | `GET ConsultaComprobantes` | 409/500 (paso nuevo) |
| 5 | Creando orden de pago | Aplica el comprobante ya verificado contra la cta cte del proveedor | `POST OrdenesPago` | 409 "no existe para aplicarlo" (**SIN RESOLVER**) |
| 6 | Confirmando en BAS | Lectura del 201 de la propia respuesta anterior (no hay una llamada GET separada) | — | — |

Transversales a **todos** los pasos 2-6: `POST /auth/token` (autenticación BAS) y la capa
de persistencia PocketBase (lee antes, escribe "processing" antes de tocar BAS, escribe
"success"/"failed" al final).

---

## 3. `POST /auth/token` — Autenticación BAS

**Qué hace.** Endpoint OAuth2 (password grant / refresh_token grant) de la BAS CS WebAPI.
Devuelve un `access_token` Bearer + `refresh_token` que Invoicy usa para autenticar TODAS
las demás llamadas a BAS. Implementado en `utils/bas.py` (`BasClient`), con cacheo en
memoria: `_solicitar_token()` hace el POST real, `_guardar_token()` persiste el token y su
expiración, `_token_valido()` chequea vigencia, `get_token()` es el punto de entrada
público (cache → refresh → re-login), `_invalidar_token()` lo resetea.

**Cuándo se usa.** Transversal — corre por debajo de cualquier llamada a BAS (pasos 2 a 6).
`_request()` (el wrapper HTTP interno) llama `self.get_token()` antes de cada llamada. Solo
golpea la red si no hay token cacheado, si venció (con margen de 60s), o tras un 401. Como
`BasClient` es un singleton de proceso (`InvoiceOrchestrator.__init__`, instanciado una vez
a nivel de módulo), el token cacheado se reutiliza entre **todas** las facturas mientras el
backend siga corriendo — no hay login por request entrante a Invoicy.

**Request.**

| Campo | Tipo | Requerido | Notas |
|---|---|---|---|
| `grant_type` | string | **Sí** (único required del schema) | `"password"` (login) o `"refresh_token"` (renovación) |
| `client_id` | string | No (default swagger `"api"`) | Siempre enviado; env `BAS_CLIENT_ID` |
| `client_secret` | string | No (default swagger `"secret"`) | Siempre enviado; env `BAS_CLIENT_SECRET` |
| `username` | string | No | Solo si `grant_type="password"`; env `BAS_USER` (`"sa"`, confirmado real) |
| `password` | string | No | Solo si `grant_type="password"`; env `BAS_PASSWORD` |
| `refresh_token` | string | No | Solo al renovar |

Content-Type real: `multipart/form-data` (forzado por `files={k: (None, str(v)) ...}` en
`bas.py`, NO `application/x-www-form-urlencoded`).

**Response.**

| Status | Cuándo | Ejemplo |
|---|---|---|
| 200 | Credenciales/refresh válidos | `{"access_token": "<jwt>", "token_type": "Bearer", "expires_in": 6000, "refresh_token": "<opaque>"}` (100 min, verificado real) |
| 400/401 | Falta `grant_type`, o credenciales/refresh inválidos | Ambos casos usan el **mismo** manejo de excepción en el cliente — no se distinguen |
| 0 (sintético) | Faltan `BAS_USER`/`BAS_PASSWORD` en env | `BasApiError(0, "Faltan BAS_USER / BAS_PASSWORD", "/auth/token")` — nunca llega a hacer la request HTTP |
| (excepción cruda) | BAS caído / timeout (30s default) | `requests.post()` no está en un try/except — se propaga `ConnectionError`/`Timeout` sin envolver en `BasApiError` |

**Reglas de negocio.**
- `get_token()` intenta **siempre** refrescar primero si hay `refresh_token` cacheado; solo
  si eso falla cae a re-login completo con `password`.
- La vigencia se calcula como `expires_in - token_margin` (margen de 60s), nunca
  `expires_in` a secas — evita usar un token que vence a mitad de una request en vuelo.
- Ante un 401 en **cualquier** llamada de negocio (no solo `/auth/token`), `_request()`
  invalida el token y reintenta la llamada original **una sola vez**.

**Limitaciones/errores reales.**
- El Swagger declara seguridad Bearer global sobre todo el documento (incluido este
  endpoint) — artefacto de generación automática, no comportamiento real: el login
  funciona sin ningún header `Authorization`.
- No se puede expresar en OpenAPI 3.0 la dependencia condicional
  (`username`+`password` solo si `password`; `refresh_token` solo si `refresh_token`) — la
  regla vive en el `if/else` de `_solicitar_token`, no en el schema.
- 400 y 401 se manejan de forma **idéntica** (misma excepción genérica) — el caller solo
  puede diferenciarlos leyendo `e.status_code` a mano.
- Si el refresh Y el re-login final fallan, la excepción del re-login se propaga **sin
  capturar** — no hay un tercer fallback.
- Si BAS alguna vez responde 200 sin `expires_in` (campo no marcado `required` en el
  schema), el cliente calcula una expiración ya vencida y fuerza re-auth en la siguiente
  llamada, sin aviso previo.

**Relación con el flujo.** Es el único endpoint cuyo fallo bloquea **todos** los demás
pasos del checklist (2 a 6), no solo el siguiente. No tiene un paso visual propio en la UI
— es la capa 0 invisible.

**Archivos:** `utils/bas.py` (líneas 88-194), `routes/process_invoice_google_2.py` (línea
230, instancia única), `docs/bas-orden-de-pago-research.md` (líneas 31-51).

---

## 4. `GET/POST/PUT/DELETE /api/Proveedores` — Maestro de proveedores

**Qué hace.** Maestro de proveedores de BAS. 5 paths reales: `GET /api/Proveedores`
(listado paginado), `POST /api/Proveedores` (alta), `GET /api/Proveedores/{id}` (lectura),
`PUT /api/Proveedores/{id}` (modificación), `DELETE /api/Proveedores/{id}` (baja), y
`GET /api/Proveedores/razonsocial={x}` (búsqueda exacta). Envuelto en `utils/bas.py`:
`obtener_proveedor`, `buscar_proveedor_por_razon_social`, `listar_proveedores`,
`buscar_proveedor_por_cuit`, `crear_proveedor`, y las orquestaciones
`construir_payload_proveedor` / `verificar_o_dar_de_alta_proveedor`.

Los schemas de request (`Entidadesv2.Maestras.Proveedor`) y response
(`...ProveedorResponse`) son **distintos** en el Swagger pero comparten exactamente los
mismos campos escribibles (`CuentasCorrientes`, `Empresas` incluidos) —
`ProveedorResponse` solo agrega `Fechareg`/`Observaciones` de solo lectura.

**Cuándo se usa (matiz importante).** El "paso 2" del checklist (click humano) **casi
nunca** golpea este endpoint de verdad. Hay dos momentos distintos:
1. **Ingesta** (webhook/upload, antes de que exista intención de pago):
   `InvoiceOrchestrator.procesar_factura_en_bas` → `_obtener_o_verificar_proveedor_bas` →
   cache en memoria → cache PocketBase (`bas_providers`) → recién si ambos fallan, llama a
   BAS real (`verificar_o_dar_de_alta_proveedor`). El resultado se persiste en
   `bas_processing_status.proveedor_codigo`.
2. **Creación de la orden de pago** (click humano): el endpoint orquestador **lee**
   `proveedor_codigo` de PocketBase (ya resuelto en el paso anterior) y si está vacío
   responde 422 — **no vuelve a llamar a BAS Proveedores**.

**Request (alta/modificación).**

| Campo | Tipo | Requerido | Notas |
|---|---|---|---|
| `Codigo` | string(8) | Sí | Derivado de la razón social (`_derivar_codigo_proveedor`: mayúsculas, sin acentos/espacios, truncado a 8) |
| `RazonSocial` | string(60) | Sí | |
| `EmpresaAlta` | int(0-99) | Sí | Empresa que dio de alta — **no** es lo mismo que el array `Empresas` (habilitación real) |
| `TratImpositivo` / `TratImpositivoProv` | string(3) | Sí | De `GET /api/TratamientosImpositivos(Provinciales)/{empresa}` |
| `NumeroImpositivoTipo` / `NumeroImpositivo1` | string | No | `"80"` (CUIT) + el CUIT sin guiones |
| `CuentasCorrientes[]` | `{ImputacionContable:int, PorDefecto:bool}` | No (schema) / **crítico en runtime** | Ver bug "cuenta 0" abajo |
| `Empresas[]` | `{Codigo:int}` | No | **Descartado** como causa del bug — 9/25 proveedores reales tienen `Empresas:[]` y funcionan bien |

**Response.** 200 (lectura OK) · 201 (alta/PUT — sí, el Swagger declara 201 también para
PUT, atípico) · 202 (DELETE) · **204** (sin coincidencia en GET, no 404) · 400/401/404/406
· 409 (conflicto, ej. Código duplicado — **no** confundir con el 409 "cuenta 0", que ocurre
en `ComprobantesCompra`, un endpoint vecino) · 500.

**Reglas de negocio.**
- **No existe filtro por CUIT** en la API. `buscar_proveedor_por_cuit` pagina TODO el
  maestro (hasta 20 páginas × 500 = 10.000 proveedores) comparando `NumeroImpositivo1`
  dígito a dígito. Peor caso (CUIT inexistente): recorre el maestro completo antes de
  devolver `None` — mitigado con cache de 2 niveles (memoria + PocketBase).
- **Regla crítica (bug ya resuelto):** un proveedor dado de alta sin `CuentasCorrientes`
  queda "desnudo" contablemente. Al registrar una factura con `MetodoPago="C"`, BAS no
  puede resolver la cuenta/moneda del proveedor → cae a cuenta "0" → 409 en
  `ComprobantesCompra` (no acá).
- **Fix aplicado:** `construir_payload_proveedor`/`verificar_o_dar_de_alta_proveedor`
  reciben `imputacion_contable` opcional; si se pasa, arman `CuentasCorrientes` con esa
  cuenta marcada `PorDefecto`. El call site real pasa
  `BAS_IMPUTACION_CONTABLE_PROVEEDORES=211001` (`utils/bas_config.py`) — cuenta
  **"Proveedores"** genérica del plan de cuentas, confirmada al 100% (25/25) en un muestreo
  real de proveedores activos.
- `CodigoCuentaCorriente` (usado en `OrdenesPago`/`ComprobanteCompra`) **es el propio
  `Codigo`** del proveedor — no confundir con `CuentasCorrientes[].ImputacionContable`
  (la cuenta contable del plan de cuentas). Confusión ya cometida y corregida en esta
  investigación.

**Limitaciones/errores reales.**
- Sin filtro por CUIT → costo de performance real en el peor caso (ver arriba).
- El endpoint en sí **nunca** devuelve el error de "cuenta 0" — aparece un paso después,
  lo que dificulta diagnosticar la causa raíz mirando solo el error.
- El fix de `211001` es específico de **Empresa 1** (PLATINUM HOMES) — si en el futuro se
  necesita un proveedor de otra empresa, hay que confirmar contra `GET /api/Cuentas` de esa
  empresa antes de reusar el valor a ciegas.
- El builder de Invoicy nunca envía `Empresas[]` al dar de alta — asimetría real frente a
  un proveedor "completo" del maestro, aunque confirmado que no es la causa de ningún bug.

**Relación con el flujo.** Aguas abajo inmediato: `POST /api/ComprobantesCompra` (usa
`Proveedor.Codigo`) y `POST /api/OrdenesPago` (usa el mismo código como
`CodigoCuentaCorriente`). Si falla acá, `procesar_factura_en_bas` corta de inmediato — es
un guard duro.

**Archivos:** `utils/bas.py` (líneas 294-361, 432-475, 480-486, 514-567, 725-734),
`utils/bas_config.py` (línea 67), `utils/pocketbase_client.py` (cache de 2 niveles),
`docs/bas-comprobante-compra-cuenta-0-diagnostico.md`.

---

## 5. `GET /api/ConsultaComprobantesExternos` — ¿La factura ya existe?

**Qué hace.** Busca si un comprobante de compra ya existe en BAS, identificado por el
**número externo del proveedor** (el impreso en la factura real), no por la numeración
interna que BAS le asignaría. Si lo encuentra, devuelve el objeto completo con
Prefijo/Numero **internos**; si no, `None`. Envuelto en `consultar_comprobante_externo()`
(`utils/bas.py`), que valida client-side la combinación de parámetros antes de llamar.

**Cuándo se usa.** Primer sub-paso real del paso 3 ("Registrando comprobante de compra").
Es la puerta de entrada de `crear_orden_de_pago_desde_factura()`: decide si el flujo puede
saltarse el alta (factura ya registrada) o si hay que pasar por alta + verificación (caso
normal — las facturas de Invoicy nacen de fotos, **nunca preexisten** en BAS).

**Request.**

| Campo | Requerido | Notas |
|---|---|---|
| `Empresa`, `Sucursal`, `Comprobante` | Sí | Siempre `1`, `1`, `"MA"` en producción |
| `Prefijo` + `Numero` (interno) **O** `FechaComprobanteExterno` + `PrefijoComprobanteExterno` + `NumeroComprobanteExterno` | Una de las dos combinaciones completas | Invoicy **siempre** usa la externa — nunca conoce la interna de antemano |

**Response.**

| Status | Cuándo | Notas |
|---|---|---|
| 200 con match | Ya existe | `{IdTransaccion, Prefijo, Numero, CodigoCuentaCorriente, Anulado, ...}` |
| **200 con `[]`** | Sin coincidencia | Comportamiento **real** confirmado — el Swagger documenta 204, pero el servidor responde 200+`[]` |
| 204 | Documentado en Swagger, no reproducido en vivo | El código lo soporta igual (`_json_o_none`) |
| 400 | Falta la combinación completa de params | *"You must provide the Prefix and Number or Date, Prefix and External Number."* (real, verificado) |

**Reglas de negocio.**
- Es el mecanismo de **idempotencia** del flujo: evita duplicar el alta si ya se registró
  en un intento anterior.
- `Empresa`/`Sucursal`/`Comprobante` no son datos de la factura, son config fija de esta
  instalación.

**Limitaciones/errores reales.**
- Discrepancia Swagger vs. comportamiento real (200+`[]` en vez de 204) — ya mencionada.
- **Bug de trazabilidad en código vecino:** si la verificación post-registro (§6) falla, el
  `BasApiError` que se lanza le pone `path="/api/ConsultaComprobantesExternos"` (este
  endpoint) aunque la llamada real que falló fue a `/api/ConsultaComprobantes` (interno,
  §6) — inconsistencia entre el texto del mensaje (correcto) y el campo `path` (incorrecto).
- El checklist visual **no tiene un paso dedicado** para esta consulta inicial — un fallo
  acá es indistinguible de un fallo del POST de alta que la sigue (ambos caen en
  `failedStepIndex=2`, "Registrando comprobante de compra").
- Sin desambiguación si hay más de una coincidencia — toma siempre la primera.

**Relación con el flujo.** Si encuentra match → salta directo a armar la Orden de Pago. Si
no → sigue a `POST /api/ComprobantesCompra` (§6).

**Archivos:** `utils/bas.py` (líneas 221-264, 613-623), `docs/bas-orden-de-pago-research.md`
(líneas 62-90).

---

## 6. `POST /api/ComprobantesCompra` — Registrar la factura

**Qué hace.** Registra en BAS, como cabecera + 1 línea de ítem genérica, una factura de
compra que Invoicy extrajo de una foto y que todavía no existe en BAS. Corre en el **100%**
de los casos reales (las fotos nunca preexisten).

**Cuándo se usa.** Paso 3 del checklist. Se dispara dentro de
`crear_orden_de_pago_desde_factura()` cuando el paso anterior (§5) no encontró la factura.

**Request — campos requeridos EN RUNTIME (más allá del schema OpenAPI).**

| Campo | ¿Required por schema? | ¿Required en runtime? | Valor real usado |
|---|---|---|---|
| `Comprobante` | Sí | Sí | `"MA"` (Factura de Compra A — "MB" no tiene talonario en esta instalación) |
| `EmitidoPor` | Sí | Sí | `"2"` (CAE) |
| `Fecha`, `Total` | Sí | Sí | De la factura extraída |
| `Prefijo` | No | **Sí** (error #1 histórico si falta) | `"00001"` hardcodeado (TODO: resolver dinámico) |
| `TotalGravado` | No | **Sí** (error #12) | = `Total` (no se descompone gravado/IVA real) |
| `Deposito` | No | **Sí** (error #1) | `1` |
| `Caja` | No | **Sí** (error #5) | `"1"` |
| `MetodoPago` | No (default `"D"` Contado) | **Sí, de negocio** (error #11 con default) | `"C"` (cuenta corriente — decisión de diseño de 2 pasos) |
| `Proveedor` | No | Sí | `Proveedor.Codigo` resuelto en §4 |
| `NumeroCAIoCAE`/`VencimientoCAIoCAE` | No | **Sí cuando `EmitidoPor="2"`** (error #10) | De la factura |
| `Vencimientos[]` | Sí | Sí | 1 línea, `Importe=Total` |
| `Items[]` | No (schema permite vacío) | **Sí** (error #3 — SP crashea sin ítems) | **Siempre exactamente 1 ítem genérico**, nunca los ítems reales |
| `Items[].CodigoItem` | Sí | Sí | Siempre `"Gs Gs 21%"` (único ítem verificado con posición contable COM) |
| `Items[].TipoEntrega` | Sí | Sí | `"E"` (no mueve stock — `"O"` rompía, errores #4/#8) |
| `Items[].CentroApropiacionA/B` | No | **Sí** (error #9, FK violation) | `"SD"` |

**Por qué solo 1 ítem genérico, nunca los reales:** las fotos que procesa Invoicy no traen
`CodigoItem` del maestro de BAS — no hay forma de mapear "Coca-Cola 1.5L" a un código
interno desde una imagen.

**Response.**

| Status | Cuándo | Ejemplo real |
|---|---|---|
| 201 | Todo OK | `{"IdTransaccion": 274465, "Comprobantes": [{"Prefijo": "00001", "Numero": "00021883", ...}]}` (2026-07-18, proveedor SUPERCOO ya arreglado) |
| 400 | Validación de formato | Ej. `TotalGravado` no coincide con la suma de líneas |
| **409** | Conflicto de negocio — **la forma MÁS común de error real** (10 de 13 casos históricos) | Ver catálogo completo abajo |
| 500 | Bug de robustez de BAS | *"Incorrect syntax near FROM (SP_ICR_COMPROB_COMPRA_ITEMS)"* cuando se manda sin ítems |

**Catálogo completo de errores reales encontrados (histórico, todos resueltos salvo aclaración):**

| # | Mensaje real | Causa | Fix |
|---|---|---|---|
| 1 | "El depósito 0 no pertenece a la empresa..." | Falta `Deposito` | `Deposito: 1` |
| 2 | "La imputación contable indicada X es distinta a la definida en parámetros para compras contado" | `ImputacionContable` explícito ≠ parámetro (etapa temprana, con `MetodoPago` default) | Ya no aplica — se pasó a `MetodoPago="C"` |
| 3 | "Incorrect syntax near FROM (SP_ICR_COMPROB_COMPRA_ITEMS)" | SQL roto en BAS cuando no hay ítems | Mandar ≥1 ítem (bug de BAS, no configurable) |
| 4/8 | "El talonario NO tiene talonario asociado para ingresos/egresos de mercadería" | `TipoEntrega="O"` mueve stock | `TipoEntrega="E"` |
| 5 | "La caja (null) no está definida (SP_ICR_COMPROB_PAGOS)" | Falta `Caja` | `Caja: "1"` |
| 6 | "No se indicaron líneas de ítems (SP_GENEROASI)" | Campos de ítem incorrectos (`Cantidad` no existe) | `CantidadPrimeraUnidad`, `PrecioUnitario`, etc. |
| 7 | "La posición contable del ítem 006 Insumos no está definida para COM" | Ítem sin posición contable de Compras | `CodigoItem="Gs Gs 21%"` (verificado con COM) |
| 9 | "FK_MVSITEMS_CENTROSAP" (FK violation) | Faltan `CentroApropiacionA/B` | `"SD"` |
| 10 | "Debe ingresar el número de C.A.E. y/o vencimiento" | `EmitidoPor="2"` exige CAE | `NumeroCAIoCAE` + `VencimientoCAIoCAE` |
| 11 | "Si el método de pago es Contado, debe tener líneas de pago" | `MetodoPago` default `"D"` | `MetodoPago="C"` |
| 12 | "El total gravado (0.00) no coincide con la suma de las líneas" | Falta `TotalGravado` | `TotalGravado = Total` |
| **13** | **"No se pudo establecer la moneda correspondiente a la cuenta 0" (SP_ICR_VALIDA_CODTAB)** | Proveedor sin `CuentasCorrientes` | **RESUELTO 2026-07-18** — ver §4 |

**Reglas de negocio.**
- Antes de confiar en el 201, el flujo hace una verificación independiente (§7) — nunca se
  llama a `OrdenesPago` confiando ciegamente en este POST.
- Dos call sites arman este mismo payload con una diferencia: la ingesta automática
  (siempre `dry_run=True`) usa `categoria → codigo_item_de_categoria()`; el endpoint real
  (único `dry_run=False`) lee `invoice_items` **de PocketBase** (post-revisión humana).

**Relación con el flujo.** Predecesor: §4 (proveedor) + §5 (consulta externa, condicional).
Sucesor: §7 (verificación GET independiente) → §8 (Orden de Pago).

**Archivos:** `utils/bas.py` (líneas 488-501, 569-719), `utils/bas_config.py`,
`routes/process_invoice_google_2.py` (líneas 1456-1476 y 2897-2917 — casi idénticos),
`docs/bas-orden-de-pago-research.md`, `docs/bas-comprobante-compra-cuenta-0-diagnostico.md`.

---

## 7. `GET /api/ConsultaComprobantes` — Verificación post-escritura (paso NUEVO)

**Qué hace.** Busca UN comprobante por su **numeración interna** de BAS
(Empresa+Sucursal+Comprobante+Prefijo+Numero) — a diferencia de §5, que busca por número
externo. Se usa exclusivamente como "doble check": confirmar con un GET independiente que
un comprobante recién creado por el POST de §6 realmente quedó persistido, antes de
intentar pagarlo.

**Cuándo se usa.** Paso 4 del checklist ("Confirmando que la factura quedó registrada"),
agregado esta semana. Corre **solo** si (a) la factura era nueva (no la encontró §5) y (b)
`dry_run=False`. Si la factura ya existía de antes, este GET **nunca** se llama.

**Request.**

| Campo | Requerido | Notas |
|---|---|---|
| `Empresa`, `Sucursal`, `Comprobante` | Sí | `1`, `1`, `"MA"` |
| `Prefijo`, `Numero` | Sí | Los que devolvió el eco del POST de §6 |
| `Fecha` | No | Nunca se pasa en esta verificación |

**Response.**

| Status | Cuándo | Notas |
|---|---|---|
| 200 con match | Confirmado | Los valores de Prefijo/Numero de ESTA respuesta se usan para armar la OP, no el eco del POST — "más confiables" |
| 200 con `[]` | No encontrado — **confirmado en vivo** (probado con `Numero=99999999`, `Comprobante="XX"`, `Empresa=99`, todos devuelven 200+`[]`, sin distinguir "inválido" de "no encontrado") | |
| 204 | Documentado en Swagger, no reproducido | |

**Reglas de negocio — patrón "write-then-read-back":**
- Si `prefijo_int`/`numero_int` vienen vacíos del POST anterior → `BasApiError(500, ...)`
  **antes** de intentar este GET.
- Si el GET no encuentra el comprobante → `BasApiError(409, ...)` y se **corta el flujo
  completo** — nunca se intenta la Orden de Pago contra una factura que podría no existir.

**Limitaciones/errores reales.**
- **Bug de trazabilidad (ya mencionado en §5):** el `path` del `BasApiError` de "no
  encontrado" queda mal etiquetado como `/api/ConsultaComprobantesExternos` en vez de
  `/api/ConsultaComprobantes` — inconsistencia entre el texto (correcto) y el campo `path`.
- El texto real que dispara la clasificación correcta en el frontend
  (`inferMissionOutcome`) es la frase **"verificación posterior"** — cualquier otro error de
  este endpoint (401/500 reales, o el caso de 500 "sin Prefijo/Numero") NO contiene esa
  frase y cae por error en el paso 3 en vez del paso 4 — falso negativo de atribución de
  paso en la UI, documentado pero no arreglado.
- El endpoint no distingue "parámetros inválidos" de "no encontrado" — ambos dan 200+`[]`.

**Relación con el flujo.** Entre §6 (`ComprobantesCompra`) y §8 (`OrdenesPago`). Si falla,
§8 **nunca** se ejecuta.

**Archivos:** `utils/bas.py` (líneas 199-219 `consultar_comprobante()`, líneas 651-683 el
bloque de verificación dentro de `crear_orden_de_pago_desde_factura`),
`ticket-ai-dashboard/lib/payment-order-mission.ts` (líneas 65-66, el matching de
"verificación posterior").

---

## 8. `POST /api/OrdenesPago` — Crear la Orden de Pago (BLOQUEO ACTUAL, SIN RESOLVER)

**Qué hace.** Da de alta una Orden de Pago (comprobante "OP") y, en la MISMA llamada, la
imputa (aplica) contra una o más facturas ya existentes en la cuenta corriente del
proveedor, vía `ComprobantesAplicados[]`. Atómico — sin necesitar una segunda llamada.

**Cuándo se usa.** Paso 5 del checklist. Se dispara DESPUÉS de que §7 confirmó que la
factura quedó persistida.

**Request — campos requeridos EN RUNTIME.**

| Campo | ¿Schema? | ¿Runtime? | Notas |
|---|---|---|---|
| `Fecha`, `Total`, `Empresa`, `Sucursal` | Sí | Sí | Únicos 4 `required` formales del schema |
| `Prefijo` | No | **Sí** | Talonario de la propia OP — sin él, 400 `{Prefijo: required}` |
| `PrefijoCuentaCorriente` | No | **Sí** | Pattern `[CPA]` — `"P"` para proveedores |
| `CodigoCuentaCorriente` | No | **Sí** | = `Proveedor.Codigo` (no `ImputacionContable`) |
| `Caja` | No | **Sí** | Para poder registrar el medio de pago |
| `ComprobantesAplicados[]` | No (array) | **Sí, de facto** | Cada item: `*Comprobante`, `*Prefijo`, `*Numero` (internos, de §7), `*Importe` |
| Un array de medio de pago (`Efectivos[]`, etc.) | No | **Sí, de facto** | Cada línea: `*MedioPago` (sin endpoint que lo exponga — `"1"` identificado empíricamente como válido para efectivo) + `IngresooEgreso="E"` |
| `ImputacionContable` (cabecera) | No | Mecanismo **alternativo**, incompatible con `CodigoCuentaCorriente` | Combinarlos da: *"No puede indicar Aplicaciones para una cuenta que no tiene subcuentas clientes o proveedores"* |

**Response.**

| Status | Cuándo |
|---|---|
| 201 | **NUNCA logrado en vivo** para este endpoint — el bloqueo de abajo lo impide siempre |
| 400 | Validación de forma (`{Prefijo: required, CodigoCuentaCorriente: required}`, etc.) |
| **409 — BLOQUEO ACTUAL** | *"El comprobante MA 00001-00021876 no existe para aplicarlo. (SP_ICR_COMPROB_APL)(SP_ICR_COMPROB_CAJA)"* — pese a que `GET ConsultaComprobantes` confirma que existe y `Anulado:false` |

**Catálogo de ~13 hipótesis probadas y descartadas para el bloqueo 409 (ninguna lo resolvió):**

| Hipótesis | Resultado |
|---|---|
| Timing/reintento simple | Mismo error |
| `ImputacionContable` dentro de `ComprobanteAplicado` | Mismo error |
| `Comprobante:"FAC A"` en vez de `"MA"` | 400 de validación (confirma que "MA" es correcto) |
| `Importe` negativo | Error **distinto** (validación de signo corre antes) |
| Candidatos de `MedioPago` (EF/001/EFE/CAJ/100) | "Medio de pago inexistente" — `"1"` es el único que pasa |
| `MonedaComprobante="L"` en factura nueva | Mismo error de aplicación |
| `MonedaCtaCte="L"` en la factura | Rompe algo **distinto** (FK_TRANSAC_CUENTAS) |
| `MonedaCtaCte="L"` en la OP | Mismo error |
| `ImputacionContable` en cabecera de OP | Error **nuevo** (mecanismo incompatible, confirma que no deben combinarse) |
| Formato de `Numero`, padding de `CodigoCuentaCorriente`, `Fecha`/`FechaVencimiento` en aplicados | Ninguno cambió el error |
| Proveedor nuevo vs. preexistente (Thymbra) | Mismo error — confirma que **no depende del proveedor** |

**Hipótesis no descartadas:** un paso de "confirmación"/cierre adicional que la app de
escritorio de BAS haría y la API REST no expone, o que los SPs de la capa de integración
REST (prefijo `SP_ICR`) sean distintos/incompletos respecto a los de la interfaz clásica.
**Confirmado que este bloqueo es independiente del bug "cuenta 0" ya resuelto** (SPs
distintos: `SP_ICR_COMPROB_APL`/`SP_ICR_COMPROB_CAJA` vs. `SP_ICR_VALIDA_CODTAB`).

**Reglas de negocio.**
- Si `crear_orden_de_pago()` falla, la factura **ya registrada no se pierde** — el caller
  distingue éxito/fallo por una clave `_error` separada del resultado de la factura.
- `dry_run=False` (impacto real) solo ocurre en un único lugar de todo el sistema (§9).
- El medio de pago se resuelve por `metodo_pago` del usuario vía `METODO_PAGO_ARRAY_BAS`;
  `"transferencia"` exige además `bas_cuenta_bancaria` configurado, si falta → 422 antes de
  tocar BAS.
- El `Importe` de `ComprobantesAplicados`/`Total` de la OP usa la variable `monto` (que
  puede diferir de `invoice.total` si el usuario lo sobrescribe explícitamente en el
  request) — diseño que en teoría soporta pagos parciales, aunque **no verificado en vivo**
  porque el bloqueo 409 lo impide.

**Limitaciones/errores reales adicionales.**
- No hay endpoint para consultar el catálogo real de `MedioPago`.
- No hay endpoint para consultar el estado de cuenta corriente de un **proveedor**
  (`EstadoCtaCteCliente` y similares son exclusivos de Clientes) — dificulta diagnosticar
  el bloqueo desde afuera de la API.
- `CodigoCuentaCorriente = Proveedor.Codigo` tiene alta confianza pero **no está
  100% confirmado con un 201 real end-to-end** (el bloqueo lo impide).

**Relación con el flujo.** Recibe su insumo de §7 (Prefijo/Numero internos verificados) y
de §4 (`CodigoCuentaCorriente`). Es el único paso cuyo fallo **no revierte** los pasos
anteriores — la factura queda registrada aunque la OP falle, y `/retry-op/{process_id}`
permite reintentar **solo** este paso.

**Archivos:** `utils/bas.py` (líneas 366-430, 503-509, 569-719),
`docs/bas-orden-de-pago-research.md` (líneas 324-466), `docs/bas-comprobante-compra-cuenta-0-solucion.md` (sección 6).

---

## 9. `POST /gemini2/payment-orders/{process_id}/create` — El orquestador central

**Qué hace.** Es EL endpoint que amarra todo: el único lugar de todo el sistema donde
`dry_run` finalmente pasa a `False`, y el que ejecuta, en un único request/response
síncrono, la secuencia completa de §3 a §8. Sin él, todos los demás endpoints de BAS son
piezas sueltas.

**Cuándo se usa.** Es el destino final del click humano en "Crear orden de pago" — llamado
por el proxy de Next.js (§10). Nunca se llama desde la ingesta automática (esa usa
`procesar_factura_en_bas`, siempre `dry_run=True`).

**Secuencia exacta (líneas reales, `routes/process_invoice_google_2.py:2812-2981`):**

1. `_verificar_secreto_invoicy(x_invoicy_secret)` (línea 2840) — valida `X-Invoicy-Secret`.
2. `body.metodo_pago not in METODO_PAGO_ARRAY_BAS` → 422 (línea 2842-2843).
3. `get_invoice_by_process_id(process_id)` → 404 si `None` (línea 2845-2847).
4. `invoice.review_status != "confirmed"` → 409 (línea 2848-2849).
5. `get_bas_processing_status(process_id)` → `proveedor_codigo`; falta → 422 (línea 2851-2854).
6. `get_payment_method(body.metodo_pago)` → `bas_medio_pago_codigo`; falta → 422 (línea 2856-2861).
7. Si `metodo_pago=="transferencia"`, valida `bas_cuenta_bancaria` → 422 si falta (línea 2870-2872).
8. Arma `item_pago` (`MedioPago`, `Importe=monto`, `IngresooEgreso="E"`, `CuentaBancaria` si aplica) (línea 2865-2874).
9. `get_invoice_items(invoice["id"])` — **de PocketBase**, no de la extracción original de Gemini (línea 2879).
10. Arma `comprobante_compra_payload` completo (línea 2897-2917) — mismo shape que §6.
11. `upsert_payment_order(status="processing", ...)` — **antes** de tocar BAS, sobrevive un crash (línea 2923-2933).
12. `crear_orden_de_pago_desde_factura(..., dry_run=False)` — acá ocurre TODA la cadena §3-§8 (línea 2937-2953).
13. `upsert_payment_order(status="success"|"failed", bas_op_prefijo, bas_op_numero, bas_error)` (línea 2966-2973).
14. Devuelve `{success, process_id, orden_pago, error, payment_order}` (línea 2975-2981) — **siempre HTTP 200** si llegó hasta acá.

**Request.**

| Campo | Tipo | Requerido | Notas |
|---|---|---|---|
| `metodo_pago` | string | Sí | `"efectivo"` / `"cheque"` / `"transferencia"` |
| `monto` | number | No | Default: `invoice.total` |
| `requested_by` | string | Sí | Id de PocketBase (`users`) — resuelto por el proxy Next.js, **nunca** confiado del cliente |

**Response — dos formas incompatibles.**

1. **HTTPException pre-BAS** (nunca se llegó a tocar BAS): status 404/409/422, body
   `{"detail": "..."}`.
2. **200 siempre** (ya se escribió el row "processing" y se llamó a BAS, haya salido bien o
   mal): `{"success": bool, "process_id", "orden_pago", "error": string|null, "payment_order"}`.
   El campo `error`, cuando existe, tiene uno de tres formatos fijos:
   - `"BasApiError {code} en /api/ComprobantesCompra: ..."` → paso 3 (§6).
   - `"BasApiError {code} en /api/ConsultaComprobantesExternos: ...verificación posterior..."` → paso 4 (§7), **etiqueta de path heredada e incorrecta** (ver §5/§7).
   - `"Orden de pago falló: ..."` → paso 5 (§8).

**Reglas de negocio.**
- **NO reusa `bas_processing_status.comprobante_registrado` a ciegas** — ese flag puede
  venir de un intento `dry_run=True` de la ingesta automática que nunca escribió nada real.
  Arma el payload de cero y deja que `crear_orden_de_pago_desde_factura` haga su propio
  chequeo real (§5).
- Revalida `review_status=="confirmed"` server-side — no confía en que el caller ya lo haya
  chequeado.
- `retry_count` se calcula leyendo el `payment_order` existente para ese `process_id` — solo
  cuenta intentos que llegaron a pasar TODAS las validaciones previas (404/409/422 nunca
  llegan a escribir el row "processing").
- El upsert final (paso 13) **no** pasa `invoice=` explícito — es un `UPDATE` sobre el row
  ya creado en el paso 11, coherente con la regla de PocketBase de que `invoice` solo es
  obligatorio en la creación (§10).

**Limitaciones/errores reales.**
- Cualquier excepción NO capturada por `except BasApiError`/`except Exception` (líneas
  2957-2962) se pierde — el `except Exception` genérico solo guarda `str(e)`, perdiendo
  `status_code`/`path` estructurados si la excepción original los tenía.
- El `Total`/`TotalGravado` del comprobante de compra usa siempre `invoice.get("total")`
  (el total completo de la factura), mientras que el `Importe` de la Orden de Pago usa
  `monto` (que puede ser parcial si el usuario lo especifica) — esto es coherente con
  soportar pagos parciales, pero **nunca se probó en vivo** porque el bloqueo de §8 lo
  impide; vale la pena confirmarlo el día que se resuelva ese bloqueo.
- Es completamente **síncrono**: si BAS tarda (o el proceso se cuelga a mitad de la
  llamada a `crear_orden_de_pago_desde_factura`), el request HTTP entero queda colgado —
  no hay timeout propio en este endpoint más allá del `timeout=30` del cliente HTTP interno
  de `BasClient`.

**Relación con el flujo.** Es el "spine" — todo lo de §3 a §8 vive DENTRO de la ejecución de
este único endpoint. Hacia arriba, lo llama el proxy Next.js (§10). Hacia los lados, es el
único punto que persiste el resultado final en `payment_orders` (§10.b).

**Archivos:** `routes/process_invoice_google_2.py` (líneas 2790-2982 completas),
`utils/bas.py`, `utils/bas_config.py`, `utils/pocketbase_client.py`.

---

## 10. `POST /api/payment-orders/[processId]` — Proxy Next.js (punto de entrada humano)

**Qué hace.** Route Handler de Next.js que actúa como proxy de confianza entre el navegador
y Invoicy. Sin lógica de negocio de BAS propia: valida la sesión PocketBase, valida config
de servidor, valida que el body traiga `metodo_pago`, resuelve `requested_by` desde la
sesión server-side, agrega el header secreto `X-Invoicy-Secret`, y reenvía a §9. La
respuesta se retransmite **sin reshaping** ("proxy transparente").

**Cuándo se usa.** Se dispara al click en "Crear orden de pago"/"Reintentar" en
`PaymentOrderPanel.tsx`. Es el único código de producción que llama a esta ruta con `fetch`
real — la demo (`/demo`) reusa la misma coreografía visual pero con una promesa simulada.

**Request.**

| Campo | Tipo | Requerido | Notas |
|---|---|---|---|
| `metodo_pago` | string | Sí | El proxy solo valida `typeof === "string"` — **no** valida que sea un valor permitido (esa validación real vive en §9) |
| `monto` | number | No | Si no es `number` válido, se omite (no se rechaza con 400) |
| `requested_by` | — | (no viene del cliente) | Resuelto **exclusivamente** de `session.authStore.record.id` — un `requested_by` que el cliente mande sería ignorado |

**Response — 4 casos propios (antes de llegar a Invoicy) + passthrough.**

| Status | Cuándo | Body |
|---|---|---|
| 401 | Sin sesión PocketBase válida | `{"error": "No autenticado."}` |
| 500 | Falta config de servidor (URL/SECRET_KEY) | `{"error": "Falta INVOICE_API_BAS_INTERNAL_URL o SECRET_KEY en el servidor."}` |
| 400 | Body no parsea o falta `metodo_pago` | `{"error": "Falta metodo_pago."}` |
| 401 | Sesión válida pero sin `record.id` (defensivo, difícil de alcanzar) | `{"error": "No se pudo resolver el usuario de la sesión."}` |
| 502 | `fetch()` falla antes de recibir respuesta de Invoicy (DNS, timeout, Invoicy caído) | `{"error": "<mensaje real>"}` |
| (passthrough) | Lo que responda §9, tal cual | Body y status idénticos a §9 |

**Reglas de negocio.**
- Existe **específicamente** para que `X-Invoicy-Secret` nunca viva en el cliente.
- `requested_by` se resuelve server-side porque el navegador no tiene forma segura de
  probar su identidad ante Invoicy sin exponer el secreto.
- Los errores DE RED (502) se distinguen explícitamente de los errores DE NEGOCIO — el
  cliente los trata como "conexión perdida" genérica, no atribuida a un paso específico.

**Limitaciones/errores reales.**
- Los 4 errores propios del proxy usan la forma `{error: "..."}`, distinta de la forma
  `{detail: "..."}` de FastAPI/Invoicy — diseño intencional para separar "nunca llegamos a
  intentar nada en BAS" de "BAS rechazó algo en un paso concreto". `inferMissionOutcome`
  (frontend) maneja ambas formas.
- No valida el VALOR de `metodo_pago` ni rango/signo de `monto` — deliberadamente delgado,
  delega esas reglas a Invoicy.
- No hay rate limiting ni CSRF token explícito en este archivo — la única protección es la
  cookie de sesión + la revalidación server-side de Invoicy.

**Relación con el flujo.** Es el disparador humano de los 6 pasos. Es agnóstico a los
sub-pasos internos de BAS — solo ve una request/response de Invoicy, nunca un stream de
progreso. Toda la sensación de "pasos" es la coreografía client-side de
`useMissionChoreography` sincronizada con esa única respuesta.

**Archivos:** `ticket-ai-dashboard/app/api/payment-orders/[processId]/route.ts`,
`PaymentOrderPanel.tsx`, `hooks/use-mission-choreography.ts`, `lib/payment-order-mission.ts`.

---

## 11. Capa de persistencia PocketBase (transversal)

**Qué hace.** No es un endpoint BAS — es la capa de persistencia (`utils/pocketbase_client.py`,
616 líneas) que acompaña todo el flujo. PocketBase **nunca** habla con BAS directamente.
Cliente REST síncrono con el mismo patrón estructural que `BasClient` (token cacheado +
refresh + re-login + reintento en 401), 100% "best effort": **todos** los métodos públicos
atrapan cualquier excepción y devuelven `None`/`False`/`[]` en vez de propagar — PocketBase
jamás debe romper el flujo real con BAS.

**Colecciones relevantes al flujo, y en qué paso participan:**

| Colección | Método | Paso | Campos clave |
|---|---|---|---|
| `invoices` | `get_invoice_by_process_id` | 1 | `review_status`, `total`, `fecha_emision`, `cae` |
| `bas_processing_status` | `get_bas_processing_status` | 2 | `proveedor_codigo`, `comprobante_prefijo/numero`, `orden_pago_status` |
| `bas_payment_methods` | `get_payment_method` | 1/2 | `bas_medio_pago_codigo`, `bas_cuenta_bancaria`, `confirmado` |
| `invoice_items` | `get_invoice_items` | 3 | Ítems **post-revisión humana**, no la extracción original |
| `payment_orders` | `get_payment_order` / `upsert_payment_order` | antes y después de 3-6 | `status` (`processing`/`success`/`failed`, **sin** `pending`), `bas_op_prefijo/numero`, `bas_error` |
| `bas_providers` | `get_provider_cache` / `set_provider_cache` | 2 (ingesta) | Cache 2do nivel de proveedores por CUIT |

**Reglas de negocio.**
- `upsert_bas_processing_status`/`upsert_payment_order`: en un **UPDATE** persisten
  cualquier campo que se les pase; en un **CREATE** exigen el kwarg `invoice` (relation
  requerida) — si falta, no lanzan excepción, solo loguean warning y devuelven `None` **sin
  persistir nada**. Riesgo real de pérdida silenciosa de estado si el caller no revisa el
  valor de retorno.
- `get_invoice_items()` lee de PocketBase (post-revisión), no del LLM original — deliberado.
- `payment_orders.status` **no** incluye `"pending"` — un row solo se crea en el momento en
  que un humano pide la orden, pasa directo a `"processing"`.
- `bas_processing_status` (sonda automática, siempre `dry_run=True`) y `payment_orders`
  (acción humana con dinero real) son colecciones deliberadamente **separadas**.
- Ningún campo de PocketBase, en ninguna colección, guarda `IdTransaccion` — el estado se
  reduce a booleanos/enums + identificadores de negocio (`proveedor_codigo`,
  `comprobante_prefijo/numero`, `bas_op_prefijo/numero`).
- Auth de PocketBase vs. BAS: mismo patrón general, implementaciones distintas —
  PocketBase manda el token **sin** `"Bearer "`, no tiene `refresh_token` propio (reusa el
  access_token expirado contra `/auth-refresh`), y no expone `expires_in` (decodifica el
  claim `exp` del JWT a mano, con `fallback_ttl=600s` si falla).

**Limitaciones/errores reales.**
- **Gap real de código encontrado:** `orchestrator._pb_client.obtener_file_token()` y
  `.adjuntar_archivo_original()` se llaman desde `routes/process_invoice_google_2.py`
  (líneas 1790, 2685, 2755) pero **no existen** como métodos en `PocketBaseClient` —
  confirmado con `grep` sin resultados en todo el repo. Si esas líneas se ejecutan,
  lanzarían `AttributeError` no atrapado. No relacionado al flujo de "Crear orden de pago"
  documentado acá, pero es un hallazgo real que vale la pena arreglar.
- Pérdida silenciosa de estado si falta `invoice` en una creación nueva (ver arriba).
- `_upsert()` genérico hace `find_then_write` (2 requests HTTP secuenciales, sin
  transacción) — ventana de carrera teórica si dos requests concurrentes crean el mismo
  `process_id` a la vez.
- Ningún método distingue "PocketBase no configurado" de "PocketBase caído" de "no existe
  el record" — todos colapsan al mismo `None`/`[]`/`False`.

**Relación con el flujo.** Es el "tejido conectivo": nunca decide nada de negocio con BAS,
pero es la única fuente de verdad que sobrevive un restart/crash del proceso Python — a
diferencia del estado en memoria del orquestador.

**Archivos:** `utils/pocketbase_client.py` (616 líneas), migraciones en
`ticket-ai-infra/pocketbase/pb_migrations/` (`1783483765` a `1783483865`).

---

## 12. Glosario de confusiones ya cometidas (y corregidas)

| Término A | Término B | Diferencia real |
|---|---|---|
| Número **externo** (`PrefijoComprobanteExterno`/`NumeroComprobanteExterno`) | Número **interno** (`Prefijo`/`Numero`) | El externo es el impreso en la factura del proveedor; el interno lo asigna BAS al registrar. Invoicy solo conoce el externo hasta que BAS confirma el interno. |
| `Proveedor.CuentasCorrientes[].ImputacionContable` | `CodigoCuentaCorriente` (de `OrdenDePago`/`ComprobanteCompra`) | El primero es la cuenta **contable** del plan de cuentas (causa del bug "cuenta 0"); el segundo **es literalmente el `Codigo`** del proveedor, usado para atar la OP a su cuenta corriente. Mismo "nombre de concepto", campos distintos. |
| `Proveedor.EmpresaAlta` | `Proveedor.Empresas[]` | `EmpresaAlta` = quién dio de alta al proveedor. `Empresas[]` = en qué empresas está habilitado para operar. Un proveedor puede tener `EmpresaAlta=1` y `Empresas=[]` a la vez (así nació SUPERCOO) — confirmado que `Empresas` vacío **no** es causa de ningún bug conocido. |
| `GET /api/ConsultaComprobantesExternos` | `GET /api/ConsultaComprobantes` | El primero busca por número externo (del proveedor); el segundo por número interno (de BAS). Ambos pueden devolver 200+`[]` para "no encontrado" pese a que el Swagger documenta 204. |
| `bas_processing_status.orden_pago_status` | `payment_orders.status` | El primero es la sonda automática de la ingesta (siempre `dry_run=True`, informativo); el segundo es la acción humana real con dinero de por medio. Colecciones deliberadamente separadas. |

---

## 13. Estado actual (2026-07-18)

| Pieza | Estado |
|---|---|
| Autenticación BAS (§3) | ✅ Funciona |
| Proveedores (§4) | ✅ Funciona — bug "cuenta 0" resuelto |
| Consulta externa (§5) | ✅ Funciona |
| Registro de comprobante (§6) | ✅ Funciona — 201 real confirmado (`IdTransaccion 274465`) |
| Verificación post-registro (§7) | ✅ Implementado y funcionando (paso nuevo) |
| **Orden de Pago (§8)** | ❌ **Bloqueado** — 409 "no existe para aplicarlo", causa raíz sin identificar tras ~13 variantes |
| Orquestador (§9) | ✅ Funciona (hasta donde §8 se lo permite) |
| Proxy dashboard (§10) | ✅ Funciona |
| PocketBase (§11) | ✅ Funciona (con el gap de `obtener_file_token`/`adjuntar_archivo_original` documentado, ajeno a este flujo) |

**Siguiente paso lógico:** el único bloqueo real que impide cerrar el flujo de punta a
punta es §8 (`OrdenesPago`). Requiere acceso al lado servidor de BAS (logs del SP
`SP_ICR_COMPROB_APL`) — ya se agotaron las hipótesis verificables desde la API externa.
