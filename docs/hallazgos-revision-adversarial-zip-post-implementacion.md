===== 1. [CRITICO] base_process_id de cliente no se valida como único: dos solicitudes concurrentes con el mismo id se pisan en disco y en PocketBase =====
Dimension: process_id
Archivo/ubicacion: routes/process_invoice_google_2.py | _extraer_zip_y_despachar_individualmente (líneas 2582-2765, esp. carpeta_base=2619, carpeta_miembro=2688-2690, shutil.rmtree(carpeta_base)=2757); llamada desde /process-invoice (base_process_id=id, línea 2884) y desde /website-upload (base_process_id=process_id, línea 3039)

DESCRIPCION:
El código asume que 'el índice de posición garantiza unicidad' del process_id de cada factura hija, pero eso solo es cierto si base_process_id (el 'id' de /process-invoice o el 'process_id' de /website-upload, ambos strings arbitrarios que llegan del cliente en el Form) es realmente único por invocación. Nada en el código lo valida: no hay chequeo contra PocketBase de que ese process_id/prefijo ya esté en uso, ni lock en memoria, ni rechazo si coincide con uno en curso. Si dos requests (concurrentes o superpuestas en el tiempo) llegan con el mismo base_process_id, ambas comparten literalmente la misma carpeta_base='./downloads/{base_process_id}' y los mismos índices 000, 001, 002..., así que extraen y sobrescriben los mismos archivos en disco, y generan el mismo string de process_id para posiciones equivalentes -- que al hacer upsert_invoice (clave única = process_id) mezcla/pisa en PocketBase el registro de una factura con el de otra completamente distinta. Peor aún: al terminar su propio despacho, cada ejecución hace shutil.rmtree(carpeta_base, ignore_errors=True) SIN verificar que la otra ejecución concurrente ya haya terminado -- borra la carpeta entera, incluidos los archivos que la otra ejecución todavía necesita leer para las facturas que le faltan procesar.

ESCENARIO DE FALLA:
Un integrador (p.ej. un workflow de automatización o wa-bot) llama POST /gemini2/process-invoice con id='lote-agosto' y un ZIP de 3 facturas. La request tarda (background), y ante un timeout HTTP el integrador reintenta automáticamente con el MISMO id='lote-agosto' y un ZIP DISTINTO (otras 3 facturas reales). Nada rechaza el segundo request. Ambas ejecuciones extraen a downloads/lote-agosto/000/, .../001/, .../002/ simultáneamente, pisando físicamente los bytes del otro proceso a mitad de la extracción o de la lectura por Gemini. Cuando la primera ejecución en terminar corre shutil.rmtree('downloads/lote-agosto'), borra los archivos que la segunda ejecución todavía está por despachar -- esas facturas quedan status='error' en PocketBase con un FileNotFoundError que no tiene nada que ver con su contenido real. Además, como el process_id de la posición 0 es idéntico en ambos casos ('lote-agosto--000-invoice.pdf'), el upsert de la segunda factura pisa el registro de PocketBase de la primera: dos facturas reales y distintas terminan colapsadas en una sola fila, perdiendo la trazabilidad de una de las dos. El mismo mecanismo es trivialmente disparable a propósito contra /website-upload, que es público (sin secret_key, solo rate-limited 5/min) y acepta el campo process_id directamente del formulario sin verificar que provenga de /website-upload/init.

POR QUE IMPORTA: Rompe la premisa central que el propio docstring del código declara ('el índice de posición... garantiza unicidad'): esa garantía depende de un supuesto no verificado. El resultado no es un error visible y claro sino silencioso -- facturas reales que se pierden, se mezclan con otras, o fallan con un mensaje de error que no refleja la causa real, en un sistema que además alimenta el registro de pagos en BAS.

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué el hallazgo contra el código real (no solo el diff) y se sostiene.

Confirmado en /Users/jacobdominguez/Documents/dinardi/Invoicy/routes/process_invoice_google_2.py:
1. `base_process_id` es 100% controlado por el cliente y nunca validado como único: en /process-invoice es `id: str = Form(None)` (comentario propio del código: "ID único para trackear el proceso" — una aspiración, no una garantía) usado tal cual como `base_process_id=id`; en /website-upload es `process_id: str = Form(None)`, usado tal cual como `base_process_id=process_id` en la rama ZIP. En ningún lugar se hace un `_find_one`/lookup contra PocketBase para rechazar un process_id ya en uso, ni existe ningún lock en memoria keyed por process_id (busqué explícitamente "en_curso", "IN_FLIGHT", locks de process_id en todo el repo — no hay nada; el único lock en memoria del archivo es para proveedor+comprobante de BAS, no relacionado).
2. `/website-upload/init` sí genera process_id con uuid4 internamente (colisión no factible), pero el endpoint /website-upload real NO verifica que el `process_id` recibido en el Form provenga efectivamente de /init — lo usa tal cual venga. Y es público, sin secret_key, solo rate-limited 5/min (no evita 2 requests con el mismo string en esa ventana).
3. `carpeta_base = f"./downloads/{base_process_id}"` y `carpeta_miembro = f"{carpeta_base}/{indice:03d}"` se confirman en el código real; dos ejecuciones con el mismo base_process_id comparten literalmente esas rutas.
4. `shutil.rmtree(carpeta_base, ignore_errors=True)` al final de `_extraer_zip_y_despachar_individualmente` es incondicional — no verifica si otra ejecución concurrente con el mismo base_process_id sigue usando esa carpeta.
5. `upsert_invoice` (utils/pocketbase_client.py:328) confirmado como upsert real por clave `process_id`: hace `_find_one` por ese campo y, si existe, hace PATCH (merge) sobre el mismo record — dos facturas reales con process_id colisionado sí se pisan en PocketBase.
6. El único gate de c

====================================================================================================

===== 2. [CRITICO] base_process_id sin sanear se usa para construir una ruta de disco (path traversal) y puede reintroducir '/' como separador real del process_id =====
Dimension: process_id
Archivo/ubicacion: routes/process_invoice_google_2.py | /process-invoice: id: str = Form(None) (línea 2797), zip_carpeta = f"./downloads/{id}" + os.makedirs + shutil.move (líneas 2871-2874); /website-upload: process_id: str = Form(None) (línea 2974, endpoint público sin secret_key, línea 2984), zip_carpeta = f"./downloads/{process_id}" + os.makedirs + shutil.move (líneas 3026-3029)

DESCRIPCION:
_sanear_nombre_para_process_id solo se aplica al NOMBRE del archivo dentro del ZIP, nunca a base_process_id (el 'id' de /process-invoice, o el 'process_id' de /website-upload) -- ambos son strings que llegan tal cual del cliente en el body del form, sin ninguna validación de caracteres, longitud, ni verificación de que no contengan '/', '..' u otros separadores. Ese valor se usa DIRECTAMENTE como componente de una ruta de disco real (f"./downloads/{id}" u os.makedirs + shutil.move), algo que antes de este diff no existía para la rama ZIP (antes se extraía siempre a una carpeta fija 'downloads', sin id de por medio) -- es una superficie nueva introducida por este cambio. Además, ese mismo valor sin sanear queda como PREFIJO literal del process_id de cada factura hija (f"{base_process_id}--{indice:03d}-{nombre}"), así que si contiene un '/' el process_id final vuelve a contener un '/' real -- exactamente el bug que todo el cambio de separador ('/' -> '--') dice haber resuelto, solo que ahora reintroducido por el lado del prefijo en vez del propio código de despacho.

ESCENARIO DE FALLA:
Un caller (en /website-upload no hace falta ni secret_key, solo pasar el rate limit de 5/min) envía multipart/form-data a POST /gemini2/website-upload con file=cualquier.zip (con 1 PDF válido adentro para pasar _validar_zip_rapido) y process_id='../../../../tmp/pwn'. El backend ejecuta os.makedirs('./downloads/../../../../tmp/pwn', exist_ok=True) y shutil.move del ZIP subido hacia esa ruta -- creando directorios y escribiendo archivos fuera de downloads/, en una ubicación arbitraria del filesystem del servidor (alcance exacto depende de permisos del proceso, pero es escritura de archivos fuera del sandbox esperado en un endpoint público). Alternativamente, con process_id='foo/bar' (sin intención de path traversal), el ZIP se extrae igual, pero cada factura hija recibe process_id='foo/bar--000-factura.pdf': ese '/' rompe el path converter {process_id}:str de FastAPI en los 8 endpoints (retry-op, invoices/{process_id}/file, retry-extraction, payment-orders/{process_id}/create, register-comprobante, recheck-provider, DELETE invoices, DELETE payment-orders) -- la factura queda registrada en PocketBase con un process_id al que ningún cliente normal (dashboard incluido) puede apuntar correctamente vía esos endpoints, huérfana en la práctica.

POR QUE IMPORTA: Es exactamente la clase de vulnerabilidad (path traversal en escritura de archivos) y exactamente la clase de bug (process_id con '/' rompiendo el path converter) que este mismo diff dice haber eliminado -- solo que el vector de entrada no es el separador interno (ya corregido) sino el propio base_process_id que el cliente controla y que nunca se valida, y en /website-upload ese cliente es literalmente cualquiera en internet.

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué el hallazgo contra el código real (no solo el diff) y se sostiene.

