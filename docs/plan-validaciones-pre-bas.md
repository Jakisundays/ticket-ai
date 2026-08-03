# Plan de validaciones pre-envío a BAS

**Objetivo:** identificar toda validación que debería correr sobre los datos de una factura ANTES de enviarlos a BAS, para evitar que BAS rechace la creación del comprobante, la orden de pago o la aplicación del pago — o, peor, que los acepte con datos incorrectos.

**Método:** este plan se construyó leyendo el código real (`utils/bas.py`, `routes/process_invoice_google_2.py`, el esquema de extracción de Gemini en `tools_standard.py`, las migraciones de PocketBase del repo hermano `ticket-ai-infra/pocketbase/pb_migrations/`, y los docs de incidentes reales ya documentados en `docs/`), y luego se sometió a una revisión adversarial en dos ejes: **cobertura** (¿qué campo enviado a BAS quedó sin validación?) y **precisión** (¿la regla citada es exactamente lo que dice el código, hoy?). Las correcciones de esa revisión ya están incorporadas abajo — se marcan con 🔧 donde cambiaron algo del análisis original, y se agregó una etapa nueva (Etapa 9) con los hallazgos que la revisión encontró y que no eran "validaciones de campo" sino bugs de integridad que deben resolverse junto con este plan.

**No implementado — es un plan.** Ninguna de estas validaciones existe hoy en código salvo que se indique explícitamente "ya implementado".

---

## Diagnóstico de partida

Hoy la única validación real que corre sobre los datos de una factura es `jsonschema.validate()` contra el esquema de las tools de Gemini (`process_invoice_google_2.py:803`): valida **tipos y presencia de campos**, nunca reglas de negocio (rangos, checksums, coherencia aritmética, formatos de fecha — ni siquiera se activa un `FormatChecker`). Todo lo demás que hoy impide (o no impide) que BAS rechace o corrompa datos vive disperso en comentarios de código, nombres de constantes y, sobre todo, en la memoria de incidentes reales ya sufridos en producción.

---

## 1. Mapa de etapas del flujo real

0. **Extracción y estructuración** (Gemini → `formatear_factura`) — dato 100% crudo.
1. **Resolución del proveedor en BAS** (`_obtener_o_verificar_proveedor_bas` — buscar por CUIT/RUC o dar de alta).
2. **Verificación de duplicado** (`GET /api/ConsultaComprobantesExternos`).
3. **Construcción y envío del Comprobante de Compra** (`POST /api/ComprobantesCompra`).
4. **Verificación post-escritura** (`GET /api/ConsultaComprobantes` independiente).
5. **Revisión y confirmación humana** (gate `review_status == "confirmed"` — único punto antes de mover dinero real).
6. **Construcción y envío de la Orden de Pago** (`POST /api/OrdenesPago`, siempre con `ComprobantesAplicados=[]`).
7. **Aplicación del comprobante a la Orden de Pago** (`POST /api/AplicacionesComprobantes`).
8. **Transversales / sistema** (autenticación, `monto_override`, reintentos, trazabilidad) — no son un paso secuencial, son controles que aplican en varios puntos.
9. **Integridad transversal y anti-duplicación** (hallazgos de la revisión crítica — no estaban en el análisis inicial).

---

## Etapa 0 — Extracción y estructuración de datos

**V0.1 — CUIT/RUC del emisor (`emisor.id_fiscal`)**
- **Campo:** `emisor.id_fiscal`, normalizado a solo dígitos.
- **Condición:** longitud válida para el tipo de documento fiscal (11 dígitos si es CUIT) y, si es calculable, dígito verificador correcto. Hoy solo se valida "no vacío".
- **Por qué importa:** un dígito mal leído por OCR pasa como CUIT sintácticamente plausible y puede terminar dando de alta un proveedor nuevo con identidad incorrecta en el maestro real de BAS — contamina el ERP con dinero real detrás. (`process_invoice_google_2.py:1381,1486-1497`)
- **Mensaje al usuario:** "El número de identificación fiscal del proveedor no parece válido. Revisá la factura y corregilo antes de continuar."
- **Etapa:** 0. **Prioridad:** Crítica.

**V0.2 — Asimetría emisor vs. receptor en `id_fiscal`**
- **Campo:** `emisor.id_fiscal`.
- **Condición:** aplicar la misma instrucción anti-alucinación que ya tiene `receptor.id_fiscal` ("si no está explícitamente escrito, no devolver valor") y no marcarlo como obligatorio en el esquema de extracción.
- **Por qué importa:** hoy el esquema fuerza al modelo a inventar un CUIT plausible cuando está borroso, porque es `required` sin salvaguarda (a diferencia del receptor). (`tools_standard.py:91-119` vs `121-148`)
- **Mensaje:** "No pudimos leer con certeza el identificador fiscal del emisor. Confirmalo manualmente."
- **Etapa:** 0. **Prioridad:** Alta.

**V0.3 — Número de comprobante y su parseo prefijo/número externo** 🔧
- **Campo:** `comprobante.numero` → `prefijo_externo` / `numero_externo`, resuelto en `_extraer_prefijo_numero_comprobante_externo` (líneas 121-134, único call-site en la línea 476 del flujo automático).
- **Condición:** debe contener un separador reconocible y la parte numérica debe ser dígitos puros; si no matchea el patrón esperado, **bloquear** en vez de caer silenciosamente a `numero_externo = 0`.
- **Por qué importa:** el fallback a `0` puede hacer que dos facturas distintas compartan el mismo "número externo 0" para el mismo prefijo, rompiendo la deduplicación real de `ConsultaComprobantesExternos` — riesgo de alta duplicada.
- **Mensaje:** "No pudimos identificar el número de comprobante de forma confiable. Por favor verificá y corregí el número antes de registrar la factura."
- **Etapa:** 0. **Prioridad:** Crítica.
- *Corrección de precisión: el borrador original citaba mal la ubicación del bug (decía que estaba "replicado" en las líneas 3304-3305 del endpoint de creación de OP humana; esas líneas solo leen un valor ya calculado, no vuelven a parsear nada). La validación en sí sigue siendo válida.*

**V0.4 — Moneda de la factura**
- **Campo:** `comprobante.moneda`.
- **Condición:** debe ser exactamente `"ARS"` (o la moneda que la instalación de BAS maneje) para permitir el envío automático; cualquier otro valor bloquea el registro hasta soporte de multi-moneda o conversión manual.
- **Por qué importa:** el payload hacia BAS no incluye ningún campo de moneda — una factura en USD se registraría con el monto nominal como si fueran pesos, sin conversión ni aviso, generando un registro contable real incorrecto. (`process_invoice_google_2.py:1582-1612,3306-3331`)
- **Mensaje:** "Esta factura parece estar en una moneda distinta a pesos. Por ahora no se puede registrar automáticamente — contactá al equipo para procesarla manualmente."
- **Etapa:** 0. **Prioridad:** Crítica.

**V0.5 — Fecha de emisión (`comprobante.fecha_emision`)**
- **Campo:** `comprobante.fecha_emision`.
- **Condición:** debe ser una fecha real y parseable (activar `FormatChecker` de jsonschema o parsear con `dateutil`), no una fecha futura, y no mayor a X períodos de atraso configurable.
- **Por qué importa:** hoy `format:'date'` es puramente decorativo en el schema (sin `FormatChecker`) y el valor viaja tal cual a `FechaComprobanteExterno`/vencimientos sin ningún parseo — un formato ambiguo (DD/MM vs MM/DD) o inventado pasa sin fricción. (`tools_standard.py:72-76`; `process_invoice_google_2.py:772-803`)
- **Mensaje:** "La fecha de emisión de la factura no es válida o no pudo leerse correctamente. Verificala antes de continuar."
- **Etapa:** 0. **Prioridad:** Alta.

