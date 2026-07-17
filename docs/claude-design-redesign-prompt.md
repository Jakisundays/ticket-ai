# Prompt para Claude Design — Rediseño enterprise de Ticket AI Dashboard

> Este documento es un prompt listo para pegar en un proyecto de Claude Design. Fue generado auditando el código real del repo `ticket-ai-dashboard` (Next.js 16 App Router + PocketBase + shadcn/ui), no a partir de una descripción genérica. Todos los hallazgos citados (rutas, componentes, bugs de UX) existen hoy en el código.

---

## 1. Contexto del producto

**Ticket AI Dashboard** es una herramienta interna (no pública, salvo una pantalla) para el equipo que opera un pipeline de extracción de facturas por IA. Un backend en Python (Invoicy) recibe facturas por WhatsApp, email o un formulario web, las procesa con Gemini/Claude, y las guarda en PocketBase. Este dashboard es donde un humano:

1. Revisa los datos que la IA extrajo de cada factura junto a la imagen/PDF original.
2. Corrige lo que haga falta y confirma la factura.
3. Una vez confirmada, elige un método de pago y dispara la creación de una Orden de Pago real en BAS ERP (el sistema contable de la empresa).

Es un **producto de uso diario, repetitivo, por un equipo pequeño y no técnico** (no developers). No es una landing page ni un sitio de marketing: es una herramienta de trabajo. El criterio de éxito es que se sienta como Linear, Stripe Dashboard o Notion: familiar, rápida, sin fricción, sin sorpresas.

**Stack real (no cambiar):**
- Next.js 16, App Router, Server Components por defecto.
- PocketBase como backend de datos (colecciones: `invoices`, `invoice_items`, `bas_processing_status`, `bas_category_map`, `bas_payment_methods`, `payment_orders`, `users`, `service_accounts`).
- **shadcn/ui ya instalado**: preset `radix-nova`, `baseColor: neutral`, iconLibrary `lucide`, con soporte de tema OKLCH para light **y dark mode** ya definido en `app/globals.css` (incluye tokens `--sidebar-*` completos, aunque hoy no hay ningún componente Sidebar).
- Componentes shadcn ya presentes en `components/ui/`: `button`, `table`, `badge`, `select`, `input`, `label`, `separator`, `skeleton`, `sonner` (toasts), `alert`, `alert-dialog`.
- Tailwind v4, fuentes Geist Sans/Mono vía `next/font`.
- Autenticación: sesión de la colección `users` (nunca `service_accounts`, esa es exclusiva del backend Python), verificada en `proxy.ts` (el middleware de Next 16) de forma optimista y de nuevo server-side en cada Server Component.

**Idioma:** toda la interfaz está en español (Argentina/Latam). Mantené el idioma y la terminología de dominio existente (factura, comprobante, emisor, receptor, orden de pago, cola de revisión, etc.) — no traduzcas ni inventes nueva terminología sin justificarlo.

---

## 2. Tu rol y alcance — SOLO UI

Actuá como **senior product designer + design engineer** de un equipo de diseño enterprise. No entregues solo mockups: entregá código shadcn/ui listo para producción, con cada estado de interacción implementado, cada micro-detalle cuidado, y una justificación de diseño detrás de cada decisión.

**El alcance de este trabajo es estrictamente UI/UX visual y de interacción — no una reestructuración de producto.** Concretamente:

- Sí: diseño visual, jerarquía, layout, espaciado, tipografía, color, componentes, estados (loading/empty/error/success), interacciones, motion, accesibilidad, responsive, consistencia entre pantallas.
- No: cambiar qué hace cada pantalla, fusionar o eliminar rutas, rediseñar el modelo de datos, tocar lógica de negocio o de fetching, "arreglar" bugs de comportamiento del código, o proponer nuevos flujos/funcionalidades.

