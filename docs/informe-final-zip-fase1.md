# Informe final — manejo de ZIP en la ingesta de facturas (Fase 1)

> Nada desplegado a producción. Cubre: rediseño de arquitectura
> (`docs/plan-fase1-zip-REDISEÑO.md`) + seis rondas de revisión adversarial
> + los fixes que salieron de cada una, incluyendo el cierre completo de
> durabilidad (Pass 0 de pre-registro + split de semáforos, rondas 5-6) —
> ver sección 6. Candidato a listo para producción: ningún riesgo
> crítico/serio conocido queda sin resolver; los riesgos residuales que
> quedan (sección 14) son angostos, documentados, y de la misma clase que
> cualquier secuencia de llamadas de red no transaccional.

## 1. Qué se cambió

**Identidad**: cada uno de los 3 canales (email, `/website-upload`,
`/process-invoice`) genera su propio `ingest_id` (uuid4, server-side) al
detectar un ZIP. El `id`/`process_id` que manda el cliente pasa a llamarse
`client_reference` — solo trazabilidad en logs, nunca construye una ruta de
disco ni una clave de PocketBase.

**Fallos parciales**: se eliminó `zip_ref.testzip()` (gate global que
abortaba todo el ZIP por un miembro corrupto). Ahora `zip_ref.extract()`
detecta CRC corrupto/encriptación por miembro, aislado — verificado
empíricamente que un miembro roto no afecta a los demás.

**Durabilidad**: `_extraer_zip_y_despachar_individualmente` tiene Fase A
(extrae + registra cada factura como `status="pending"` en PocketBase de
inmediato, antes de esperar turno) y Fase B (despacha el trabajo pesado).
Un barrido único al arrancar el proceso (`_barrido_huerfanos_al_arrancar`)
recupera filas huérfanas de un reinicio, comparando contra el momento real
en que el proceso arrancó (no contra una antigüedad fija — ver hallazgo #9
más abajo, corregido). **Actualización, ronda 4**: Fase A ahora también
adjunta el archivo crudo a PocketBase (`documento_original`, vía el
`adjuntar_archivo_original` que ya existía y usaba Fase B) apenas registra
cada miembro como `"pending"` — no solo cuando le llega su turno de
procesamiento pesado. Esto cierra el hueco, encontrado dos veces de forma
independiente en las rondas 2 y 3, donde una factura huérfana quedaba
visible como `status="error"` pero el botón "Reintentar" le devolvía 422
por no tener el archivo original guardado. Ver sección 14 para la
investigación de infraestructura que llevó a esta decisión.

**Carga**: contador global de "trabajo en vuelo" (`threading.Lock`, no
`asyncio.Lock`, porque se toca desde un hilo real de `asyncio.to_thread` y
desde el event loop) — un ZIP que sumaría demasiado trabajo pendiente se
rechaza completo, síncrono y claro, antes de aceptarlo.

**Notificaciones**: 1 email de confirmación por ZIP completo (no por
factura), 1 `fire_webhook` por factura individual — payload de éxito y de
error EXACTOS al contrato ya existente de `worker()`, inconsistencias
propias incluidas (éxito usa `"id"`, error usa `"process_id"`). Un ZIP
rechazado por completo ahora sí dispara `fire_webhook` (nunca email — esa
es la semántica que `worker()` ya tenía).

**Seguridad** (2 rondas de fixes, ver secciones 12-13): saneo estructural
de `file_name` en el webhook de email, ruta única por-request en los otros
2 canales, `fire_webhook`/`enviar_email`/`download_file_from_url`/
`get_file_type_from_url` ya no bloquean el event loop, `download_file_from_url`
ahora tiene tope real de tamaño y de duración total (no solo un timeout de
inactividad por-lectura), truncado de nombre de archivo por bytes reales
(no por caracteres).

## 2. Qué se eliminó/reemplazó del diseño anterior

- `zip_ref.testzip()` como gate global — reemplazado por verificación
  por-miembro dentro de `zip_ref.extract()`.
- Uso directo de `id`/`process_id` del cliente para construir rutas de
  disco (`f"./downloads/{id}"`) — reemplazado por `ingest_id` server-side.
- Reconstrucción manual de `ruta_extraida` vía `os.path.join` +
  `miembro.filename` — reemplazado por el valor de retorno real de
  `zip_ref.extract()` (más preciso para el chequeo de Zip Slip).
- Payload de `fire_webhook` por factura con forma inventada — reemplazado
  por réplica exacta del contrato de `worker()`.
- Umbral de antigüedad fijo (30 min) en el barrido de huérfanos —
  reemplazado por comparación contra el arranque real del proceso.
- Truncado de nombre de archivo por caracteres (`[:200]`) — reemplazado por
  truncado por bytes reales (`_truncar_nombre_a_bytes`).

## 3. Flujo final — canal email

1. `webhook_endpoint` recibe el JSON, genera `process_id` (uuid4) — se
   reusa tal cual como `ingest_id` si el adjunto es un ZIP.
2. Descarga el adjunto vía `asyncio.to_thread` (ya no bloquea el event
   loop), con timeout de conexión + tope real de tamaño y duración total.
3. Si es ZIP: `_validar_zip_rapido` (síncrono, barato) — si falla, dispara
   `fire_webhook` de error de inmediato y responde. Si pasa, despacha
   `_extraer_zip_y_despachar_individualmente` vía `asyncio.create_task`
   (no bloquea la respuesta) con `enviar_resumen_por_email=True` y
   `on_item_completado=orchestrator.fire_webhook`.
4. Cada factura aceptada: Fase A la registra `pending` de inmediato; Fase B
   la procesa y dispara `fire_webhook` con el payload exacto de `worker()`.