**V0.6 — Cantidad, precio unitario y precio total por ítem** 🔧 *(ampliada)*
- **Campo:** `items.detalles[].cantidad`, `precio_unitario`, y en particular `precio_total` (el default silencioso más crítico de los tres, porque es exactamente el valor que se convierte en `ImporteGravado` — el monto contable real que BAS registra por línea).
- **Condición:** si Gemini no extrajo alguno de estos, no completar con default silencioso (`cantidad=1`, `precio_unitario=0`, `precio_total=0`); validar por ítem que `precio_total` no sea `None`/ausente antes de aceptar la línea, y validar que `cantidad × precio_unitario ≈ precio_total` (tolerancia de redondeo).
- **Por qué importa:** hoy `item.get("cantidad", 1)`, `item.get("precio_unitario", 0)` e `item.get("precio_total", 0)` insertan valores indistinguibles de datos reales en el ERP sin ninguna bandera de revisión. Si falta `precio_total`, la línea se contabiliza en BAS con importe $0 sin log ni bandera propia — y no hay garantía de que la reconciliación agregada de V0.7 lo detecte si el subtotal extraído también arrastra el mismo error de lectura. (`process_invoice_google_2.py:1523,1530-1531,3276-3277`)
- **Mensaje:** "Algunos ítems de la factura tienen cantidad o precio incompletos. Completalos antes de registrar la factura."
- **Etapa:** 0. **Prioridad:** Alta.

**V0.7 — Reconciliación entre `items.total`/`subtotal` extraído y la suma real de ítems** 🔧
- **Campo:** `items.subtotal` / `items.total` (extraídos) vs. suma de `ImporteGravado` calculada por el sistema.
- **Condición:** la diferencia entre ambos no debe superar un umbral razonable (1-2%, descontando el factor de IVA conocido); si la supera más allá de lo explicable por IVA, marcar para revisión humana antes de enviar a BAS.
- **Por qué importa:** sirve como red de seguridad genérica contra ítems faltantes o mal leídos en la extracción.
- **Mensaje:** "El total que figura en la factura no coincide con la suma de los ítems detectados. Puede haber un ítem faltante o un precio mal leído — revisá antes de continuar."
- **Etapa:** 0. **Prioridad:** Alta.
- *Corrección de precisión: el borrador original usaba como evidencia el incidente real de TotalGravado=11959.08 vs. suma de líneas 9883.54 — pero esa discrepancia se explica EXACTAMENTE por el 21% de IVA (9883.54×1.21=11959.08 exacto), no por un ítem faltante. Ese incidente sustenta V3.2 (la regla de cabecera del lado BAS), no el riesgo de "ítem faltante" que esta validación busca cubrir. La validación en sí sigue siendo razonable, pero necesita evidencia propia distinta si se quiere justificar con un incidente real.*

**V0.8 — Líneas de descuento/bonificación con importes negativos**
- **Campo:** `items.detalles[]` con `precio_total`/`precio_unitario`/`cantidad` negativos o descripción de descuento.
- **Condición:** deben excluirse (o tratarse con una categoría/`CodigoItem` contable específica) del array que se envía a BAS, igual que ya se hace para Google Sheets.
- **Por qué importa:** hoy el filtro `_es_descuento()` solo se aplica a las filas de Sheets; el loop que arma `items_bas` no lo usa, por lo que un descuento puede llegar a BAS con una categoría sin sentido y un `ImporteGravado` negativo, distorsionando `total_gravado`. (`process_invoice_google_2.py:1251-1265` vs `1520-1554`)
- **Mensaje:** "Detectamos un descuento o bonificación en la factura. Confirmá cómo debe registrarse antes de continuar."
- **Etapa:** 0. **Prioridad:** Alta.

**V0.9 — CAE y vencimiento de CAE** 🔧 *(reformulada — requisito incondicional, no condicional)*
- **Campo:** `otros.CAE`, `otros.vencimiento_CAE`.
- **Condición:** deben estar presentes y el CAE debe tener el formato numérico esperado (14 dígitos), con fecha de vencimiento parseable — **para toda factura procesada por este pipeline**, no solo "si es electrónica".
- **Por qué importa:** `BAS_EMITIDO_POR_CAE = "2"` está hardcodeado y se envía siempre, incondicionalmente, para toda factura — no hay ninguna rama en el código que derive `EmitidoPor` del tipo real de comprobante. Por lo tanto BAS exige CAE/vencimiento en runtime para toda factura, incluidas facturas C de monotributistas que legítimamente no tienen CAE. Peor: si el CAE fue alucinado por el modelo, el comprobante queda etiquetado como electrónico verificado sin que nada lo confirme. (`utils/bas_config.py:108`; uso en `process_invoice_google_2.py:1598,3318`)
- **Mensaje:** "Falta el CAE o su fecha de vencimiento en esta factura. Verificalo antes de registrarla."
- **Etapa:** 0. **Prioridad:** Crítica.
- **Recomendación adicional (no solo validación):** derivar `EmitidoPor` dinámicamente según el tipo real de comprobante/condición del emisor en vez de hardcodearlo — hoy el sistema no tiene forma de manejar correctamente una factura no electrónica.

**V0.10 — Condición de IVA (`emisor.condicion_iva` / `receptor.condicion_iva`)**
- **Campo:** ambos.
- **Condición:** el valor debe pertenecer al enum correspondiente a esa entidad (son enums distintos entre emisor y receptor) y no cruzarse.
- **Por qué importa:** si el modelo devuelve un valor válido para el otro rol (p. ej. "Monotributo" en el objeto receptor), la extracción entera falla y reintenta hasta 6 veces. (`tools_standard.py:107-116,137-145`)
- **Mensaje:** "No pudimos determinar con certeza la condición fiscal del emisor o receptor. Verificá los datos de la factura."
- **Etapa:** 0. **Prioridad:** Media.

**V0.11 — Categoría de ítem (`categoria`) y su `CodigoItem` resultante**
- **Campo:** `categoria` → `CodigoItem` (vía `codigo_item_de_categoria`).
- **Condición:** el `CodigoItem` resuelto debe tener configurada la `PosicionContable` para el concepto **COM** (Compras) en el catálogo de BAS antes de usarlo en una línea de comprobante.
- **Por qué importa:** solo 193 de 255 ítems (76%) tienen esa posición configurada; usar uno sin ella provoca el rechazo real "La posición contable del ítem ... no está definida para el concepto COM (ACTUREF)", y generó un diagnóstico erróneo de varios días. (`bas-orden-de-pago-research.md:226,270-278`)
- **Mensaje:** "Uno de los ítems de la factura no tiene una categoría contable configurada correctamente en el sistema. Contactá al equipo antes de continuar."
- **Etapa:** 0. **Prioridad:** Crítica.

**V0.12 — Normalización de moneda/tipo/subtipo/forma de pago**
- **Campo:** `invoices.moneda`, `tipo_comprobante`, `subtipo_comprobante`, `forma_pago` (texto libre en PocketBase, sin enum).
- **Condición:** normalizar contra un catálogo cerrado antes de persistir/usar, en vez de aceptar cualquier string de la extracción.
- **Por qué importa:** sin normalización, valores como "ARS" vs "Pesos" vs "$" conviven en la base y pueden interpretarse distinto en cada punto del pipeline. (migración `1783483765_create_invoices_collection.js`, repo `ticket-ai-infra`)
- **Mensaje:** "Algunos datos de la factura no tienen un formato reconocido. Revisalos antes de continuar."
- **Etapa:** 0. **Prioridad:** Media.

**V0.13 — Deduplicación por `content_hash`**
- **Campo:** `content_hash` del archivo.
- **Condición:** verificar explícitamente en código de aplicación (no confiar en el índice de base) que no exista ya una factura con el mismo hash antes de iniciar el registro contable.
- **Por qué importa:** el índice de `content_hash` en PocketBase no es único — el dedup es 100% lógica de aplicación; un bug ahí dejaría pasar una factura duplicada directo a BAS. (migración `1783483905_create_batch_import_collections.js`, repo `ticket-ai-infra`)
- **Mensaje:** "Esta factura parece ya haber sido cargada anteriormente. Verificá antes de procesarla de nuevo."
- **Etapa:** 0. **Prioridad:** Alta.

> Nota: las migraciones de PocketBase citadas en este plan (`1783483*.js`) viven en el repo hermano `ticket-ai-infra/pocketbase/pb_migrations/`, no dentro de `Invoicy/`.

---

## Etapa 1 — Resolución del proveedor en BAS