Cada pantalla mantiene exactamente las mismas acciones, los mismos datos y el mismo comportamiento que tiene hoy — lo único que cambia es cómo se ve, cómo se organiza visualmente, y cómo se siente interactuar con ella. Si en el camino notás algo que excede esto (un bug de lógica, una oportunidad de simplificar un flujo), anotalo en una sola línea al final de esa pantalla bajo "Fuera de alcance" y seguí — no lo implementes ni lo desarrolles.

---

## 3. Principios rectores (no negociables)

Estos tres marcos gobiernan cada decisión. Son complementarios, no alternativos.

### 3.A — Emil Kowalski (motion e interacción)

Este es un producto de uso repetitivo, no una pieza de marketing. Aplicá el filtro de frecuencia antes de animar nada:

- **Nunca animés acciones de teclado.** Este dashboard ya tiene una cultura de atajos (j/k para moverse en la cola, Cmd/Ctrl+Enter para confirmar, en `InvoiceReviewForm.tsx`). Esas acciones se usan decenas de veces por sesión — cero animación, siempre.
- **150–250ms para casi todo.** Dropdowns, selects, popovers: 150–250ms. Modales/diálogos: 200–300ms. Nunca superes 300ms en una transición de UI.
- **`ease-out` para elementos que entran, nunca `ease-in`.** Usá curvas custom (`cubic-bezier(0.23, 1, 0.32, 1)` como base), no los easings default de CSS.
- **Transiciones interrumpibles, no keyframes**, para cualquier cosa que el usuario dispare seguido (toasts, hover states, focus rings).
- **Solo animá `transform` y `opacity`.** Nunca `padding`, `margin`, `width`, `height`.
- **`transform-origin` consciente del trigger** en cualquier popover/dropdown (los diálogos sí quedan centrados — ver `alert-dialog.tsx`, que ya existe).
- **Motion transmite estado, no decoración.** Feedback de guardado, cambio de estado (needs_review → confirmed), aparición de un error: sí. Loops decorativos, parallax, choreography de carga de página: no, nunca en esta app.
- **`prefers-reduced-motion` obligatorio** en cualquier animación no trivial.
- Da feedback táctil real en botones y filas clicables: `scale(0.97)` en `:active`, nunca un simple cambio de color como único feedback.

### 3.B — Impeccable, registro "product" (consistencia y sistema)

Esta app es 100% registro **"product"** (herramienta con tareas), no "brand". Aplicá:

- **Una sola familia tipográfica** (Geist ya está — no la cambies ni agregues una display font). Escala fija en `rem`, no `clamp()` fluido: los usuarios ven esto en un monitor de escritorio a DPI consistente.
- **Ratio de escala ajustado** (1.125–1.2 entre pasos): más pasos tipográficos que en una landing, con menos contraste entre ellos.
- **Color restrained por defecto.** Un acento único para acciones primarias, selección actual e indicadores de estado — nunca decorativo. Vocabulario semántico de estado completo: hover, focus, active, disabled, selected, loading, error, warning, success, info. Estandarizalo una vez y reusalo en las 9 pantallas.
- **Cada componente interactivo necesita sus 7 estados**: default, hover, focus, active, disabled, loading, error. Si falta alguno hoy, es un hallazgo a corregir, no un detalle menor.
- **Skeletons para loading, nunca spinners** sueltos en medio del contenido.
- **Empty states que enseñan la interfaz**, no un "no hay nada" plano.
- **Mismo componente para la misma acción en todas las pantallas.** Si "Guardar" se ve distinto en `CategoryMapEditor.tsx` y en `PaymentMethodsEditor.tsx`, uno de los dos está mal — y hoy los dos son un `<button>` a mano, sin usar el `Button` de shadcn que ya existe.
- **Modal como último recurso, no como primer instinto.** Agotá alternativas inline/progresivas antes de un diálogo. El único modal real que existe hoy (`ReopenButton.tsx`, un `AlertDialog`) está bien justificado — no agregues más diálogos sin la misma justificación (acción irreversible o con consecuencia real).
- **Densidad está permitida y es correcta acá** (tablas con muchas filas, paneles con muchas etiquetas): no vacíes la interfaz "para que se vea premium". Este es un producto denso por naturaleza (factura con ítems, catálogos de BAS); la elegancia viene de la organización, no de quitar información.