1) `/website-upload` (líneas 2964-3050 de routes/process_invoice_google_2.py) es efectivamente público: el docstring lo dice explícitamente ("Puerta de entrada pública (sin secret_key)...") y el código lo confirma -- no hay ningún parámetro `secret_key` ni chequeo de autenticación, solo `@limiter.limit("5/minute")` por IP. `process_id: str = Form(None, ...)` no tiene ningún `pattern`/regex/constraint de Pydantic, así que llega tal cual del body multipart.

2) Confirmé la línea exacta: `zip_carpeta = f"./downloads/{process_id}"` seguida de `os.makedirs(zip_carpeta, exist_ok=True)` y `shutil.move(file_location, zip_path)` (líneas 3026-3029). Ese `process_id` sin sanear pasa además como `base_process_id` a `_extraer_zip_y_despachar_individualmente`, donde vuelve a usarse crudo en `carpeta_base = f"./downloads/{base_process_id}"` y en `carpeta_miembro = f"{carpeta_base}/{indice:03d}"` (con su propio `os.makedirs`). Un `process_id` con secuencias `../` sí produce path traversal real: `os.makedirs` con `exist_ok=True` crea todos los directorios intermedios sin fallar aunque ya existan, y no hay ninguna resolución/normalización ni chequeo tipo `os.path.realpath(...).startswith(...)` en esta ruta (ese chequeo anti zip-slip sí existe, pero es sobre `miembro.filename` DENTRO del zip -- líneas 237-246 -- nunca sobre `base_process_id`). Es decir, el mecanismo de defensa que el propio diff ya implementó para un vector similar (zip slip interno) no se aplicó al vector externo (`base_process_id`).

3) Confirmé con `grep` que el mismo patrón (`f"./downloads/{id}"` + `os.makedirs` + `shutil.move`) también existe en `/process-invoice` (líneas 2871-2874), aunque ese endpoint sí exige `secret_key != os.getenv("SECRET_KEY")` (línea 2814) -- reduce la severidad ahí (caller de confianza) pero no elimina el bug de diseño: cualquier backend con el secreto (wa-bot, n8n, etc.) puede, por error o compromiso, enviar u

====================================================================================================

===== 3. [MODERADO] Dos routers legados montados en producción (/process-invoice y /gemini/process-invoice) siguen armando process_id con '/' real en su manejo de ZIP -- el mismo bug, sin corregir =====
Dimension: process_id
Archivo/ubicacion: routes/process_invoice.py | routes/process_invoice.py líneas 936 y 949 (rama ZIP de POST /process-invoice, sin prefijo); routes/process_invoice_google.py líneas ~1175 y ~1187 (rama ZIP de POST /gemini/process-invoice) -- ambos routers incluidos en server.py (include_router) junto al corregido process_invoice_google_2.py

DESCRIPCION:
El diff corrige el separador '/' -> '--' únicamente en process_invoice_google_2.py (prefijo /gemini2) y en batch_import.py. Pero routes/process_invoice.py (endpoint 'Procesar factura - Claude', sin prefijo, monta POST /process-invoice) y routes/process_invoice_google.py (endpoint 'Procesar factura - GEMINI', prefijo /gemini, monta POST /gemini/process-invoice) tienen su PROPIO manejo de ZIP, todavía con `"process_id": f"{id}/{file_name_in_zip}"` -- literal '/' como separador real, no en un comentario. Ambos routers siguen registrados vía app.include_router(...) en server.py y por lo tanto siguen siendo rutas HTTP reales y alcanzables en el proceso en ejecución (salvo que algo fuera del repo, como nginx, las bloquee explícitamente, lo cual no pude verificar). El impacto práctico está acotado porque ninguno de los dos orchestrators de esos archivos usa PocketBase (no llaman a pb_client/upsert_invoice en ningún lado) -- por lo que sus process_id con '/' nunca llegan a colisionar con los 8 endpoints de process_invoice_google_2.py, que sí leen de PocketBase.

ESCENARIO DE FALLA:
Si algún caller (histórico, mal configurado, o un integrador que todavía apunta a la URL vieja /process-invoice o /gemini/process-invoice en vez de /gemini2/process-invoice) sube un ZIP con un archivo llamado 'factura.pdf', el item despachado a orchestrator.task_queue.put(...) o orchestrator.fire_webhook(...) lleva process_id='{id}/factura.pdf' -- si ese id o ese payload se llegara a usar más adelante como path param de algún endpoint con {process_id}:str (hoy no existe tal endpoint en esos dos routers, pero el dato ya sale con '/' hacia fire_webhook, que si el consumidor externo de ese webhook intenta construir una URL con ese process_id tal cual, se rompe igual que se rompía antes del fix en process_invoice_google_2.py).

POR QUE IMPORTA: Responde directamente a la pregunta de auditoría ('¿quedó algún lugar que todavía arme un process_id con /... como separador real?') -- la respuesta es sí, en código real y montado, no en un comentario ni en un archivo muerto. Aunque el radio de impacto hoy es bajo porque esos pipelines no comparten la capa de PocketBase con los 8 endpoints auditados, es la misma clase de bug que el resto del diff se propuso eliminar y quedó fuera del alcance de la corrección.

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué el hallazgo contra el código fuente real, no contra la descripción del revisor, y se sostiene.

Hechos confirmados de forma independiente:

1. routes/process_invoice.py líneas 938 y 950 (el revisor dijo 936/949, offset menor sin importancia) contienen literalmente `"process_id": f"{id}/{file_name_in_zip}"` -- separador "/" real, no en comentario. Confirmado leyendo el archivo directamente.

2. routes/process_invoice_google.py líneas 1175 y 1187 contienen exactamente el mismo patrón `"process_id": f"{id}/{file_name_in_zip}"`. Coincide con lo que reportó el revisor.

3. server.py líneas 65-66 hacen `app.include_router(process_invoice_router)` y `app.include_router(process_invoice_google_router)` sin ninguna condición -- ambos routers están montados incondicionalmente en el proceso FastAPI real, junto al corregido process_invoice_google_2.py (línea 67). No encontré ningún nginx.conf en el repo que pudiera bloquear estas rutas (el propio revisor ya admitió no poder verificar eso).

4. Confirmé que ninguno de los dos archivos referencia pb_client/PocketBase/upsert_invoice (grep vacío en ambos) -- el acotamiento de impacto que propone el revisor es correcto.

5. Fui más allá del hallazgo original y verifiqué la propagación real: en process_invoice.py, la clase InvoiceOrchestrator SÍ define `self.task_queue = asyncio.Queue()` (línea 126) y tiene un worker en background que hace `await self.task_queue.get()` -- es decir, la rama ZIP de este endpoint es un pipeline VIVO y funcional, no código muerto: un archivo "compatible" dentro de un ZIP subido a POST /process-invoice realmente se encola con process_id conteniendo "/" y se procesa por el worker real.

6. En process_invoice_google.py, en cambio, la clase InvoiceOrchestrator solo define `self.job_queue` (no `task_queue`), así que la línea 1177 `await orchestrator.task_queue.put(item)` dispara AttributeError y el request entero cae en el except genérico (500) -- este archivo tiene un bug adicional, no relacionado c

====================================================================================================

===== 4. [SERIO] esZip del frontend (por extensión) no coincide con la detección real del backend (por magic bytes) -> placeholder "pending" huérfano permanente cuando el contenido es un ZIP pero el nombre del archivo no termina en .zip =====
Dimension: cola_review_status
Archivo/ubicacion: ticket-ai-dashboard/components/SubirFacturaForm.tsx | SubirFacturaForm.tsx líneas 108-121 (variable esZip y salto de reservarProcessId); Invoicy/routes/process_invoice_google_2.py función website_upload, rama `if kind.mime in ("application/zip", "application/x-zip-compressed")` (~línea 3025) y website_upload_init (~línea 2927-2949)

DESCRIPCION:
El frontend decide si un archivo "es un ZIP" mirando solo el nombre (`file.name.toLowerCase().endsWith(".zip")`), y SOLO en ese caso salta la llamada a POST /website-upload/init (que crea una fila real en PocketBase con status="pending" ANTES de subir el archivo). El backend, en cambio, decide si el archivo recibido es un ZIP mirando los magic bytes reales via `filetype.guess()` (kind.mime), completamente independiente del nombre. Estos dos criterios pueden divergir: cualquier archivo cuyo contenido real sea un ZIP pero cuyo nombre termine en una extensión no-.zip permitida (.pdf/.png/.jpg/.jpeg/.webp/.gif) hace que el frontend NO salte la reserva -- reserva un process_id single-invoice normal vía /init (fila "pending" real en PocketBase) -- y luego el backend, al leer los magic bytes, SÍ toma la rama ZIP usando ese mismo process_id reservado como `base_process_id`. La rama ZIP (tanto si `_validar_zip_rapido` acepta el ZIP como si lo rechaza) nunca vuelve a tocar ese process_id base: solo crea/actualiza filas nuevas con process_id sufijado (`{base_process_id}--{indice:03d}-{nombre}`) para cada factura extraída. La fila "pending" original queda huérfana para siempre -- nadie la transiciona a "processing", "completed" ni "error".