**V1.1 — Código de proveedor (`Proveedor.Codigo`)**
- **Campo:** `Codigo`, derivado de la razón social.
- **Condición:** ≤ 8 caracteres, alfanumérico sin acentos/espacios, y verificar que no colisione con un proveedor ya existente con otra identidad antes de usarlo (no solo truncar y confiar).
- **Por qué importa:** el truncado a 8 caracteres no garantiza unicidad — una colisión pisaría datos de otro proveedor o generaría un alta duplicada mal identificada. (`utils/bas.py:318,1032-1041,705`)
- **Mensaje:** "No pudimos generar un código único para este proveedor. Se requiere revisión manual."
- **Etapa:** 1. **Prioridad:** Alta.

**V1.2 — Tratamiento impositivo (`TratImpositivo`/`TratImpositivoProv`)** 🔧
- **Campo:** códigos de tratamiento impositivo.
- **Condición:** hoy el sistema usa constantes hardcodeadas (`BAS_TRAT_IMPOSITIVO_RI="2"`, `BAS_TRAT_IMPOSITIVO_PROV_RI="1"`) verificadas una vez contra esta instalación real — no se consultan en runtime. La validación necesaria es distinta a "consultarlas siempre en vivo": debe alertar si el sistema se despliega alguna vez contra otra instalación de BAS sin re-verificar esos códigos contra `GET /api/TratamientosImpositivos(Provinciales)`.
- **Por qué importa:** un código válido en otra instalación de BAS puede ser inválido (o aceptado con semántica fiscal incorrecta) en esta. (`utils/bas_config.py:109-110`; `utils/bas.py:455-471`)
- **Mensaje:** "No se pudo determinar el tratamiento impositivo correcto para este proveedor. Contactá al equipo antes de darlo de alta."
- **Etapa:** 1. **Prioridad:** Crítica (como control de despliegue, no como llamada en runtime).
- *Corrección de precisión: el borrador original describía esto como si ya se consultara en vivo contra BAS ("nunca hardcodeados"); en realidad el único caller real pasa constantes hardcodeadas verificadas manualmente una vez.*

**V1.3 — Cuenta corriente por defecto del proveedor (`CuentasCorrientes[].ImputacionContable`)**
- **Campo:** `CuentasCorrientes[]` al dar de alta un proveedor nuevo.
- **Condición:** siempre debe incluir una cuenta con `ImputacionContable=211001` (verificada empíricamente en 25/25 proveedores reales) y `PorDefecto:true`.
- **Por qué importa:** sin esto, cualquier `ComprobanteCompra` con `MetodoPago='C'` falla con "no se pudo establecer la moneda correspondiente a la cuenta 0" — ya ocurrió en producción con SUPERCOOP (18-jul-2026). Ya existe un fix (`asegurar_cuenta_corriente_proveedor`), pero solo si el caller pasa `imputacion_contable`; si no se pasa nada, el bug puede reaparecer. (`utils/bas.py:472-482,531-566,690-703`)
- **Mensaje:** "No se pudo configurar la cuenta contable de este proveedor. La factura no puede registrarse hasta resolverlo."
- **Etapa:** 1. **Prioridad:** Crítica.

**V1.4 — Actualización de proveedor existente (PUT completo, no parcial)**
- **Campo:** cualquier campo de `Proveedores/{codigo}` al actualizar.
- **Condición:** siempre hacer GET completo + merge + reescritura entera; nunca un PUT con objeto parcial.
- **Por qué importa:** no está documentado si BAS soporta reemplazo parcial — un PUT parcial mal soportado podría vaciar campos existentes (ej. `CuentasCorrientes`) sin aviso. (`utils/bas.py:511-529`)
- **Mensaje:** "No se pudo actualizar la información del proveedor de forma segura. Reintentá o contactá soporte." (interno)
- **Etapa:** 1. **Prioridad:** Alta.

> Nota descartada: `Proveedor.Empresas[]` vacío **no** es causa de ningún rechazo (9/25 proveedores reales activos lo tienen vacío) — no gastar esfuerzo de validación ahí.

---

## Etapa 2 — Verificación de duplicado del comprobante

**V2.1 — Combinación de parámetros de `ConsultaComprobantesExternos`**
- **Campo:** `FechaComprobanteExterno` + `PrefijoComprobanteExterno` + `NumeroComprobanteExterno`.
- **Condición:** los tres deben estar presentes y ser la numeración **externa** (del proveedor), nunca mezclados con numeración interna de BAS.
- **Por qué importa:** BAS exige exactamente una de dos combinaciones completas; una combinación parcial o mezclada nunca llega a validarse en el servidor, falla local o da un 400 confuso. (`utils/bas.py:256-287`)
- **Mensaje:** "Faltan datos del número de comprobante del proveedor para verificar si ya fue cargado. Completalos antes de continuar."
- **Etapa:** 2. **Prioridad:** Crítica.

---

## Etapa 3 — Construcción y envío del Comprobante de Compra

**V3.1 — Fecha de asiento (`ComprobanteCompra.Fecha`)**
- **Campo:** `Fecha` (cabecera).
- **Condición:** debe ser la fecha de hoy (fecha de asiento en el subdiario), nunca la fecha real del documento — esa va aparte en `FechaComprobanteExterno`.
- **Por qué importa:** usar la fecha real dispara "fecha anterior al cierre operativo del subdiario" en cuanto la factura tiene más de un período contable de atraso — ya pasó con una factura real (Litoral Gas, emitida 2025-05-21, cargada 2026-07-21). (`process_invoice_google_2.py:1585-1595,3309-3315`)
- **Mensaje:** "Esta factura tiene una fecha antigua. Se registrará contablemente con la fecha de hoy; el número y fecha original del documento quedan guardados igual."
- **Etapa:** 3. **Prioridad:** Crítica.

**V3.2 — Total / TotalGravado de cabecera**
- **Campo:** `Total`, `TotalGravado`.
- **Condición:** deben ser exactamente la suma redondeada (2 decimales) de `ImporteGravado` de los ítems — nunca el total con IVA incluido ni un "subtotal" extraído aparte.
- **Por qué importa:** BAS valida esta igualdad y rechaza con "no coincide con la suma de los totales gravados de las líneas" si no matchea — confirmado con una factura real (11959.08 vs 9883.54 esperado, exactamente el factor de IVA 21%). (`process_invoice_google_2.py:1556-1570,3293-3298`)
- **Mensaje:** "Hubo un problema calculando el total de la factura para registrarla contablemente. Contactá al equipo técnico."
- **Etapa:** 3. **Prioridad:** Crítica.

**V3.3 — Redondeo a 2 decimales**
- **Campo:** `Total`, `TotalGravado`, `ImporteGravado`.
- **Condición:** redondear explícitamente a 2 decimales antes de enviar (no confiar en la suma de floats).
- **Por qué importa:** la acumulación de ruido de precisión (ej. 54981.340000000004) es rechazada por BAS con "must have not more than 5 decimals" — confirmado en runtime el 2026-07-31. (`process_invoice_google_2.py:1567-1570,3296-3298`)
- **Mensaje:** "Hubo un error de formato en los montos de la factura. Reintentá o contactá soporte." (técnico)
- **Etapa:** 3. **Prioridad:** Alta.

**V3.4 — `ImporteTotal` de cada ítem**
- **Campo:** `Items[].ImporteTotal`.
- **Condición:** debe ser igual a `ImporteGravado` del mismo ítem — nunca sumarle el IVA a mano.
- **Por qué importa:** si no coincide con lo que BAS calcula internamente a partir del `Impuesto` configurado en el catálogo del ítem, rechaza con "no son consistentes" (confirmado, corregido, con 2 altas reales exitosas tras el fix). (`process_invoice_google_2.py:1532-1550,3278-3287`)
- **Mensaje:** "Hubo una inconsistencia en el cálculo de impuestos de un ítem. Contactá al equipo técnico." (técnico)
- **Etapa:** 3. **Prioridad:** Crítica.

