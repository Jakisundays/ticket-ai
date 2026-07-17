# Plan de implementación — Rediseño «Legado» (Reconstrucción Senior Home)

**Fecha:** 17-jul-2026 · **Fuente de diseño:** `Reconstrucción Senior Home.zip` (10 mockups DesignSync `.dc.html` + design system «Legado» completo con tokens light/dark) · **Destino:** `ticket-ai-dashboard` (Next.js 16, React 19, Tailwind v4, shadcn/ui, PocketBase, next-themes, sonner, lucide).

---

## 0. Resumen del análisis

### 0.1 Diseño fuente

El zip contiene una reconstrucción pantalla-por-pantalla del dashboard actual, re-diseñada con el design system **Legado** (The Senior Home). Mapea 1:1 con las rutas existentes:

| Mockup | Ruta actual |
|---|---|
| `Login.dc.html` | `app/login/page.tsx` |
| `Dashboard.dc.html` (nuevo: "Inicio") | **no existe** — `app/page.tsx` hoy redirige; hay que crear `app/(dashboard)/page.tsx` |
| `Cola de Revision.dc.html` | `app/(dashboard)/queue/` |
| `Facturas.dc.html` | `app/(dashboard)/invoices/` |
| `Factura Detalle.dc.html` | `app/(dashboard)/invoices/[id]/` |
| `Ordenes de Pago.dc.html` | `app/(dashboard)/payment-orders/` |
| `Metodos de Pago.dc.html` | `app/(dashboard)/payment-methods/` |
| `Categorias BAS.dc.html` | `app/(dashboard)/category-map/` |
| `Subir Factura.dc.html` | `app/subir-factura/page.tsx` |
| `Sidebar.dc.html` | `components/SidebarNav.tsx` + layout |

**Lenguaje visual del diseño (extraído de los tokens y mockups):**

- **Paleta:** navy institucional de 4 niveles (`--azul-insignia #003863` marca/CTA, `--azul-profundo #0B2E4F` banda del sidebar, `--azul-interactivo #1A5E99` links/focus, celestes bruma/niebla decorativos) sobre canvas **marfil `#FAF7F0`** (nunca blanco puro de página); blanco `Lino` solo como elevación (cards). Oro `#A9812F` ceremonial (badges premium), nunca CTA. Semánticos con par color/bruma: éxito `#1E7A46/#E8F4EC`, alerta `#8F6A1F/#FBF1DC`, error `#B3362B/#FAEAE8`.
- **Dark mode «Noche en el Home»:** no invierte — navy cálido `#0C1B2B` como canvas, superficies `#13273B/#1B3450`, el botón primario se invierte a crema/navy (inversión ceremonial), bordes celestes translúcidos.
- **Tipografía:** Montserrat 600/700 (display/headings/labels/overlines), Open Sans 400/600 (cuerpo), **Lora itálica 500** solo para el wordmark "Ticket AI" (momento ceremonial). Overlines: `.68–.72rem`, tracking `.09em`, uppercase. Numéricos siempre `tabular-nums`.
- **Espaciado y forma:** escala 4px (`--esp-1..10`), ritmo universal 24px (padding de cards). Radios: 8 chips, 12 inputs, 16 cards, 24 modales/login-card, **pill 999 exclusivo de botones y badges**, círculo para avatares/iconos-feature. Bordes 1px, **1.5px en controles de formulario**.
- **Elevación:** 4 niveles de sombra con tinte navy (`--sombra-1..4`), nunca negro puro; hairline `#EDE8DC` para divisores internos. Cards **sin borde**, solo sombra.
- **Motion:** tokens 120/180/260/400ms, `ease-out` medido (`cubic-bezier(.25,.46,.45,.94)`) y `--ease-entrada-suave (.16,1,.3,1)`; press `scale(0.98)`; card hover `sombra 1→2 + translateY(-2px)`; keyframes `lg-fade-up`, `lg-scale-in`, `lg-shimmer` (skeletons), `lg-spin`; `prefers-reduced-motion` obligatorio.
- **Layout de app:** sidebar navy `--banda` de **246px** sticky (grupos "Panorama / Trabajo diario / Configuración", ítem activo con `inset 3px 0 0 var(--crema)` + fondo celeste 14%, badge crema con contador en "Cola de revisión", footer con avatar de iniciales crema + logout, toggle de tema dentro del sidebar). Header sticky de **64px** con `backdrop-filter: blur(12px)` sobre canvas al 92%, hairline inferior, título + metadato contextual a la derecha. Main `max-width: 1240px` (960px en Cola), padding `32px 36px 48px`.
- **Patrones repetidos:** metric cards (overline + icono en círculo bruma + cifra 1.9rem + subtexto semántico, clickeables con hover-lift); tablas con thead fondo canvas + overline, filas hover celeste-niebla 55%, hairlines; badges pill de estado (4 buckets: confirmada/éxito, revisión/alerta, procesando/celeste, error); cards-lista con avatar de iniciales (Cola); inputs 44–46px borde 1.5 `--borde-fuerte` con focus `--anillo-foco`; empty states centrados con icono en círculo bruma + copy con salida; skeletons shimmer; estados idle/uploading/success/error en Subir Factura; detalle de factura en grid `1fr 340px` con panel lateral sticky.
- **Responsive declarado en mockups:** `≤1100px` columnas → 1; `≤940px` sidebar se oculta y aparece `data-mobilebar` (banda superior navy — hay que diseñar el drawer móvil, el mockup no lo resuelve); `≤760px` métricas 2 col; `≤480px` 1 col; tablas con `overflow-x:auto; min-width:920px`.
- **Voz:** voseo argentino, sentence case, verbos concretos, errores sin culpa con salida ("Escribí a sistemas@dinardi.com"), sin emojis.