ESCENARIO DE FALLA:
Un usuario arrastra (drag-and-drop, que no respeta el atributo `accept` del <input>) un archivo cuyo contenido real es un ZIP pero cuyo nombre es, por ejemplo, "facturas_agosto.pdf" (renombrado por error, o generado así por una herramienta externa/integración). Frontend: esZip=false (no termina en ".zip") -> llama a /website-upload/init -> se crea en PocketBase invoices.process_id="website-<uuid-A>" con status="pending". Frontend envía el archivo con process_id="website-<uuid-A>". Backend: extension="pdf" pasa el filtro; process_id reusa "website-<uuid-A>"; filetype.guess() detecta magic bytes de ZIP real -> entra a la rama ZIP con base_process_id="website-<uuid-A>". Caso A (ZIP válido): se despachan N facturas reales con process_id "website-<uuid-A>--000-...", "--001-...", etc. -- todas visibles y correctas -- PERO la fila original "website-<uuid-A>" queda en status="pending" para siempre en la cola de revisión, sin archivo ni datos detrás, indistinguible a simple vista de una factura real pendiente. Caso B (ZIP rechazado por _validar_zip_rapido, ej. demasiados archivos o corrupto): el backend hace `shutil.rmtree` del directorio y lanza HTTPException 400 -- pero tampoco toca PocketBase, así que la misma fila "pending" huérfana queda igual, ahora sin ningún proceso real asociado ni siquiera parcialmente.

POR QUE IMPORTA: Este es exactamente el bug de fila fantasma que la investigación previa dice haber cerrado (agregando la condición esZip en SubirFacturaForm.tsx), pero el fix usa un criterio (nombre de archivo) distinto al que realmente decide el comportamiento del backend (contenido real). El equipo de revisión que confirma facturas para pagos reales en BAS vería una fila "pending" que nunca avanza, sin archivo adjunto y sin forma de resolverla (un retry-extraction fallaría porque no hay documento real detrás) -- ensucia la cola de forma permanente y erosiona la confianza en que "pending" siempre significa "algo real está en curso". Además, ningún test offline existente (revisé test_offline_zip_website_upload.py, casos C1-C6) ejercita el caso "extensión no-.zip pero contenido sí es ZIP" -- todos los casos con contenido ZIP real usan nombre "facturas.zip", así que este camino específico nunca se corrió ni en producción documentada ni en la suite de pruebas.

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué el hallazgo línea por línea contra el código real (no contra el diff solamente) y se sostiene.

1) Frontend (ticket-ai-dashboard/components/SubirFacturaForm.tsx, líneas 108-117): `esZip` se calcula únicamente por nombre (`file.name.toLowerCase().endsWith(".zip")`), y `reservarProcessId()` (que hace POST a `/website-upload/init`) solo se salta cuando `esZip` es true. El `accept={EXTENSIONES_PERMITIDAS.join(",")}` del input (línea 182) es la única otra defensa relacionada con extensión, y `accept` no se aplica a drag-and-drop (`handleDrop` solo lee `event.dataTransfer.files?.[0]` sin ningún filtro).

2) `website_upload_init` (process_invoice_google_2.py, ~2927-2949) confirma exactamente lo que dice el hallazgo: genera `process_id = f"website-{uuid.uuid4()}"` y hace `orchestrator._pb_client.upsert_invoice({"process_id": process_id, "status": "pending", ...})` — o sea, crea una fila real "pending" en PocketBase ANTES de que exista ningún archivo.

3) `website_upload` (~2989-3070): el filtro de extensión es por NOMBRE (`extension = file.filename.split(".")[-1].lower()`), no por contenido — un archivo "facturas_agosto.pdf" pasa este filtro. El `process_id` recibido del formulario (el reservado por `/init`) se reutiliza tal cual (`process_id = process_id or f"website-{uuid.uuid4()}"`). Solo DESPUÉS se guarda a disco y se hace `kind = filetype.guess(f.read(262))` — detección real por magic bytes. Si `kind.mime` resulta ser zip, entra a la rama zip usando ese `process_id` (el ya reservado) como `base_process_id` de `_extraer_zip_y_despachar_individualmente`.

4) Confirmé que `_extraer_zip_y_despachar_individualmente` (grep de `base_process_id` en todo el archivo) NUNCA hace upsert/update sobre el `base_process_id` mismo — solo lo usa para nombrar la carpeta de extracción, los logs, y como prefijo de los process_id sufijados (`{base_process_id}--{indice:03d}-{nombre}`) de cada factura individual. No existe ningún mecanismo de limpieza, cron o expiración de filas "pe

====================================================================================================

===== 5. [SERIO] ZIP grande sin registro durable hasta que le toca el turno: pérdida silenciosa y total ante un reinicio/crash del backend =====
Dimension: background_tasks
Archivo/ubicacion: routes/process_invoice_google_2.py | _extraer_zip_y_despachar_individualmente (definida ~línea 2579 del diff / línea 2619 en el archivo real, loop final de despacho en líneas ~2744-2755); comparar con routes/batch_import.py líneas 139-158 (create_batch_item con status="pending" ANTES de subir bytes) y 311-385 (find_stale_batch_items + endpoint /retry-failed)

DESCRIPCION:
Los archivos extraídos de un ZIP se acumulan en la lista en memoria `archivos_a_despachar` (variable local de la coroutine, no persistida en ningún lado) y se despachan uno por uno con `await _procesar_en_background(**archivo)` dentro de la MISMA background task. El único registro durable de una factura (el placeholder status="processing" en PocketBase) se crea recién DENTRO de `_procesar_imagen_o_pdf_impl`, es decir, solo cuando a ese archivo le llega su turno en el loop Y logra adquirir PROCESSING_SEMAPHORE (tamaño 1 por defecto, compartido además con /process-invoice, /website-upload individuales y batch_import). Dado que cada factura real puede tardar 1-2 minutos (Gemini + búsqueda de proveedor en BAS, según el propio comentario histórico de MAX_ARCHIVOS_ZIP que este diff reemplaza), un ZIP de 100 archivos puede hacer que el archivo #90 o #100 espere decenas de minutos u horas antes de que exista CUALQUIER rastro suyo en PocketBase. Si el proceso se reinicia (deploy, OOM en el droplet de 1 vCPU/960MB que los propios comentarios del archivo mencionan repetidamente) en cualquier momento de esa ventana, todo archivo que todavía esperaba su turno desaparece sin dejar ningún rastro: no hay fila en PocketBase, no hay log persistente, nada que permita saber que ese archivo existió. El propio repositorio ya resolvió este problema exacto para la feature hermana de importación masiva (batch_import.py: cada item nace como fila "pending" antes de que lleguen los bytes, y find_stale_batch_items + /retry-failed detectan y reintentan huérfanos tras un reinicio a mitad de un batch) pero esa protección no se trasladó a la feature de ZIP, que es justamente la que ahora permite lotes de hasta 100 archivos por un solo request.

ESCENARIO DE FALLA:
Alguien sube un ZIP de 80 facturas por /website-upload (o llega por el webhook de email). El servidor responde 201 "80 archivos recibidos, cada uno se procesa de forma independiente" de inmediato. La task en background arranca, despacha con éxito los primeros 12 archivos (cada uno visible y persistido en la cola de revisión), y al minuto ~20 el droplet sufre un OOM-kill y el proceso de Invoicy se reinicia. Los 68 archivos restantes -- ya extraídos en disco bajo ./downloads/{process_id}/012/ … /079/ pero cuyo turno en el for-loop nunca llegó -- desaparecen: ninguno tiene fila "processing" ni "error" en PocketBase, y no existe ningún job/endpoint que detecte que "faltan 68 de 80". Quien subió el ZIP ya recibió una confirmación de éxito para las 80 facturas y no tiene forma de enterarse de que 68 nunca se procesaron; el hueco solo se descubre días después si alguien nota manualmente que faltan facturas de ese proveedor.

POR QUE IMPORTA: Invoicy alimenta un flujo real hacia BAS (ERP/pagos): una factura que desaparece sin rastro puede traducirse en un pago que nunca se genera ni se reclama, sin ninguna alerta operativa ni forma de reconciliar después. El propio código demuestra que el equipo ya identificó y resolvió este exacto problema para batch_import, pero la feature de ZIP -- que es la protagonista de este diff -- quedó sin esa red de seguridad, justo cuando habilita lotes mucho más grandes (hasta 100 archivos) que cualquier subida individual previa.

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué el hallazgo línea por línea contra el código real (no solo el diff) y se sostiene en su totalidad.

Confirmaciones clave:

1. `_extraer_zip_y_despachar_individualmente` (process_invoice_google_2.py, def en línea 2579 real) construye `archivos_a_despachar` como lista local en memoria durante la fase de extracción (líneas 2620-2738) — no hay ninguna escritura a PocketBase en esa fase, solo `zip_ref.extract()` a disco y un `.append()` al dict Python.

2. El despacho real es un for-loop secuencial dentro de la MISMA coroutine/task (líneas 2744-2755): `for archivo in archivos_a_despachar: await _procesar_en_background(**archivo)`. Confirmado exactamente en esas líneas.

3. `_procesar_en_background` (línea 2484) solo envuelve `_procesar_imagen_o_pdf`, que a su vez (línea 2118, wrapper delgado) hace `async with PROCESSING_SEMAPHORE:` ANTES de llamar a `_procesar_imagen_o_pdf_impl` (línea 2137-2140). El único placeholder durable `upsert_invoice(status="processing")` vive recién dentro de `_impl` (línea 2238), es decir, después de adquirir el semáforo. Confirmé que `PROCESSING_SEMAPHORE = asyncio.Semaphore(min(int(os.getenv("INVOICY_MAX_CONCURRENT_PROCESSING","1")),2))` (línea 91) tiene tamaño 1 por defecto y es un único objeto global compartido por todos los callers (el propio docstring de la línea 2126-2132 lo dice explícitamente: "el gate de concurrencia global... para que el semáforo cubra a TODOS los callers reales").