### 3.C — Taste Skill: solo sus reglas universales, no sus reglas de landing page

La skill de "gusto/anti-slop" declara **explícitamente fuera de alcance** a dashboards y paneles de admin (recomienda un design system real como shadcn/ui para esos casos, que es justo lo que ya usamos). Por eso **NO apliques** sus reglas de hero, marquees, eyebrows, bento grids ni nada orientado a página de marketing — son irrelevantes acá y producirían un dashboard que se ve como landing page, que sería un error.

Sí aplicá sus reglas universales, porque valen en cualquier superficie:

- **Contraste real:** texto de cuerpo ≥4.5:1 contra su fondo; el gris claro "por elegancia" sobre un fondo casi blanco es el error más común de diseño generado por IA. Auditá cada `text-gray-400`/`text-gray-500` que hoy se usa como texto secundario — varios de ellos (placeholders, metadata en `/payment-orders`, `/invoices`) están al límite.
- **Contraste en botones:** verificá cada botón contra su fondo (WCAG AA 4.5:1). Ningún botón fantasma sin borde sobre un fondo similar.
- **Nombres y datos de ejemplo reales**, no genéricos, si necesitás mockear algo para un estado vacío o de ejemplo.
- **Sin em-dash como muleta de estilo** en ningún copy que escribas (usá punto, coma o dos puntos).
- **Un lock de color y de forma (radios) por página**: si elegís `rounded-lg` para cards, usalo en las 9 pantallas. Hoy hay mezcla real: `rounded-md` en inputs/botones, `rounded-lg` en contenedores, sin una regla explícita documentada.

---

## 4. Sistema de diseño existente — punto de partida obligatorio, no lo reinventes

Este es el hallazgo más importante de la auditoría: **el proyecto ya tiene un sistema de tokens shadcn/ui completo y correctamente configurado (`app/globals.css`), pero casi ninguna pantalla lo usa.**

Casi todo el código actual está escrito con clases de Tailwind ad hoc y hardcodeadas (`bg-white`, `text-gray-900`, `text-gray-500`, `border-gray-200`, `bg-gray-50`) en lugar de los tokens semánticos que ya existen (`bg-card`, `text-foreground`, `text-muted-foreground`, `border-border`, `bg-muted`). Esto significa que:

- **El dark mode está definido en CSS pero se rompería si se activara hoy** — ningún componente real lee las variables de tema, todos usan grises de Tailwind fijos.
- Los tokens `--sidebar-*` existen (`sidebar`, `sidebar-foreground`, `sidebar-primary`, `sidebar-accent`, `sidebar-border`, `sidebar-ring`) pero no hay ningún componente de sidebar en la app — la navegación hoy es una barra superior plana con 5 links de texto sin estado activo/seleccionado.

**Primer paso del rediseño: migrar todo a los tokens semánticos existentes** (no inventar unos nuevos) y decidir conscientemente si dark mode se soporta desde ya (los tokens están listos) o se pospone — pero documentá la decisión, no la dejes implícita.

Dos bugs concretos de la base de tokens que confirmá y corregí antes de construir nada encima:

- **La fuente Geist Sans probablemente no se está aplicando.** En `app/globals.css`, el bloque `@theme inline` define `--font-mono: var(--font-geist-mono)` correctamente, pero `--font-sans: var(--font-sans)` es **circular** (nunca apunta a `var(--font-geist-sans)`, que sí se carga en `app/layout.tsx`). Tailwind v4 tiene su propio fallback de `--font-sans` (una pila de fuentes del sistema), así que `html { @apply font-sans; }` probablemente esté renderizando la fuente del sistema, no Geist. Confirmalo visualmente (inspeccioná el `font-family` computado) antes de asumir que "Geist" es la tipografía real hoy.
- **Dark mode no puede activarse.** El proyecto depende de `next-themes` (lo usa `components/ui/sonner.tsx` vía `useTheme()`), pero no hay ningún `<ThemeProvider>` de `next-themes` montado en `app/layout.tsx` ni en ningún otro lugar. Aunque migrés todo a tokens semánticos, nada va a alternar la clase `.dark` sin este provider — es un prerequisito, no un detalle menor.
- Los tokens `--chart-1` a `--chart-5` también existen sin ningún gráfico en la app, y `--sidebar-primary` en modo oscuro es el único color con croma (azul) de toda la paleta — es un resto del scaffold de shadcn, no una decisión de marca. No asumas que hay más infraestructura de la que realmente existe.

### Inventario de componentes shadcn ya instalados (usalos, no reinventes)
`button`, `table`, `badge`, `select`, `input`, `label`, `separator`, `skeleton`, `sonner` (toaster), `alert`, `alert-dialog`.

### Componentes shadcn que faltan y vas a necesitar
`card`, `dialog` (distinto de alert-dialog, para formularios/edición), `dropdown-menu`, `tabs`, `tooltip`, `sheet` (para drawers), `command` (paleta de comandos, ver sección 6), `checkbox`, `textarea`, `pagination` o el patrón oficial de shadcn para paginar tablas, `sidebar` (el bloque oficial de shadcn, ya que los tokens `--sidebar-*` están listos para consumirlo), `breadcrumb`, `avatar` (para el usuario logueado), `data-table` (TanStack Table + shadcn, para las 4 tablas crudas que existen hoy).

### Patrones legacy a eliminar activamente (inconsistencias reales encontradas)

- **`window.alert()`** usado en `components/CategoryMapEditor.tsx` y `components/PaymentMethodsEditor.tsx` para errores — el resto de la app (`InvoiceReviewForm.tsx`, `ReopenButton.tsx`, `PaymentOrderPanel.tsx`) ya usa `toast.error()` de sonner. Unificá todo a toasts.
- **`StatusBadge.tsx`** es un `<span>` a mano con un diccionario de colores Tailwind fijos, no el `Badge` de shadcn. Once estados posibles hoy: `pending`, `queued`, `needs_review`, `processing`, `completed`, `confirmed`, `done`, `success`, `error`, `failed`. Consolidá esto en un solo vocabulario semántico (ver 3.B) sobre el `Badge` real.
- **4 pantallas usan `<table>` HTML plano** con `<input>`/`<select>` sueltos dentro de cada celda (`/invoices`, `/category-map`, `/payment-methods`, `/payment-orders`) en vez del `Table` de shadcn o un data-table real.
- **Botones hechos a mano** (`<button className="rounded-md bg-gray-900 ...">`) en al menos 6 lugares (`LogoutButton.tsx`, `CategoryMapEditor.tsx`, `PaymentMethodsEditor.tsx`, `login/page.tsx`, `subir-factura/page.tsx`, la versión bloqueada de `invoices/[id]/page.tsx`) en vez del `Button` de shadcn, que ya está instalado.
- **La navegación (`app/(dashboard)/layout.tsx`) no marca la pestaña activa** — un link visitado se ve igual que el resto.
- **Loading state = texto plano "Cargando…"** en `CategoryMapEditor.tsx` y `PaymentMethodsEditor.tsx`, sin skeleton.

---

## 5. Fase 0 — Fundamentos compartidos (hacelo ANTES de tocar una sola pantalla)

Como pide el brief original: auditá y reconstruí la base común antes de rediseñar pantalla por pantalla, para que las 9 pantallas hereden consistencia real en vez de coincidencia accidental.