**V3.5 — Depósito (`ComprobanteCompra.Deposito`)**
- **Campo:** `Deposito`.
- **Condición:** debe enviarse explícitamente el depósito real de la instalación (valor verificado: `1`, "DEPÓSITO PLATINUM") — nunca confiar en un default de 0.
- **Por qué importa:** sin esto, BAS rechaza de entrada con "El depósito 0 no pertenece a la empresa". (`bas-orden-de-pago-research.md:220`)
- **Mensaje:** "No se pudo determinar el depósito para registrar la factura. Contactá al equipo técnico." (configuración de sistema)
- **Etapa:** 3. **Prioridad:** Crítica.

**V3.6 — Método de pago (`ComprobanteCompra.MetodoPago`)**
- **Campo:** `MetodoPago`.
- **Condición:** enviar siempre explícitamente `"C"` (Cuenta Corriente) — nunca dejar el default del campo, que es `"D"` (Contado).
- **Por qué importa:** si se deja el default, BAS exige líneas de pago completas en el mismo POST, registrando la factura como ya pagada — rompe deliberadamente la separación entre registro y pago que sostiene todo el flujo de dos pasos. (`bas-orden-de-pago-research.md:318`)
- **Etapa:** 3. **Prioridad:** Crítica. (configuración de sistema, no depende del usuario)

**V3.7 — Ítems: al menos 1 línea real, con nombres de campo correctos**
- **Campo:** `Items[]`.
- **Condición:** nunca enviar vacío (mínimo 1 ítem); usar los nombres reales `CantidadPrimeraUnidad`, `PrecioUnitario`, `ImporteGravado`, `ImporteTotal`, `NumeroUnidadMedida="1"` — no un campo `Cantidad` inexistente en el schema real.
- **Por qué importa:** cabecera sin ítems crashea el stored procedure de BAS con error de sintaxis SQL; un nombre de campo incorrecto deja las cantidades en 0 silenciosamente, sin error visible en el momento. (`bas-orden-de-pago-research.md:222,225,235`)
- **Mensaje:** "No se detectaron ítems válidos en la factura para registrar. Revisá el documento antes de continuar."
- **Etapa:** 3. **Prioridad:** Crítica.

**V3.8 — Tipo de entrega del ítem (`Items[].TipoEntrega`)**
- **Campo:** `TipoEntrega`.
- **Condición:** debe ser `"E"` (entrega pendiente, no mueve stock) — nunca `"O"`.
- **Por qué importa:** `"O"` intenta mover mercadería contra un remito, y el talonario de facturas de compra tipo A de esta instalación no tiene remito asociado. (`bas-orden-de-pago-research.md:223`)
- **Etapa:** 3. **Prioridad:** Crítica. (configuración de sistema)

**V3.9 — Caja del comprobante (`ComprobanteCompra.Caja`)**
- **Campo:** `Caja`.
- **Condición:** enviar explícitamente `"1"` — no confiar en que la Sucursal tenga una caja default configurada.
- **Por qué importa:** sin esto, BAS rechaza citando un problema de configuración de la Sucursal, fácil de mal-diagnosticar como error del lado Invoicy. (`bas-orden-de-pago-research.md:224`)
- **Etapa:** 3. **Prioridad:** Crítica. (configuración de sistema)

**V3.10 — Centro de apropiación (`Items[].CentroApropiacionA/B`)**
- **Campo:** `CentroApropiacionA`, `CentroApropiacionB`.
- **Condición:** enviar `"SD"` (sin definir) en ambos si no hay un centro de costos específico.
- **Por qué importa:** sin este campo, el INSERT en BAS falla por violación de clave foránea, exponiendo un error de bajo nivel de base de datos directamente al cliente. (`bas-orden-de-pago-research.md:316`)
- **Etapa:** 3. **Prioridad:** Crítica. (configuración de sistema)

**V3.11 — Numeración externa de la factura**
- **Campo:** `PrefijoComprobanteExterno`/`NumeroComprobanteExterno`/`FechaComprobanteExterno`.
- **Condición:** deben coincidir exactamente con el documento real del proveedor (ver V0.3) y no ser sobreescritos por un valor cacheado obsoleto al reutilizarse en pasos posteriores (creación de OP).
- **Por qué importa:** es la clave que usa `ConsultaComprobantesExternos` para dedup — un valor mal parseado produce falsos negativos de "no existe" y altas duplicadas.
- **Mensaje:** "El número de comprobante del proveedor no pudo verificarse correctamente. Confirmalo antes de registrar la factura."
- **Etapa:** 3. **Prioridad:** Crítica.

**V3.12 — Código de tipo de comprobante**
- **Campo:** código de `Comprobante` en el alta (`MA`/`MB`/`MC`/`MI`/`MM`).
- **Condición:** debe ser uno de los códigos válidos para factura de compra; en la práctica siempre `"MA"`.
- **Por qué importa:** usar otro código rompe la coherencia entre `ConsultaComprobantesExternos` y `ComprobantesCompra`. (`utils/bas.py:29-30`)
- **Etapa:** 3. **Prioridad:** Media. (configuración de sistema)

**V3.13 — Campo `Vencimientos`** *(nueva, hallada en la revisión crítica — no estaba en el análisis original)*
- **Campo:** `Vencimientos[0].FechaVencimiento` / `.Importe`, enviado en cada `ComprobanteCompra`.
- **Condición:** `FechaVencimiento` debe ser una fecha real parseada (misma validación que V0.5); definir explícitamente si `Importe` debe ser el neto (`total_gravado`) o el total con IVA incluido (lo que efectivamente vence a pagar) y validar consistencia con ese criterio.
- **Por qué importa:** este campo se manda siempre pero ningún análisis previo lo había cubierto; hereda el mismo riesgo de fecha no parseada que V0.5, aplicado a un campo distinto. (`process_invoice_google_2.py:1610`)
- **Mensaje:** "No pudimos determinar correctamente la fecha o el monto de vencimiento de esta factura. Verificalo antes de continuar."
- **Etapa:** 3. **Prioridad:** Media.

---

## Etapa 4 — Verificación post-escritura del comprobante

**V4.1 — Confirmación de persistencia real**
- **Campo:** respuesta de `GET /api/ConsultaComprobantes` tras el alta.
- **Condición:** debe confirmar que el comprobante quedó realmente persistido antes de continuar al paso de Orden de Pago — no confiar ciegamente en el 201 de creación.
- **Por qué importa:** ya es la práctica implementada en el código; mantenerla es la red de seguridad que evita construir una Orden de Pago sobre un comprobante que en realidad no quedó registrado.
- **Mensaje:** "No pudimos confirmar que la factura quedó registrada correctamente. No se generará la orden de pago hasta verificarlo."
- **Etapa:** 4. **Prioridad:** Alta.

---

## Etapa 5 — Revisión y confirmación humana (gate antes de mover dinero real)

**V5.1 — `review_status` explícito** 🔧 *(prioridad revisada a la baja)*
- **Campo:** `invoices.review_status`.
- **Condición:** tratar tanto `""` como ausencia del campo como no confirmado.
- **Por qué importa:** en el único lugar donde realmente se autoriza el pago real (`crear_orden_pago`, línea 3187), la condición ya es `if invoice.get("review_status") != "confirmed"` — una comparación por desigualdad que ya trata correctamente `""` y ausencia como no confirmado. El riesgo remanente es solo en código futuro o en otras vistas (dashboard) que puedan comparar por igualdad contra `"needs_review"` en vez de desigualdad contra `"confirmed"`.
- **Mensaje:** "Esta factura todavía no fue confirmada por un revisor. No se puede generar el pago hasta que se confirme."
- **Etapa:** 5. **Prioridad:** Media *(bajada de Alta — el gate crítico ya está bien implementado)*.

**V5.2 — Re-derivación de datos tras corrección humana**
- **Campo:** `proveedor_codigo`, `comprobante_prefijo`, `comprobante_numero` usados al crear la Orden de Pago real.
- **Condición:** si la revisión humana corrigió el CUIT del emisor o el número de comprobante, estos tres valores deben recalcularse contra los datos actuales del invoice, no reutilizar los resueltos durante el procesamiento automático original (dry run).
- **Por qué importa:** hoy `crear_orden_pago` los toma de `bas_processing_status` calculado ANTES de la revisión humana — si el humano corrigió un dato mal extraído, el pago real podría vincularse al proveedor o comprobante equivocado pese a que ya se arregló en el dashboard. Es dinero real mal direccionado. (`process_invoice_google_2.py:3171-3172,3190-3193,3304-3305`)
- **Mensaje:** "Los datos del proveedor o del comprobante fueron corregidos después del procesamiento inicial. Estamos revalidando antes de generar el pago."
- **Etapa:** 5. **Prioridad:** Crítica.

