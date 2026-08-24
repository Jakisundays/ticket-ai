# Rediseño de arquitectura — manejo de ZIP (post-revisión adversarial)

> Ningún código tocado desde acá. Este documento reemplaza el diseño de
> `docs/plan-fase1-zip-FINAL.md` en los puntos donde la revisión adversarial
> post-implementación (`docs/hallazgos-revision-adversarial-zip-post-implementacion.md`,
> 14 hallazgos confirmados) mostró que "cambio chico y aislado" ya no
> describe correctamente el trabajo. Estructura pedida: separar preexistente
> vs. regresión vs. decisión nueva, después la arquitectura mínima corregida
> punto por punto, comparada contra el estado anterior.

## 0. Clasificación de los 14 hallazgos

### (a) Preexistentes (no introducidos por este diff, código no tocado)

| # | Hallazgo | Dónde vive realmente |
|---|---|---|
| 9 (numeración de la revisión) | `file_type` puede ser `None` en el webhook de email para el camino NO-zip → `AttributeError` | Línea sin tocar por el diff, camino de archivo suelto por email |
| 12 | `routes/process_invoice.py` y `routes/process_invoice_google.py` arman `process_id` con `/` real en su propio manejo de ZIP | Dos routers legados, nunca tocados en esta feature |

Ninguno de los dos se origina en este trabajo. Los trato como deuda técnica
a decidir por separado (ver sección 8), no como parte de esta corrección.

### (b) Regresiones o vulnerabilidades introducidas por este diff

| # | Hallazgo | Severidad |
|---|---|---|
| 1 | Path traversal vía `id`/`process_id` → `shutil.rmtree` arbitrario | Crítico |
| 2 | `base_process_id` sin garantía de unicidad → colisión disco/PocketBase | Crítico |
| 3 | `testzip()` aborta el ZIP completo por 1 miembro corrupto/encriptado | Serio |
| 4 | Sin durabilidad ante reinicio a mitad de un ZIP grande | Serio |
| 5 | ZIP por email pierde el email de confirmación + webhook externo por factura | Serio |
| 6 | `UnicodeDecodeError` no capturado en `_validar_zip_rapido` → 500 | Serio |
| 7 | Contrato de respuesta del webhook de email cambia (success=true con 0 procesables) | Moderado |
| 8 | Parseo sync del directorio central antes del límite + rate limit no ajustado a "1 request = hasta 100 jobs" | Serio |
| 10 | Chequeo de Zip Slip compara una ruta mal recalculada | Moderado |
| 11 | Extracción + `testzip()` sin gate de concurrencia, bloquea el event loop | Moderado |
| — | `esZip` (frontend, por nombre) vs. magic bytes (backend) pueden divergir → fila "pending" huérfana | Serio |

Todos estos son responsabilidad de esta corrección — es donde se concentra
el rediseño de abajo.

### (c) Decisiones de diseño que había que tomar explícitamente y no se tomaron

- Cómo se genera la identidad de una ingesta ZIP (¿confiar en el cliente o
  generar en el servidor?).
- Cómo se recupera trabajo pendiente tras un reinicio, sin introducir el
  concepto de "lote" visible.
- Qué efectos (email, webhook) le corresponden a una factura individual
  extraída de un ZIP vs. al ZIP como envío.
- Qué modelo de límite de carga tiene sentido cuando 1 request puede generar
  hasta 100 unidades de trabajo pesado.
- Quién es la fuente de verdad sobre "esto es un ZIP": el nombre del
  archivo (frontend) o el contenido real (backend).

---

## 1. Identidad y filesystem (hallazgos #1 y #2, resueltos juntos)

**Diagnóstico común a ambos.** El diseño actual usa el `id`/`process_id`
que manda el cliente en el `Form` como si fuera, a la vez, (a) un
componente de ruta de disco y (b) una garantía de unicidad. Ninguna de las
dos cosas es cierta: es un string arbitrario, no autenticado en
`/website-upload`, sin ningún whitelist de caracteres. Esa es la causa raíz
única detrás de los dos hallazgos críticos — no son dos bugs que corregir
por separado, son la misma decisión de diseño mal tomada.

