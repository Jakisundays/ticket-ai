# Plan de remediación de UI — Ticket AI Dashboard

> Auditoría realizada el 2026-07-15 contra **producción real** (`http://137.184.219.162:3000`)
> en 4 viewports: móvil 375×812, tablet 768×1024, laptop 1280×720/800, desktop nativo.
> Complementada con lectura del código fuente (los hallazgos citan archivo y línea).
>
> Limitación de la auditoría: la base de producción todavía no tiene facturas, así que
> los estados "con data" (tablas pobladas, split-view de revisión, panel de orden de
> pago) se auditaron contra el código + lo verificado en dev local con data real.
> Antes de dar por cerrada la Fase 2, repetir la pasada visual con data real cargada.

---

## Resumen ejecutivo

El dashboard se diseñó e implementó **desktop-first y solo se verificó en desktop**.
En 1280px+ la UI está sólida (tokens, tipografía y spacing consistentes — eso ya se
unificó en el rediseño de julio). Los problemas reales están en **móvil y tablet**:
el shell de navegación no colapsa nunca, y la pantalla más importante del producto
(revisión de facturas) es directamente inusable en pantallas chicas.

Las páginas públicas (`/login`, `/subir-factura`) ya son responsive y no necesitan
trabajo.

---

## Hallazgos, priorizados

### P0 — Bloqueantes (la app es inusable en móvil por esto)

**UI-1 · El sidebar nunca colapsa**
- Evidencia: en 375px el sidebar fijo ocupa ~62% del ancho; el contenido queda en una
  columna de ~140px con el texto cortado palabra por palabra (verificado en prod).
- Causa: `app/(dashboard)/layout.tsx` monta `<Sidebar collapsible="none" className="w-[236px]">`.
  El componente shadcn ya trae el modo mobile (Sheet + `useIsMobile`) pero está
  explícitamente desactivado, y ninguna página renderiza un `SidebarTrigger`.
- Fix: pasar a `collapsible="offcanvas"`, y agregar el trigger (hamburguesa) al header
  de página en <md. Como los headers son por-página, conviene extraer un componente
  compartido `PageHeader` (título + slot de acciones + trigger condicional) y usarlo
  en las 7 páginas — de paso elimina 7 copias del mismo markup de header.

**UI-2 · Split-view de revisión de facturas roto en <lg**
- Causa: `app/(dashboard)/invoices/[id]/page.tsx` rama needs_review usa
  `w-2/5 min-w-[280px]` (visor) + `flex-1` (formulario), lado a lado siempre.
  En 375px: visor 280px + formulario ~95px. En tablet 768: visor 307px + form 460px,
  apretado pero al límite.
- Fix: en <lg apilar verticalmente con el visor colapsable (details/accordion o tabs
  "Archivo | Datos"). El formulario es la herramienta principal; el visor es consulta.
- Nota: los atajos j/k/⌘+Enter no deben romperse — son solo de teclado físico, no
  requieren adaptación mobile, pero el pill de hints de atajos debería ocultarse en
  táctil (`hidden md:flex`).

### P1 — Alto impacto

**UI-3 · Tablas anchas en móvil (4 pantallas)**
- `InvoicesTable` (9 columnas), `PaymentOrdersTable` (8), `CategoryMapEditor`,
  `PaymentMethodsEditor`. El wrapper de shadcn da `overflow-x-auto`, así que
  técnicamente scrollean, pero: (a) scroll horizontal en una tabla editable con
  inputs (categorías/métodos) es pésima UX táctil; (b) la fila expandible de órdenes
  de pago y el row-click de facturas compiten con el gesto de scroll.
- Fix por pantalla:
  - Facturas y Órdenes de pago (solo lectura): en <md, renderizar como lista de cards
    (comprobante, emisor, total, badges) — el mismo patrón que ya usa la Cola de revisión,
    que sí funciona en móvil.
  - Categorías BAS y Métodos de pago (editables): en <md, card por fila con los campos
    apilados y el feedback inline debajo.

**UI-4 · El shell no llena la altura del viewport en tablet**
- Evidencia: en 768×1024 el layout termina a ~920px y abajo queda una franja de fondo
  vacía (verificado en prod).
- Causa probable: `h-svh` en `SidebarInset` + `min-h-0` en el provider vs `min-h-full`
  del body — la cadena de alturas no se propaga cuando el viewport es más alto que
  el contenido. Diagnosticar con devtools y fijar una sola estrategia de altura
  (`h-dvh` en el wrapper del provider es el candidato).

### P2 — Medio

**UI-5 · Inputs de búsqueda truncados en viewports intermedios**
- El placeholder "Buscar por proveedor o comprobante" se corta en tablet y el input
  tiene ancho fijo (`w-[280px]`/`w-80`) que desborda la toolbar en <md.
