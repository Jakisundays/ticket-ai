// "review_status != confirmed" (no "= needs_review") a propósito -- filas
// legacy con review_status "" también cuentan como pendientes.
const EXTRAIDA_SIN_CONFIRMAR = 'status = "completed" && review_status != "confirmed"';

// deleted_at = "" excluye lo soft-deleted (ver components/DeleteRowMenu.tsx)
// -- SIEMPRE tiene que ir en cualquier filtro de este archivo. Bug real
// 2026-08-18: el badge del sidebar (app/(dashboard)/layout.tsx), el card de
// Inicio (app/(dashboard)/page.tsx) y la navegación prev/next de una factura
// (app/(dashboard)/invoices/[id]/page.tsx) tenían cada uno su propia copia
// pegada del filtro de abajo; cuando se agregó este deleted_at = "" solo se
// actualizó app/(dashboard)/queue/page.tsx, las otras 3 copias quedaron
// desactualizadas y contaban facturas ya borradas -- 13 en el badge contra 4
// realmente visibles en /queue. Única fuente de verdad para que esto no
// vuelva a pasar: quien necesite este filtro importa una constante de acá,
// nunca escribe el string de nuevo.
const NO_BORRADA = 'deleted_at = ""';

/** "Facturas que requieren acción humana ahora": extracción terminada y
 * todavía sin confirmar, sin contar las borradas. Usado por el badge del
 * sidebar, el card "Pendientes de revisión" de Inicio, y la navegación
 * prev/next dentro de una factura -- ver comentario de NO_BORRADA arriba. */
export const FILTRO_FACTURAS_PENDIENTES = `${EXTRAIDA_SIN_CONFIRMAR} && ${NO_BORRADA}`;

/** Igual que FILTRO_FACTURAS_PENDIENTES pero además incluye status
 * pending/processing/error -- toda la actividad en curso, no solo lo que
 * ya requiere revisión humana. Usado por app/(dashboard)/queue/page.tsx
 * para dar visibilidad temprana desde que se crea el placeholder de una
 * factura, no recién cuando termina de procesarse. */
export const FILTRO_COLA_COMPLETA = `((${EXTRAIDA_SIN_CONFIRMAR}) || status = "pending" || status = "processing" || status = "error") && ${NO_BORRADA}`;