**V5.2b — `bas_codigo_item` no se recalcula tras corregir la categoría** *(nueva, hallada en la revisión crítica)*
- **Campo:** `invoice_items.bas_codigo_item`.
- **Condición:** al confirmar el pago (o al guardar cualquier edición de `categoria` en `invoice_items`), recalcular `bas_codigo_item` con `codigo_item_de_categoria(categoria_actual)` antes de construir `items_bas`, y bloquear el envío si el resultado es `None`/vacío.
- **Por qué importa:** el campo se calcula una sola vez al persistir la factura y `crear_orden_pago` lo lee tal cual desde PocketBase sin recalcularlo. Si un revisor corrige la categoría de un ítem durante la revisión (exactamente el tipo de corrección que V5.2 anticipa para otros campos), el gasto queda mal imputado contablemente pese a que ya se corrigió en la UI. (`process_invoice_google_2.py:3273`)
- **Mensaje:** "La categoría de un ítem fue corregida después del procesamiento inicial. Estamos revalidando la imputación contable antes de generar el pago."
- **Etapa:** 5. **Prioridad:** Alta.

**V5.3 — Inmutabilidad efectiva de una factura confirmada**
- **Campo:** cualquier campo de `invoices`/`invoice_items` de una factura ya `confirmed` (o con `payment_order` exitosa).
- **Condición:** verificar en código de aplicación (no solo en hooks) que no se editaron datos relevantes entre la confirmación y la creación efectiva de la Orden de Pago; si hubo cambios, forzar nueva revisión.
- **Por qué importa:** la protección declarativa de PocketBase (`updateRule`) no impide editar una factura confirmada — el candado real vive en hooks JS que no fueron auditados en esta pasada.
- **Mensaje:** "Los datos de esta factura cambiaron después de la confirmación. Se requiere una nueva revisión antes de continuar."
- **Etapa:** 5. **Prioridad:** Media.

**V5.4 — Trazabilidad de quién confirmó/solicitó** 🔧
- **Campo:** `confirmed_by`, `requested_by`, `deleted_by`, `created_by`.
- **Condición:** el diseño actual ya resuelve esto en el backend intermedio (Next.js), no en Invoicy directamente: Invoicy confía en que el único caller autorizado (el backend de Next.js, que ya resolvió la sesión real) los llena correctamente, protegido por el secreto compartido `_verificar_secreto_invoicy`. La validación pendiente no es "que Invoicy valide la sesión" sino evaluar si esa barrera (secreto compartido) es suficiente — rotación del secreto, logging de qué llamó al backend Next.js.
- **Por qué importa:** si esa barrera se debilita (secreto filtrado, endpoint expuesto sin el header), se rompe la auditoría de quién autorizó cada pago real. (`process_invoice_google_2.py:3435-3441`)
- **Etapa:** 5. **Prioridad:** Alta.
- *Corrección de precisión: el borrador original presentaba esto como un riesgo no mitigado; en realidad es un diseño deliberado y documentado con una mitigación real (secreto compartido) que el plan original no reconocía.*

**V5.5 — Monto de la Orden de Pago (`payment_orders.monto`)**
- **Campo:** `monto`.
- **Condición:** mayor a 0, y coincidente con el total de la factura salvo que se use `monto_override` de forma explícita y controlada (ver V8.1).
- **Por qué importa:** el campo es `required` en PocketBase pero sin mínimo — un 0 o negativo pasaría el schema de la base sin problema y generaría una orden de pago sin sentido económico.
- **Mensaje:** "El monto a pagar no es válido. Verificalo antes de generar la orden de pago."
- **Etapa:** 5. **Prioridad:** Crítica.

---

## Etapa 6 — Construcción y envío de la Orden de Pago

**V6.1 — Talonario de la OP (`OrdenDePago.Prefijo`)**
- **Condición:** siempre presente, resuelto dinámicamente (`buscar_prefijo_talonario`) o desde configuración conocida — aunque el schema de BAS no lo marque required.
- **Por qué importa:** BAS lo exige en runtime pese a no marcarlo required en el Swagger — confirmado con un 400 real `{Prefijo: required}`. (`utils/bas.py:765-767`)
- **Etapa:** 6. **Prioridad:** Crítica. (configuración de sistema)

**V6.2 — Caja de la OP**
- **Condición:** siempre presente.
- **Por qué importa:** sin ella, ninguno de los arrays de medios de pago puede procesarse aunque el resto del payload sea válido. (`utils/bas.py:413-414,769-770`)
- **Etapa:** 6. **Prioridad:** Alta. (configuración de sistema)

**V6.3 — Cuenta corriente del proveedor en la OP**
- **Campo:** `CodigoCuentaCorriente` (= `Proveedor.Codigo`, NO la cuenta contable) + `PrefijoCuentaCorriente`.
- **Condición:** `CodigoCuentaCorriente` debe ser el código maestro del proveedor; `PrefijoCuentaCorriente` debe ser exactamente `"P"` para pagos a proveedores.
- **Por qué importa:** confundir `CodigoCuentaCorriente` con `ImputacionContable` liga la OP a la cuenta contable equivocada; un `PrefijoCuentaCorriente` fuera de `{C,P,A}` es rechazado con 400. Ambos son requeridos en runtime aunque el Swagger no lo marque. (`utils/bas.py:376-384,424-430`)
- **Mensaje:** "No se pudo vincular correctamente la cuenta del proveedor a la orden de pago. Contactá al equipo técnico."
- **Etapa:** 6. **Prioridad:** Crítica.

**V6.4 — No combinar mecanismos de imputación**
- **Condición:** `ImputacionContable` (cabecera) vs. `PrefijoCuentaCorriente`+`CodigoCuentaCorriente` son alternativos — usar solo uno.
- **Por qué importa:** combinarlos no da el error esperado de "conflicto" sino uno que aparenta ser un problema de configuración de cuentas, llevando a diagnósticos equivocados. (`bas-orden-de-pago-research.md:369`)
- **Etapa:** 6. **Prioridad:** Alta. (configuración de sistema)

**V6.5 — Sentido del movimiento (`IngresooEgreso`)**
- **Condición:** siempre `"E"` (Egreso) para una Orden de Pago a proveedor.
- **Por qué importa:** enviar `"I"` sería semánticamente un cobro, no un pago, descuadrando la cuenta corriente del proveedor real. (`utils/bas.py:58-60,82-86`)
- **Etapa:** 6. **Prioridad:** Crítica. (control automático interno)

**V6.6 — `ComprobantesAplicados` vacío al crear la OP**
- **Condición:** siempre `[]` en el POST de creación.
- **Por qué importa:** aplicar en el mismo paso dispara el error de identidad de Windows del servicio BAS con su SQL Server ("El usuario SERVERBAS2\Administrator no existe..."), no corregible desde el payload — es la razón de ser del flujo en dos pasos. (`utils/bas.py:606-618,860-866`)
- **Etapa:** 6. **Prioridad:** Crítica. (arquitectura)

**V6.7 — Nombre de clave del array de medio de pago**
- **Condición:** debe ser exactamente una de `Efectivos`/`PagosPorBanco`/`CobrosPorBanco`/`Cheques`/`ChequesPropios`/`Pagares`/`PagaresPropios`/`Tarjetas`.
- **Por qué importa:** cualquier otro nombre da un 400 genérico difícil de diagnosticar. (`utils/bas.py:34-46,447-452`)
- **Etapa:** 6. **Prioridad:** Media. (configuración de sistema)

**V6.8 — Código de `MedioPago` dentro de cada array**
- **Condición:** usar únicamente los códigos verificados empíricamente para esta instalación (Efectivo: 1/2/10/11/12; Cheque recibido: 4/6; transferencia: 8; Tarjeta: 9) — nunca inventar un código.
- **Por qué importa:** no existe endpoint que exponga este catálogo; un código no verificado es rechazado, y el rango 13-20 directamente no existe. (`bas-orden-de-pago-research.md:328-331,625-663`)
- **Mensaje:** "El medio de pago seleccionado no está disponible en el sistema. Elegí otro medio de pago."
- **Etapa:** 6. **Prioridad:** Crítica.