4. `shutil.rmtree(carpeta_base, ...)` (línea 2757) corre solo al FINAL del for-loop completo, así que ante un crash a mitad de camino los archivos de los ítems no despachados quedan huérfanos en disco sin ninguna fila en PocketBase que los referencie.

5. Comparé con batch_import.py: confirmé que `create_batch_item(..., status="pending")` se crea ANTES de subir bytes (línea 139-147), que cada item corre en su PROPIA `asyncio.create_task` disparada por su propia request de upload (no un for-loop compartido — el propio comentario de `_procesar_ite

====================================================================================================

===== 6. [MODERADO] La extracción + verificación profunda del ZIP (testzip) no tiene ningún gate de concurrencia y bloquea el event loop completo del proceso =====
Dimension: background_tasks
Archivo/ubicacion: routes/process_invoice_google_2.py | _extraer_zip_y_despachar_individualmente, bloque `with zipfile.ZipFile(zip_path, "r") as zip_ref:` (línea real ~2657), `zip_ref.testzip()` en la línea ~2659, y el loop de extracción/clasificación de miembros inmediatamente después (hasta ~línea 2735, antes del primer `await _procesar_en_background`)

DESCRIPCION:
A diferencia del procesamiento real de cada factura (_procesar_imagen_o_pdf, protegido por PROCESSING_SEMAPHORE), la fase de abrir el ZIP + zip_ref.testzip() (que el propio comentario del código dice que es ~2617x más lento que el chequeo de metadata, precisamente porque descomprime todo para validar el CRC) + el loop de zip_ref.extract(...) + filetype.guess(...) para cada uno de hasta 100 miembros corre enteramente sincrónica, sin ningún `await` de por medio (no se usa asyncio.to_thread ni run_in_executor en ningún punto de esta función), y sin ningún semáforo que limite cuántas extracciones de ZIP pueden correr 'a la vez'. Dado que el servidor corre, según los propios comentarios del archivo, en un droplet de 1 vCPU (casi con certeza un solo worker/un solo event loop), mientras esta fase corre para un ZIP grande, ningún otro request concurrente en TODO el proceso -- otro upload, un healthcheck, una consulta al dashboard, otro webhook -- puede avanzar, porque el único hilo del event loop está ocupado ejecutando código Python/zlib puramente sincrónico. Y como esta fase no está gateada por PROCESSING_SEMAPHORE (que sí protege el trabajo pesado real de Gemini/BAS), varios ZIPs subidos casi al mismo tiempo por canales distintos generan varias tasks que, aunque en teoría cada una debería ceder el control, en la práctica cada una monopoliza el hilo durante su propia fase de extracción antes de llegar a su primer await real.

ESCENARIO DE FALLA:
Se suben casi simultáneamente dos ZIPs de ~90 archivos y ~400MB descomprimidos cada uno (dentro de los límites permitidos: MAX_ARCHIVOS_ZIP=100, MAX_ZIP_DESCOMPRIMIDO_BYTES=500MB) por dos canales distintos (por ejemplo /website-upload y el webhook de email, que no comparten rate limiter entre sí). Cada task creada por asyncio.create_task empieza a extraer y clasificar sus ~90 miembros sin ceder el control ni una sola vez hasta terminar toda su extracción. Durante esos varios segundos (I/O de disco más testzip recalculando el CRC de cientos de MB), cualquier otro request al servidor -- incluyendo un healthcheck de monitoreo o una factura suelta que alguien más está subiendo en paralelo por /process-invoice -- queda sin respuesta, aunque nada de eso dependa de Gemini ni de BAS.

POR QUE IMPORTA: PROCESSING_SEMAPHORE fue diseñado explícitamente para no saturar la única vCPU del droplet durante el trabajo pesado de facturación -- pero la nueva fase de extracción+testzip de ZIPs, que puede ser igual de costosa para archivos grandes, quedó completamente afuera de ese control. Esto introduce un vector nuevo y sin límite de concurrencia que puede degradar la disponibilidad de todo el servicio (no solo del canal que subió el ZIP) cada vez que entran varios ZIPs pesados en una ventana corta de tiempo.

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué el hallazgo leyendo el diff completo y el código fuente real (no solo la descripción del revisor).

Confirmado en routes/process_invoice_google_2.py, función `_extraer_zip_y_despachar_individualmente` (líneas 2579-2765 en el archivo real, coincide exactamente con el diff):

1. La función es `async def`, pero desde `with zipfile.ZipFile(zip_path, "r") as zip_ref:` (línea 2657) hasta el final del primer `for indice, miembro in enumerate(miembros):` (línea 2738) no hay NINGÚN `await` real que ceda el control del event loop -- ni `asyncio.sleep`, ni `to_thread`, ni `run_in_executor`. `zip_ref.testzip()` (2659), `zip_ref.extract()` (2690), `filetype.guess()` (2703), `os.makedirs`/`os.path.realpath` son todas llamadas síncronas/bloqueantes (CPU-bound por zlib, I/O de disco). El único `await` dentro del primer loop es `_notificar_no_soportado_si_corresponde`, que solo se ejecuta para archivos rechazados y de todas formas no ayuda a los casos exitosos que son la mayoría. El primer `await _procesar_en_background(...)` real que puede ceder el control ocurre recién en el SEGUNDO loop (línea 2746), después de que TODA la extracción ya terminó.

2. Los 3 call sites (`process_invoice` línea ~2882, `website_upload` línea ~3037, `webhook_endpoint` línea ~3179) todos llaman `asyncio.create_task(_extraer_zip_y_despachar_individualmente(...))` sin ningún semáforo ni gate de concurrencia alrededor -- a diferencia de `_procesar_imagen_o_pdf`, que sí está protegido por `PROCESSING_SEMAPHORE` (confirmado en línea 2137: `async with PROCESSING_SEMAPHORE:`).

3. Confirmé el supuesto de despliegue del reviewer: Dockerfile línea 29 y docs internos confirman `uvicorn server:app` sin flag `--workers` (un solo worker = un solo event loop), y el propio código lo admite explícitamente en un comentario existente (línea ~116): "con un solo worker de uvicorn no hay interleaving real". Varios docs (`plan-importacion-masiva-facturas.md`, `plan-manejo-zips-sin-lotes.md`) confirman "droplet de p

====================================================================================================

===== 7. [SERIO] testzip() aborta el ZIP COMPLETO por un solo miembro corrupto o encriptado, en vez de aislar solo ese archivo =====
Dimension: manejo_errores
Archivo/ubicacion: routes/process_invoice_google_2.py | _extraer_zip_y_despachar_individualmente, líneas ~2657-2670 (llamada a zip_ref.testzip() y el branch 'if miembro_corrupto is not None')

DESCRIPCION:
El docstring de la función promete explícitamente 'fallos aislados en las DOS etapas: un archivo que no se puede extraer no aborta el resto de la extracción'. Pero ANTES de entrar al bucle de extracción por-miembro, la función llama a zip_ref.testzip(), que recorre y descomprime TODOS los miembros del ZIP para chequear su CRC. Si testzip() encuentra un solo miembro con CRC corrupto (devuelve su nombre, no None) o si algún miembro está encriptado (levanta RuntimeError al intentar leerlo sin password), la función hace 'return resultado' de inmediato con aceptados=0 y error_zip seteado -- el bucle de extracción/despacho por índice NUNCA llega a correr, así que NINGÚN archivo del ZIP se procesa, incluidos los que están perfectamente bien.

ESCENARIO DE FALLA:
Un ZIP con 99 facturas PDF válidas + 1 archivo cuyo byte de datos comprimidos se corrompió en tránsito (un solo bit flippeado, típico de una subida con conexión inestable) o que quedó accidentalmente protegido con contraseña -> testzip() detecta el problema en ese único miembro -> la función retorna con aceptados=0, error_zip='ZIP corrupto: ...' -- las 99 facturas válidas NO se procesan, y quien subió el ZIP tiene que identificar/quitar el archivo problemático y resubir TODO el lote. Confirmado con el propio test A6 del repo (test_offline_zip_extraccion.py, líneas 388-401): un ZIP con 'a.pdf' válido + 'b.pdf' con CRC corrupto produce aceptados==0 y '_procesar_en_background NUNCA fue llamada', documentando este comportamiento tal cual, sin que ningún test lo señale como problema.

POR QUE IMPORTA: Contradice directamente el objetivo de diseño central de esta feature (aislar fallos parciales, evitar el concepto de 'lote todo-o-nada') y afecta los 3 canales por igual, ya que todos pasan por esta misma función compartida. En un caso de uso real (importar facturas contables reales), un solo archivo dañado descarta silenciosamente TODAS las demás facturas del envío.

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Confirmé el hallazgo leyendo el código real, no la descripción del revisor.

En routes/process_invoice_google_2.py, _extraer_zip_y_despachar_individualmente (líneas 2656-2670): dentro del `with zipfile.ZipFile(...)`, ANTES del bucle `for indice, miembro in enumerate(miembros)` (que arranca en la línea 2673 y es el que hace el manejo aislado por-miembro), se llama `zip_ref.testzip()` (línea 2659). Si detecta un miembro con CRC corrupto (devuelve nombre, no None) o si algún miembro requiere password y dispara RuntimeError (capturado explícitamente en el except de la línea 2660, que incluye RuntimeError), la función hace `return resultado` de inmediato (líneas 2663 y 2670) con `aceptados=0` -- el bucle de extracción/despacho individual nunca se ejecuta, así que NINGÚN archivo del ZIP se procesa, ni los válidos.

Verifiqué además:
1. _validar_zip_rapido() (líneas 2529-2576), que corre síncronamente en los 3 endpoints ANTES de crear el background task, documenta explícitamente en su propio docstring (línea 2539) que "A propósito NO llama zip_ref.testzip()" por costo (~2617x más lento medido) -- por diseño, deja pasar ZIPs con CRC corrupto o miembros encriptados hacia la etapa async, donde recién ahí se descubre el problema y se aborta TODO.
2. Los 3 canales (webhook email, /website-upload, /process-invoice) pasan por _validar_zip_rapido() y luego por esta misma función compartida vía asyncio.create_task -- confirmado leyendo los 3 call-sites en el diff (líneas ~496-514 para website-upload, ~605-620 para el webhook de email). El escenario afecta a los 3 por igual, tal como afirma el revisor.
3. El propio test A6 (scripts/test_offline_zip_extraccion.py, líneas 282-293 y 391-401) construye exactamente el escenario propuesto (a.pdf válido + b.pdf con 1 byte corrompido en los datos comprimidos) y el test PASA verificando `aceptados == 0` y `_procesar_en_background NUNCA fue llamada` -- es decir, el comportamiento está probado y aceptado como "correcto" por el test, sin que na

====================================================================================================

===== 8. [SERIO] UnicodeDecodeError de la librería zipfile escapa sin capturar en _validar_zip_rapido -> 500 en vez del 400 que la función existe para producir =====
Dimension: manejo_errores
Archivo/ubicacion: routes/process_invoice_google_2.py | _validar_zip_rapido, línea 2550 (except tuple) alrededor del 'with zipfile.ZipFile(zip_path, "r") as zip_ref:' de la línea ~2543

DESCRIPCION:
El except de _validar_zip_rapido solo cubre (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, RuntimeError). Pero zipfile.ZipFile() puede levantar UnicodeDecodeError al abrir un ZIP cuyo directorio central marca el bit de flag UTF-8 (0x0800) en el nombre de un miembro pero cuyos bytes no son UTF-8 válido -- el propio código fuente de zipfile hace 'filename.decode("utf-8")' sin manejo de errores en ese caso. UnicodeDecodeError es subclase de ValueError, NO de ninguna de las 4 excepciones capturadas -- lo verifiqué con issubclass() y lo reproduje construyendo un ZIP así a mano y ejecutando la función real (extraída vía AST del archivo de producción, igual que hacen los tests del repo): la excepción escapa de _validar_zip_rapido sin capturar.

ESCENARIO DE FALLA:
Un cliente sube (vía /gemini2/process-invoice o /gemini2/website-upload) un ZIP cuyas entradas se escribieron con el flag UTF-8 activado pero con bytes de nombre mal codificados (una inconsistencia real de algunas herramientas de zip/locales, o un archivo corrupto/manipulado). _validar_zip_rapido(zip_path) se llama sincrónicamente dentro del handler del endpoint; abrir el ZIP para leer infolist() levanta UnicodeDecodeError, que no está en el except tuple, se propaga, y termina atrapada solo por el 'except Exception as e: raise HTTPException(status_code=500, ...)' genérico de process_invoice (línea ~2909) o de website_upload (línea ~3074) -- el cliente recibe un 500 'Error interno del servidor' crudo con el texto interno de la excepción, en vez del 400 claro 'El ZIP está corrupto o no es un ZIP válido' que _validar_zip_rapido existe específicamente para dar en estos casos.

POR QUE IMPORTA: Es exactamente la misma clase de bug que ya se encontró y corrigió DOS veces en esta misma implementación (kind.media_type, kind is None) -- un except demasiado angosto que dejaba escapar una excepción real de una librería de terceros. Ninguno de los 17 tests offline ejercita este caso (grep confirma cero menciones de UnicodeDecodeError en los tests de ZIP).

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué el hallazgo de forma independiente y se sostiene.

1. Código real: en routes/process_invoice_google_2.py, _validar_zip_rapido (línea 2529) tiene exactamente:
   ```
   try:
       with zipfile.ZipFile(zip_path, "r") as zip_ref:
           miembros = [i for i in zip_ref.infolist() if not i.is_dir()]
   except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, RuntimeError) as e:
       return {"ok": False, "error": ...}
   ```
   Solo esas 4 excepciones están cubiertas.

2. Confirmé con issubclass() que UnicodeDecodeError (vía UnicodeError -> ValueError -> Exception) NO es subclase de zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error ni RuntimeError. Los cuatro resultados dieron False.

3. Inspeccioné el zipfile.py real de la instalación de Python (3.13.2) usada: en _RealGetContents, líneas ~1487-1490, si el bit de flag `_MASK_UTF_FILENAME` (0x0800) está activo, hace `filename = filename.decode('utf-8')` SIN try/except -- exactamente como describe el revisor. Esto corre dentro de ZipFile.__init__, o sea al abrir el archivo (la línea `with zipfile.ZipFile(zip_path, "r") as zip_ref:`).

4. Reproduje el caso de forma concreta: construí a mano (via struct.pack) un ZIP mínimo con un miembro cuyo flag central-directory tiene el bit UTF-8 activado pero cuyo nombre son bytes inválidos en UTF-8 (b'\xff\xfe\x00bad'). Al abrirlo con zipfile.ZipFile("r") directamente, Python lanza `UnicodeDecodeError('utf-8', b'\xff\xfe\x00bad', 0, 1, 'invalid start byte')`.

5. Extraje la función _validar_zip_rapido REAL del archivo de producción vía AST (mismo método que usan los tests del repo), la ejecuté con las constantes reales (MAX_ARCHIVOS_ZIP=20, MAX_ZIP_DESCOMPRIMIDO_BYTES=500*1024*1024) contra ese ZIP malformado, y la excepción escapó de la función sin ser capturada -- confirmado en la ejecución real, no solo por inspección.

6. Confirmé los dos call sites HTTP: en process_invoice (línea 2876, dentro de un try cuyo except en 2907-2913 es `except HTTPException: raise`

====================================================================================================

===== 9. [MODERADO] webhook de email: file_type puede ser None y '.startswith()' revienta con AttributeError -- PRE-EXISTENTE, no introducido por este diff, pero mismo patrón de bug que la feature ya corrigió dos veces =====
Dimension: manejo_errores
Archivo/ubicacion: routes/process_invoice_google_2.py | webhook_endpoint, línea 3199: 'elif file_type == "application/pdf" or file_type.startswith("image/"):' -- esta línea NO fue tocada por el diff, solo la rama ZIP de arriba (líneas 3158-3197) fue migrada

DESCRIPCION:
orchestrator.get_file_type_from_url() (línea 1955) devuelve None en varios casos reales -- status_code de descarga distinto de 200, filetype.guess() sin reconocer el contenido, o CUALQUIER excepción (red, timeout) -- pese a estar anotada '-> str'. Si file_type es None y no es ZIP, la condición 'file_type == "application/pdf"' es False y Python evalúa 'file_type.startswith("image/")' sobre None -> AttributeError. Queda atrapado por el except genérico de la función completa (línea 3290), así que no produce un 500 crudo, pero pierde el mensaje específico 'Invalid file type: None' y la rama de manejo dedicada (línea 3208-3217), devolviendo en su lugar un mensaje genérico 'Error interno al procesar el webhook'.

ESCENARIO DE FALLA:
Llega un webhook de email cuyo adjunto da timeout de red al descargarlo para detectar el tipo, o cuyo contenido no es reconocible por filetype.guess() -> get_file_type_from_url devuelve None -> AttributeError no capturado específicamente -> la respuesta es un mensaje interno genérico en vez de 'Invalid file type: None', dificultando que el bot de email (el caller real) distinga esto de una falla real del servidor.

POR QUE IMPORTA: Marco esto como severidad menor a los dos anteriores porque NO es parte del diff bajo revisión (la línea es idéntica a como estaba antes) y porque el manejador genérico ya evita que escale a un crash real -- lo incluyo solo porque la tarea pidió explícitamente buscar este mismo patrón de bug (acceso a atributo de un valor que puede ser None) 'en cualquiera de los 3 canales', y esta instancia sobrevive sin arreglar en el canal de email para el camino de archivo suelto (no-ZIP), aunque el patrón ya se corrigió dos veces en el código nuevo de esta misma feature.

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué el hallazgo contra el código real, no contra la descripción del revisor.

1) get_file_type_from_url (process_invoice_google_2.py, líneas 1955-1965) efectivamente puede devolver None pese a su anotación "-> str": si response.status_code != 200 cae al "return None" final; si hay cualquier excepción (timeout, error de red) el except devuelve None; y si filetype.guess(content) no reconoce el contenido, "kind" es None/falsy, el "if kind: return kind.mime" no se ejecuta y también cae al "return None" final.

2) En webhook_endpoint, línea 3128, file_type = orchestrator.get_file_type_from_url(attachments) — este valor se usa sin sanear. Confirmé en el diff (zip_diff.patch) que la línea "elif file_type == "application/pdf" or file_type.startswith("image/"):" (línea 3199 del archivo actual) aparece como línea de CONTEXTO (sin + ni -), es decir NO fue tocada por este diff — coincide con lo que afirma el hallazgo. Solo la rama ZIP de arriba (antes migrada a _validar_zip_rapido + _extraer_zip_y_despachar_individualmente) fue reescrita.

3) Reproducción concreta y realista, sin necesidad de invocar condiciones de carrera: si el adjunto del email NO es imagen/PDF/ZIP (p. ej. un .docx, .txt o .csv adjuntado por error, algo plausible en un flujo de email real), filetype.guess() no lo reconoce -> file_type=None. La descarga real (download_file_from_url, que solo chequea status_code, no el contenido) sí puede tener éxito igual. Se llega entonces a la rama "elif" con file_type=None: "None == 'application/pdf'" es False, y Python evalúa "None.startswith('image/')" -> AttributeError. Esa excepción no está en ninguna rama try/except específica de esa sección, así que sube y la atrapa el except genérico de la función completa (línea 3290), devolviendo el mensaje genérico "Error interno al procesar el webhook" (200 con success:false) en lugar del mensaje específico "Invalid file type: None" de la rama else (línea 3208-3217), que nunca se alcanza.

4) El endpoint no tiene status_co

====================================================================================================

===== 10. [SERIO] ZIP por email pierde el email de confirmación al remitente y el webhook externo por factura =====
Dimension: compatibilidad_endpoints
Archivo/ubicacion: routes/process_invoice_google_2.py | webhook_endpoint(), rama ZIP (líneas ~3158-3197) vs. InvoiceOrchestrator.worker() (enviar_email en línea ~739, fire_webhook en líneas ~756 y ~767)

DESCRIPCION:
Antes del diff, TODO archivo que llegaba por el canal de email (suelto o extraído de un ZIP) terminaba encolado en orchestrator.job_queue y lo procesaba worker(), que por cada factura llama self.enviar_email(from_email, subject_for_file, html_body) (confirmación al remitente) y self.fire_webhook(result) tanto en éxito como en error. El diff reescribió SOLO la rama ZIP de webhook_endpoint para despachar cada archivo extraído directamente a _extraer_zip_y_despachar_individualmente -> _procesar_en_background -> _procesar_imagen_o_pdf_impl. Verificado por grep: enviar_email tiene un único call site en todo el archivo (dentro de worker()) y _procesar_imagen_o_pdf_impl nunca lo invoca; fire_webhook en la nueva ruta solo se llama para archivos 'no soportados' y solo si notificar_no_soportados_por_webhook=True, flag que el origen 'email' nunca pasa (queda en su default False). El archivo suelto (no-ZIP) por email sigue yendo por job_queue/worker() sin cambios, así que ahora el mismo endpoint /gemini2/webhook se comporta de forma distinta según si el adjunto es un ZIP o no.

ESCENARIO DE FALLA:
Un remitente manda por email un ZIP con 3 facturas (imagen/PDF) a la casilla que alimenta /gemini2/webhook. Las 3 se procesan y quedan bien persistidas en PocketBase/Sheets/BAS (eso sí sigue funcionando), pero el remitente NUNCA recibe el email de confirmación que sí recibía si mandaba esas mismas 3 facturas en 3 emails separados (comportamiento previo, sigue intacto para adjuntos sueltos), y cualquier sistema externo que escucha WEBHOOK_URL para reaccionar a facturas procesadas no se entera de ninguna de las 3 -- ni éxito ni error -- porque para origen='email' nunca se llama fire_webhook.

POR QUE IMPORTA: Es una regresión silenciosa de un comportamiento que YA existía (y que el propio canal de email sigue teniendo para adjuntos sueltos). Los tests offline de esta feature (test_offline_zip_webhook_email.py) solo verifican ruteo -- que se use create_task en vez de job_queue.put -- no la presencia/ausencia de estos efectos secundarios, así que es fácil que pase desapercibido hasta que alguien en producción note que las notificaciones dejaron de llegar justo para los ZIPs.

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué el hallazgo directamente contra el código real (no solo contra el diff) y se sostiene punto por punto.

1) `enviar_email` tiene un ÚNICO call site en todo `process_invoice_google_2.py`: línea 739, dentro de `InvoiceOrchestrator.worker()`. `fire_webhook` tiene 3 call sites: líneas 756 y 767 (ambas dentro de `worker()`, incondicionales — se llama tanto en éxito como en el `except` de cada item) y línea 2641 (dentro del nuevo helper `_notificar_no_soportado_si_corresponde`, gateado por `notificar_no_soportados_por_webhook`).

2) En `webhook_endpoint()` (líneas 3158-3197 del archivo actual), la rama ZIP invoca `_extraer_zip_y_despachar_individualmente(zip_path=file_location, base_process_id=process_id, origen="email")` — sin pasar `notificar_no_soportados_por_webhook`, que por default queda en `False` — y hace `return` inmediatamente. La rama PDF/imagen suelta (líneas 3199-3264), en cambio, no retorna ahí: cae al código común que arma `job` y hace `await orchestrator.job_queue.put(job)`, que es lo único que consume `worker()`.

3) Comparando con el diff: ANTES del cambio, la rama ZIP (bloque `try/except` que extraía y clasificaba en `files_to_process`/`files_skipped`) NO tenía ningún `return` en el camino exitoso — solo los `except` (ZIP corrupto/demasiado grande) retornaban antes. Es decir, antes SÍ caía al mismo código común de `job_queue.put()` que la rama PDF/imagen, y por lo tanto los archivos de un ZIP por email SÍ pasaban por `worker()` (con `enviar_email` + `fire_webhook` incondicional). El diff introdujo el `return` temprano nuevo (líneas 3185-3197), lo que efectivamente saca a la rama ZIP del flujo `job_queue`/`worker()` — esto es precisamente la regresión que reporta el hallazgo.

4) Encontré además una prueba textual independiente, no tocada por este diff: el comentario en las líneas ~2331-2337 (dentro de `_procesar_imagen_o_pdf_impl`, código preexistente) dice literalmente "...a diferencia del branch ZIP, que encola vía job_queue/worker()" y otro 

====================================================================================================

===== 11. [MODERADO] El webhook de email ahora reporta 'success' inmediato aunque el ZIP no tenga ningún archivo procesable, y cambia el contrato de respuesta =====
Dimension: compatibilidad_endpoints
Archivo/ubicacion: routes/process_invoice_google_2.py | webhook_endpoint(), rama ZIP (líneas ~3158-3197)

DESCRIPCION:
Antes del diff, para un ZIP recibido por email el código clasificaba TODOS los miembros de forma síncrona (dentro del propio request, antes de responder) y, si terminaba con files_to_process vacío (ej. un ZIP donde todos los archivos son de un tipo no soportado), respondía explícitamente {success: False, status: 'error', message: 'No files to process'}. La respuesta de éxito además incluía to_process_count, skipped_count, files_to_process y files_skipped. Con el diff, _validar_zip_rapido -- el único chequeo síncrono que corre antes de responder -- no mira el tipo de contenido de los miembros, solo cuenta y peso total; la extracción real ocurre después en un asyncio.create_task nunca esperado, y el endpoint ya respondió {success: True, status: 'processing', ...} sin los 4 campos mencionados.

ESCENARIO DE FALLA:
Alguien manda por email un ZIP con puros archivos .txt o .docx (ningún tipo soportado). Antes: el caller de /gemini2/webhook recibía success:False de inmediato y sabía que nada se iba a procesar. Ahora: recibe success:True, status:'processing', como si las facturas fueran a aparecer, y solo en los logs del servidor (nunca en la respuesta HTTP) queda registrado que el 100% de los archivos terminaron en 'no_soportados' -- cualquier automatización que confiaba en success/files_skipped de esta respuesta para decidir si reintentar, avisar al remitente o loguear el resultado, ahora actúa sobre una señal de éxito falsa.

POR QUE IMPORTA: Es un cambio de contrato de respuesta (campos ausentes, semántica de 'success' más laxa) en un endpoint que ya construía esos campos deliberadamente para que un caller externo los leyera -- exactamente el tipo de ruptura sutil de 'comportamiento existente' que esta auditoría pide vigilar, y no está cubierto por los tests offline actuales (que solo chequean process_id/kwargs de ruteo, no la forma de la respuesta ni el caso 'ZIP con cero archivos soportados').

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué el hallazgo directamente contra el código fuente actual (no solo el diff) y se sostiene.

1. Leí el diff completo (zip_diff.patch). El hunk de `webhook_endpoint()` reemplaza toda la rama ZIP: antes hacía `zip_ref.extractall(temp_dir)` + `os.walk` + `mimetypes.guess_type` por miembro, clasificando SÍNCRONAMENTE en `files_to_process`/`files_skipped` antes de responder. Ahora solo corre `_validar_zip_rapido()` (que, según su propio docstring, "sin descomprimir nada" solo cuenta miembros y suma `file_size` — nunca mira tipo de contenido) y si pasa, hace `asyncio.create_task(_extraer_zip_y_despachar_individualmente(...))` sin await y devuelve de inmediato `{"success": True, "status": "processing", ...}`.

2. Crucialmente, el hunk del diff termina justo donde antes clasificaba archivos, y NO muestra el punto donde before/after se construía la respuesta final — porque ese código compartido (usado también por PDF/imagen) está MÁS ABAJO en la función y no fue tocado por el diff. Lo leí directamente en el archivo real (`routes/process_invoice_google_2.py` líneas 3219-3229 y 3267-3279):
   - Línea 3219: `if not files_to_process: return {"success": False, "status": "error", "message": "No files to process", ...}` — sigue existiendo en el archivo.
   - Líneas 3268-3279: la respuesta de éxito para el camino compartido sigue incluyendo `to_process_count`, `skipped_count`, `files_to_process`, `files_skipped`.
   Esto confirma que ese era exactamente el comportamiento pre-diff para ZIP (la rama ZIP vieja no tenía su propio `return`, caía a este bloque compartido después de clasificar), y que post-diff la rama ZIP nueva tiene su propio `return` temprano (líneas 3185-3197) que la saltea por completo — nunca llega a `if not files_to_process` ni construye esos 4 campos.

3. Confirmé que `_validar_zip_rapido` (tal como está en el diff) no verifica tipo de contenido de ningún miembro — solo cuenta y peso total — por lo que un ZIP con puros `.txt`/`.docx` pasa esa validación sin 

====================================================================================================

===== 12. [CRITICO] Path traversal en process_id/id permite shutil.rmtree sobre cualquier directorio (incluida la raíz de la app) sin autenticación =====
Dimension: seguridad
Archivo/ubicacion: routes/process_invoice_google_2.py | líneas 2619 (carpeta_base), 2757 (rmtree final), 2871/2878 (/process-invoice), 3026/3033 (/website-upload)

DESCRIPCION:
Tanto /process-invoice (`id: str = Form(None)`) como /website-upload (`process_id: str = Form(None)`) reciben ese valor DIRECTAMENTE del cliente como string, sin ningún whitelist de caracteres ni validación de formato (no se exige uuid, no hay regex). Ese valor se usa tal cual para construir una ruta de carpeta con f-strings: `zip_carpeta = f"./downloads/{id}"` (línea 2871) y `zip_carpeta = f"./downloads/{process_id}"` (línea 3026), y luego, dentro de `_extraer_zip_y_despachar_individualmente`, `carpeta_base = f"./downloads/{base_process_id}"` (línea 2619). Ninguna de las 3 hace basename() ni rechaza ".." antes de usarlo. El path resultante se usa para `os.makedirs(..., exist_ok=True)`, `shutil.move()` del zip subido, y -- lo más grave -- para `shutil.rmtree(zip_carpeta, ignore_errors=True)` en la rama de validación fallida (líneas 2878 y 3033) y para `shutil.rmtree(carpeta_base, ignore_errors=True)` al terminar de procesar exitosamente (línea 2757). Verifiqué empíricamente (Python 3.13 local, misma construcción f-string) que con `process_id=".."` la ruta `"./downloads/.."` resuelve (realpath) exactamente al directorio de trabajo del proceso -- que según el propio Dockerfile del repo es `/app` (WORKDIR /app). No hace falta ningún conocimiento del deployment: ".." siempre apunta un nivel arriba de downloads/, es decir la raíz de la app, en cualquier despliegue.

ESCENARIO DE FALLA:
Un atacante anónimo (sin login, sin secret_key) hace POST a /website-upload -- protegido solo por rate limiting (@limiter.limit("5/minute"), sin ninguna otra autenticación, según el propio docstring del endpoint ("Puerta de entrada pública (sin secret_key)") -- con `file` = cualquier ZIP pequeño (incluso corrupto a propósito para que _validar_zip_rapido falle rápido) y el campo de formulario `process_id=".."`. Esto hace que `zip_carpeta` resuelva al directorio raíz de la aplicación. Como `_validar_zip_rapido` rechaza el ZIP (corrupto/demasiado grande/demasiados archivos), el código ejecuta inmediatamente `shutil.rmtree(zip_carpeta, ignore_errors=True)` sobre esa ruta -- borrando recursivamente TODO el árbol de la aplicación (código fuente, entorno, todo lo que el proceso pueda escribir) con `ignore_errors=True` silenciando cualquier fallo parcial. Incluso si el ZIP fuera válido, el mismo destino se alcanza más tarde vía el `shutil.rmtree(carpeta_base, ...)` al final de `_extraer_zip_y_despachar_individualmente`. Para /process-invoice el mismo vector existe vía el campo `id`, pero requiere conocer el `secret_key` compartido -- de todas formas es una regresión real: el código ZIP anterior (pre-diff) extraía siempre a una carpeta plana fija `"downloads"` sin usar `id` para construir rutas, así que este patrón peligroso es enteramente nuevo de este diff.

POR QUE IMPORTA: 

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué el hallazgo línea por línea contra el código fuente real (no solo el diff) y no encontré ninguna mitigación que el revisor haya pasado por alto.

Confirmado en routes/process_invoice_google_2.py:
- Línea 2797: `id: str = Form(None)` (proceso-invoice) y línea 2974: `process_id: str = Form(None)` (website-upload) — ningún Field(pattern=...), regex ni validador Pydantic acompaña a estos parámetros. `grep` de "regex|pattern=|sanitiz|basename(id)|basename(process_id)" en todo el archivo no arrojó resultados.
- Línea 2871: `zip_carpeta = f"./downloads/{id}"` y línea 3026: `zip_carpeta = f"./downloads/{process_id}"` — construcción directa por f-string, sin os.path.basename ni chequeo de "..".
- Línea 2619 (dentro de `_extraer_zip_y_despachar_individualmente`): `carpeta_base = f"./downloads/{base_process_id}"` — mismo patrón, recibe el valor crudo del caller.
- shutil.rmtree confirmado en 3 puntos exactos que mencionaba el revisor: línea 2878 (`process-invoice`, rama de validación fallida), línea 3033 (`website-upload`, rama de validación fallida) y línea 2757 (fin de `_extraer_zip_y_despachar_individualmente`, se ejecuta incluso si el ZIP es válido).
- El único chequeo de path traversal que SÍ existe en el diff (línea ~240, comparación de `ruta_extraida.startswith(realpath(carpeta_miembro))`) protege contra Zip Slip vía NOMBRES DE MIEMBRO dentro del ZIP -- un vector distinto. Confirmé que scripts/test_offline_zip_extraccion.py efectivamente testea ese vector (test A13, línea ~515-561 con `sanear("../../etc/algo.pdf")`), pero ningún test offline ejercita process_id="..' o id=".." como valor del campo de formulario -- es un hueco de cobertura real, no una duda mía.
- Confirmé empíricamente con Python 3.11 (misma versión que corre en el Dockerfile) que `os.path.realpath("./downloads/..")` resuelve exactamente al cwd del proceso (verificado con un repro real, no solo teoría).
- Confirmé en Dockerfile: `WORKDIR /app`, sin ninguna directiva `USER` — el proceso uvicorn 

====================================================================================================

===== 13. [MODERADO] El chequeo manual de Zip Slip compara una ruta mal calculada (a partir del nombre crudo, no del que realmente escribió zip_ref.extract()) y puede descartar en silencio facturas legítimas =====
Dimension: seguridad
Archivo/ubicacion: routes/process_invoice_google_2.py | líneas 2690-2701 (zip_ref.extract + cálculo de ruta_extraida + chequeo startswith)

DESCRIPCION:
El chequeo hace `ruta_extraida = os.path.realpath(os.path.join(carpeta_miembro, miembro.filename))` usando el NOMBRE CRUDO del miembro del zip (sin sanear), y luego valida `ruta_extraida.startswith(realpath(carpeta_miembro))`. Pero `zip_ref.extract()`, en la línea inmediatamente anterior, ya escribió el archivo en el disco usando SU PROPIA sanitización interna de stdlib (desde Python 3.6, `_extract_member` separa el nombre por '/', descarta componentes vacíos/'.'/'..'  y solo une lo que queda) -- un algoritmo distinto al `os.path.normpath` que usa `os.path.realpath`. Verifiqué empíricamente que para un nombre de miembro como `"a/../a/evil.pdf"`: (1) el archivo REAL termina escrito en `carpeta_miembro/a/a/evil.pdf` (stdlib elimina el token '..' sin cancelar el 'a' anterior), mientras que (2) `ruta_extraida` calculada por el chequeo da `carpeta_miembro/a/evil.pdf` (os.path.realpath sí colapsa 'a/../a' a 'a'), una ruta que NO EXISTE. El chequeo `startswith` da `True` (pasa como "válida" porque sigue siendo descendiente de carpeta_miembro), así que el código sigue adelante y llama `filetype.guess(ruta_extraida)` sobre un archivo inexistente, lo cual lanza `FileNotFoundError` -- atrapado por el `except Exception` genérico del miembro (línea ~2731), que termina clasificando ese archivo como `"no_soportados"` / `"error de extracción"`. Es decir: una factura legítima dentro del ZIP, cuyo nombre interno tenga ese patrón de rutas relativas (no necesariamente malicioso -- puede surgir de ciertas herramientas de compresión o de una re-zipeada de una carpeta con nombres repetidos), se pierde en silencio aunque se haya extraído correctamente y de forma segura. Además, esto confirma que el chequeo manual no aporta protección real contra el path traversal clásico ("../../etc/passwd"): verifiqué por separado que zip_ref.extract() YA sanea ese caso antes de escribir a disco (un miembro "../../../../tmp/x" termina de forma segura en `carpeta_miembro/tmp/x`, nunca escapa) -- el chequeo manual solo 'acierta' por casualidad en ese caso (la ruta cruda con '../' calculada vía realpath cae fuera de carpeta_miembro y por eso se rechaza), no porque esté verificando la ruta real que stdlib escribió.

ESCENARIO DE FALLA:
Un ZIP con un archivo llamado, por ejemplo, `facturas/../facturas/factura_julio.pdf` (nombre perfectamente válido para herramientas de compresión que no colapsan rutas internas) se extrae correctamente en `carpeta_miembro/facturas/facturas/factura_julio.pdf`, pero el chequeo calcula `carpeta_miembro/facturas/factura_julio.pdf` (que no existe), `filetype.guess()` lanza FileNotFoundError, y la factura queda registrada como "tipo no soportado"/error de extracción y NUNCA llega a `_procesar_en_background` -- se pierde sin que el uploader tenga forma de saberlo (el mensaje de éxito ya se devolvió antes de que esto se ejecute en background).

POR QUE IMPORTA: 

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué el hallazgo empíricamente contra el código real, no solo leyendo la descripción del revisor.

1) Leí el diff (zip_diff.patch) y confirmé que el código en routes/process_invoice_google_2.py hace exactamente lo descrito:
   carpeta_miembro = f"{carpeta_base}/{indice:03d}"
   zip_ref.extract(miembro, carpeta_miembro)
   ruta_extraida = os.path.realpath(os.path.join(carpeta_miembro, miembro.filename))
   if not ruta_extraida.startswith(os.path.realpath(carpeta_miembro)): ... continue
   tipo_real = filetype.guess(ruta_extraida)
   ... todo envuelto en un try/except Exception genérico que marca el ítem como "no_soportados"/"error de extracción".
   El valor de retorno de zip_ref.extract() (que sí sería la ruta real escrita) nunca se captura ni se usa — se recalcula desde el nombre crudo.

2) Inspeccioné el código fuente real de zipfile._extract_member en la stdlib (Python 3.13, /opt/homebrew/.../zipfile/__init__.py) y confirmé el algoritmo que describe el revisor: separa por os.path.sep, descarta componentes en ('', '.', '..'), y re-une lo que queda — antes de os.path.normpath. Es un algoritmo distinto a os.path.realpath.

3) Reproduje el escenario con un ZIP real (zipfile.writestr con nombre de miembro "a/../a/evil.pdf"):
   - zip_ref.extract() escribió el archivo en disco en .../extracted/a/a/evil.pdf (confirmado con os.path.exists → True, y con el valor de retorno de extract()).
   - El cálculo manual del chequeo (os.path.realpath sobre el nombre crudo) dio .../extracted/a/evil.pdf — una ruta DISTINTA que no existe (os.path.exists → False).
   - El chequeo startswith() dio True (pasa como "válida").
   - Con el venv real que tiene filetype==1.2.0 instalado, ejecuté filetype.guess() sobre esa ruta inexistente y confirmé que lanza FileNotFoundError — exactamente la excepción que el try/except genérico del bloque atraparía, clasificando el archivo como "no_soportados"/"error de extracción" pese a haberse extraído correctamente y de forma seguridad.

4) También 

====================================================================================================

===== 14. [SERIO] El parseo del directorio central de un ZIP con cientos de miles de entradas ocurre ANTES del límite de 100 archivos y corre sync en el request; sumado a eso, un solo request ahora dispara hasta 100 jobs de procesamiento en vez de 1, sin ajuste al rate limit =====
Dimension: seguridad
Archivo/ubicacion: routes/process_invoice_google_2.py | líneas 2529-2549 (_validar_zip_rapido) y el @limiter.limit("5/minute") de /website-upload (línea 2963)

DESCRIPCION:
(a) `_validar_zip_rapido` abre el ZIP con `zipfile.ZipFile(zip_path, "r")` y llama `zip_ref.infolist()` (línea 2549) para poder CONTAR las entradas contra `MAX_ARCHIVOS_ZIP=100` -- pero construir esa lista completa de objetos `ZipInfo` requiere parsear TODO el directorio central del ZIP primero; el límite de 100 solo se aplica DESPUÉS de pagar ese costo. Medí empíricamente: un ZIP de 25.5MB con 300.000 entradas de 0 bytes tarda ~0.8s y usa ~195MB de RAM pico solo para abrir+listar, antes de que el chequeo de cantidad pueda rechazarlo. Esto corre SINCRÓNICAMENTE dentro del handler HTTP (no hay `await run_in_threadpool`), en un droplet documentado de 1 vCPU/960MB, y no encontré ningún límite de tamaño de subida a nivel de aplicación en este repo (el "hasta 15 MB" de SubirFacturaForm.tsx es solo texto de UI, no hay chequeo de `file.size` ni client-side ni server-side). Escalando la cantidad de entradas (varios millones, en un archivo que seguiría siendo de un tamaño de subida plausible) el consumo de memoria superaría fácilmente el presupuesto de RAM del droplet. Esta debilidad de "hay que parsear todo antes de poder limitar" ya existía en el código viejo de /process-invoice (namelist() tiene el mismo costo), pero antes de este diff /website-upload RECHAZABA cualquier ZIP explícitamente ("no se aceptan ZIP por este canal") -- es decir, este vector queda expuesto por primera vez en un endpoint público sin autenticación recién con este diff. (b) Independientemente de (a): /website-upload tiene `@limiter.limit("5/minute")` como única protección (no secret_key). Antes de este diff, cada request de ese endpoint disparaba como máximo 1 job de procesamiento (Gemini + búsqueda en BAS). Con este diff, un solo request con un ZIP puede disparar hasta 100 jobs independientes vía `asyncio.create_task`. Esto multiplica por hasta 100x el throughput real de jobs que un único caller anónimo puede encolar por minuto (hasta ~500 jobs/min en vez de 5/min), sin que el rate limiter haya sido ajustado para tenerlo en cuenta -- cada job es una llamada paga a Gemini más una búsqueda en BAS.

ESCENARIO DE FALLA:
Un atacante sin autenticar construye un ZIP de pocas decenas de MB pero con cientos de miles (o millones) de entradas de 0 bytes y lo sube a /website-upload. El `zipfile.ZipFile()+infolist()` de `_validar_zip_rapido` consume varios cientos de MB de RAM y corre en el hilo/loop que atiende el request, antes de poder aplicar el límite de 100 archivos -- en el droplet de 960MB esto puede provocar OOM del proceso completo, afectando a todos los usuarios. Alternativamente, con ZIPs válidos y bajo el límite de 100 archivos, el mismo atacante repite 5 uploads/minuto (el máximo que permite el rate limit) y logra encolar hasta 500 facturas/min para procesamiento real contra Gemini y BAS, un volumen 100x mayor al que el rate limit fue diseñado para permitir antes de que este canal aceptara ZIP.

POR QUE IMPORTA: 

VEREDICTO (razonamiento verificacion adversarial):
es_real: True
Verifiqué ambas mitades del hallazgo contra el código real y con reproducción empírica, no solo contra la descripción del revisor.

(a) Parseo sync del directorio central antes del límite de 100:
- Leí `_validar_zip_rapido` (process_invoice_google_2.py líneas ~2529-2549): abre con `zipfile.ZipFile(zip_path, "r")` y hace `zip_ref.infolist()` para poder contar `len(miembros)` contra `MAX_ARCHIVOS_ZIP=100` (línea ~2565 en el chequeo de cantidad). Técnicamente, `zipfile.ZipFile.__init__` ya parsea TODO el directorio central al abrir (antes de que se pueda llamar `.infolist()`), así que el costo se paga sí o sí antes de poder rechazar por cantidad — el reviewer tiene razón en el mecanismo.
- Confirmé que las 3 llamadas a `_validar_zip_rapido` (líneas 2876, 3031, 3160) son directas, sin `await run_in_threadpool(...)` en ningún lado del archivo (grep no encontró ninguna ocurrencia) — corre síncronamente dentro de un `async def` handler, bloqueando el event loop.
- Reproduje empíricamente (no confié en los números del reviewer): construí un ZIP de 300.000 entradas de 0 bytes (~29MB) y medí con `/usr/bin/time -l`: 0.94s real, **195MB de peak memory footprint** — prácticamente idéntico a lo que reportó el revisor (~0.8s / ~195MB), lo que corrobora independientemente su medición.
- Confirmé el spec real del droplet (1 vCPU/960MB) citado en comentarios del propio código (línea ~87) y en `docs/plan-manejo-zips-sin-lotes.md` — no es una cifra inventada por el revisor.
- Confirmé que `/website-upload` es público sin autenticación: el propio docstring del endpoint dice literalmente "Puerta de entrada pública (sin secret_key) ... Protegida con rate limiting" (línea ~2984), y que antes de este diff rechazaba ZIP explícitamente (el diff borra el string "no se aceptan ZIP por este canal").

Matiz que el revisor no verificó y que sí encontré: existe un `client_max_body_size 20m;` global en `ticket-ai-infra/gateway/nginx.conf` (línea 6, confirmado leyendo el archivo real de infraestructu

====================================================================================================