1. **Navegación**: decidí y justificá top-bar vs. sidebar real. Los tokens `--sidebar-*` ya están listos para un sidebar shadcn oficial; hoy hay 5 secciones (Cola de revisión, Facturas, Categorías BAS, Métodos de pago, Órdenes de pago) más sesión de usuario — evaluá si eso amerita un sidebar con agrupación (ej. "Trabajo diario" vs "Configuración") o si un top-bar mejorado (con estado activo, breadcrumbs) alcanza. Cualquiera sea la decisión, resolvé el estado activo/seleccionado, que hoy no existe.
2. **Header/identidad de sesión**: email del usuario + logout hoy es texto plano + botón suelto. Diseñá un patrón consistente (avatar/iniciales + dropdown-menu con "Cerrar sesión", por ejemplo).
3. **Breadcrumbs**: hoy cada pantalla de detalle tiene su propio link "← Volver" hardcodeado (a `/queue`, a `/invoices`, a `/facturas`) sin patrón común. Definí un breadcrumb real y reusable.
4. **Botones**: variantes y tamaños consistentes (primario, secundario, destructivo, ghost, tamaño según densidad de la pantalla), con los 7 estados de 3.B.
5. **Badges/vocabulario de estado**: un solo mapa semántico para los ~11 valores de estado que existen hoy en distintas colecciones (`status` de invoice, `review_status`, `orden_pago_status`, `payment_orders.status`).
6. **Inputs, Selects, Checkboxes**: patrón único de label-arriba/helper-text/error-debajo (ya es la convención implícita, formalizala con los componentes reales de shadcn en vez de `<input>` sueltos).
7. **Tablas**: definí el patrón de tabla de datos (shadcn `table` + TanStack Table si hace falta orden/filtro/paginación) que van a compartir las 4 pantallas con tablas.
8. **Diálogos vs. Drawers**: cuándo usar `AlertDialog` (acción destructiva/irreversible, como reabrir), cuándo `Dialog` (formulario corto), cuándo un `Sheet`/drawer (edición más larga sin abandonar el contexto de la lista).
9. **Toasts**: consolidá TODO el manejo de errores/éxito en sonner, eliminando los dos `window.alert()` restantes.
10. **Estados vacíos y skeletons**: un patrón de esqueleto por tipo de layout (tabla, lista de cards, formulario) que calce con el layout final, no un spinner genérico.
11. **Paleta de comandos (opcional pero justificado)**: dado que el equipo ya tiene una cultura de atajos de teclado (j/k, Cmd+Enter) en la revisión de facturas, evaluá un `Cmd+K` global (shadcn `command`) para saltar a "Cola de revisión", "Buscar factura por comprobante", etc. Solo si de verdad reduce fricción, no como adorno.

---

## 6. Pantallas a rediseñar — ninguna se salta

Para **cada una** de las 9 pantallas, entregá:

1. **Objetivo de la pantalla** (una frase: qué tarea resuelve, quién la usa, con qué frecuencia).
2. **Problemas de UX/UI detectados** (usá los hallazgos reales que te doy abajo como piso, sumá los que encuentres vos).
3. **Mejoras propuestas**, cada una justificada (por qué, no solo qué).
4. **Rediseño del layout** con componentes shadcn/ui reales (código, no wireframe).
5. **Jerarquía visual** explícita (qué mira el usuario primero, segundo, tercero).
6. **Formularios, tablas, filtros, navegación y acciones** mejorados donde aplique.
7. **Todos los estados**: loading (skeleton específico de esta pantalla), empty, error, success.
8. **Interacciones, animaciones y microinteracciones** (con duración/easing concretos, siguiendo 3.A).
9. **Accesibilidad y responsive** (foco de teclado, ARIA donde corresponda, colapso mobile si aplica — esto es una herramienta interna usada mayormente en desktop, pero no asumas que nunca se abre en una laptop chica o tablet).
10. **Consistencia** con la Fase 0 (mismo botón, mismo badge, mismo patrón de tabla que el resto).