### 0.2 Proyecto destino — estado actual

- **Stack ideal para esto:** Tailwind v4 CSS-first (`@theme inline` en `globals.css`), shadcn/ui completo (sidebar, dialog, sheet, command, table, skeleton, sonner…), `next-themes` ya instalado, lucide, `tw-animate-css`.
- **Tema actual:** neutro/gris con primario azul genérico oklch; ya existe un **vocabulario de estado de 5 buckets** (`--status-*` + `StatusBadge`) — encaja directo con los badges del diseño.
- **Arquitectura sana:** route group `(dashboard)`, server components + client islands (`InvoicesTable`, `QueueList`, editores), `loading.tsx` por ruta, `PageHeader`, `NavLink`, `CommandPalette`, hooks (`use-mobile`), `lib/format`.
- **Brechas vs. diseño:** no hay página Inicio/Dashboard; sidebar es blanca shadcn estándar (el diseño pide banda navy custom con contador y theme-toggle integrado); tipografía Geist (el diseño pide Montserrat/Open Sans/Lora); dark mode usa clase `.dark` de shadcn (compatible con next-themes, hay que mapear tokens); radios/bordes/sombras difieren; no hay motion system.

### 0.3 Decisiones de adaptación (diseño → sistema existente)

1. **No se importan los tokens Legado en paralelo**: se **remapean** a las variables semánticas shadcn existentes (`--background←marfil/canvas`, `--card←lino`, `--primary←azul-insignia`, `--ring←azul-interactivo`, `--sidebar←banda`, `--muted←crema/wash`, `--border←borde`, `--status-*←pares bruma/color`). Los componentes shadcn heredan el tema sin tocar su código. Solo se agregan tokens nuevos donde shadcn no tiene concepto: `--hairline`, `--oro`, `--surface-wash`, la familia de motion y sombras.
2. **Dark mode**: se mantiene la clase `.dark` + `next-themes` (attribute="class") en lugar del `data-theme` del mockup — mismo resultado, cero fricción con `@custom-variant dark` ya definido.
3. **Iconos**: lucide-react ya presente; se estandariza `strokeWidth={1.75}` (decisión de legibilidad del DS) vía wrapper o prop consistente.
4. **Pill only en botones/badges**: se ajustan `button.tsx` y `badge.tsx` a `rounded-full`; cards a 16px; dialogs a 24px — actualizando el lock de radios de Fase 0 previa documentado en `globals.css`.
5. **El drawer móvil** (no resuelto en mockups) se implementa con el `Sheet`/`Sidebar collapsible="offcanvas"` ya existente, tematizado como la banda navy.

