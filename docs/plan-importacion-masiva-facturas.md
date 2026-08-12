# Plan: importación masiva de facturas desde una carpeta local

Plan de diseño (no implementado todavía). Sintetizado a partir de un panel de
3 arquitecturas independientes evaluadas por 9 jueces contra el código real
del repo — ver "Cómo se llegó a este plan" al final.

## Objetivo

Procesar automáticamente todos los archivos de
`/Users/jacobdominguez/Documents/dinardi/Invoicy/facturas` (carpeta en la Mac
del usuario), incluidos `.zip` con carpetas anidadas o zips-dentro-de-zips,
sin intervención manual durante el procesamiento, con progreso, estado por
archivo, errores y resumen final visibles en tiempo real en el dashboard.

## Inventario real de la carpeta (ya inspeccionado)

8 PDF + 5 JPEG sueltos + 1 JSON (fixture, no es factura) + 3 ZIP:

- `damian.zip`: 4 archivos planos.
- `facturas.zip`: carpeta `facturas/` con 9 archivos (4 PDF + 5 JPEG).
- `facturas2.zip`: carpeta `facturitas/` con 2 archivos.

**Duplicados reales confirmados**: `MercadoPago_4.pdf` existe suelto, en
`facturas.zip` Y en `facturas2.zip` (3 copias del mismo archivo). Las 5 fotos
de WhatsApp sueltas están duplicadas dentro de `facturas.zip`. Sin dedup, la
misma factura real se registraría más de una vez en BAS — impacto contable
real, no cosmético.

## Restricción central de diseño

El droplet de producción (1 vCPU / 960MB) **no tiene acceso** a la carpeta de
la Mac del usuario. Cualquier diseño que asuma "el servidor escanea la
carpeta" es inválido de entrada. El descubrimiento y la extracción de zips
(incluidos anidados) tienen que ocurrir en la máquina que sí tiene los bytes:
la Mac del usuario, vía un script local.

## Qué se reusa tal cual (no se reinventa)

- **`_procesar_imagen_o_pdf`** (`routes/process_invoice_google_2.py`): sigue
  siendo la única función que llama a Gemini, integra BAS, guarda en Sheets,
  sube a Drive y persiste en PocketBase. No se reimplementa nada de eso.
- **Patrón realtime del dashboard**: suscripciones nativas de PocketBase
  (`pb.collection(...).subscribe(...)`), nunca polling ni SSE/WS custom —
  calcado de `QueueRealtime.tsx` (colección completa, debounce 500ms,
  `router.refresh()`) y `ExtractionProgress.tsx` (un record puntual, estado
  local).
- **Patrón "responder rápido + procesar en background"**: `asyncio.create_task`
  (no `BackgroundTask` de FastAPI — así es como lo hace el código real hoy,
  en los 5 call-sites verificados) + 201 inmediato, para no chocar con el
  timeout de 200s de nginx.
- **Carpeta de trabajo `downloads/`**: la que ya usa el código real (no
  `uploads/`).
- **Header `X-Invoicy-Secret`**: mismo mecanismo que ya protege
  `retry-orden-pago`, `reintentar-extraccion`, `crear-orden-pago`,
  `eliminar-invoice`.

## Qué NO se reusa, y por qué

- **`processing_jobs`**: es del flujo viejo de ingesta por email (un solo
  consumer de `job_queue`/`worker()`), su schema no tiene contadores
  agregados por decisión ya documentada en el código
  (`"total_items no es un campo del schema de processing_jobs"`), y mezclar
  dos productores distintos bajo un mismo enum de status ensuciaría su
  semántica. Queda intacta, sin tocar.
- **`self.semaphore` del orchestrator**: pertenece exclusivamente al flujo de
  email (`process_item`/`job_queue`); el branch de archivo suelto y el de ZIP
  nunca lo tocan hoy. No corresponde reusarlo para esto.

---

## 1. Modelo de datos (dos colecciones nuevas + un campo)

Se diseña el modelo primero: tanto el trigger local (script) como el trigger
server-side (reintento) escriben al mismo modelo, nunca a un estado paralelo.

### `import_batches` (una fila por corrida)