**V6.9 — Array `Efectivos`: sin campo `Fecha`**
- **Condición:** no incluir `Fecha` (solo `MedioPago`, `Importe`, `IngresooEgreso`).
- **Por qué importa:** `additionalProperties:false` — agregar `Fecha` rompe la llamada; confirmado en vivo el 21-jul-2026. (`process_invoice_google_2.py:3206-3213`)
- **Etapa:** 6. **Prioridad:** Alta. (configuración de sistema)

**V6.10 — Array `Cheques`: campos obligatorios y tipo correcto**
- **Condición:** requiere `Fecha` + `NumeroExterno`; usar siempre `Cheques` (cheque de terceros), nunca `ChequesPropios` en esta instalación.
- **Por qué importa:** `ChequesPropios` fallaría porque no hay ningún medio de pago tipo cheque propio habilitado en Caja 1. (`process_invoice_google_2.py:3214-3226`)
- **Mensaje:** "El tipo de cheque seleccionado no está habilitado. Elegí 'cheque recibido/de terceros'."
- **Etapa:** 6. **Prioridad:** Alta.

**V6.11 — Array `PagosPorBanco`** 🔧 *(separada en dos)*
- **(a) `CuentaBancaria` obligatoria — ya implementado, mantener.** El endpoint `crear_orden_pago` ya valida `if not metodo_bas.get("bas_cuenta_bancaria"): raise HTTPException(422, ...)` antes de armar el payload. (`process_invoice_google_2.py:3227-3229`) No es una brecha, es una validación existente que debe conservarse.
- **(b) Truncado silencioso de `Numero` a 15 caracteres — brecha real, pendiente.** `Numero` (transferencia o `process_id`) se trunca a 15 caracteres en silencio (`process_invoice_google_2.py:3235`); debería advertirse en vez de truncar sin aviso, para no perder información necesaria para conciliar el pago.
- **Mensaje:** "El número de transferencia es muy largo y se recortará — verificá que siga siendo identificable."
- **Etapa:** 6. **Prioridad:** Media (solo el punto b sigue pendiente).

**V6.12 — Array `Tarjetas`: `Plan` y `CodigoTarjeta` obligatorios**
- **Condición:** deben existir en el catálogo de tarjetas configurado (`bas_payment_methods`) antes de intentar el pago.
- **Por qué importa:** ya rechaza con 422 antes de llegar a BAS si faltan — mantener esta validación existente. (`process_invoice_google_2.py:3238-3254`)
- **Etapa:** 6. **Prioridad:** Crítica (mantener lo ya implementado).

**V6.13 — Importe de aplicación positivo**
- **Campo:** `ComprobantesAplicados[].Importe` y por extensión cualquier importe de pago.
- **Condición:** siempre mayor a 0.
- **Por qué importa:** BAS valida el signo antes de buscar el comprobante — un importe negativo o cero da un mensaje distinto de rechazo. (`bas-orden-de-pago-research.md:344,363`)
- **Mensaje:** "El monto a aplicar debe ser mayor a cero. Verificá el monto antes de continuar."
- **Etapa:** 6. **Prioridad:** Crítica.

---

## Etapa 7 — Aplicación del comprobante a la Orden de Pago

**V7.1 — Código de comprobante en la aplicación (`Comprobante`)**
- **Condición:** siempre la constante fija `"OP"` (2 caracteres) — nunca `"OPG"` (3 caracteres), que es lo que devuelve la propia respuesta de creación de la OP.
- **Por qué importa:** es el bug más fácil de cometer — usar el valor que BAS acaba de devolver en el 201 en vez de la constante fija rompe la aplicación con un 400. (`utils/bas.py:48-52,620-624,976`)
- **Etapa:** 7. **Prioridad:** Crítica. (control interno)

**V7.2 — Imputación contable obligatoria**
- **Campo:** `AplicacionesComprobantes.ImputacionContable`.
- **Condición:** siempre presente (valor verificado: `211001`) aunque el schema de BAS lo marque nullable.
- **Por qué importa:** sin ella, BAS responde "Debe indicar la imputación contable" — hoy el flujo de alto nivel directamente salta este paso si no se pasó, dejando la OP creada pero sin aplicar (dinero comprometido sin conciliar). (`utils/bas.py:625-626,954-960`)
- **Mensaje:** "No se pudo aplicar el pago al comprobante por un dato faltante. La orden de pago quedó creada pero requiere conciliación manual."
- **Etapa:** 7. **Prioridad:** Crítica.

**V7.3 — Numeración externa en la aplicación**
- **Condición:** `Prefijo`/`Numero` deben ser la numeración externa de la factura del proveedor, nunca la interna asignada por BAS.
- **Por qué importa:** con la numeración interna, BAS responde "no existe para aplicarlo" — BAS resuelve externo→interno por su cuenta. (`utils/bas.py:627-630,976-989`)
- **Etapa:** 7. **Prioridad:** Crítica. (control interno)

**V7.4 — Numeración interna de la OP en la cabecera de aplicación**
- **Condición:** si la respuesta de creación de la OP no trae `Prefijo`/`Numero`, no intentar aplicar — dejar la OP marcada explícitamente como "sin aplicar" para reconciliación manual, nunca mandar un valor vacío/None a BAS.
- **Por qué importa:** mandar un valor arbitrario o vacío generaría un pago "huérfano" registrado contra nada identificable. (`utils/bas.py:924-927,962-969`)
- **Mensaje:** "La orden de pago se creó correctamente pero no pudo aplicarse automáticamente al comprobante. Quedó pendiente de conciliación manual."
- **Etapa:** 7. **Prioridad:** Crítica.

**V7.5 — Detección de la limitación sistémica no resoluble (`SP_ICR_COMPROB_APL`)**
- **Condición:** si la firma del error coincide con `SP_ICR_COMPROB_APL`/`SP_ICR_COMPROB_CAJA`, marcar la operación como "requiere soporte de BAS" y no reintentar automáticamente esperando un resultado distinto.
- **Por qué importa:** se probaron ~37 variantes de payload en dos investigaciones separadas sin éxito — es una limitación confirmada del stored procedure de esta instalación, no un problema de datos de entrada. Reintentar generaría más órdenes de pago sueltas sin aplicar. (`evidencia-sp-icr-comprob-apl-para-bas.md`)
- **Mensaje:** "No pudimos aplicar el pago al comprobante por una limitación del sistema contable. El equipo técnico ya fue notificado para resolverlo con soporte de BAS."
- **Etapa:** 7. **Prioridad:** Crítica.

---

## Etapa 8 — Transversales / sistema

**V8.1 — `monto_override` (mecanismo de importación masiva de prueba)**
- **Condición:** nunca negativo ni cero; su uso debe quedar registrado explícitamente como "monto de prueba/importación", nunca indistinguible de una factura real; restringido a flujos de importación masiva. Consistente con la regla ya establecida: toda prueba real contra BAS debe ser Total=1 peso.
- **Por qué importa:** reemplaza in-place todo el bloque de ítems/subtotal/total antes de Sheets/BAS/PocketBase — sin control, una factura real podría quedar registrada con un monto ficticio sin rastro explícito de que era de prueba. (`process_invoice_google_2.py:1926-1959,2042-2051`)
- **Mensaje:** "Esta carga usa un monto de prueba y no refleja el valor real de la factura — confirmá que corresponde a un proceso de importación controlado."
- **Etapa:** 8. **Prioridad:** Crítica.

**V8.2 — Credenciales de autenticación disponibles**
- **Condición:** deben existir antes de intentar cualquier llamada — si no, fallar rápido con un mensaje claro en vez de intentar la red.
- **Por qué importa:** sin esto, el cliente lanza error antes de llamar a la red; si el refresh falla y hay un segundo 401, el error se propaga y bloquea todo el pipeline de BAS. (`utils/bas.py:138-162,173-187,211-217`)
- **Mensaje:** "No se pudo conectar con el sistema contable en este momento. Reintentá en unos minutos o contactá al equipo técnico."
- **Etapa:** 8. **Prioridad:** Alta.