---

## Fase 1 — Fundaciones del Design System (tokens, tipografía, motion)

- **Objetivo:** que todo el vocabulario visual Legado exista como tokens antes de tocar una sola pantalla; un commit de tema que ya "re-pinta" la app entera de forma coherente aunque los layouts sigan iguales.
- **Archivos a modificar:** `app/globals.css` (bloque `:root`, `.dark`, `@theme inline`), `app/layout.tsx` (fonts con `next/font/google`: Montserrat 600/700, Open Sans 400/600, Lora 500 italic → variables `--font-display/--font-body/--font-serif`; `ThemeProvider` de next-themes con `attribute="class" defaultTheme="system" enableSystem`).
- **Nuevos artefactos:** ninguno de componente; sí secciones nuevas en `globals.css`: sombras `--shadow-1..4` (light navy / dark celeste), `--hairline`, motion tokens (`--dur-*`, `--ease-*`), keyframes `lg-fade-up/lg-scale-in/lg-shimmer/lg-spin`, utilities `.overline`, `.tabular`, bloque `@media (prefers-reduced-motion: reduce)`.
- **Tareas:**
  1. Mapear paleta light: background `#FAF7F0`, card/popover `#FFFFFF`, primary `#003863`/fg blanco, ring `#1A5E99`, muted `#FBF4E1`, muted-fg `#6E6B61`, border `#DDD6C8`, input `#A39D8E` (borde fuerte de controles), destructive `#B3362B`, sidebar `#0B2E4F` + sub-tokens del sidebar (fg crema translúcido, accent celeste 14%, primary crema).
  2. Mapear `.dark` («Noche en el Home»): background `#0C1B2B`, card `#13273B`, popover `#1B3450`, **primary crema `#FBF4E1` con fg navy** (inversión ceremonial), borders celestes translúcidos, sidebar `#081724`.
  3. Remapear los 5 buckets `--status-*` a los pares bruma/color Legado en ambos temas (procesando→celeste-niebla/azul, revisión→alerta, confirmada→éxito, error→error, neutral→piedra).
  4. Fonts: reemplazar Geist; `--font-sans←Open Sans`, `--font-heading←Montserrat`, agregar `--font-serif←Lora`.
  5. Radios: `--radius-lg: 12px` (inputs/controles), `--radius-xl: 16px` (cards/tablas), `--radius-2xl: 24px` (dialogs), badges/botones → pill (se aplica en Fase 2).
- **Riesgos:** contraste — validar AA de `--alerta #8F6A1F` sobre bruma y de textos suaves sobre marfil; el cambio de `--input` a borde más oscuro afecta todos los formularios (revisar visualmente); fonts de Google agregan peso (usar `display: swap`, subset latin).
- **Dependencias:** ninguna nueva de npm (todo presente).
- **Criterios de finalización:** la app entera renderiza con paleta/tipografía Legado en light y dark sin regresiones de layout; toggle de next-themes persiste y respeta sistema; `npm run build` limpio; captura de cada ruta en ambos temas.

## Fase 2 — Primitivos UI (shadcn re-tematizado)

