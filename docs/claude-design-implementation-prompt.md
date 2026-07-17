# Prompt para implementar el rediseño de Claude Design en el repo real

> Prompt para la sesión de Claude Code que va a integrar el rediseño ya generado dentro del código real de `ticket-ai-dashboard`. Los archivos fuente están en:
> `/Users/jacobdominguez/Desktop/Rediseño enterprise Ticket AI Dashboard/`
> (12 archivos `.dc.html` + una carpeta `lib/` + `support.js` + `.thumbnail`). Pegá este prompt tal cual al iniciar la tarea — ya tiene el mapeo hecho, no hace falta volver a inventariar.

---

## 1. Qué son estos archivos (leé esto antes de tocar nada)

Los `.dc.html` **no son componentes React ni código para copiar literal**. Son mockups interactivos de alta fidelidad generados por Claude Design, con su propio motor de preview standalone:

- `support.js` interpreta un runtime propio (`<x-dc>`, clases que extienden `DCLogic`, bindings tipo `{{ variable }}`, directivas custom `<sc-if>`/`<sc-for>` y atributos `style-hover=`/`style-focus=`/`style-active=` para estados de interacción).
- `lib/data-store.js` es un **mock de PocketBase que persiste en `localStorage`** del navegador (`tad_prototype_db_v1`) — simula el backend real para que el prototipo se pueda navegar de punta a punta sin servidor. Trae datos de ejemplo con nombres reales (proveedores como "Distribuidora Norte S.R.L.", "ElectroSur Mayorista S.R.L.") y un vocabulario de estado unificado (`STATUS_META`, 5 buckets: `neutral`/`info`/`warning`/`success`/`destructive`).
- El prototipo soporta `?vista=loading`, `?vista=error`, `?vista=empty` (en Cola de revisión y Facturas) y `?vista=429` (en Subir factura) para forzar esos estados sin esperar a que ocurran — abrilos así en el navegador para ver esos estados diseñados antes de reimplementarlos.

**Tu trabajo es usar cada `.dc.html` como especificación visual/de interacción autoritativa** (colores exactos en OKLCH, spacing, radios, tipografía, estados, copy, animaciones) **y reimplementarla en TSX real** usando shadcn/ui + los tokens de `app/globals.css`, cableada a la lógica y los datos REALES que ya existen en el repo (PocketBase real, no el mock de `data-store.js`). No intentes importar o ejecutar los `.dc.html`/`support.js` dentro de la app Next.js.

Abrí cada `.dc.html` directo en el navegador (doble click, o `open archivo.dc.html`) para verlo renderizado e interactuar antes de reimplementarlo en código — es mucho más rápido que leer el HTML/JS crudo para entender el diseño.

---

## 2. Leé `notas-diseno.dc.html` primero — son las decisiones de sistema ya tomadas, no sugerencias

Esa página (abrila en el navegador) documenta las decisiones de Fase 0. Ya están tomadas — implementalas tal cual, no las re-evalúes:

1. **Migración a tokens semánticos**: todo el color pasa de clases Tailwind hardcodeadas (`bg-white`, `text-gray-500`, `border-gray-200`) a un vocabulario semántico (fondo/texto/texto secundario/borde/superficie) aplicado igual en las 9 pantallas.
2. **Bug de tipografía corregido**: `--font-sans: var(--font-sans)` en `app/globals.css` es circular — corregilo para que apunte a `var(--font-geist-sans)`. Geist Sans/Mono pasan a ser la tipografía real.
3. **Dark mode: pospuesto a propósito** — no montar `ThemeProvider` de `next-themes` ni agregar toggle en esta pasada. Es una decisión documentada, no una tarea pendiente a resolver vos.
4. **Acento de marca**: `--sidebar-primary` en modo oscuro (`oklch(0.488 0.243 264.376)` en el original) era el único color con croma de todo el scaffold, sin uso real. Se adoptó como el único acento de marca del producto. **El valor final usado en todo el prototipo es `oklch(0.52 0.17 258)`** — usalo para acciones primarias, selección activa, y el estado "info". Confirmalo contra los `.dc.html` (aparece en los 9) antes de fijarlo en `globals.css`.
5. **Navegación: sidebar, no barra superior**. Usa los tokens `--sidebar-*` (ya existían sin uso). Agrupado en dos secciones: **"Trabajo diario"** (Cola de revisión, Facturas, Órdenes de pago) y **"Configuración"** (Categorías BAS, Métodos de pago) + link a "Notas de diseño". Header del sidebar: "TA" / "Ticket AI". Pie: menú de usuario con iniciales, email, "Equipo Dinardi", "Cerrar sesión".
6. **Vocabulario de estado unificado**: los ~10 valores crudos del backend (`pending`, `queued`, `needs_review`, `processing`, `completed`, `confirmed`, `done`, `success`, `error`, `failed`) se consolidan en 5 buckets visuales (`neutral`, `info`, `warning`/advertencia, `success`/éxito, `destructive`/error) a través de un único componente de badge (ver `StatusBadge.dc.html` y `STATUS_META` en `lib/data-store.js`), reusado en las 9 pantallas.
7. **Radios: lock de 3 niveles** — 6px para badges/chips, 8px para controles (botones, inputs, dropdowns), 12px para contenedores (cards, tablas, diálogos). No uses un valor fuera de esa escala salvo casos puntuales ya presentes en el diseño (íconos circulares, avatares, etc.).
8. **`window.alert()` eliminado**: en Categorías BAS y Métodos de pago se reemplazó por feedback inline por fila ("guardando…" → "guardado", con ícono), no un toast ni un modal, porque la acción es local a esa fila.
9. **Modal como último recurso**: el único diálogo modal de todo el rediseño sigue siendo "Reabrir factura" (`AlertDialog`), porque revierte una confirmación real. Todo lo demás (guardar una categoría, crear una orden de pago, subir un archivo) se resuelve en línea.
10. **Paleta de comandos (⌘K)**: se agregó — el equipo ya usa atajos a diario (j/k, ⌘+Enter). Permite saltar a cualquier sección o buscar una factura por comprobante/proveedor sin salir del teclado.