- Fix: `w-full max-w-[320px]` + placeholder corto en móvil, y permitir que la toolbar
  (búsqueda + filtro + contador) haga wrap con `flex-wrap gap-2`.

**UI-6 · Toolbar de filtros sin wrap en Facturas/Órdenes**
- Mismo grupo que UI-5: en ~640-900px el Select de estados y el contador se pisan.
  Se resuelve junto con UI-5.

**UI-7 · Verificación pendiente de la rama "confirmada" del detalle**
- El layout de dos columnas usa `flex-wrap` con `basis`, que en teoría apila bien en
  móvil, pero nunca se verificó visualmente (ni en dev ni en prod — no existía ninguna
  factura confirmada). Verificar cuando haya data, junto con el `PaymentOrderPanel`.

### P3 — Pulido

**UI-8 · Targets táctiles**: los botones `size="sm"`/íconos de 28px del toggle
  confirmado y fila expandible están debajo de los 44px recomendados para táctil.
  Subir el área de hit con padding/pseudo-elemento en <md.
**UI-9 · Command palette (⌘K) invisible en móvil**: no hay forma de abrirla sin
  teclado. O se agrega un botón de búsqueda en el header mobile, o se acepta que es
  una feature desktop-only (decisión de producto, default: desktop-only, documentarlo).
**UI-10 · `?vista=loading/error/empty`**: los mockups soportaban forzar estados para
  QA; el código real solo tiene `loading.tsx` en Facturas. Agregar `loading.tsx` a
  Cola de revisión, Órdenes de pago, Categorías y Métodos para paridad de skeletons.

### Lo que NO está roto (no tocar)

- Desktop 1280px+: verificado pantalla por pantalla en el rediseño de julio; tokens,
  radios, tipografía y estados ya son consistentes. No re-abrir.
- `/login` y `/subir-factura` en móvil: verificados hoy en prod, correctos.
- Branding: el acento único `oklch(0.52 0.17 258)`, Geist, y el lock de radios 6/8/12
  ya están aplicados de punta a punta — la remediación debe **reusar esos tokens**,
  cero colores/valores nuevos.

---

## Plan de ejecución

### Fase 1 — Shell responsive (P0) · ~1 sesión
1. Extraer `components/PageHeader.tsx` (título, slot de acciones, `SidebarTrigger`
   visible solo en <md) y adoptarlo en las 7 páginas.
2. Cambiar el sidebar a `collapsible="offcanvas"` y verificar el Sheet mobile
   (navegación, estado activo, user menu, cerrar al navegar).
3. Rehacer el layout del detalle de factura (rama needs_review) para <lg:
   tabs o visor colapsable + formulario a ancho completo; ocultar hints de atajos.
4. Arreglar la cadena de alturas del shell (UI-4).
5. **Gate de verificación**: pasada completa en 375/768/1280 de las 7 pantallas,
   con screenshots, sin scroll horizontal de página en ninguna.

### Fase 2 — Contenido responsive (P1-P2) · ~1 sesión
6. Card-lists mobile para Facturas y Órdenes de pago.
7. Cards editables mobile para Categorías BAS y Métodos de pago (mismo feedback
   inline por fila que ya existe).
8. Toolbars con wrap + inputs fluidos (UI-5/6).
9. Cargar data de prueba en staging y verificar la rama confirmada + PaymentOrderPanel
   (UI-7) en los 4 viewports.
10. **Gate**: repetir la pasada visual CON data real en todos los breakpoints.

### Fase 3 — Pulido (P3) · ~media sesión
11. Targets táctiles ≥44px en controles interactivos de filas.
12. `loading.tsx` en las 4 rutas que faltan.
13. Decisión documentada sobre ⌘K en móvil.
14. Pasada final de QA: Lighthouse (accesibilidad + best practices), test con
    throttling de red, y prueba en al menos un teléfono físico real del equipo.

### Criterios de aceptación globales
- Ninguna página produce scroll horizontal del body en 320–1920px.
- Toda acción disponible en desktop tiene un camino equivalente en móvil
  (excepto atajos de teclado, por naturaleza).
- Cero valores de color/espaciado nuevos: todo sale de los tokens existentes.
- Los flujos críticos (login → cola → revisar → confirmar → orden de pago) se
  completan en un teléfono real sin zoom manual.

### Orden de despliegue
Cada fase se buildea y verifica primero en local (dev :3001), después rebuild del
contenedor `dashboard` en el Droplet (~2 min, sin downtime del resto del stack) y
re-verificación contra prod. Las fases son independientes y desplegables por separado.