- **Objetivo:** que `components/ui/*` expresen el DS: pill buttons con press-scale, inputs 1.5px, cards sin borde con sombra, badges pill.
- **Componentes afectados / archivos:** `components/ui/button.tsx` (rounded-full, alturas 44/48, `active:scale-[0.98]`, hover `bg → azul-interactivo`, transiciones con tokens, variante `secondary` = borde fuerte + hover celeste-niebla), `input.tsx` y `textarea.tsx` y `select.tsx` (h-11, `border-[1.5px] border-input`, focus ring 4px `--anillo-foco`, hover border), `card.tsx` (border-0, `shadow-(--shadow-1)`, rounded-2xl→16px), `badge.tsx` (pill, font-display 600, variantes de estado vía `StatusBadge` existente), `table.tsx` (thead bg-background overline, hairlines, hover de fila celeste-niebla/55), `dialog.tsx`+`alert-dialog.tsx`+`sheet.tsx` (radius 24, overlay navy translúcido + blur 4px, animación `lg-scale-in`), `skeleton.tsx` (shimmer `lg-shimmer` sobre `--muted`), `checkbox.tsx`, `label.tsx`, `separator.tsx` (hairline), `dropdown-menu.tsx`, `command.tsx`, `tooltip.tsx`, `sonner.tsx` (estilos success/error con brumas).
- **Nuevos componentes:** `components/ui/metric-card.tsx` (overline + icono en círculo bruma tintable + valor tabular 1.9rem + subtexto semántico; variantes `href` clickeable con hover-lift), `components/EmptyState.tsx` (icono círculo bruma + título display + copy con acción), `components/Overline.tsx` (o utility class), `components/InitialsAvatar.tsx` (crema/navy y celeste/navy).
- **Riesgos:** cambiar radios/alturas de primitivos toca todas las pantallas — hacerlo en un solo PR y revisar cada ruta; `active:scale` en botones dentro de tablas puede sentirse raro (limitar a botones reales).
- **Dependencias:** Fase 1.
- **Criterios de finalización:** página de prueba temporal (o revisión ruta por ruta) mostrando todos los primitivos en ambos temas; focus visible correcto por teclado en todos; sin bordes en cards; press-scale funcionando; `prefers-reduced-motion` los desactiva.

## Fase 3 — Shell de aplicación (sidebar navy + header blur)

- **Objetivo:** replicar el chrome del diseño: sidebar banda navy 246px con wordmark Lora, grupos con overlines, ítem activo con inset crema, contador de cola, theme-toggle y usuario integrados; header sticky 64px con blur y metadato contextual.
- **Archivos a modificar:** `app/(dashboard)/layout.tsx` (ancho 246px, header con slot de metadato, sección "Subir factura" al fondo), `components/SidebarNav.tsx` (nuevo grupo "Panorama" con Inicio `/`; iconografía del mockup: LayoutDashboard, Inbox, FileText, CreditCard, Tags, Wallet, UploadCloud; badge contador en Cola), `components/NavLink.tsx` (estados: texto crema 72%, hover blanco/7%, activo celeste-14% + `shadow-[inset_3px_0_0]` crema, transiciones `--dur-instante`), `components/UserMenu.tsx` (avatar iniciales crema + nombre + "Cerrar sesión", borde superior celeste-14%), `components/PageHeader.tsx` (h 64px, blur 12px sobre canvas 92%, hairline, título Montserrat 1.06rem, slot derecho para metadato/acciones), `components/ui/sidebar.tsx` (solo variables de color vía tokens — evitar fork).
- **Nuevos componentes:** `components/ThemeToggle.tsx` (ítem de sidebar Sol/Luna con label "Modo claro/oscuro", next-themes, sin flash — `suppressHydrationWarning`), `components/QueueCountBadge.tsx` (server: cuenta de PocketBase; o pasar count desde layout).
- **Riesgos:** el contador de cola en un layout server-cacheado puede quedar stale (usar revalidación corta o fetch client con SWR ligero); el `Sheet` móvil de shadcn-sidebar debe heredar la banda navy (verificar tokens `--sidebar-*` en el sheet); z-index del header vs. dialogs.
- **Dependencias:** Fases 1–2.
- **Criterios de finalización:** navegación completa con estados activos correctos en las 7 rutas; toggle de tema desde el sidebar sin flash; drawer móvil navy funcional ≤940px (breakpoint de `use-mobile` alineado); teclado: orden de foco lógico, `aria-current` en activo.