| campo | tipo | notas |
|---|---|---|
| `label` | text | ej. `"facturas_2026-07-31"` |
| `status` | select: `running` \| `completed` \| `completed_with_errors` \| `failed` | se escribe solo 2 veces: al crear y al cerrar |
| `total_files` | number | descubiertos tras expandir zips anidados |
| `total_unique` | number | tras dedup |
| `total_duplicates` | number | omitidos por hash duplicado |
| `started_at` | date | |
| `finished_at` | date, nullable | |
| `created_by` | relation → users | quién lo disparó |

Índice por `status` (listar batches activos).

### `import_batch_items` (una fila por archivo descubierto)

| campo | tipo | notas |
|---|---|---|
| `batch` | relation → `import_batches`, required | indexado |
| `invoice` | relation → `invoices`, nullable | se setea al completar |
| `original_path` | text | ej. `facturas.zip/facturas/MercadoPago_4.pdf` — conserva el linaje |
| `file_name` | text | |
| `zip_source` | text, nullable | ej. `facturas.zip` |
| `content_hash` | text (sha256), **indexado** | motor del dedup |
| `file_size` | number | |
| `status` | select: `pending` \| `uploading` \| `processing` \| `completed` \| `error` \| `skipped_duplicate` | |
| `error_message` | text, nullable | truncado a ~2000 chars |
| `attempt_count` | number, default 0 | veces que corrió el pipeline completo (distinto del `extraction_attempt` interno de Gemini) |
| `duplicate_of` | relation → `import_batch_items` (self), nullable | item "ganador" cuando `status=skipped_duplicate` |
| `process_id` | text | `f"batch:{batch_id}/{item_id}"` |
| `updated_at` | date | para detectar items huérfanos tras un restart, ver §6 |

Índices: `batch`, `content_hash`, compuesto `(batch, status)`.

### Cambio en `invoices`: agregar `content_hash`

Text, nullable, indexado, **no único a nivel schema** (la unicidad se decide
en código, no se fuerza, para no romper filas legacy). Se completa **desde
ahora en adelante, dentro de `_procesar_imagen_o_pdf` misma**, no solo en el
flujo de batch — así el dedup cubre también lo que ya sube un usuario por
`/website-upload` o `/process-invoice` suelto, no solo lo importado en lote.
Sin esto, un batch importado hoy nunca detectaría que una factura ya fue
subida manualmente la semana pasada.

---

## 2. Descubrimiento y extracción (zips anidados incluidos)

100% en la Mac del usuario. Script nuevo: `Invoicy/scripts/batch_import.py`
(reusa `hashlib`, `zipfile`, y el mismo mecanismo de `.env` /
`X-Invoicy-Secret` que ya usan `scripts/test_*.py`).

1. Recorre `facturas/` recursivamente (`pathlib.Path.rglob`).
2. Cada `.zip` se extrae a un temp dir (`tempfile.mkdtemp(prefix="invoicy-batch-")`).
   Si un miembro extraído es también `.zip`, se re-entra. Topes de seguridad:
   profundidad máx. 5, máx. 500 archivos totales, máx. 500MB descomprimidos
   (anti zip-bomb — antes de extraer, sumar `zi.file_size` de las entradas o
   chequear ratio `file_size/compress_size`).
3. **Zip-slip**: al extraer, validar que el path resuelto de cada entrada
   quede dentro del directorio destino (`Path.resolve()` +
   `is_relative_to`), rechazando entradas con `../` — riesgo real de path
   traversal al descomprimir zips, aunque el usuario sea de confianza.
4. Filtra por la misma whitelist de extensiones que ya usa el backend
   (pdf/jpg/jpeg/png/webp/…) — excluye automáticamente el `.json` fixture y
   basura de macOS (`__MACOSX/`, `._*`, `.DS_Store`) sin lógica especial,
   marcado `skipped_unsupported` (no es error, no frena nada).
5. Calcula `sha256` de cada archivo real resultante y arma un manifiesto en
   memoria: `{original_path, file_name, content_hash, size, zip_source}`.

`--dry-run`: imprime el manifiesto completo (descubrimiento + qué se
dedupearía) **sin llamar al backend ni subir nada** — imprescindible dado que
esto tiene efectos reales e irreversibles en BAS. Correr siempre primero.

---

## 3. Dedup por contenido (protocolo manifiesto-primero)

Dos fases, para no gastar ancho de banda subiendo algo que se va a descartar:

**Fase A — manifiesto**: el script llama a `POST /admin/batch-import/start`
con el manifiesto completo (solo metadatos + hash, sin bytes). El endpoint:

- Crea `import_batches` (`status=running`, `total_files=len(manifest)`).
- Por cada hash: busca en `invoices.content_hash` (histórico completo,
  cualquier fuente, `deleted_at = ""`) — si existe, crea el
  `import_batch_item` con `status=skipped_duplicate` apuntando a esa invoice.
- Entre los hashes repetidos dentro del propio manifiesto (el caso real:
  `MercadoPago_4.pdf` triplicado, las 5 fotos de WhatsApp), marca todos menos
  el primero como `skipped_duplicate` con `duplicate_of`.
- Responde con la lista de `item_id` que sí necesitan subir bytes.

**Fase B — subida**: el script sube solo los archivos "ganadores" (uno por
hash) a `POST /admin/batch-import/item/{item_id}/upload`.

**Limitación a documentar**: el hash detecta duplicados byte-idénticos. Una
foto re-comprimida distinta de la misma factura física no se detecta — no es
el caso confirmado en el inventario actual; si aparece en el futuro, la
mitigación sería una defensa en profundidad a nivel BAS (número de
comprobante + monto + proveedor), fuera de alcance de esta fase.

---

## 4. Disparo del procesamiento — dos triggers, un mismo modelo

**Trigger primario — script local** (`scripts/batch_import.py`):
habla únicamente HTTPS con el backend, mismo secreto que ya protege
`/process-invoice`. **No requiere credenciales de PocketBase en la laptop**
— todas las escrituras las hace el backend, mismo límite de confianza que ya
existe hoy.

**Trigger secundario — server-side**
(`POST /admin/batch-import/{batch_id}/retry-failed`): botón en el dashboard,
re-ejecuta el pipeline para items en `status=error` **y también para items
"huérfanos"** (`status` en `uploading`/`processing` con `updated_at` de hace
más de 15 minutos — ver §6), incrementando `attempt_count` sobre las mismas
filas, sin crear un batch nuevo.

Nuevos endpoints en `Invoicy/routes/batch_import.py`:

1. `POST /admin/batch-import/start` — manifiesto → dedup → crea batch + items.
2. `POST /admin/batch-import/item/{item_id}/upload` — multipart, un archivo
   real. Guarda en `downloads/batches/{batch_id}/{item_id}_{file_name}`,
   dispara `asyncio.create_task(...)`, responde 201 de inmediato.
3. `POST /admin/batch-import/{batch_id}/retry-failed` — reintento server-side.

---

## 5. Concurrencia y rendimiento

**Semáforo global a nivel módulo**, no por-endpoint — esto es lo que
distingue a este diseño de una alternativa más ingenua:

```python
# process_invoice_google_2.py, a nivel de módulo
PROCESSING_SEMAPHORE = asyncio.Semaphore(
    int(os.getenv("INVOICY_MAX_CONCURRENT_PROCESSING", "1"))
)
```

Envuelve `_procesar_imagen_o_pdf` en su propia definición
(`async with PROCESSING_SEMAPHORE:`), no en el wrapper del batch. Al vivir
dentro de la función misma, protege automáticamente los **tres** call sites
que la invocan hoy: los dos loops de ZIP ya existentes **y** el nuevo wrapper
de batch — así un lote grande no le roba recursos a alguien subiendo una
factura en vivo por el sitio público, y viceversa. Cap duro en código a
`min(env, 2)` para que nadie lo suba a un valor que tumbe el droplet.

Valor por defecto: **N=1** (estrictamente secuencial), mismo criterio ya
deliberado en el código de ZIP existente. Con 50 archivos a ~2min c/u: peor
caso ~100 min, típico realista bastante menor (el "par de minutos" es cota
superior). Aceptable porque el POST ya respondió 201 al toque — nadie espera
bloqueado — y el progreso es visible en vivo por Realtime, no es una caja
negra.

Nota real verificada en el panel de jueces: las llamadas a BAS/Gemini hoy
usan `requests` síncrono dentro del loop async, así que en la práctica *ya*
se serializan sin necesidad de semáforo. El semáforo sigue siendo buena
práctica — explícito, configurable, a prueba de una futura migración a
cliente HTTP async — pero vale ser honesto: no es la única razón por la que
hoy no hay concurrencia real.

---

## 6. Manejo de errores y recuperación (incluye el gap que el panel marcó como no negociable)

Wrapper nuevo `_procesar_item_de_batch(item_id, ...)` en `routes/batch_import.py`:

```
1. PB update item_id → status=processing
2. try: resultado = await _procesar_imagen_o_pdf(...)  # ya trae 6 reintentos internos
   → PB update item_id → status=completed, invoice=<relation>, attempt_count+=1
3. except Exception as e:
   → PB update item_id → status=error, error_message=str(e)[:2000], attempt_count+=1
   → log, NO re-raise
4. finally: si count(items del batch en pending/processing) == 0:
   → cerrar el batch (self-closing, sin endpoint explícito de "finish")
```

Cada item corre en su **propia** task disparada por su **propia** request de
upload — a diferencia del `for` compartido de los ZIP existentes, acá un
error en el item 3 de 24 no puede afectar la programación de los items
4–24, porque ya fueron disparados todos antes de que el item 3 termine.

**Recuperación tras un restart del backend a mitad de un batch** (gap real
detectado en la evaluación, sin mitigación en las 3 propuestas originales):
el cierre "self-closing" nunca dispara si el proceso muere a mitad de camino
— items quedan colgados en `uploading`/`processing` para siempre. Mitigación:
`retry-failed` (§4) también recoge items con `updated_at` de más de 15
minutos en esos dos estados, no solo `status=error`. No hace falta un reaper
en background con polling propio (no encaja con "sin polling" del resto del
sistema) — alcanza con que la UI ofrezca "Reintentar" también sobre esos
items huérfanos, con un aviso visible de "puede estar colgado".

Errores en la fase de discovery (zip corrupto, cifrado, excede el tope) se
capturan por archivo top-level: ese zip se marca error con motivo explícito,
el discovery sigue con los demás.

Estado final: `completed` si `total_error==0`, si no
`completed_with_errors` — nunca bloquea el cierre del batch.

---

## 7. UI del dashboard

Ruta nueva: `app/(dashboard)/lotes` (lista) y `app/(dashboard)/lotes/[batchId]`
(detalle) — nombrado en español, consistente con el resto de la app
(`/queue`→"Cola de revisión", `/invoices`→"Facturas").

**Lista** (`page.tsx`, Server Component): SSR de `import_batches` desc, badge
de `status`, `total_files`/`total_unique`, `started_at`.
`BatchesRealtime.tsx` (Client Component) se suscribe a
`pb.collection('import_batches').subscribe('*', cb)`, debounce 500ms,
`router.refresh()` — calcado de `QueueRealtime.tsx`.

**Detalle** (`page.tsx`, Server Component): SSR del batch + todos sus
`import_batch_items` (fetch único, sin paginar — a esta escala, decenas de
filas, tallar los agregados en memoria con JS plano es más simple y barato
que queries de conteo separadas). Por archivo: `file_name`, `zip_source`,
badge de status (mismo componente `StatusBadge` ya usado para
`invoices.status`), `error_message` colapsable, `attempt_count`, link a la
factura resultante cuando `completed`.

`BatchItemsRealtime.tsx`: se suscribe a `import_batch_items` filtrado por
`batch`. **Riesgo a validar en implementación**: el filtro por-suscripción
(`subscribe('*', {filter: ...})`) no tiene precedente de uso verificado en
este codebase — los dos `subscribe` reales usan `'*'` sin filtro o un solo
ID. Si el SDK no lo soporta como se espera, el fallback seguro (ya probado
en este mismo proyecto) es suscribirse a `'*'` sin filtro y descartar en el
callback JS los eventos que no correspondan a `batchId`.

Banner de resumen agregado, **derivado, no persistido como contador**: *"24
encontradas · 19 únicas · 5 duplicados omitidos · 14 completadas · 2 con
error · 3 procesando · 0 pendientes · tiempo transcurrido 12m34s"*. Decisión
clave que evita toda una clase de bugs: no hay campos
`processed_count`/`success_count` con incrementos concurrentes en
`import_batches` — se derivan siempre tallando `import_batch_items` al
renderizar. Los únicos writes al batch en sí son creación y cierre — cero
condiciones de carrera de contadores.

Botón "Reintentar fallidos" (visible si hay errores o huérfanos, ver §6).

---

## 8. Plan de implementación por fases

**Fase 0 — Modelo de datos**: migraciones PocketBase para `import_batches` e
`import_batch_items`; agregar `content_hash` a `invoices`; reglas de acceso
iguales a las que ya protegen `invoices`.