**Principio nuevo: ningún valor que llega del cliente toca jamás la
construcción de una ruta de disco ni se usa como clave real de PocketBase.**
El servidor genera su propia identidad para cada ingesta de ZIP; el valor
del cliente, si vino, se guarda como metadata de trazabilidad, nada más.

### Diseño concreto

- Al entrar una ingesta ZIP por cualquier canal, el servidor genera
  `ingest_id = uuid.uuid4().hex` — un valor fresco, generado en el backend,
  imposible de influir desde afuera.
- **Todas** las rutas de disco (`carpeta_base`, `zip_carpeta`, carpetas por
  índice) se arman exclusivamente con `ingest_id`. El campo `id`/`process_id`
  del formulario nunca aparece en un `f"./downloads/{...}"`, ni directa ni
  indirectamente. Esto elimina el path traversal de raíz: no hay ningún
  string de cliente que ese código concatene en una ruta, así que no
  importa si alguien manda `".."`, `"/etc"`, o basura binaria — no tiene
  ningún efecto porque nunca se usa para eso.
- El `process_id` real de cada factura hija (la clave única de PocketBase,
  el valor que reciben los 8 endpoints) pasa a ser
  `f"{origen}--{ingest_id}--{índice:03d}"` (`origen` = `"email"` /
  `"web"` / `"api"`, constante fija por canal, no dato de usuario). Como
  `ingest_id` es un uuid4 fresco por invocación, dos requests —
  concurrentes, reintentos tras timeout, o directamente maliciosos con el
  mismo `id` de cliente — jamás comparten carpeta ni prefijo. El problema
  de colisión (#2) queda resuelto como efecto directo de esto, no con un
  mecanismo aparte de "chequear si ya existe": estructuralmente no puede
  existir la colisión.
- El valor de `id`/`process_id` que mandó el cliente (si vino) se guarda
  como un campo de metadata en cada factura hija (reusar/agregar
  `client_reference` o similar) — solo para trazabilidad de "el caller X
  llamó esto 'lote-agosto'", nunca para construir nada.
- Consecuencia lateral que también cierra sola: el `shutil.rmtree` final ya
  no puede borrarle el trabajo a otra ejecución, porque no hay forma de que
  dos ejecuciones compartan `carpeta_base` — cada `ingest_id` es único, así
  que cada `rmtree` solo puede tocar su propio árbol.
- **Qué NO cambia:** el flujo de archivo individual (no-ZIP) en los 3
  canales sigue usando el `id`/`process_id` del cliente exactamente como
  hoy (upsert directo, sin carpeta por-id) — ese camino nunca tuvo el
  problema porque nunca usó ese valor para armar una ruta nueva; queda
  fuera de este cambio, tal como pediste que quedara.

### Comparación contra el estado anterior

| | Antes de este diff | Diseño actual (con los 2 críticos) | Diseño corregido |
|---|---|---|---|
| Identidad de una ingesta ZIP | No existía (ZIP no se aceptaba en website-upload; en process-invoice se extraía a una carpeta fija sin id) | `id`/`process_id` del cliente, sin validar | `ingest_id` generado por el servidor, cliente solo aporta metadata |
| Unicidad garantizada por | — | Nada (asumida, no verificada) | Estructural (uuid4 fresco por invocación) |
| Superficie de path traversal | No existía para ZIP | Sí, vía `id`/`process_id` | Ninguna (el cliente nunca llega a una ruta) |

---

## 2. Fallos parciales — rediseño de la validación (hallazgo #3)

**Diagnóstico.** `zip_ref.testzip()` es una verificación TODO-o-nada: si
cualquier miembro tiene el CRC corrupto o está encriptado, devuelve ese
nombre y la función entera aborta con `aceptados=0`, aunque el resto de los
archivos esté perfecto. Esto contradice el requisito central del proyecto
desde el primer mensaje.

**Principio nuevo: separar checks GLOBALES (pueden rechazar todo el ZIP,
antes de gastar ningún recurso) de checks POR MIEMBRO (aíslan solo a ese
archivo).**

### Diseño concreto

- Checks globales (rechazan el ZIP completo, sin cambios respecto al
  diseño actual): que `zipfile.ZipFile()` pueda siquiera abrir el archivo
  (si ni el índice central se puede leer, no hay ZIP que procesar), y los
  límites de `_validar_zip_rapido` (cantidad de archivos, peso
  descomprimido declarado). Estos siguen corriendo síncronos, baratos,
  antes de crear cualquier task — sin cambios.
- **Se elimina el `zip_ref.testzip()` como gate previo al loop.** En su
  lugar, la verificación de integridad se mueve DENTRO del loop por
  miembro, junto a la extracción: para cada miembro, la lectura real
  (`zip_ref.read(miembro)` u operación equivalente que fuerza la
  verificación de CRC) queda envuelta en el mismo `try/except` que ya
  protege la extracción y clasificación de ESE miembro. Si ese miembro en
  particular tiene el CRC corrupto (`zipfile.BadZipFile`) o requiere
  contraseña (`RuntimeError`), se clasifica como "no soportado" con el
  motivo correspondiente y el loop sigue con el siguiente miembro — exactamente
  el mismo patrón de aislamiento que ya existe hoy para tipo-no-soportado,
  ZIP-anidado y error-de-extracción.
- Resultado: un ZIP con 99 facturas válidas y 1 corrupta/encriptada
  despacha las 99 y reporta la 1 como no procesable — nunca aborta el lote
  entero.

### Comparación contra el estado anterior

| | Antes de este diff | Diseño actual (bug #3) | Diseño corregido |
|---|---|---|---|
| Un miembro corrupto/encriptado | No existía el concepto (ZIP no se procesaba miembro a miembro con esta garantía) | Aborta el ZIP completo, 0 despachados | Aísla solo ese miembro, el resto se despacha |

---

## 3. Durabilidad ante reinicio, sin exponer "lotes" (hallazgo #4)

**Diagnóstico.** Un archivo extraído vive solo en una lista en memoria
(`archivos_a_despachar`) hasta que le toca su turno en el loop secuencial.
El único registro durable (`status="processing"` en PocketBase) se crea
recién dentro de `_procesar_imagen_o_pdf_impl`, que puede tardar minutos en
llegarle el turno a un archivo si hay 90 adelante. Si el proceso reinicia
antes de esa fila existir, el archivo desaparece sin dejar rastro.

**Principio: reusar el patrón que `batch_import.py` ya resolvió para el
mismo problema exacto — "pending" antes de procesar, detección de
huérfanos después — pero apoyado en la colección `invoices` que ya existe,
sin crear ninguna entidad ni pantalla nueva.**

### Diseño concreto

- Se divide `_extraer_zip_y_despachar_individualmente` en dos fases
  explícitas dentro de la misma función (no dos funciones separadas, no
  dos requests):
  - **Fase A — extracción + registro durable.** Para cada miembro que pasa
    los checks (tipo soportado, no corrupto, etc.), inmediatamente después
    de extraerlo se hace un `upsert_invoice({process_id, status:"pending", ...})`
    — el mismo tipo de upsert que ya existe, solo que se adelanta: hoy pasa
    recién cuando le toca el turno en `_procesar_imagen_o_pdf_impl`; con
    este cambio pasa apenas se extrae y valida, ANTES de esperar en la cola
    del semáforo. Esta fase no espera a Gemini/BAS de nadie — es barata,
    solo I/O a PocketBase.
  - **Fase B — despacho real.** Recién acá se itera y se llama a
    `_procesar_en_background` para cada uno, como hoy (secuencial,
    protegido por `PROCESSING_SEMAPHORE`). `_procesar_imagen_o_pdf_impl`
    sigue haciendo su propio upsert a `status="processing"` cuando le toca
    — ahora simplemente transiciona una fila que YA existía como
    "pending", en vez de crearla recién ahí.
- Con esto, apenas termina la Fase A, cada factura del ZIP tiene una fila
  real y visible en la cola de revisión — aunque su procesamiento pesado
  todavía no arrancó. Un reinicio a mitad de la Fase B deja esas filas en
  `status="pending"`, nunca las hace desaparecer.
- **Recuperación de huérfanos:** se agrega un barrido liviano (al arrancar
  el backend, o como chequeo periódico simple) que busca `invoices` con
  `status IN ("pending", "processing")` cuyo `updated` sea más viejo que un
  umbral (ej. 30 minutos) y las pasa a `status="error"` con un
  `error_message` explícito ("interrumpido por reinicio del servidor").
  **No hace falta ningún endpoint nuevo ni botón nuevo**: apenas la fila
  queda en `status="error"`, el botón "Reintentar" que ya existe en el
  dashboard (`/invoices/{process_id}/retry-extraction`) la resuelve, igual
  que resuelve cualquier otro error de extracción hoy.
- Esto es deliberadamente MÁS simple que el mecanismo de `batch_import.py`
  (que tiene su propia colección `import_batch_items`, su propio endpoint
  `/retry-failed` agrupado): acá no hace falta nada de eso porque cada
  factura YA es una fila independiente de `invoices` desde el principio —
  el único elemento que faltaba tomar prestado era "crear la fila antes de
  procesar, no después", que es una sola línea de más por miembro.

### Comparación contra el estado anterior

| | Antes de este diff | Diseño actual (bug #4) | Diseño corregido |
|---|---|---|---|
| Registro durable de una factura de ZIP | No aplicaba (ZIP no se procesaba así) | Recién cuando le toca el turno (puede ser minutos/horas después) | Inmediatamente tras extraerla, antes de esperar en cola |
| Recuperación tras crash | No aplicaba | Ninguna — desaparece sin rastro | Barrido de huérfanos + botón "Reintentar" ya existente |
| Concepto nuevo expuesto al cliente | — | — | Ninguno |

---

## 4. Notificaciones y compatibilidad del canal de email (hallazgos #5 y #7)

**Diagnóstico.** Antes de este diff, absolutamente todo lo que llegaba por
email (suelto o de un ZIP) pasaba por `worker()`, que dispara
`enviar_email` (confirmación al remitente) y `fire_webhook` (evento externo)
por cada archivo, éxito o error. La rama ZIP nueva saca el procesamiento de
ese pipeline sin reemplazar ninguno de los dos efectos, y además cambia el
contrato de la respuesta HTTP inmediata (ya no refleja si había algo
procesable).

**Decisión concreta que pediste que no dejara implícita:**

### Email de confirmación al remitente: **uno por ZIP, no uno por factura**

Recomiendo esto y no "uno por factura", por tres razones técnicas/de
producto:

1. Un remitente que manda un ZIP de 50 facturas no debería recibir 50
   emails de confirmación — es spam desde su perspectiva, y nadie diseñó
   el email de confirmación pensando en ese volumen.
2. La acción del remitente fue una sola (mandar un email con un adjunto);
   el acuse de recibo natural es uno por esa acción, no uno por cada
   artefacto que el sistema decida extraer de ella internamente — es
   coherente con el principio general de todo este trabajo ("para el
   cliente, sea transparente"), aplicado ahora al canal de email en vez de
   al dashboard.
3. Timing: se puede enviar apenas termina la Fase A (extracción + registro
   durable) de la sección 3, sin esperar a que las 50 facturas terminen su
   procesamiento pesado (que puede tardar bastante) — mantiene la
   confirmación rápida, como es hoy para un archivo suelto.

El contenido pasa de "acá está tu factura procesada" (email actual,
1-a-1) a un resumen ("recibimos tu ZIP con 50 archivos, 47 se están
procesando, 3 no son un tipo soportado") — un template nuevo, chico, no
una reconstrucción del email individual.

### Webhook externo (`fire_webhook`): **uno por factura, no uno por ZIP**

Acá la recomendación es la opuesta, porque el consumidor es una máquina,
no una persona: los sistemas externos que ya escuchan `WEBHOOK_URL`
probablemente reaccionan a "una factura se procesó" (ej. para actualizar
su propio estado, disparar una reconciliación puntual). Devolverles un
único payload gigante con 50 resultados adentro los obligaría a cambiar su
propio parser sin necesidad — mientras que mantener el mismo evento
granular de siempre (uno por factura, éxito o error) preserva
compatibilidad real con lo que ya consumen hoy.

### Diseño concreto

- `_extraer_zip_y_despachar_individualmente` gana un parámetro opcional
  (mismo patrón que `notificar_no_soportados_por_webhook`, no una rama de
  código nueva por canal): `on_item_completado` (callback opcional,
  invocado por cada factura al terminar su procesamiento, éxito o error) y
  `enviar_resumen_por_email` (bool, default False).
- Solo `origen="email"` activa ambos: pasa `enviar_resumen_por_email=True`
  (dispara el email-resumen al terminar la Fase A) y un callback que llama
  `fire_webhook(resultado)` por cada factura al completarse (equivalente a
  lo que hacía `worker()`, pero enganchado desde el nuevo camino).
- `/website-upload` y `/process-invoice` no pasan ninguno de los dos —
  igual que hoy, sin efectos nuevos ahí (nunca los tuvieron, salvo el caso
  puntual ya resuelto de `notificar_no_soportados_por_webhook` en
  process-invoice).

### Contrato de respuesta del webhook de email (#7)

Se mantiene tal como estaba antes de este diff: la respuesta inmediata NO
puede prometer éxito si `_validar_zip_rapido` no puede saber (sin
descomprimir) si habrá algo procesable. Lo que sí se puede corregir sin
tocar el diseño de "checks baratos antes, pesados en background": una vez
que la Fase A (sección 3) termina y ya se sabe cuántos fueron aceptados
vs. no soportados, ESE es el momento natural para el email-resumen de
arriba, que si terminó en 0 aceptados puede decir "no encontramos ningún
archivo procesable" — la respuesta HTTP inmediata sigue siendo
`"processing"` (no puede ser distinto sin volver a validar contenido
sync), pero el followup real (el email) sí refleja la verdad, cerrando la
brecha de UX que señalaba el hallazgo sin reintroducir trabajo pesado
sincrónico en el request.

---

## 5. Seguridad y robustez (hallazgos #6, #10, #11)

| Hallazgo | Fix |
|---|---|
| #6 — `UnicodeDecodeError` sin capturar | Ampliar el `except` de `_validar_zip_rapido` a `(zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, RuntimeError, UnicodeError, OSError)` — mismo criterio que las 2 correcciones anteriores de esta misma feature (excepción real de librería externa, antes no contemplada). |
| #10 — Zip Slip compara una ruta mal recalculada | Usar el valor de retorno real de `zip_ref.extract(miembro, carpeta_miembro)` (la ruta que realmente escribió la stdlib) en vez de reconstruirla a mano con `os.path.join` + `miembro.filename`. El chequeo `startswith` pasa a validar la ruta real, no una recalculada que puede no coincidir. |
| #11 — extracción bloquea el event loop | Envolver la fase de extracción + verificación por miembro (todo lo que hoy corre sync dentro de `_extraer_zip_y_despachar_individualmente`) en `asyncio.to_thread(...)` — corre en un hilo real del sistema operativo, cede el control del event loop mientras zlib/disco trabajan, sin reescribir la lógica a formato executor manual. |

### Concurrencia — qué corre dónde

| Fase | Dónde corre | Gate de concurrencia |
|---|---|---|
| `_validar_zip_rapido` (chequeos baratos, sync, en el handler HTTP) | Hilo principal del event loop | Ninguno (es barato por diseño, ver sección 6 para el límite real de carga) |
| Extracción + verificación por miembro (Fase A) | `asyncio.to_thread` (hilo separado) | `ZIP_EXTRACTION_SEMAPHORE` nuevo, tamaño chico (ej. 2) — evita que muchas extracciones pesadas compitan a la vez por CPU, sin compartir el semáforo del trabajo de Gemini/BAS (evita starvation cruzada) |
| Despacho pesado por factura (Fase B, `_procesar_imagen_o_pdf`) | Event loop (ya es async real, con `await` a APIs externas) | `PROCESSING_SEMAPHORE` existente, sin cambios |

---

## 6. Carga y rate limiting (hallazgo #8)

**Diagnóstico.** El límite actual (`5/minute` por IP en `/website-upload`)
limita REQUESTS, no TRABAJO. Antes de ZIP, 1 request = como máximo 1 job
pesado. Ahora 1 request puede generar hasta 100. Multiplicar el límite de
requests no resuelve nada (5 requests × 100 = 500 jobs de todas formas).

**Principio: el límite tiene que estar en la cantidad de TRABAJO en vuelo
en todo el sistema, no en la cantidad de requests que lo originan.**

### Diseño concreto

- Nuevo contador global, en memoria: `TRABAJO_EN_VUELO` — cuenta cuántas
  facturas están en `status IN ("pending", "processing")` en todo el
  sistema en este momento (se puede mantener como contador in-process
  incrementado/decrementado en los mismos puntos donde hoy se hace el
  upsert de "pending"/"processing"→"completed"/"error", sin necesidad de
  consultar PocketBase en cada request).
- Constante `MAX_TRABAJO_EN_VUELO` (a calibrar contra el droplet real —
  con concurrencia 1-2 y ~1-2 min por factura, un valor como 150-200
  representa horas de backlog, un techo razonable de "esto ya es
  demasiado, no aceptes más").
- Cuando `_validar_zip_rapido` pasa sus chequeos baratos (cantidad, peso),
  se agrega UN chequeo más, igual de barato: ¿el tamaño de este ZIP
  (`len(miembros)`) sumado a `TRABAJO_EN_VUELO` actual supera
  `MAX_TRABAJO_EN_VUELO`? Si sí, se rechaza el ZIP COMPLETO ahí mismo,
  síncrono, con un mensaje claro ("el sistema está saturado, esperá unos
  minutos y reintentá") — el caller nunca queda esperando horas sin
  saberlo, exactamente el mismo principio que ya se aplicó a
  `MAX_ARCHIVOS_ZIP`.
- El rate limit de requests (`5/minute`) se mantiene sin cambios — sigue
  cumpliendo su rol de evitar abuso de REQUESTS (ej. spamear el endpoint
  con miles de ZIPs vacíos para gastar CPU en validaciones), un problema
  distinto al de volumen de trabajo aceptado.
- Con esto, "5 ZIPs simultáneos de 100 archivos" no se convierte en 500
  jobs: el primero o segundo que haga que `TRABAJO_EN_VUELO` se acerque al
  techo empieza a rechazar los siguientes, síncrono y claro, en vez de
  aceptarlos todos y dejarlos pudrirse en una cola invisible.

---

## 7. Fila fantasma frontend/backend (hallazgo sin numerar en el resumen, dimensión `cola_review_status`)

**Diagnóstico.** El frontend decide "esto es un ZIP" mirando el nombre del
archivo; el backend decide lo mismo mirando los magic bytes reales. Cuando
divergen, la fila "pending" que `/website-upload/init` reservó de
antemano queda huérfana para siempre — nadie la transiciona nunca.

**Principio: el backend tiene que poder cerrar sus propios cabos sueltos,
sin depender de que el frontend haya adivinado bien.**

### Diseño concreto

- Se mantiene `/website-upload/init` tal como está (la reserva anticipada
  para dar visibilidad instantánea en la cola sigue siendo una propiedad
  de UX real, no se elimina).
- Se mantiene también la heurística del frontend (`esZip` por extensión)
  como una OPTIMIZACIÓN — evita el round-trip innecesario de `/init` en el
  caso común — pero deja de ser la única defensa.
- **Cambio real:** dentro de `website_upload`, si el request llega con un
  `process_id` (asumido reservado por `/init`) y la detección real por
  magic bytes determina que el contenido es un ZIP, el backend elimina esa
  fila placeholder ANTES de despachar las facturas reales del ZIP (mismo
  mecanismo de borrado que ya existe para `DELETE /invoices/{process_id}`,
  reusado internamente — no hace falta soft-delete con motivo, porque esa
  fila nunca representó una factura real, era pura infraestructura de UX).
- Con esto, la coincidencia frontend/backend deja de ser una precondición
  de corrección — en el peor caso (frontend adivinó mal), el backend
  limpia su propio error un paso después, y el resultado final es
  idéntico al caso en que el frontend hubiera adivinado bien desde el
  principio.

---

## 8. Routers legados (hallazgo #12) — análisis de impacto, sin corregir ahora

Tal como pediste, no lo meto en el alcance de esta fase, pero acá está el
análisis para decidir:

- `routes/process_invoice.py` (`POST /process-invoice`, sin prefijo): su
  rama ZIP está **viva y funcional** — tiene su propio `task_queue` y
  worker real que la consume. Arma `process_id` con `/` real. Verificado
  que **no usa PocketBase en absoluto** (ningún `pb_client`/`upsert_invoice`
  en todo el archivo) — así que ese `process_id` roto nunca colisiona con
  la colección real de `invoices` ni con los 8 endpoints auditados. Es
  deuda técnica con un bug real, pero sin el mismo radio de impacto que el
  que se corrigió en `process_invoice_google_2.py`.
- `routes/process_invoice_google.py` (`POST /gemini/process-invoice`): su
  rama ZIP llama a `orchestrator.task_queue.put(...)`, pero esa clase
  orchestrator solo define `job_queue` — **cualquier ZIP subido a este
  endpoint hoy crashea con `AttributeError` antes de llegar a usar el
  `process_id` roto**. Está montado y alcanzable, pero completamente no
  funcional para ZIP (ya, independientemente de este hallazgo).

**Mi lectura:** ninguno de los dos representa una vulnerabilidad funcional
activa equivalente a la de `process_invoice_google_2.py` hoy mismo — es
deuda técnica real, en código alcanzable, pero sin el mecanismo de
colisión/fila-huérfana que motivó la corrección de esta fase. Lo dejaría
para una tarea aparte de limpieza de routers legados, no bloqueante para
esta fase.

---

## 9. Cambios exactos por archivo (nueva versión, reemplaza los del plan anterior)

**`routes/process_invoice_google_2.py`**
- Nuevas constantes: `ZIP_EXTRACTION_SEMAPHORE`, `MAX_TRABAJO_EN_VUELO`,
  contador `TRABAJO_EN_VUELO` (con sus incrementos/decrementos en los
  puntos de upsert existentes).
- `_validar_zip_rapido`: except ampliado (`UnicodeError`, `OSError`); nuevo
  chequeo de `TRABAJO_EN_VUELO` + tamaño del ZIP.
- `_extraer_zip_y_despachar_individualmente`: firma nueva (`ingest_id`
  generado internamente, ya no recibe `base_process_id` del caller para
  construir rutas — lo genera él mismo; parámetros nuevos
  `on_item_completado`, `enviar_resumen_por_email`); Fase A (extracción +
  registro durable + integridad por-miembro, sin `testzip()` global,
  corriendo en `asyncio.to_thread`) separada de Fase B (despacho);
  `ruta_extraida` toma el valor de retorno real de `zip_ref.extract(...)`.
- `webhook_endpoint`, `website_upload`, `process_invoice`: dejan de pasar
  el `id`/`process_id` de cliente como `base_process_id` — se guarda como
  metadata (`client_reference`), la función genera su propio `ingest_id`.
  `website_upload` gana la limpieza del placeholder huérfano cuando
  detecta ZIP con `process_id` ya reservado.
- Nuevo barrido de huérfanos (invoices `pending`/`processing` viejas →
  `error`), ubicación a definir (arranque del proceso o tarea periódica
  liviana).

**`routes/batch_import.py`**: sin cambios adicionales a los ya hechos
(separador `--`, ya corregido y testeado).

**`ticket-ai-dashboard/components/SubirFacturaForm.tsx`**: sin cambios
adicionales — la heurística `esZip` actual queda como optimización, no
como corrección (la corrección real es del lado del backend, sección 7).

## 10. Qué se elimina del diff actual

- La llamada a `zip_ref.testzip()` como gate previo al loop (reemplazada
  por verificación por-miembro dentro del loop).
- El uso directo de `id`/`process_id` del cliente para construir cualquier
  ruta de disco (`zip_carpeta`, `carpeta_base`) — se reemplaza por
  `ingest_id` generado en el servidor.
- La reconstrucción manual de `ruta_extraida` vía `os.path.join` +
  `miembro.filename` (se reemplaza por el valor de retorno real de
  `zip_ref.extract(...)`).

## 11. Batería de tests nueva (cubre los 14 hallazgos, uno a uno)

1. `id`/`process_id` con `".."`, `"/etc/x"`, `"/"` → ninguna ruta de disco
   fuera de `downloads/` se toca; `ingest_id` real nunca coincide con el
   valor del cliente.
2. Dos requests concurrentes con el mismo `id`/`process_id` de cliente →
   ambos ZIPs se procesan íntegros, sin pisarse en disco ni en PocketBase
   (cada uno con su propio `ingest_id`).
3. ZIP con 1 miembro CRC-corrupto + 5 válidos → los 5 se despachan, el
   corrupto se reporta aislado (reemplaza/extiende el actual test A6, que
   hoy documenta el comportamiento viejo como "correcto").
4. ZIP con 1 miembro encriptado (password) + N válidos → mismo aislamiento.
5. Simular un "reinicio" (cortar la ejecución entre Fase A y Fase B,
   verificar que las filas ya existen en `status="pending"` antes de que
   arranque el procesamiento pesado); test del barrido de huérfanos
   (fila vieja `pending`/`processing` → pasa a `error`, aparece con botón
   "Reintentar" funcional).
6. Email por ZIP: 1 sola llamada a `enviar_email` por ZIP (no N);
   `fire_webhook` llamado N veces (una por factura), con el mismo payload
   por-factura que tenía `worker()`.
7. Contrato de respuesta del webhook de email con 0 archivos procesables →
   validar el mensaje real que ve el caller inmediato + el contenido del
   email-resumen posterior.
8. ZIP con un miembro cuyo nombre fuerza `UnicodeDecodeError` en
   `zipfile.ZipFile()` → `_validar_zip_rapido` devuelve 400 controlado, no
   500.
9. Zip Slip: miembro con nombre `"a/../a/evil.pdf"` → se despacha
   correctamente usando la ruta REAL devuelta por `extract()`, no se pierde
   en silencio.
10. Extracción de un ZIP grande no bloquea otro request concurrente barato
    (ej. un healthcheck) — test de que el event loop sigue respondiendo
    mientras `asyncio.to_thread` corre en paralelo.
11. `TRABAJO_EN_VUELO` cerca del techo + un ZIP nuevo que lo superaría →
    rechazo síncrono claro, cero tasks creadas.
12. `esZip` del frontend "adivina mal" (contenido ZIP con nombre no-.zip;
    simulable llamando al backend directo con ese caso) → la fila
    placeholder de `/init` se borra, no queda huérfana.
13. Regresión completa de los 17 tests existentes (11 preexistentes + 6 de
    esta feature) + los nuevos de esta lista, todos en verde antes de
    considerar esto terminado.
14. Los 3 canales comparados entre sí una vez más (mismo criterio que ya
    se usó): `origen`, flags de notificación, `ingest_id` vs. `id` de
    cliente, ninguno con comportamiento cruzado accidental.

---

## 12. ¿Fase única o dividir en Fase 1 (seguridad/infra) + Fase 2 (ZIP)?

Mi recomendación, ya que la pediste explícitamente: **no dividir en dos
fases separadas** — las secciones 1 (identidad/filesystem) y 3
(durabilidad) no son "infraestructura genérica" separable de ZIP, son
literalmente la base sobre la que ZIP tiene que pararse; separar solo
tendría sentido si "Fase 2: ZIP" fuera a esperar mucho tiempo, y no hay
ningún motivo para dejar el path traversal (#1) sin resolver mientras
tanto — es una vulnerabilidad real, hoy, en código ya escrito.

Lo que sí propongo es un **orden de prioridad DENTRO de una sola fase**,
para que si hay que cortar en algún punto, lo ya hecho sea seguro de
dejar a medio camino:

1. **Bloqueante, primero:** sección 1 (identidad/filesystem) — sin esto,
   nada de lo demás importa.
2. **Correctitud central:** secciones 2 (fallos parciales) y 3
   (durabilidad) — son el corazón de lo que pediste desde el primer
   mensaje de todo este trabajo.
3. **Endurecimiento:** sección 5 (seguridad/robustez) — chico, mecánico,
   mismo patrón que ya aplicamos dos veces.
4. **Decisiones de producto, necesitan tu confirmación explícita antes de
   escribir código:** secciones 4 (email/webhook) y 6 (rate
   limiting/backpressure) — ya te dejé mi recomendación concreta en cada
   una, pero quedan pendientes de tu OK.
5. **Fuente de verdad backend:** sección 7 — chico, autocontenido.
6. **Aparte, sin bloquear esto:** sección 8 (routers legados) — deuda
   técnica, no vulnerabilidad activa equivalente.

Con esto tenés la arquitectura mínima corregida completa. Sigo sin tocar
código hasta que la revises.