## Fase 4 — Página Inicio (nueva) 

- **Objetivo:** construir `app/(dashboard)/page.tsx` según `Dashboard.dc.html`: saludo con overline "Resumen operativo", 4 metric cards, tabla "Actividad reciente", panel "Cola de revisión" (3 ítems + botón secundario) y card wash "Canales de ingreso" con barras.
- **Archivos:** nuevos `app/(dashboard)/page.tsx`, `app/(dashboard)/loading.tsx` (skeleton shimmer replicando el layout — el mockup lo especifica), componentes locales `DashboardMetrics.tsx`, `RecentActivityTable.tsx`, `QueuePreviewCard.tsx`, `ChannelsCard.tsx`; modificar `app/page.tsx` (redirigir `/` a esta página o moverla), `lib/pocketbase-server.ts` si faltan queries agregadas (conteos, suma mensual, canales).
- **Datos reales:** pendientes de revisión + antigüedad de la más vieja, procesadas/mes con delta vs. mes anterior, volumen ARS mensual, órdenes fallidas de la semana, últimos 6 procesos, top-3 de la cola, conteo por canal (WhatsApp/email/formulario). Reusar `lib/format.ts` (moneda ARS, fechas relativas "Hoy, 10:42").
- **Riesgos:** costo de 5–6 queries a PocketBase en un server component (paralelizar con `Promise.all`; considerar `unstable_cache` corto); los deltas mensuales requieren filtros por fecha correctos en zona AR.
- **Dependencias:** Fases 1–3 (usa MetricCard, tabla, badges).
- **Criterios de finalización:** Inicio con datos reales en ambos temas; cards clickeables navegan (Cola, Órdenes); grid responsive 4→2→1; skeleton visible en navegación fría; saludo con nombre del usuario logueado y fecha del día en es-AR.

## Fase 5 — Pantallas de flujo (Cola, Facturas, Detalle)

- **Objetivo:** fidelidad pixel-nivel de las tres pantallas núcleo.
- **Cola** (`queue/page.tsx`, `QueueList.tsx`, `queue/loading.tsx`): header con badge "N pendientes" + metadato "Ordenadas por antigüedad"; buscador 44px con icono; cards-lista con `InitialsAvatar` celeste, número+tipo tabular, monto, espera (color alerta/error si >2 días), chevron, hover-lift + `active:scale-[0.99]`; empty state "Todo al día" (éxito) y "Sin resultados" diferenciados; `max-width: 960px`.
- **Facturas** (`invoices/page.tsx`, `InvoicesTable.tsx`, `RetryButton.tsx`): toolbar búsqueda + select de estado nativo estilizado (o `Select` shadcn) + contador de resultados; tabla 8 columnas con 3 columnas de badges (Estado/Revisión/Estado BAS) sobre `StatusBadge`; filas clickeables solo cuando hay detalle; `overflow-x` con `min-width` en tablet.
- **Detalle** (`invoices/[id]/*`): grid `minmax(0,1fr) 340px`, panel lateral **sticky** (Orden de pago + Estado BAS); secciones card "Datos de la factura" (grid `auto-fit minmax(200px,1fr)` de pares label-overline/valor), "Ítems" (tabla), visor de archivo; `InvoiceReviewForm` con inputs 1.5px y acciones pill; `ReopenButton`/`PaymentOrderPanel` tematizados; breadcrumb/back al listado.
- **Riesgos:** el detalle es la pantalla más densa (18 campos) — mantener el formulario controlado existente sin romper la lógica de confirmación a BAS (**regla: toda prueba real contra BAS con Total=1 peso**); sticky lateral vs. altura de viewport corta (usar `top` + `max-height` scroll interno).
- **Dependencias:** Fases 1–3.
- **Criterios de finalización:** flujo completo subir→cola→revisar→confirmar→orden visto en navegador en light/dark/móvil; estados vacíos y de error reproducidos; sin regresión funcional (revisión, reintento, reapertura).