5. Al terminar todo el ZIP: 1 email-resumen (menciona aceptadas,
   no-soportadas, Y errores de despacho — antes se omitían en silencio).
6. Archivo suelto (no ZIP): camino viejo intacto, `job_queue`/`worker()`
   sin cambios.

## 4. Flujo final — plataforma (`/website-upload`, `/process-invoice`)

1. El archivo se guarda con una ruta única por-request (prefijo `uuid4.hex`,
   nombre original truncado a 200 bytes reales) — nunca colisiona aunque
   dos uploads manden el mismo filename.
2. Si el contenido real (magic bytes) es un ZIP: se genera `ingest_id`
   propio, se limpia cualquier placeholder huérfano de `/website-upload/init`
   que hubiera quedado reservado (solo si existe y está en `"pending"` —
   nunca toca una factura ajena), y se despacha igual que el canal email
   (sin `enviar_resumen_por_email` ni `on_item_completado` — esos son
   exclusivos de email).
3. `/process-invoice` es el único que pasa
   `notificar_no_soportados_por_webhook=True` (comportamiento preexistente
   preservado).
4. Archivo suelto: camino viejo intacto.

## 5. Cómo se garantiza identidad segura

`ingest_id` SIEMPRE lo genera el servidor (`uuid.uuid4().hex`), nunca el
cliente. Ningún valor de cliente (`id`, `process_id`, `file_name` del
webhook, `file.filename` del multipart) se usa para construir una ruta de
disco sin pasar antes por saneo (`_sanear_nombre_para_process_id`,
`_truncar_nombre_a_bytes`) y, en el caso del webhook de email, una
verificación estructural adicional de que la ruta final resuelve dentro del
directorio temporal autorizado. Verificado con 7 payloads de path
traversal distintos (`".."`, rutas absolutas, barras dobles, etc.) contra
la función compartida y contra 2 endpoints, con espías reales sobre
`os.makedirs`/`shutil.move`/`shutil.rmtree` — ninguno escapó jamás.

## 6. Cómo se garantiza durabilidad