**V8.3 — Manejo de extracción imposible (documento ilegible)**
- **Condición:** si se agotan los 6 intentos de extracción, debe lanzarse un error explícito y visible en el dashboard — nunca devolver `None` silenciosamente.
- **Por qué importa:** ya hubo un bug real de producción (off-by-one) donde esto pasaba silenciosamente y el caller reventaba con un error críptico, confirmado con una imagen corrupta el 18-jul-2026. Ya corregido, pero merece test de regresión dado el historial.
- **Mensaje:** "No pudimos leer esta factura correctamente. Por favor subí una imagen o PDF más claro, o cargá los datos manualmente."
- **Etapa:** 8. **Prioridad:** Media.

**V8.4 — Consistencia entre páginas de un PDF multi-página**
- **Condición:** aplicar la misma reconciliación de V0.7 sobre el resultado combinado de todas las páginas.
- **Por qué importa:** no existe hoy ninguna reconciliación de código entre páginas — toda la responsabilidad de "juntar" correctamente una factura multi-página recae 100% en el modelo. (`process_invoice_google_2.py:988-1046`)
- **Mensaje:** "Esta factura tiene varias páginas — revisá que el total coincida con la suma de todos los ítems antes de confirmar."
- **Etapa:** 8. **Prioridad:** Media.

**V8.5 — Reintento de Orden de Pago (`retry-op`) sobre datos obsoletos**
- **Condición:** aplicar la misma validación de re-derivación de V5.2 — no reintentar con datos resueltos antes de una corrección humana posterior.
- **Por qué importa:** mismo riesgo que V5.2 pero en el camino de reintento, que es precisamente el que se usa después de una falla — mayor probabilidad de que haya habido corrección humana en el medio.
- **Mensaje:** "Antes de reintentar el pago, estamos revalidando los datos del proveedor y del comprobante por si fueron corregidos."
- **Etapa:** 8. **Prioridad:** Alta.

**V8.6 — Consistencia de `import_batch_items` (invoice vs. duplicado)**
- **Condición:** un ítem completado debe tener exactamente uno de `invoice`/`duplicate_of_invoice`/`duplicate_of` poblado (nunca ambos, nunca ninguno).
- **Por qué importa:** no hay ninguna restricción de base que lo garantice — es responsabilidad exclusiva del código que procesa cada ítem del batch.
- **Mensaje:** "Hay una inconsistencia en el registro de esta importación masiva. Contactá al equipo técnico antes de continuar."
- **Etapa:** 8. **Prioridad:** Media.

**V8.7 — Bloqueo de borrado con pago exitoso**
- **Condición:** bloquear cualquier intento de eliminar (soft-delete) una factura u orden de pago con `status="success"` sin resolución previa explícita.
- **Por qué importa:** ya implementado (409 si hay una payment_order exitosa sin resolver); mantenerlo es crítico porque el hard-delete físico técnicamente sigue siendo posible vía cuentas de servicio. **Ver V9.3 — este guardrail tiene un hueco real cuando se reutiliza un registro previamente eliminado.**
- **Mensaje:** "Esta factura tiene un pago ya efectuado y no puede eliminarse. Contactá al equipo si necesitás corregirla."
- **Etapa:** 8. **Prioridad:** Alta.

---

## Etapa 9 — Integridad transversal y anti-duplicación (hallazgos de la revisión crítica)

Estos diez puntos no aparecieron en el análisis inicial — los encontró la segunda pasada de verificación adversarial al re-leer `utils/bas.py` y `process_invoice_google_2.py` contra el borrador. Varios de estos no son "campo mal formateado" sino **riesgo real de dinero duplicado o pago no aplicado que nadie detecta**, así que se listan aparte con la misma prioridad.

**V9.1 — `/retry-op` nunca escribe de verdad en BAS**
- **Campo/mecanismo:** endpoint `retry-op/{process_id}`.
- **Condición:** debe ejecutar con `dry_run=False` cuando la factura ya fue confirmada y registrada realmente en BAS (mismo criterio que `/payment-orders/create`). Hoy `dry_run=True` está **hardcodeado**, no es una variable.
- **Por qué importa:** es la ruta de recuperación explícita para el caso más grave del sistema (OP huérfana / factura registrada sin pago aplicado) y está funcionalmente inerte — siempre simula y siempre reporta éxito/fallo sobre una simulación, nunca sobre una escritura real. El equipo puede creer que ya resolvió un pago pendiente cuando en realidad nunca se ejecutó. (`process_invoice_google_2.py:2891`)
- **Mensaje:** "El reintento de la orden de pago no pudo confirmarse como una operación real. Contactá al equipo técnico antes de asumir que el pago se aplicó."
- **Etapa:** 9. **Prioridad:** Crítica.

**V9.2 — `/payment-orders/create` no es idempotente ante doble confirmación**
- **Campo/mecanismo:** endpoint `payment-orders/{process_id}/create` (el único lugar donde `dry_run` pasa a `False`).
- **Condición:** antes de crear una OP real, verificar si ya existe una `payment_order` con `status=="success"` para ese `process_id` y, si existe, devolver "ya resuelto" en vez de crear una segunda — el mismo chequeo que `/retry-op` sí tiene.
- **Por qué importa:** un doble click, un reintento de red del dashboard, o dos requests concurrentes crean una **segunda Orden de Pago real en BAS para la misma factura** — pagándole al proveedor dos veces. El upsert final además sobrescribe `bas_op_prefijo`/`bas_op_numero`, borrando el rastro de la primera OP exitosa. (`process_invoice_google_2.py:3334`)
- **Mensaje:** "Esta factura ya tiene un pago registrado. Si necesitás generar un pago adicional, contactá al equipo técnico."
- **Etapa:** 9. **Prioridad:** Crítica.

**V9.3 — Una OP nueva hereda `deleted_at` de una OP eliminada previa**
- **Campo:** `payment_orders.deleted_at` en el flujo de `upsert_payment_order`.
- **Condición:** al reutilizar un registro de `payment_order` para una nueva creación real, limpiar explícitamente `deleted_at`/`deleted_by`/`delete_reason`; o, más simple, no reusar el mismo registro — crear uno nuevo por cada intento real exitoso.
- **Por qué importa:** si una OP exitosa se soft-elimina y luego se vuelve a confirmar el pago, la nueva OP real y exitosa queda persistida en el mismo registro que sigue marcado `deleted_at`. Esto rompe el guardrail de V8.7: `eliminar_invoice` solo bloquea el borrado si encuentra una `payment_order` activa (`not deleted_at`) — con `deleted_at` heredado, una factura con una Orden de Pago real y vigente en BAS puede borrarse sin el 409 que V8.7 asume que existe. (`utils/pocketbase_client.py:663`)
- **Mensaje:** (control interno) — si se detecta el estado inconsistente: "Hay una orden de pago activa que quedó marcada como eliminada por error. Contactá al equipo técnico antes de continuar."
- **Etapa:** 9. **Prioridad:** Crítica.

**V9.4 — Sin lock ante registro concurrente duplicado en BAS (TOCTOU)**
- **Mecanismo:** secuencia `GET ConsultaComprobantesExternos → si no existe, POST ComprobantesCompra` dentro de `crear_orden_de_pago_desde_factura`.
- **Condición:** agregar exclusión mutua (lock en memoria o en PocketBase, keyed por CUIT+prefijo+número externo) que serialice esta secuencia para la misma combinación proveedor+comprobante, sin importar qué endpoint la dispare.
- **Por qué importa:** `PROCESSING_SEMAPHORE` solo envuelve la extracción de Gemini, no esta secuencia — y al menos tres caminos independientes (worker automático, `/retry-op`, `/payment-orders/create`, además de importación masiva) pueden dispararla para la misma factura. Dos invocaciones concurrentes pueden ambas leer "no existe" antes de que cualquiera confirme su POST, terminando en **dos altas del mismo comprobante en BAS**. (`utils/bas.py:790`)
- **Mensaje:** "Esta factura está siendo procesada en este momento. Esperá unos segundos y volvé a intentar."
- **Etapa:** 9. **Prioridad:** Crítica.

