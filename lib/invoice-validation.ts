/**
 * Mismas reglas que Invoicy/utils/validaciones_pre_bas.py (Etapa 0 del plan
 * de validaciones pre-BAS) y que ticket-ai-infra/pocketbase/pb_hooks/invoices.pb.js
 * (la barrera real -- este módulo es solo para feedback en vivo en el
 * formulario de revisión, ver docs/plan-validacion-antes-de-confirmar.md en
 * el repo Invoicy). Duplicado a mano en TypeScript porque corre en el
 * navegador; SI SE CAMBIA UNA REGLA ACÁ, revisar también esos otros dos
 * archivos.
 *
 * `total` (agregado 2026-08-19, junto con el cambio de arquitectura que
 * ancla Total/TotalGravado/TotalIva de BAS a invoice.total en vez de a la
 * suma de items -- ver utils/bas_payload.py en Invoicy) espeja
 * validar_total del lado Python, y la misma regla en el hook de PocketBase
 * (ticket-ai-infra/pocketbase/pb_hooks/invoices.pb.js): obligatorio y > 0
 * en las 3 capas.
 *
 * El hook de PocketBase es la única barrera que no se puede saltear -- esto
 * es puramente UX (feedback inmediato mientras se edita, sin esperar a que
 * el hook rechace el request).
 */

const MONEDAS_VALIDAS = new Set(["ARS", "$", "PESOS", "PESO ARGENTINO"]);

export interface InvoiceDraftForValidation {
  emisor_cuit: string;
  moneda: string;
  fecha_emision: string;
  numero_comprobante: string;
  cae: string;
  cae_vencimiento: string;
  total: number;
}

export interface ItemDraftForValidation {
  id: string;
  precio_total: number;
}

export type InvoiceFieldWithError =
  | "emisor_cuit"
  | "moneda"
  | "fecha_emision"
  | "numero_comprobante"
  | "cae"
  | "cae_vencimiento"
  | "total";

export interface InvoiceValidationResult {
  fieldErrors: Partial<Record<InvoiceFieldWithError, string>>;
  /** itemId -> mensaje, para resaltar la fila puntual en la tabla de ítems. */
  itemErrors: Record<string, string>;
  /** "sin ítems" o "no cierran contra subtotal/total" -- no es por ítem individual. */
  itemsSummaryError: string | null;
  /** Lista plana para el banner resumen ("Faltan N datos obligatorios..."). */
  messages: string[];
  isValid: boolean;
}

// Mismo parseo manual que utils/validaciones_pre_bas.py e invoices.pb.js --
// new Date(string) interpreta "01/08/2026" como MM/DD/YYYY (US), no como
// DD/MM/YYYY (argentino), dando fechas silenciosamente equivocadas.
export function parsearFecha(valor: string | null | undefined): Date | null {
  const texto = (valor || "").trim();
  if (!texto) return null;

  let m = texto.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (m) {
    const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
    return Number.isNaN(d.getTime()) ? null : d;
  }

  m = texto.match(/^(\d{2})\/(\d{2})\/(\d{4})$/) || texto.match(/^(\d{2})-(\d{2})-(\d{4})$/);
  if (m) {
    const d = new Date(Date.UTC(Number(m[3]), Number(m[2]) - 1, Number(m[1])));
    return Number.isNaN(d.getTime()) ? null : d;
  }

  return null;
}

/**
 * Convierte cualquiera de los 3 formatos aceptados (o ya ISO) al formato
 * YYYY-MM-DD que exige <input type="date">. Facturas viejas con
 * fecha_emision/cae_vencimiento en DD/MM/YYYY o DD-MM-YYYY (Gemini no
 * estaba forzado a un formato único antes de esto) mostrarían el picker
 * nativo en blanco si se le pasa el string crudo tal cual -- este helper
 * evita esa pérdida de visibilidad. Si el valor no es parseable, devuelve
 * "" (el picker queda vacío, honesto -- ya se marca aparte con
 * fieldErrors, no se inventa una fecha).
 */
export function fechaComoInputDate(valor: string | null | undefined): string {
  const fecha = parsearFecha(valor);
  return fecha ? fecha.toISOString().slice(0, 10) : "";
}