### Fuera de alcance (documentado a propósito — NO lo resuelvas, son decisiones de producto)

- Login: no se agregó "olvidé mi contraseña".
- Facturas: qué hacer con una factura en estado "Error" de extracción (reprocesar/descartar/escalar) no está definido.
- Órdenes de pago: el único camino frente a un error de BAS es "Reintentar" (que va a seguir fallando mientras el bloqueo del ERP siga); no hay forma de cancelar o marcar para seguimiento manual.
- Factura confirmada: hoy se puede reabrir aunque ya tenga una orden de pago exitosa en BAS; si eso debería bloquearse es una decisión de producto.
- Categorías BAS / Métodos de pago: solo se puede desactivar una fila, no eliminarla.

Si en el camino se te ocurre resolver alguno de estos, no lo hagas — están fuera de alcance a propósito.

---

## 3. Mapeo exacto: archivo de diseño → archivo real

| Archivo `.dc.html` | Pantalla real |
|---|---|
| `login.dc.html` | `app/login/page.tsx` |
| `cola-de-revision.dc.html` | `app/(dashboard)/queue/page.tsx` |
| `listado-facturas.dc.html` | `app/(dashboard)/invoices/page.tsx` |
| `factura-revision.dc.html` | `app/(dashboard)/invoices/[id]/page.tsx` (rama `needs_review`) + `InvoiceFileViewer.tsx` + `InvoiceReviewForm.tsx` |
| `factura-confirmada.dc.html` | `app/(dashboard)/invoices/[id]/page.tsx` (rama `confirmed`) + `PaymentOrderPanel.tsx` + `ReopenButton.tsx` |
| `mapa-categorias-bas.dc.html` | `app/(dashboard)/category-map/page.tsx` + `components/CategoryMapEditor.tsx` |
| `metodos-de-pago.dc.html` | `app/(dashboard)/payment-methods/page.tsx` + `components/PaymentMethodsEditor.tsx` |
| `ordenes-de-pago.dc.html` | `app/(dashboard)/payment-orders/page.tsx` |
| `subir-factura.dc.html` | `app/subir-factura/page.tsx` |
| `StatusBadge.dc.html` | `components/StatusBadge.tsx` (rehacer sobre `components/ui/badge.tsx` de shadcn, con los 5 buckets) |
| `Icon.dc.html` | No es un componente nuevo — documenta qué íconos de `lucide-react` usa cada estado/acción. Usalo como referencia de qué ícono elegir en cada lugar, no crees un componente `Icon` propio. |
| `notas-diseno.dc.html` | No se implementa como pantalla — es la spec de Fase 0 (sección 2) + referencia visual del nuevo shell de navegación (ver siguiente punto). |

El **shell de navegación (sidebar nuevo)** no tiene un archivo propio: se ve renderizado igual en las 9 pantallas reales y en `notas-diseno.dc.html`. Extraelo comparando 2-3 de los `.dc.html` (el chrome debería ser idéntico entre ellos) para construir el nuevo `app/(dashboard)/layout.tsx`.