**V9.5 — `TasaIva` hardcodeada a 21%, ignora la alícuota real**
- **Campo:** `Items[].TasaIva`, enviado siempre en 21 en ambos puntos donde se arma el payload.
- **Condición:** derivar `TasaIva` de la `alicuota` real que ya extrae el tool de impuestos (`tools_standard.py`), o validar que coincide con 21% antes de asumirlo; si no se puede determinar con certeza, marcar para revisión.
- **Por qué importa:** existen alícuotas de IVA legítimas de 10.5%, 0%, exentas, etc. — una factura con alícuota real distinta de 21% queda registrada en BAS con metadata impositiva incorrecta sin que nadie lo note. (`process_invoice_google_2.py:1524`)
- **Mensaje:** "La alícuota de IVA de esta factura podría no ser la estándar. Verificala antes de continuar."
- **Etapa:** 9. **Prioridad:** Alta.

**V9.6 — Retenciones/impuestos extraídos nunca llegan al payload real de BAS**
- **Campo:** `retenciones` (Ganancias, IVA, IIBB) y percepciones extraídas por el tool `impuestos_y_retenciones_de_la_factura`.
- **Condición:** si `impuestos_info.get("retenciones")` no está vacío, mapearlo a `Retenciones` en el payload de la Orden de Pago, o bloquear el pago automático y forzar revisión manual explicando que hay retenciones detectadas que no se están aplicando.
- **Por qué importa:** `construir_payload_orden_pago` acepta un parámetro `retenciones` para esto, pero cada llamador real lo deja en `None`. Una factura sujeta a retención se paga por el monto bruto en vez del neto de retención, sin ningún control fiscal de que la retención debía practicarse. (`utils/bas.py:402`)
- **Mensaje:** "Detectamos retenciones o percepciones en esta factura que no se aplicarán automáticamente al pago. Confirmá cómo deben procesarse antes de continuar."
- **Etapa:** 9. **Prioridad:** Alta.

**V9.7 — Ver V3.13** (campo `Vencimientos` sin validar) — listado en Etapa 3 por ser parte de la construcción del comprobante, pero fue un hallazgo de esta misma revisión crítica.

**V9.8 — Talonario de factura de compra (`BAS_PREFIJO_TALONARIO_MA`) hardcodeado sin resolución dinámica**
- **Campo:** `BAS_PREFIJO_TALONARIO_MA = "00001"`.
- **Condición:** resolver dinámicamente vía `buscar_prefijo_talonario(empresa, "MA")` (el mismo patrón que V6.1 sí exige para el talonario de la Orden de Pago), o al menos validar periódicamente que el prefijo hardcodeado sigue siendo válido contra `GET /api/Talonarios`.
- **Por qué importa:** el propio código tiene un TODO admitiendo que debería resolverse dinámicamente. Si la configuración de talonarios de esta instalación cambia, el `Prefijo` de todas las facturas de compra se rompe en silencio sin ninguna validación que lo detecte. (`utils/bas_config.py:114`)
- **Mensaje:** (configuración de sistema — control interno, sin exposición directa al usuario).
- **Etapa:** 9. **Prioridad:** Media.

**V9.9 — Ver V5.2b** (recalcular `bas_codigo_item`) — listado en Etapa 5 por ser parte del gate de confirmación humana, pero fue un hallazgo de esta misma revisión crítica.

**V9.10 — `monto` de `CrearOrdenPagoBody` sin cota ni comparación contra el total real**
- **Campo:** `monto` (modelo Pydantic del endpoint de creación manual de Orden de Pago).
- **Condición:** agregar `Field(gt=0)` al modelo, y una validación server-side explícita que compare `monto` contra `invoice.get("total")` (o total_gravado + IVA calculado), rechazando con 422 cualquier desvío no justificado explícitamente.
- **Por qué importa:** hoy no hay ninguna restricción de valor sobre este campo. Combinado con la falta de idempotencia (V9.2) y sin modelo de pagos parciales acumulados, nada impide ni detecta un sobre-pago o un monto completamente ajeno al valor real de la factura si algún caller (bug del dashboard, o un tercero con el secreto compartido) manda un valor negativo o desproporcionado. (`process_invoice_google_2.py:3131`)
- **Mensaje:** "El monto ingresado para el pago no es válido o no coincide con el total de la factura. Verificalo antes de continuar."
- **Etapa:** 9. **Prioridad:** Crítica.

---

## Resumen de prioridades (actualizado tras la revisión crítica)

| Prioridad | Significado | Validaciones |
|---|---|---|
| **Crítica** | BAS rechazaría la operación, o se crearía/pagaría un registro incorrecto con dinero real en juego | V0.1, V0.3, V0.4, V0.9, V0.11, V1.2, V1.3, V2.1, V3.1, V3.2, V3.4, V3.5, V3.6, V3.7, V3.8, V3.9, V3.10, V3.11, V5.2, V5.5, V6.1, V6.3, V6.5, V6.6, V6.8, V6.12, V6.13, V7.1, V7.2, V7.3, V7.4, V7.5, V8.1, **V9.1, V9.2, V9.3, V9.4, V9.10** — **39** |
| **Alta** | Error recuperable pero bloqueante, o riesgo real sin ser dinero mal direccionado de inmediato | V0.2, V0.5, V0.6, V0.7, V0.8, V0.13, V1.1, V1.4, V4.1, V5.2b, V5.4, V6.2, V6.4, V6.9, V6.10, V8.2, V8.5, V8.7, **V9.5, V9.6** — **20** |
| **Media** | Datos inconsistentes, no bloqueantes de forma inmediata | V0.10, V0.12, V3.3, V3.12, V3.13, V5.1, V5.3, V6.7, V6.11(b), V8.3, V8.4, V8.6, **V9.8** — **13** |
| **Baja** | UX / prevención temprana | Ninguna resultó lo bastante relevante como para quedar puramente en "Baja" — los campos verdaderamente opcionales (`punto_de_venta`, `subtipo`, `observaciones`) se dejan pasar sin validación adicional. |

*(V6.11(a) y V6.12 se mantienen documentadas como validaciones **ya implementadas** que no deben removerse, no como pendientes nuevas.)*

---

## Orden sugerido de implementación

1. **Primero, lo que puede estar causando pérdida de dinero real hoy sin que nadie lo note:** V9.2 (doble OP), V9.3 (deleted_at heredado), V9.4 (lock anti-duplicado), V9.1 (retry-op inerte), V9.10 (monto sin cota) — son bugs de integridad, no solo validaciones de campo, y el borrador original no los había detectado.
2. **Luego, las validaciones críticas de Etapa 3 y 6-7** (V3.1-V3.11, V6.1-V6.13, V7.1-V7.5) — son las que más incidentes reales ya documentados tienen detrás; implementarlas previene la mayoría de los rechazos ya vistos en producción.
3. **Luego, Etapa 0** (extracción) — son la primera línea de defensa, pero tienen menor prioridad relativa porque los datos mal extraídos hoy igual quedan atrapados (aunque tarde y con peor mensaje de error) en las validaciones de Etapa 3.
4. **Por último, Etapa 1, 5, 8 y las de prioridad Media** — mejoran la calidad de vida del equipo y cierran huecos de auditoría, pero no bloquean el flujo si no están.

---

**Fuentes:** todos los hallazgos citados provienen de la lectura directa de `utils/bas.py`, `routes/process_invoice_google_2.py`, `tools_standard.py`, las migraciones de PocketBase en `ticket-ai-infra/pocketbase/pb_migrations/`, y los docs de incidentes reales en `Invoicy/docs/` (`bas-orden-de-pago-research.md`, `bas-comprobante-compra-cuenta-0-diagnostico.md`, `evidencia-sp-icr-comprob-apl-para-bas.md`, `consulta-comprobante-21885-para-soporte-bas.md`). Ningún punto de este plan es especulativo — cada validación está anclada a un incidente real documentado o a una regla explícita del código/documentación de BAS ya observada, y las correcciones de precisión marcadas con 🔧 ya fueron aplicadas tras verificar cada cita contra el código actual.