export function validarFacturaParaConfirmar(
  draft: InvoiceDraftForValidation,
  // Ya no se usa (ver comentario más abajo, donde antes vivía la validación
  // de ítems) -- se mantiene en la firma por estabilidad de interfaz:
  // itemErrors/itemsSummaryError del resultado siguen siendo consumidos por
  // InvoiceReviewForm.tsx, aunque ahora queden siempre vacíos.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  items: ItemDraftForValidation[]
): InvoiceValidationResult {
  const fieldErrors: InvoiceValidationResult["fieldErrors"] = {};
  const itemErrors: Record<string, string> = {};
  const itemsSummaryError: string | null = null;
  const messages: string[] = [];

  const cuit = (draft.emisor_cuit || "").replace(/\D/g, "");
  if (cuit.length !== 11) {
    fieldErrors.emisor_cuit = "El CUIT no parece válido (debe tener 11 dígitos).";
    messages.push("CUIT del emisor inválido");
  }

  const moneda = (draft.moneda || "").trim();
  if (moneda && !MONEDAS_VALIDAS.has(moneda.toUpperCase())) {
    fieldErrors.moneda = "Esta moneda no es pesos -- no se puede confirmar automáticamente.";
    messages.push("Moneda distinta a pesos");
  }

  // Desde 2026-08-19, invoice.total es la ÚNICA fuente de Total/TotalGravado/
  // TotalIva que se registra en BAS (ver utils/bas_payload.py en Invoicy) --
  // ya no hay ningún ítem de respaldo del cual reconstruirlo si falta o es
  // inválido, así que se corta acá con el mismo criterio que validar_total.
  if (!(draft.total > 0)) {
    fieldErrors.total = "El total de la factura tiene que ser mayor a cero.";
    messages.push("Total de la factura inválido");
  }

  const fechaEmision = parsearFecha(draft.fecha_emision);
  if (!fechaEmision) {
    fieldErrors.fecha_emision = "La fecha no es válida o no pudo leerse.";
    messages.push("Fecha de emisión inválida");
  } else if (fechaEmision.getTime() > Date.now()) {
    fieldErrors.fecha_emision = "La fecha de emisión es futura.";
    messages.push("Fecha de emisión futura");
  }

  const numeroCompleto = (draft.numero_comprobante || "").replace(/\s/g, "");
  const partes = numeroCompleto.split("-");
  if (partes.length !== 2 || !partes[0] || !/^\d+$/.test(partes[1])) {
    fieldErrors.numero_comprobante = "Formato esperado: 00001-00000123.";
    messages.push("Número de comprobante con formato inválido");
  }

  // EmitidoPor="2" (factura electrónica) está fijo para TODA factura de este
  // pipeline -- el CAE es obligatorio siempre, no solo "si es electrónica".
  const cae = (draft.cae || "").trim();
  if (!/^\d{14}$/.test(cae)) {
    fieldErrors.cae = "Obligatorio -- 14 dígitos.";
    messages.push("Falta el CAE");
  }
  if (!parsearFecha(draft.cae_vencimiento)) {
    fieldErrors.cae_vencimiento = "Obligatorio y debe ser una fecha válida.";
    messages.push("Falta el vencimiento del CAE");
  }

  // Desde 2026-08-19, invoice.total (validado arriba) es la ÚNICA fuente de
  // Total/TotalGravado/TotalIva -- los ítems solo eligen el CodigoItem de la
  // línea BAS (utils/bas_payload.py en Invoicy). Por eso ACÁ no se valida
  // nada de los ítems: ni que existan, ni que precio_total sea positivo, ni
  // que sumen contra subtotal/total -- un descuento, bonificación o ajuste
  // negativo es un ítem perfectamente válido y no debe bloquear la
  // confirmación. (Esta sección históricamente sí bloqueaba por eso -- ver
  // git blame -- quitado a pedido explícito del usuario tras encontrar que
  // seguía marcando ítems negativos reales como "inconsistentes" pese a que
  // el Total ya no dependía de ellos.) La única validación real de ítems que
  // importa (¿resuelve un CodigoItem?) corre en Invoicy, que es el único
  // lugar con acceso al catálogo real de bas_items -- acá no hay forma de
  // replicarla ni tendría sentido intentarlo.

  return {
    fieldErrors,
    itemErrors,
    itemsSummaryError,
    messages,
    isValid: messages.length === 0,
  };
}