Orden sugerido (sigue el flujo real de trabajo del equipo, no el orden alfabético de rutas):

### 6.1 — Login (`/login`)
Formulario de email/contraseña hecho a mano (`<input>` planos, sin `Label`/`Input` de shadcn). Sin loading skeleton, sin "olvidé mi contraseña" (evaluar si hace falta dado que es un equipo interno chico), error inline simple, sin foco visible consistente entre campos y botón. Es la primera impresión del producto — no debería sentirse menos cuidada que el resto.

### 6.2 — Cola de revisión (`/queue`)
Post-login landing. Hoy es la pantalla mejor resuelta de la app (empty state con copy útil, hover states, orden correcto). Aun así: sin filtros ni buscador visual, sin skeleton de carga, sin un estado de error diseñado. El texto "Revisar →" es `text-gray-400` sobre blanco (~2.8:1 de contraste, por debajo de WCAG AA) — corregilo.

### 6.3 — Detalle de factura, revisión editable (`/invoices/[id]`, rama `needs_review`)
El corazón del producto: split-view con el archivo original (`InvoiceFileViewer.tsx`, un iframe) a la izquierda y el formulario editable (`InvoiceReviewForm.tsx`) a la derecha. Ya tiene atajos de teclado (j/k, Cmd+Enter), auto-recálculo de `precio_total`, badge de advertencia si los ítems no cuadran con el total. La tabla de ítems es HTML crudo con inputs sueltos por celda — la parte más "hoja de cálculo" de toda la app; es candidata fuerte a un patrón de tabla editable más pulido visualmente. No rompas ni el atajo de teclado ni el auto-recálculo: son funcionalidad real, no decoración — tu trabajo es vestirlos mejor, no tocar su comportamiento.

### 6.4 — Detalle de factura, confirmada + orden de pago (`/invoices/[id]`, rama `confirmed`)
Vista bloqueada de solo lectura (grid `dt`/`dd` a mano) + `ReopenButton` (ya usa `AlertDialog`, el único modal bien justificado de la app) + `PaymentOrderPanel` (la pantalla con más adopción de shadcn hoy: `Select`, `Input`, `Button`, `Alert`, `Badge`). Tomalo como referencia de calidad mínima para el resto. El contexto de negocio importa acá para el copy y el diseño del estado de error: **crear la Orden de Pago en BAS está bloqueado del lado del ERP hoy** (un bug/config pendiente del admin de BAS, no de este código) — todo intento real falla y el error de BAS se muestra tal cual. El diseño del estado de error debe leerse como "esperando a que BAS lo resuelva", nunca como "esto está roto" — no inventes copy que sugiera lo segundo. Hoy el estado del intento automático de BAS ("Estado BAS") y el de la orden manual ("Orden de pago") son dos badges visualmente desconectados y con distinta jerarquía — mejorá su relación visual sin cambiar qué representa cada uno.

### 6.5 — Listado de facturas (`/invoices`)
Tabla HTML cruda de 9 columnas, sin filtros ni búsqueda visibles, sin indicador de carga ni de error. Bug de consistencia visible a corregir como parte de unificar el vocabulario de estados (sección 5.5): `StatusBadge` imprime el string crudo del backend (`"completed"`, `"error"`, `"success"`, `"failed"`) sin traducir, así que una pantalla en español ("Facturas", "Comprobante", "Emisión"...) tiene badges en inglés en cada fila.

### 6.6 — Órdenes de pago (`/payment-orders`)
Tabla HTML cruda de 8 columnas, columna de error truncada con `title` (tooltip nativo del navegador, no accesible ni bonito). Es una vista de auditoría, no de edición: buen candidato para un data-table con expansión de fila para ver el error completo en vez de truncarlo. Mismo bug de vocabulario que en 6.5: `StatusBadge` muestra el enum crudo (`processing`/`success`/`failed`) mientras `PaymentOrderPanel.tsx` un click al lado ya tiene un mapeo a español (`STATUS_LABEL`) — unificalo.