---

## 4. Orden de trabajo

**Fase 0 primero, antes de tocar una sola pantalla:**

1. Migrar `app/globals.css` a los tokens semánticos + fixear `--font-sans` + fijar el acento `oklch(0.52 0.17 258)` + confirmar el lock de radios (6/8/12px).
2. Construir el nuevo `app/(dashboard)/layout.tsx` (sidebar agrupado, tokens `--sidebar-*`, menú de usuario).
3. Reconstruir `components/StatusBadge.tsx` sobre el `Badge` de shadcn con los 5 buckets de `STATUS_META`.
4. Instalar los componentes shadcn que falten (`card`, `dialog`, `dropdown-menu`, `sidebar`, `command`, `checkbox`, etc. — lo que pida el diseño) con `npx shadcn@latest add ...` en vez de recrearlos a mano.
5. Armar la paleta de comandos (⌘K) global si el diseño la implementó de forma reusable en el shell.

**Después, pantalla por pantalla** (orden sugerido: Login → Cola de revisión → Revisión editable → Confirmada+pago → Listado de facturas → Órdenes de pago → Categorías BAS → Métodos de pago → Subir factura):

Por cada una: abrí el `.dc.html` correspondiente en el navegador, reimplementá su presentación en el archivo real (regla de oro abajo), y verificala en el navegador real de la app antes de pasar a la siguiente.

---

## 5. Regla de oro al integrar cada pantalla

Cada pantalla real mantiene **exactamente** su lógica actual — el diseño nuevo aporta la presentación, nada más:

- **Preservar intacto**: llamadas a PocketBase (`getPocketBase()`, `pb.collection(...)`), el fetching server-side de cada `page.tsx`, validaciones, handlers (`handleSubmit`, `handleConfirm`, `handleReopen`, `handleSave`...), rutas y params, nombres de colecciones/campos de PocketBase, los atajos de teclado de `InvoiceReviewForm.tsx` (`j`/`k`/flechas/⌘+Enter) y su auto-recálculo de `precio_total`, y toda la lógica de `proxy.ts`/`pocketbase-server.ts`.
- **Reemplazar por lo que trae el diseño**: clases de Tailwind, estructura de layout, componentes hand-rolled (`<button>`, `<input>`, `<table>` crudos), estados visuales (loading/empty/error/success), animaciones/microinteracciones, copy.
- Si el mock (`data-store.js`) modela un estado que el componente real no tiene todavía (ej. un bucket de estado, un ícono por acción), adaptalo al dato real — no inventes campos nuevos en PocketBase.
- Si el diseño usa un estado/prop que el componente real sí necesita y el mockup no lo mostró (ej. `disabled` durante `status === "saving"`), conservalo — no lo pierdas al portar el JSX.

---

## 6. Verificación obligatoria por pantalla (no alcanza con que compile)

- **Login**: entrar con el usuario de prueba vigente, ver el error real con credenciales incorrectas.
- **Cola de revisión**: lista real desde PocketBase, entrar a una factura.
- **Revisión editable**: `j`/`k` siguen moviendo entre facturas, ⌘/Ctrl+Enter sigue confirmando y avanzando, el auto-recálculo de `precio_total` sigue andando, el badge de advertencia aparece si los ítems no cuadran.
- **Confirmada + orden de pago**: `ReopenButton` sigue abriendo el `AlertDialog`; `PaymentOrderPanel` sigue pudiendo intentar crear una orden (va a fallar contra BAS real — es el comportamiento esperado, no un bug de la integración).
- **Listado de facturas, categorías, métodos de pago, órdenes de pago**: datos reales de PocketBase, feedback inline por fila donde corresponda (no más `window.alert()`).
- **Subir factura**: sigue subiendo un archivo real al endpoint público.
- **⌘K**: si se implementó, confirmá que navega a las 9 rutas reales y no a nada del prototipo.

Si algo se rompe al portar el JSX (una prop que faltaba, un handler perdido), es un bug de la integración — arreglalo antes de seguir a la próxima pantalla.

---

## 7. Qué NO tocar

- Rutas, nombres de colecciones/campos de PocketBase, el modelo de autenticación (`users` vs `service_accounts`), la lógica de `proxy.ts`, los atajos de teclado y su comportamiento, el idioma (español), la lógica de negocio de BAS (el bloqueo actual del lado del ERP es contexto real, no algo para "arreglar" con copy optimista).
- Los 5 puntos de "Fuera de alcance" de la sección 2 — quedan documentados, no implementados.
- Dark mode — pospuesto a propósito, no lo agregues por iniciativa propia.