## Fase 6 — Pantallas restantes (Órdenes, Métodos, Categorías, Login, Subir)

- **Objetivo:** completar el resto con los mismos patrones.
- **Órdenes de pago** (`payment-orders/*`): tabla con badges de estado BAS, toolbar, empty state.
- **Métodos de pago** (`payment-methods/*`, `PaymentMethodsEditor.tsx`) y **Categorías BAS** (`category-map/*`, `CategoryMapEditor.tsx`): editores con cards 16px, inputs 1.5px, botones pill, confirmaciones `alert-dialog` 24px, toasts sonner con brumas.
- **Login** (`app/login/page.tsx`): centrado, wordmark Lora 2rem + overline "Panel interno · Dinardi", card 24px sombra-2 con `lg-fade-up`, inputs 46px, CTA pill 48px con estado "Ingresando…" (spinner `lg-spin`), error en caja bruma con copy del mockup, footer mailto sistemas@dinardi.com.
- **Subir factura** (`app/subir-factura/page.tsx`): máquina de 4 estados idle/uploading/success/error del mockup; dropzone dashed 2px con hover celeste + drag-over, ficha de archivo con quitar, success `lg-scale-in` con "Subir otra factura", error con "Reintentar"; footer con canales alternativos.
- **Riesgos:** drag&drop accesible (input file real + label); validación 15MB/formatos alineada con el backend actual.
- **Dependencias:** Fases 1–2 (Login y Subir no dependen del shell).
- **Criterios de finalización:** las 9 pantallas coinciden con los mockups lado a lado (revisión visual con preview browser en 1280/768/375); login y subida probados end-to-end contra PocketBase dev.

## Fase 7 — Motion y microinteracciones (pasada transversal)

- **Objetivo:** aplicar el sistema de motion de forma consistente, sin librerías nuevas (CSS + `tw-animate-css` alcanzan; **no** introducir framer-motion salvo necesidad puntual — evaluar `motion/react` solo si una transición de página lo justifica).
- **Tareas:** entrada de páginas `lg-fade-up` 260ms en el contenido de `main` (una sola vez, no en cada card); hover-lift solo en superficies clickeables; press-scale en botones/cards-link; dialogs/sheets con scale-in/slide 180–260ms; dropdown/command 120ms; skeletons shimmer 1.4s; toasts con entrada suave; transición de tema `transition-colors` acotada (no `* { transition }` global — causa repaints); focus-visible con anillo 4px inmediato (sin transición).
- **Archivos:** `globals.css` (utilities `.animate-fade-up`, etc.), toques en primitivos ya tematizados, layouts de página.
- **Riesgos:** sobre-animar; jank en tablas grandes (animar solo contenedores, `transform/opacity` únicamente); doble animación con `loading.tsx` + fade de entrada.
- **Criterios de finalización:** inventario de interacciones revisado pantalla por pantalla; con `prefers-reduced-motion` todo queda estático; sin layout shift medible al animar.

## Fase 8 — Responsive fino

- **Objetivo:** que cada viewport se sienta diseñado, no adaptado.
- **Tareas por breakpoint:** ≤1100px columnas de Inicio/Detalle → 1 (panel lateral pasa debajo, no sticky); ≤940px drawer navy + mobilebar con wordmark y trigger; ≤760px métricas 2-col, toolbars apiladas, paddings `32/36` → `20/16`; ≤480px métricas 1-col, tablas → considerar vista de cards apiladas para Facturas/Órdenes en móvil (mejor que scroll horizontal para uso real), targets mínimos 48px; ultrawide: `max-width` 1240 centrado ya lo resuelve — verificar que el header acompañe.
- **Archivos:** todos los `page.tsx` del dashboard, `PageHeader`, `use-mobile` (alinear a 940px), tablas.
- **Riesgos:** duplicar markup tabla/cards móvil (encapsular en el mismo componente con render condicional por container query o `md:`); el sticky header + blur en iOS Safari (probar `-webkit-backdrop-filter`).
- **Criterios de finalización:** QA con `resize_window` en 375/768/1280/1440/1920 en las 9 pantallas y ambos temas; sin scroll horizontal de página en ninguna.