Fase A registra cada factura como `"pending"` en PocketBase antes de
esperar su turno de procesamiento pesado, y **adjunta el archivo original**
(`documento_original`) en ese mismo momento — no recién cuando le toca su
turno en Fase B. Un barrido al arrancar el proceso recupera cualquier fila
`"pending"`/`"processing"` cuyo `updated` sea anterior al momento real en
que ESTE proceso arrancó (no un umbral de antigüedad fijo — bug real
encontrado y corregido, ver hallazgo #9). Sin UI ni concepto de lote nuevo
— reusa el botón "Reintentar" existente.

**Investigación de infraestructura (ronda 4)**: antes de decidir cómo
cerrar el hueco de "Reintentar devuelve 422 para huérfanas de Fase A"
(sección 14 de una versión anterior de este informe), verifiqué
directamente si `./downloads/` sobrevive un reinicio real del proceso en
producción, en vez de asumirlo. Confirmado con evidencia concreta: **no
sobrevive**. `ticket-ai-infra/docker-compose.yml` (el compose REAL que
levanta los 3 servicios de producción — `invoice-api-wa`, `invoice-api-bas`,
`invoice-api-core`, los tres construidos desde este mismo repo) solo monta
3 volúmenes nombrados: `pocketbase-data`, `wa-bot-sessions`, e
`invoicy-webhooks-log` (este último en `/app/data`, no en `/app/downloads`).
Ninguno de los 3 servicios de Invoicy tiene un volumen sobre `downloads/` —
ese directorio vive únicamente en la capa de escritura efímera del
contenedor, que se pierde en cualquier recreación real (redeploy,
`docker compose up --build`, etc.), no solo en un simple restart del mismo
contenedor. Con esa certeza, apliqué la opción (a) que vos mismo elegiste
para este caso — persistir el archivo crudo en PocketBase durante Fase A —
en vez de la opción (b) (recuperar desde disco local), que hubiera sido
inútil en este deploy real.

**Costo aceptado, explícito**: para un ZIP grande, Fase A ahora hace una
llamada de red adicional (multipart, hasta 30s de timeout) por cada
miembro aceptado, además del `upsert_invoice` que ya hacía. Esto alarga el
tiempo total de Fase A y consume más ancho de banda hacia PocketBase que
antes. Verifiqué que esto no crea un riesgo nuevo: los 3 endpoints
despachan el ZIP con `asyncio.create_task` (fire-and-forget) — la respuesta
HTTP al cliente nunca espera a Fase A ni Fase B — y `adjuntar_archivo_original`
ya tenía un timeout real (30s, heredado de `PocketBaseClient.timeout`), así
que un fallo o lentitud de PocketBase en este punto no puede colgar
indefinidamente el hilo de Fase A. Es exactamente el trade-off que pediste
priorizar ("durabilidad y recuperación real por encima de ahorrar
uploads").

**Cierre completo — Pass 0 de pre-registro + split de semáforos (rondas
5-6)**: la ronda 4 dejó un residuo real: si el proceso moría a mitad del
loop de clasificación, los miembros que el loop TODAVÍA NO había alcanzado
no tenían ninguna fila en PocketBase en absoluto — ni `"pending"` ni
`"error"`. Reestructuré `_extraer_zip_y_despachar_individualmente` en 2
sub-pasadas, cada una bajo su propio semáforo (ver sección 8):

- **Pass 0** (bajo `ZIP_REGISTRO_SEMAPHORE`, concurrencia 8): ANTES de
  extraer o clasificar ningún miembro, itera TODOS los miembros del ZIP y
  hace `upsert_invoice(status="pending")` de inmediato para cada uno que
  no sea un ZIP anidado (detectable solo por nombre, sin extraer nada).
  Esto significa que para cuando la clasificación real (Pass 1) siquiera
  arranca, TODO miembro candidato ya tiene su fila — un reinicio a partir
  de ese punto ya no puede dejar a nadie sin rastro. Al tener su propio
  semáforo, separado del de la extracción pesada, Pass 0 corre casi de
  inmediato incluso con Fase A de otros ZIPs en curso — ver hallazgo serio
  #1 de la ronda 5, sección 12.
- **Pass 1** (bajo `ZIP_EXTRACTION_SEMAPHORE`, concurrencia 2, el de
  siempre): la clasificación de siempre (integridad, Zip Slip, tipo
  soportado), reusando el `process_id` que ya calculó la Pass 0. Un
  miembro ACEPTADO adjunta su archivo (`adjuntar_archivo_original`) DE
  INMEDIATO, en la misma iteración que lo acepta — ya NO en una "Pass 2"
  separada que esperaba a que TODO el ZIP terminara de clasificarse (ese
  diseño intermedio, de la primera implementación de la ronda 5, se
  cambió tras encontrar que dejaba a potencialmente TODOS los miembros de
  un ZIP sin `documento_original` si el proceso moría durante la
  clasificación — hallazgo serio #2 de la ronda 5, sección 12). Un miembro
  RECHAZADO descarta su placeholder de la Pass 0 vía `soft_delete_invoice`
  (mismo mecanismo, ya existente, que usa el placeholder huérfano de
  `/website-upload/init`) — así nunca queda como factura
  pendiente/procesable, cumpliendo el requisito de que un archivo
  rechazado no debe aparecer en la cola.

**Decisión de arquitectura, documentada explícitamente**: PocketBase
v0.39.5 (la versión real desplegada) tiene un API batch/transaccional que
podría convertir la Pass 0 en una única operación atómica — pero requiere
habilitarse explícitamente por colección en la configuración real de
producción, algo que no puedo verificar ni aplicar sin desplegar (fuera de
lo autorizado en esta sesión). Elegí NO depender de esa capacidad no
verificable: la Pass 0 hace N upserts JSON secuenciales, cada uno chico y
rápido (sin extraer ni subir ningún archivo todavía). Esto no es
matemáticamente atómico — pero reduce la ventana de riesgo al mínimo
práctico alcanzable sin infraestructura nueva ni cambios de configuración
en producción, que es lo que pediste priorizar. Si en el futuro se
confirma que el batch API está habilitado, la Pass 0 podría convertirse en
una única llamada transaccional real — lo dejo anotado como mejora futura
posible, no como algo pendiente de esta fase.

**Riesgo residual, angosto y documentado (no un "ítem abierto" — una
propiedad matemática de cualquier secuencia de llamadas de red no
transaccionales)**: si el proceso muere en el instante exacto de un
`upsert_invoice` individual DENTRO de la propia Pass 0 (no antes, no
después), los miembros después de ese punto en el mismo ZIP quedan sin
fila. Es la misma clase de residuo que ya existe para el adjunto inline de
Pass 1 (una llamada HTTP puntual por miembro, no una secuencia larga) —
angosto, aislado por miembro, verificado que no afecta a los demás
(tests A13, A15). Cerrarlo al 100%
matemático requeriría el batch API transaccional (ver decisión arriba) o
alguna forma de cola/outbox con infraestructura nueva — ninguna de las dos
es "el mínimo práctico sin infraestructura nueva" que elegí priorizar.

## 7. Cómo se aíslan fallos parciales

Separación real entre checks GLOBALES (rechazan el ZIP completo: no se
puede ni abrir, demasiados archivos, demasiado pesado, sistema saturado) y
checks POR MIEMBRO (aíslan solo ese archivo: corrupto, encriptado, tipo no
soportado, ZIP anidado, Zip Slip, error de extracción). Verificado con un
ZIP real de 1 miembro corrupto + 1 válido: el válido se despacha, el
corrupto queda aislado.

## 8. Cómo funciona el límite de carga

Contador global de "trabajo en vuelo" (facturas de ZIP `pending`/`processing`
no terminales), reservado por `_validar_zip_rapido` antes de aceptar un
ZIP y liberado por cada rama de salida de `_extraer_zip_y_despachar_individualmente`
(rechazo en Fase A, éxito o error en Fase B, más un safety-net final). Un
ZIP que llevaría el total por encima del techo se rechaza completo, sync,
antes de crear ninguna task — y esto sigue siendo cierto ANTES de que ni
Pass 0 ni Pass 1 corran una sola línea. Alcance deliberadamente limitado a
ZIP (no cubre archivo suelto, que ya estaba acotado 1:1 por sus propios
límites).

**Dos semáforos, no uno (ronda 5)**: `ZIP_REGISTRO_SEMAPHORE` (nuevo,
concurrencia 8) gatea solo la Pass 0 — liviana, solo upserts JSON, sin
extraer ni subir archivos. `ZIP_EXTRACTION_SEMAPHORE` (el de siempre,
concurrencia 2) sigue gateando la Pass 1 — extracción real (CPU/disco/zlib)
más el adjunto de cada archivo aceptado. Se separaron porque, con un solo
semáforo compartido de concurrencia 2, un 3er ZIP subido mientras 2 ya
estaban en Fase A podía quedar esperando turno varios minutos SIN haber
registrado ni una sola fila en PocketBase — si el proceso se reiniciaba en
esa espera, el ZIP entero desaparecía sin rastro, con el cliente ya
recibido un 201 de éxito (hallazgo serio real, ver sección 12). Con Pass 0
desacoplada y con más cupo, ese registro ocurre casi de inmediato incluso
con Fase A pesada de otros ZIPs en curso — sin abrir la puerta a trabajo
ilimitado: sigue acotada (8, no infinita) y el techo real de backlog total
sigue siendo `MAX_TRABAJO_EN_VUELO_ZIP`, verificado ANTES de que
`ZIP_REGISTRO_SEMAPHORE` entre en juego.

## 9. Compatibilidad de emails/webhooks

Comparación campo por campo contra `worker()` (confirmada, no solo
diseñada): payload de éxito = `{id, file_name, factura, saved, saved_items,
bas, drive_file_id, status, success}`; payload de error =
`{process_id, file_name, error, status, success}` — inconsistencia de
claves (`id` vs `process_id`) replicada tal cual, a propósito.
`_procesar_imagen_o_pdf_impl` ahora también propaga `drive_file_id` en el
dict que devuelve (antes se calculaba pero nunca se exponía).

## 10. Archivos modificados

- `routes/process_invoice_google_2.py` — el grueso del cambio. Ronda 4:
  fix de durabilidad de Fase A + reordenamiento Pass 1/Pass 2, fix H12
  (saneo estructural en `reintentar_extraccion`). Ronda 5: Pass 0 de
  pre-registro (todos los miembros candidatos, antes de clasificar
  ninguno) + descarte por soft-delete de los rechazados. Ronda 5,
  corrección sobre la corrección: split de `ZIP_EXTRACTION_SEMAPHORE` en
  dos semáforos (`ZIP_REGISTRO_SEMAPHORE` nuevo, para Pass 0; el de
  siempre para la clasificación pesada) + el adjunto de archivo movido a
  DENTRO de la misma iteración que acepta a cada miembro (ya no una
  "Pass 2" separada al final) — los 2 fixes que salieron de la revisión
  adversarial de la propia ronda 5, ver sección 12.
- `routes/batch_import.py` — **sin cambios en esta sesión** (solo tiene el
  fix de separador de una fase anterior, todavía sin commitear — confirmado
  otra vez en la ronda 5 que sigue intacto). Un hallazgo MENOR preexistente
  y fuera de alcance en este mismo archivo (nombre de archivo de manifiesto
  sin sanear) se derivó a una tarea separada (`task_96b13e9f`) en vez de
  tocarlo acá.
- `utils/pocketbase_client.py` — sin cambios en la ronda 5 (`upsert_invoice`,
  `soft_delete_invoice`, `adjuntar_archivo_original` ya existían de antes
  — la Pass 0 solo los llama desde un lugar nuevo).
- `scripts/test_offline_zip_*.py` — 6 archivos.
  `test_offline_zip_extraccion.py` ganó A1/A2 extendidos (soft-delete de
  un miembro rechazado), A12-A14 (Pass 0 pre-registra antes de cualquier
  resultado de Pass 1; resiliencia si un upsert puntual falla; invariante
  aceptado-o-descartado), y A15-A16 (ronda 5, corrección sobre la
  corrección: A15 prueba que adjunto/descarte quedan intercalados en el
  orden real de los miembros, no agrupados al final; A16 prueba con un
  semáforo real que Pass 0 corre aunque `ZIP_EXTRACTION_SEMAPHORE` esté
  100% saturado). `test_offline_zip_regresiones_notificaciones_seguridad.py`
  ganó H12 (saneo de `reintentar_extraccion`) y H13 (cobertura directa de
  `_barrido_huerfanos_al_arrancar`). `test_offline_zip_identidad_seguridad.py`
  tenía un `FakeOrchestrator` incompleto desde la ronda 4 (sin
  `adjuntar_archivo_original`) que silenciosamente tragaba el error vía el
  try/except best-effort del código real — corregido, agregando también
  `soft_delete_invoice`.
- `docs/plan-fase1-zip-REDISEÑO.md`,
  `docs/hallazgos-revision-adversarial-zip-post-implementacion.md`,
  este informe.
- `ticket-ai-dashboard/components/SubirFacturaForm.tsx` — **sin cambios**
  en esta fase (confirmado explícitamente en la 2da revisión: el backend
  ya es autosuficiente, no depende de que el frontend adivine bien).

## 11. Tests ejecutados y resultado

19 archivos de test offline, **19/19 en verde**. Incluye pruebas empíricas
reales, no solo de compilación: `asyncio.gather` con 2 invocaciones
genuinamente concurrentes sin colisión, monkeypatch real sobre
`os.makedirs`/`shutil.rmtree` para confirmar contención de rutas, un
servidor de "goteo" simulado que prueba que el corte por duración total
funciona aunque ningún timeout por-lectura se dispare nunca,
`threading.Thread` real (no solo coroutines) para el thread-safety del
contador de trabajo en vuelo, 3 payloads de path traversal contra
`reintentar_extraccion` (H12), 3 escenarios directos contra
`_barrido_huerfanos_al_arrancar` (H13), un ZIP de 5 miembros alternando
aceptado/rechazado verificando que el orden real de eventos queda
intercalado y no agrupado (A15), y un `asyncio.Semaphore` REAL adquirido
externamente para simular saturación total, confirmando que la Pass 0
corre igual (A16) — no un mock del semáforo, el objeto real de la
librería estándar.

## 12. Hallazgos encontrados durante la implementación y cómo se resolvieron

Además de los 14 hallazgos que motivaron el rediseño (ver
`docs/hallazgos-revision-adversarial-zip-post-implementacion.md`), 5 rondas
de revisión adversarial posteriores al rediseño encontraron:

**Ronda 2 (13 hallazgos, sobre el rediseño ya implementado):**
- Path traversal vía `file_name` del webhook de email (crítico,
  preexistente) → saneo + verificación estructural de contención.
- Colisión de nombre en el guardado inicial de `/process-invoice`/
  `/website-upload` (serio, preexistente) → prefijo uuid4 único.
- Payload de `fire_webhook` no compatible con `worker()` (serio, esta
  fase) → réplica exacta del contrato.
- ZIP rechazado por completo sin ninguna notificación (serio, esta fase) →
  `fire_webhook` de error (nunca email, semántica existente).
- `fire_webhook`/`enviar_email` bloqueantes, ahora invocados N veces por
  ZIP (serio) → `asyncio.to_thread` + timeout.
- Barrido de huérfanos no funciona con auto-restart real (serio) →
  corregido, ver hallazgo #9 abajo.
- Email-resumen no reconciliaba errores de despacho (moderado) →
  corregido, los menciona explícitamente.
- Truncado de nombre por caracteres, no bytes (menor, encontrado pero
  corregido recién en la ronda 3) → `_truncar_nombre_a_bytes`.
- Docstring de `ZIP_EXTRACTION_SEMAPHORE` impreciso (menor) → corregido.
- 3 hallazgos aceptados sin cambio (ver sección 14).

**Ronda 3 (sobre los fixes de la ronda 2):**
- `get_file_type_from_url` tenía el MISMO problema de bloqueo que ya se
  había corregido en `download_file_from_url`, dos líneas antes en el
  mismo flujo (**crítico**, se me había pasado por alto) → mismo fix
  aplicado.
- `timeout=15` es un timeout de inactividad por-lectura, no un límite de
  duración total — una respuesta que "gotea" bytes nunca lo dispara
  (serio) → reescrito con streaming + tope real de tamaño y de duración
  total, verificado empíricamente con una respuesta simulada que gotea.
- Truncado por caracteres (no bytes) reintroducía el mismo OSError que
  decía prevenir, con nombres ricos en tildes/ñ (moderado, no verificado
  por límite de sesión pero confirmado por mí mismo con una prueba directa)
  → `_truncar_nombre_a_bytes`, verificado con 200 caracteres 'é' (400
  bytes UTF-8) truncando correctamente a ≤200 bytes reales.
- Reafirmó (independientemente, sin haber visto la ronda 2) el mismo gap
  de durabilidad para huérfanas de Fase A — resuelto en la ronda 4, ver
  abajo y sección 6.

**Ronda 4 (sobre la decisión de durabilidad pendiente + el fix que salió de
ella):**
- Investigación de infraestructura: confirmado con evidencia directa
  (`ticket-ai-infra/docker-compose.yml`) que `./downloads/` NO sobrevive un
  reinicio real de ningún servicio de producción — ningún volumen lo cubre.
  Esto descartó la opción (b) (recuperar desde disco) y confirmó la opción
  (a) como la única que realmente cierra el hueco.
- Fix aplicado (serio, cierra un gap real de recuperación): Fase A ahora
  llama `adjuntar_archivo_original` para cada miembro aceptado, apenas lo
  registra como `"pending"` — no solo cuando le toca su turno en Fase B.
  Best-effort (un fallo acá no saca al miembro de la cola), verificado con
  tests A11/A11b nuevos.
- Revisión adversarial de 9 dimensiones sobre el fix anterior (ver sección
  13) — 9 hallazgos crudos, 9/9 evaluados (6 verificados por agentes
  independientes, 3 auto-verificados por mí por límite de sesión del
  entorno de workflows). Resultado y qué se hizo con cada uno:
  - **(SERIO, preexistente pero agravado por esta ronda)** El fix anterior
    alargaba Fase A (2 llamadas de red por miembro en vez de 1), agrandando
    la ventana de un gap más severo: miembros aún no alcanzados por el loop
    de Fase A pierden TODO rastro (ni fila `"pending"`) si el proceso muere
    a mitad de Fase A. Apliqué el reordenamiento Pass 1/Pass 2 (ver sección
    6) como mitigación mecánica, segura, sin cambio de contrato externo —
    reduce mucho la ventana, no la elimina del todo. El residuo que queda
    es el ítem que te presento en la sección 14 para tu decisión.
  - **(MODERADO)** El mismo alargamiento de Fase A retrasa el timing de
    todas las notificaciones del ZIP (Fase B, y por lo tanto todo webhook/
    email, no arranca hasta que Fase A completa) → mismo fix (Pass 1/Pass
    2) lo mitiga: el costo total de Fase A no cambia, pero ya no se suma
    latencia extra específicamente de esta ronda por member — documentado
    como costo aceptado en sección 6 (es inherente al diseño de 2 fases,
    no algo que se pueda eliminar sin perder la garantía de durabilidad).
  - **(MODERADO)** Comentario de `ZIP_EXTRACTION_SEMAPHORE` desactualizado
    (no mencionaba los nuevos PATCH multipart bajo el mismo semáforo) →
    corregido, texto actualizado.
  - **(MODERADO)** El comentario que introdujo el fix anterior sobreclamaba
    "cierra la ventana" cuando en realidad la reduce drásticamente pero no
    la elimina (2 pasos no atómicos) → comentario reescrito con lenguaje
    honesto, alineado con el reordenamiento Pass 1/Pass 2.
  - **(MODERADO)** `_barrido_huerfanos_al_arrancar` no tenía ningún test
    directo (solo `find_stale_invoices`, la pieza que orquesta, se probaba
    por separado) → agregado H13 (3 escenarios: huérfanas reales, cero
    huérfanas, PocketBase caído).
  - **(MODERADO)** `/invoices/{process_id}/retry-extraction` seguía sin
    sanear `documento_original` al construir su ruta de escritura (mismo
    patrón de riesgo que H1, sink distinto) → corregido con el mismo patrón
    ya establecido (saneo + verificación estructural de contención),
    regresión H12 agregada (3 payloads de path traversal).
  - **(MODERADO)** Los uploads directos no-ZIP (`/process-invoice`,
    `/website-upload`) mandan `file.filename` crudo (sin basename) al
    adjunto temprano de Fase B — el propio hallazgo señala, con honestidad,
    que esto solo es explotable si PocketBase no sanea nombres al guardar
    archivos (dato externo no verificable desde este repo). El fix de H12
    ya cierra el sink real (la escritura en disco de `retry-extraction`),
    así que el riesgo práctico queda mitigado independientemente de este
    punto — no toqué la fuente (`_procesar_imagen_o_pdf_impl`, usada por
    MUCHOS más callers que solo ZIP) para no ampliar el alcance sin tu OK.
  - **(MENOR/informativo)** `routes/batch_import.py` tiene un cambio real
    sin commitear (el fix de separador de rutas de una fase anterior) —
    confirmé que es legítimo y preexistente, no algo en riesgo de perderse
    por este trabajo. Ningún cambio necesario.
  - **(MENOR, preexistente, fuera de alcance)** `batch_import.py` construye
    una ruta de disco con un nombre de manifiesto sin sanear, mismo patrón
    de riesgo que H1/H12 pero en una feature completamente distinta (import
    masivo por CSV) — derivé una tarea separada en vez de tocar un archivo
    fuera del alcance de esta sesión.

**Ronda 5 (cierre completo de durabilidad — Pass 0 de pre-registro, y la
revisión adversarial que encontró 2 problemas reales en la implementación
inicial de esa misma Pass 0):**
- Implementación pedida explícitamente: Pass 0 pre-registra TODOS los
  miembros candidatos del ZIP (`upsert_invoice status="pending"`) ANTES de
  clasificar ninguno; un miembro rechazado en la clasificación real
  descarta su placeholder vía `soft_delete_invoice` (mismo mecanismo que
  el placeholder huérfano de `/website-upload/init`) — nunca queda como
  factura pendiente/procesable. Decisión de arquitectura documentada:
  NO se usa el batch API transaccional de PocketBase (existe en la v0.39.5
  desplegada, pero requiere habilitarse por colección en producción, no
  verificable sin desplegar) — se prioriza el mínimo práctico sin
  infraestructura nueva por sobre una atomicidad matemática no verificable.
  Verificado con tests A1/A2 extendidos y A12-A14 nuevos.
- Revisión adversarial de 7 dimensiones sobre esa primera implementación
  de Pass 0 — encontró **2 hallazgos SERIOS reales**, que arreglé antes de
  considerar el trabajo cerrado (no los dejé para "una próxima ronda"):
  - **(SERIO)** Un ZIP entero podía perderse SIN NINGUNA fila si el
    proceso se reiniciaba mientras la task todavía esperaba turno para
    adquirir `ZIP_EXTRACTION_SEMAPHORE` (concurrencia 2) — es decir, ANTES
    de que la Pass 0 corriera una sola vez —, con el cliente ya habiendo
    recibido un 201 de éxito. → Fix: `ZIP_REGISTRO_SEMAPHORE` nuevo,
    separado, con más concurrencia (8), gatea SOLO la Pass 0 — que ahora
    corre casi de inmediato incluso con Fase A pesada de otros ZIPs en
    curso. Ver sección 8. Verificado con test A16 (semáforo real,
    saturado a propósito).
  - **(SERIO)** El botón "Reintentar" devolvía 422 para la gran mayoría —
    potencialmente TODOS — los miembros de un ZIP si el proceso se
    reiniciaba durante la clasificación, porque el adjunto de archivo
    vivía en una "Pass 2" separada que solo arrancaba después de que la
    clasificación terminara para TODO el ZIP. Esto contradecía
    directamente lo que yo mismo había declarado "cerrado" unos párrafos
    antes en este mismo informe — encontrado por la revisión adversarial,
    no por mí. → Fix: el adjunto ahora ocurre DENTRO de la misma iteración
    que acepta a cada miembro (ya no una pasada separada al final) — así
    un reinicio a mitad de la clasificación deja a los miembros YA
    aceptados con su archivo YA adjuntado. Ver sección 6. Verificado con
    test A15 (orden de eventos intercalado, no agrupado).
  - **(MODERADO, aceptado)** Un `soft_delete_invoice` interrumpido a mitad
    de su propia secuencia GET+PATCH puede dejar a un miembro RECHAZADO
    viéndose como una factura interrumpida legítima (status="error"
    genérico) en vez de invisible. Ventana angosta, best-effort, misma
    clase de residuo que la no-atomicidad de Pass 0 — documentado como
    riesgo aceptado en sección 14, no bloquea el cierre.
  - **(MODERADO/MENOR, ya resuelto)** 2 hallazgos sobre el comentario de
    `ZIP_EXTRACTION_SEMAPHORE` desactualizado (no reflejaba que Pass 0
    agrega upserts nuevos, ni que `soft_delete_invoice`/`upsert_invoice`
    son 2 llamadas HTTP cada uno, no 1) — ya corregidos como parte natural
    de reescribir los comentarios de AMBOS semáforos al hacer el fix de
    arriba, sin necesitar una edición aparte.
- 6ta ronda de revisión adversarial, específica sobre el fix del split de
  semáforos + adjunto inline (no repite las dimensiones ya cerradas en
  rondas anteriores) — ver sección 13 para el resultado.

## 13. Resultado de la revisión adversarial final

6 rondas completas. Las primeras tres terminaron con verificación
adversarial completa (0 y 17 agentes sin error en las rondas 1 y 2
respectivamente, tras un primer intento fallido por límite de sesión). La
tercera terminó con la fase de REVIEW completa pero 3 de los 9 chequeos de
VERIFY sin poder correr por límite de sesión del entorno — los 2 hallazgos
de esa dimensión que sí llegaron a verificarse (crítico y serio) están
confirmados y corregidos; el resto de esa dimensión (truncado por bytes,
gap de durabilidad de Fase A) los verifiqué yo mismo directamente contra el
código real antes de decidir cómo proceder, en vez de asumir que eran
ciertos sin chequear.

**Ronda 4** — 9 dimensiones, mapeadas 1 a 1 contra los 9 puntos que pediste
verificar explícitamente (durabilidad, path traversal, unicidad de ids,
fallos parciales, notificaciones, rate limiting, archivos individuales
intactos, filas irrecuperables, sin cambios de producción sin tests). Las
9 completaron su fase de REVIEW. De los 9 hallazgos crudos que salieron, 6
llegaron a verificarse por un agente independiente (adversarial, tratando
de refutarlos) antes de que el entorno volviera a topar con el límite de
sesión en las últimas 3 verificaciones — para esas 3 hice yo mismo la
verificación directa contra el código real, mismo criterio que en la ronda
3. Las 9 quedaron resueltas (7 con un fix real, 1 confirmada como
no-acción-necesaria porque ya estaba bien, 1 derivada a una tarea separada
por estar fuera de alcance) — detalle completo en la sección 12. Ningún
hallazgo crítico quedó sin abordar. El único hallazgo SERIO de esta ronda
recibió una mitigación real (Pass 1/Pass 2) que reduce mucho su alcance sin
eliminarlo del todo — el residuo es exactamente el ítem que te presento en
la sección 14, no algo que decidí resolver o descartar por mi cuenta.

Después de aplicar los fixes de la ronda 4 (reordenamiento Pass 1/Pass 2,
saneo de `reintentar_extraccion`, comentarios corregidos, cobertura nueva
para el barrido de huérfanos), corrí la suite completa de nuevo:
**19/19 archivos en verde**, sin ninguna regresión sobre lo que ya estaba
verificado en rondas anteriores.

**Ronda 5** — 7 dimensiones sobre la implementación de Pass 0, mapeadas a
los 8 criterios de cierre que pediste explícitamente. Las 7 completaron su
REVIEW (13/13 agentes sin error). De 6 hallazgos crudos, 5 se confirmaron:
**2 SERIOS reales** (ver sección 12 — pérdida total de un ZIP esperando el
semáforo de extracción antes de que Pass 0 corriera, y "Reintentar" 422
para potencialmente todos los miembros de un ZIP si el reinicio ocurría
durante la clasificación), 2 moderados/menores sobre un comentario
desactualizado, y 1 moderado aceptado (soft-delete interrumpido). Los 2
serios los arreglé de inmediato (split de semáforos + adjunto inline) —
nunca los dejé para "una próxima ronda" ni los reporté sin resolver.

**Ronda 6** — verificación final, específica sobre el fix que salió de la
ronda 5 (split de semáforos + adjunto inline), sin repetir dimensiones ya
cerradas en rondas anteriores. De 3 dimensiones lanzadas, 1 completó su
verificación automática de punta a punta (adjunto inline: 5 puntos
verificados, **0 hallazgos nuevos**, confirmado en verde por los tests
A13/A15). Las otras 2 (split de semáforos; re-verificación de los 8
criterios) volvieron a toparse con el límite de sesión del entorno de
workflows — para esas hice yo mismo la verificación directa, línea por
línea, contra el código real (mismo criterio que en rondas 3 y 4): confirmé
que `async with ZIP_REGISTRO_SEMAPHORE` libera correctamente incluso si
`_pass0_pre_registrar` fallara de forma inesperada (el try/except interno
ya envuelve toda la función, y `async with` garantiza el release en
cualquier caso); que un fallo parcial de Pass 0 para un miembro puntual no
deja a `process_ids_por_indice`/`record_ids_por_process_id` en un estado
inconsistente (ya cubierto también por el test A13); que la rama
`error_apertura_pass0` libera exactamente `reservado_trabajo_en_vuelo`
unidades (ni de más ni de menos, mismo criterio que la rama "no se pudo
abrir el ZIP" de siempre); y que `_validar_zip_rapido`/
`MAX_TRABAJO_EN_VUELO_ZIP` siguen gateando ANTES de que ni `ZIP_REGISTRO_SEMAPHORE`
ni Pass 0 entren en juego. No encontré ningún hallazgo nuevo en esa
verificación manual.

Corrí la suite completa una última vez después de todos los fixes de las
rondas 5 y 6: **19/19 archivos en verde**. `git status` confirma que
`routes/batch_import.py` sigue con el único cambio preexistente de una
fase anterior (sin tocar en esta sesión) y que nada se commiteó ni se
desplegó.

## 14. Riesgos conocidos que decidimos aceptar

### Resuelto en la ronda 5 — cierre completo de durabilidad

~~Un miembro de un ZIP grande que Fase A todavía no alcanzó en su loop
podía desaparecer TOTALMENTE sin rastro si el proceso moría en ese
momento~~ — cerrado. Implementé la opción (a) que pediste explícitamente:
Pass 0 pre-registra TODOS los miembros candidatos del ZIP ANTES de
clasificar ninguno (ver sección 6). Para la pregunta de producto que este
cambio necesariamente abre — qué pasa con el placeholder de un miembro que
después resulta rechazado — elegí la alternativa que minimiza pérdida de
facturas, duplicados y cambio de UX, tal como pediste: reusar el MISMO
mecanismo de soft-delete ya existente (el que ya limpiaba el placeholder
huérfano de `/website-upload/init`), de forma que el estado final de un
miembro rechazado sea indistinguible de "nunca tuvo fila" — exactamente el
comportamiento que ya existía antes de este cambio para un miembro
rechazado, cero UX nueva, cero campo/estado nuevo en PocketBase. No agregué
UI ni concepto de lote. Verificado con 4 tests nuevos (A12: Pass 0 completa
para TODO el ZIP antes de que Pass 1 produzca cualquier resultado; A13:
resiliencia si un upsert puntual de Pass 0 falla; A14: invariante de que
todo miembro pre-registrado termina aceptado o descartado, nunca en
limbo) más A1/A2 extendidos.

### Resuelto en la ronda 4 — ya no necesita tu decisión

~~El barrido de huérfanos no podía garantizar que "Reintentar" funcionara
para una factura de ZIP huérfana dentro de Fase A~~ — cerrado. Investigué
directamente si `./downloads/` sobrevive un reinicio real (no lo asumí):
**no sobrevive**, ningún servicio de producción tiene volumen sobre esa
carpeta (ver sección 6). Con esa certeza apliqué la opción (a) que vos
mismo indicaste para ese caso — Fase A adjunta el archivo crudo a
PocketBase apenas registra cada miembro como `"pending"` — priorizando
durabilidad sobre ahorrar uploads, como pediste. Detalle completo, incluido
el costo aceptado y la ventana residual (mucho más angosta que antes), en
la sección 6.

### Aceptados, con razonamiento — no bloquean esta fase

- **Un `soft_delete_invoice` interrumpido a mitad de su propia secuencia
  interna (GET + PATCH) puede dejar a un miembro RECHAZADO viéndose como
  una factura interrumpida legítima.** Encontrado en la ronda 5: si el
  proceso muere justo entre el GET y el PATCH de `soft_delete_invoice`
  (dentro de `_descartar_placeholder_pass0`), el placeholder de ese
  miembro rechazado queda `status="pending"`, `deleted_at=""` — el barrido
  de huérfanos lo encuentra (mismo filtro que cualquier `"pending"`
  legítimo) y lo pasa a `status="error"` con el mensaje genérico de
  reinicio, sin ningún rastro del motivo real de rechazo. El resultado es
  una fila visible en el dashboard para un archivo que nunca fue una
  factura válida, y "Reintentar" le devuelve 422 (nunca tuvo
  `documento_original`). Ventana angosta (una secuencia de 2 llamadas HTTP
  puntual, no un loop entero) y misma clase de problema que la
  no-atomicidad de Pass 0 ya aceptada en la sección 6 — cerrarlo del todo
  requeriría un campo/estado nuevo en PocketBase para poder distinguir
  "descartado a medias" de "interrumpido de verdad", lo cual pediste
  evitar explícitamente si no es estrictamente necesario. No bloquea el
  cierre; queda documentado.
- **`find_stale_invoices` asume una sola instancia de `InvoiceOrchestrator`
  contra la misma PocketBase.** Verificado en la ronda 4 con más certeza
  que antes (ya no es solo "accidente de configuración" sin confirmar):
  `ticket-ai-infra/docker-compose.yml` — el compose REAL de producción —
  solo le pasa `POCKETBASE_URL`/`POCKETBASE_SERVICE_EMAIL`/
  `POCKETBASE_SERVICE_PASSWORD` a `invoice-api-bas`; `invoice-api-wa` e
  `invoice-api-core` no las tienen. De los 3, hoy solo `server_bas.py`
  monta `process_invoice_google_2` (el router de ZIP) en un servicio que
  además tiene esas variables — así que en la topología real desplegada
  hoy, solo UN proceso corre este barrido contra PocketBase. `server.py`
  también monta el mismo router pero no forma parte de los 3 servicios que
  define el compose de producción. Si en el futuro se agrega un 4to
  servicio o se reconfigura alguno de los otros dos para apuntar a la
  misma PocketBase con este router montado, el riesgo original volvería a
  aplicar — documentado para no perderlo de vista, pero hoy no es un
  riesgo activo, es una garantía real de la topología actual.
- **3 caminos de rechazo total del webhook de email preexistentes (fallo de
  descarga, tipo de archivo inválido, excepción no prevista) siguen sin
  disparar ninguna notificación.** Fuera del alcance que pediste
  explícitamente ("un ZIP rechazado completamente") — aplica a CUALQUIER
  adjunto, no solo ZIP, y es código preexistente sin tocar en esta sesión.
- ~~`/invoices/{process_id}/retry-extraction` sin sanear `documento_original`
  al construir su ruta de disco~~ — **corregido en la ronda 4** (H12, mismo
  patrón que H1). Ya no aplica, se deja tachado para que quede el rastro de
  que se detectó y se cerró, no que se pasó por alto.
- **`routes/batch_import.py` tiene el mismo patrón de riesgo (nombre de
  manifiesto sin sanear al construir una ruta de disco), pero en una
  feature completamente distinta (import masivo por CSV), fuera del diff
  de esta sesión.** Encontrado en la ronda 4 al revisar el diff completo.
  En vez de tocar un archivo fuera de alcance, lo derivé a una tarea
  separada (`task_96b13e9f` en este mismo entorno) para que no se pierda,
  con instrucciones de primero confirmar si `file_name` es realmente
  controlable por un usuario en esa feature antes de decidir el fix.
- **`_validar_zip_rapido` sigue corriendo síncrono en el event loop**
  (parsea el directorio central del ZIP antes de poder aplicar el límite
  de cantidad). Riesgo ya identificado en la primera revisión adversarial,
  mitigado parcialmente por el límite de trabajo-en-vuelo, no eliminado.
  Cerrarlo del todo requeriría mover esa validación también a un hilo,
  cambio que no pediste explícitamente y que no apliqué para no ampliar el
  alcance sin tu OK.
- **El objeto `"factura"` del payload de éxito trae algunas claves de
  bookkeeping extra** (`id`, `saved_sheet`, etc.) que el `"factura"` de
  `worker()` no tiene — no rompe consumidores razonables (JSON con claves
  de más), pero no es una réplica 100% estructural. Corregirlo de raíz
  tocaría `_procesar_imagen_o_pdf_impl` para TODOS sus callers, no solo
  ZIP — fuera de alcance.