**Fase 1 — Backend core**: modificar `_procesar_imagen_o_pdf` para calcular y
guardar `content_hash` en toda invoice nueva (beneficia también a
`/website-upload`/`/process-invoice` existentes); introducir
`PROCESSING_SEMAPHORE` global envolviendo `_procesar_imagen_o_pdf`; crear
`routes/batch_import.py` con `start` y `item/{id}/upload`; wrapper
`_procesar_item_de_batch`.

**Fase 2 — Script local**: `scripts/batch_import.py` con descubrimiento
recursivo, límites anti zip-bomb/zip-slip, hash, protocolo
manifiesto-luego-bytes, `--dry-run`. Probar `--dry-run` contra la carpeta
real y verificar a ojo contra el inventario conocido (17 ítems únicos
esperados tras dedup).

**Fase 3 — Endpoint de reintento + recuperación de huérfanos**:
`retry-failed` con el criterio de `updated_at` de §6.

**Fase 4 — Frontend**: páginas `/lotes` y `/lotes/[batchId]`,
`BatchesRealtime.tsx`, `BatchItemsRealtime.tsx` (con el fallback de filtro
del §7 si hace falta).

**Fase 5 — Prueba controlada, NO el batch completo todavía**: correr contra
2-3 archivos reales (incluyendo uno que venga de dentro de un zip y uno que
falle a propósito) para confirmar el ciclo end-to-end sin duplicar en BAS.
**Regla de seguridad ya establecida en el proyecto**: toda prueba real
contra la API de BAS debe ser `Total=1` peso. Si estos archivos de prueba no
son las facturas reales de la carpeta sino fixtures de test, aplica esa
regla — si en cambio se decide procesar ya las facturas reales acumuladas
(CasaLab, Litoral Gas, MercadoPago, Movistar…), esa regla NO aplica y se
registran con su monto real: **hay que confirmar explícitamente con el
usuario cuál es el caso antes de correr nada real**, dado el impacto
contable.

**Fase 6 — Corrida completa**: subir la carpeta real completa; confirmar que
el dedup atrapa los 3 casos reales, que el `.json` queda `skipped_unsupported`
sin frenar nada, que ambos zips con subcarpeta se extraen bien, y que el
resumen final cuadra contra el inventario conocido.

**Fase 7 (futuro, fuera de alcance inicial)**: subir
`INVOICY_MAX_CONCURRENT_PROCESSING` a 2 solo si Fase 6 muestra margen real en
el droplet; cancelar batch; export CSV.

---

## Riesgos y supuestos a verificar durante la implementación

- El filtro en `subscribe('*', {filter: ...})` de PocketBase Realtime — sin
  precedente de uso verificado en este repo (ver §7, tiene fallback).
- `INVOICY_MAX_CONCURRENT_PROCESSING=1` es punto de partida conservador, no
  medido — ajustar tras observar memoria/tiempos en la primera corrida real.
- El nombre/mecanismo exacto de la dependencia de sesión admin del dashboard
  para proteger `retry-failed` (a diferencia de `start`/`upload`, llamados
  por el script con `X-Invoicy-Secret`, no desde el browser) — confirmar
  contra el archivo real de rutas admin antes de implementar.
- Dedup es por hash exacto únicamente — no cubre reencodeos (documentado
  como limitación conocida, no bug).

---

## Cómo se llegó a este plan

Se investigó primero el código real (pipeline de procesamiento existente,
manejo de ZIP actual, colecciones de PocketBase, patrón de tiempo real del
dashboard, límites de recursos del droplet, y se confirmó con un `find`+`file`
real el inventario y los duplicados de la carpeta). Con ese contexto como
brief, se lanzaron 3 propuestas de arquitectura completas e independientes
(ángulo "script local", ángulo "endpoint admin server-side", ángulo
"modelo de datos primero"), cada una evaluada por 3 jueces independientes que
verificaron las afirmaciones técnicas contra el repo real, no solo contra el
texto de la propuesta.

Puntajes promedio: script-local-cli 34.7/50, admin-endpoint-multipart
34.7/50, **batch-entity-first 36.7/50** (ganadora). Este documento es esa
propuesta ganadora, con dos correcciones factuales que los jueces detectaron
contra el código real (`asyncio.create_task` no `BackgroundTask`;
`downloads/` no `uploads/`) y una adición que el eje de robustez marcó como
no negociable: recuperación de items huérfanos tras un restart (§6).