## Fase 9 — Accesibilidad

- **Objetivo:** AA real, teclado completo.
- **Tareas:** auditoría de contraste (especial: alerta sobre bruma, texto suave sobre marfil, crema sobre navy, links en dark); `aria-current="page"` en nav; labels/`aria-label` en buscadores, selects e icon-buttons; filas de tabla clickeables → link real interno o `role="link"` + Enter; foco atrapado en dialogs (Radix ya lo da — verificar tras retematizar); `aria-live` en resultados de búsqueda y estados de subida; jerarquía h1/h2/h3 según mockups; targets 44–48px en móvil.
- **Criterios de finalización:** navegación completa solo con teclado en las 9 pantallas; axe/lighthouse a11y ≥ 95 por ruta; validador de contraste sobre los pares de tokens en ambos temas.

## Fase 10 — Performance y pulido final

- **Objetivo:** fluidez y cierre.
- **Tareas:** fonts con `next/font` (subset, swap, sin CLS); revisar `Promise.all` en server components; `loading.tsx` en todas las rutas (falta en `[id]` y raíz); memo solo donde haya re-render medido (editores); imágenes/visor de facturas con lazy; bundle check (`next build` — vigilar que no entró nada nuevo); limpiar código muerto del tema anterior (clases sueltas, colores hardcodeados, `PageHeader` viejo si se reemplazó); actualizar `docs/` y notas de Fase 0 de radios en `globals.css`; QA visual final lado a lado mockup↔app de las 9 pantallas.
- **Criterios de finalización:** Lighthouse perf ≥ 90 en rutas clave; sin flash de tema al cargar; `npm run lint` y `build` limpios; captura final de cada pantalla en light/dark adjunta al PR.

---

## Orden recomendado y esfuerzo

| # | Fase | Esfuerzo | Depende de |
|---|---|---|---|
| 1 | Fundaciones (tokens/fonts/motion tokens) | M | — |
| 2 | Primitivos UI | M–L | 1 |
| 3 | Shell (sidebar + header) | M | 1–2 |
| 4 | Inicio (nueva) | M | 1–3 |
| 5 | Cola / Facturas / Detalle | L | 1–3 |
| 6 | Órdenes / Métodos / Categorías / Login / Subir | M | 1–2(–3) |
| 7 | Motion transversal | S–M | 2–6 |
| 8 | Responsive fino | M | 3–6 |
| 9 | Accesibilidad | S–M | todo |
| 10 | Performance + pulido | S | todo |

Fases 1+2 conviene hacerlas juntas en una rama (`redesign/legado-foundations`) porque el punto intermedio es visualmente inconsistente. De 4 a 6 se pueden paralelizar por pantalla. Cada fase termina con verificación en el preview browser (light/dark/móvil) antes de avanzar.

## Riesgos globales

1. **Regresión funcional** en formularios de revisión/confirmación BAS — el rediseño no debe tocar lógica de datos; pruebas reales contra BAS siempre con Total = 1 peso.
2. **Big-bang de primitivos** (Fase 2) repinta todo — mitigar revisando las 9 rutas en ese mismo PR.
3. **Deploy**: el proyecto ya corre en el Droplet real (backend.ticketia.devstage.com.ar) — coordinar el deploy del rediseño completo, no por fases a producción.
4. **Fuentes**: 3 familias nuevas; medir impacto y considerar recortar Lora a un solo peso itálico (solo el wordmark).