### 6.7 — Mapa de categorías BAS (`/category-map`)
Tabla HTML cruda + inputs sueltos + botón "Guardar" por fila + `window.alert()` en error + formulario de alta al pie sin usar ningún componente shadcn. Es probablemente la pantalla menos pulida de toda la app (se nota que fue la primera en construirse). Buena candidata para edición inline con feedback visual de guardado por fila (no un botón "Guardar" que solo se habilita si hay cambios, sino un patrón más fluido) — sin cambiar qué se puede o no editar.

### 6.8 — Métodos de pago (`/payment-methods`)
Estructura casi idéntica a category-map (3 filas fijas en vez de lista dinámica), mismo `window.alert()`, mismo patrón de guardado por fila. Ya tiene una buena decisión de UX (el campo "cuenta bancaria" se deshabilita salvo para "transferencia") — conservala, solo mejorá su presentación visual.

### 6.9 — Subir factura pública (`/subir-factura`)
Única pantalla sin login, protegida por rate-limiting en el backend en vez de sesión. Estados idle/uploading/success/error ya manejados, incluyendo el caso de rate-limit (429) con mensaje distinto. Le falta drag-and-drop de archivo (el patrón esperado hoy para cualquier subida de imagen/PDF) y feedback visual de qué archivo se seleccionó antes de subir.

---

## 7. Qué NO cambiar ni tocar bajo ningún concepto

- **Ningún flujo, ninguna funcionalidad, ningún comportamiento.** Cada pantalla hace exactamente lo mismo antes y después — solo cambia su presentación visual e interacción.
- **No fusionar, dividir, eliminar ni agregar rutas.** Las 9 pantallas de la sección 6 son las 9 pantallas finales.
- Slugs de ruta (`/queue`, `/invoices`, `/invoices/[id]`, `/category-map`, `/payment-methods`, `/payment-orders`, `/subir-factura`, `/login`).
- Nombres de colecciones y campos de PocketBase, lógica de fetching, validaciones y estructura de datos.
- El modelo de autenticación (`users` vs `service_accounts`) y la lógica de `proxy.ts`.
- La lógica de negocio de BAS (dry_run, dos fases de confirmación, el bloqueo actual del lado del ERP) — es contexto real para saber cómo diseñar el estado de error, no algo a "arreglar".
- El idioma (español) y la terminología de dominio existente.
- Los atajos de teclado ya implementados (j/k, Cmd/Ctrl+Enter) y su comportamiento — mejorá su descubribilidad visual si hace falta, no los quites, no les cambies el comportamiento, no les agregues animación.

Si en el camino ves un bug de lógica o una oportunidad de simplificar un flujo, escribila en una sola línea bajo un "Fuera de alcance" al final de la pantalla correspondiente y seguí — no la implementes ni la desarrolles en código.

---

## 8. Entregable esperado

1. Un **plan de ejecución por fases**: Fase 0 (fundamentos compartidos) primero, después las 9 pantallas en el orden de la sección 6 — sin saltarte ninguna.
2. Para cada fase/pantalla, el análisis completo pedido en la sección 6 (objetivo, problemas, mejoras, layout, jerarquía, estados, interacciones, accesibilidad, consistencia) seguido del código real en shadcn/ui.
3. Al final, un resumen de qué componentes nuevos de shadcn se instalaron y qué patrones legacy se eliminaron (los de la sección 4).

El objetivo final: que el equipo que usa esto todos los días sienta que está usando una herramienta de nivel Linear/Stripe/Notion, sin fricción, sin sorpresas, con cada detalle visual — por chico que sea — cuidado a propósito. El producto hace lo mismo que hoy; simplemente se ve y se siente incomparablemente mejor.
