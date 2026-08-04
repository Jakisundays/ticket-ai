/**
 * Tipos escritos a mano para el contrato de schema acordado con el backend
 * (invoice-api-bas). La instancia real de PocketBase todavia no esta
 * corriendo al momento de escribir esto, asi que estos tipos NO fueron
 * generados contra un servidor en vivo.
 *
 * Cuando la instancia real este disponible, se pueden regenerar con:
 *
 *   npx pocketbase-typegen --url <URL> --email <admin-email> --password <admin-password> --out lib/pocketbase-types.ts
 *
 * Revisa el diff contra este archivo antes de sobreescribirlo: los nombres
 * de coleccion (`Collections`) y los helpers de expand se usan en toda la
 * app.
 */

export type IsoDateString = string;
export type RecordIdString = string;

/** Campos que PocketBase agrega a todo record de cualquier coleccion. */
export interface BaseSystemFields {
  id: RecordIdString;
  created: IsoDateString;
  updated: IsoDateString;
  collectionId: string;
  collectionName: string;
}

export type InvoiceStatus = "pending" | "processing" | "completed" | "error";
export type OrdenPagoStatus = "pending" | "success" | "failed";
export type ProcessingJobStatus = "queued" | "processing" | "done" | "error";
/** "" en filas legacy anteriores a este campo -- tratar como needs_review en todos lados. */
export type ReviewStatus = "" | "needs_review" | "confirmed";
export type MetodoPago = "efectivo" | "cheque" | "transferencia" | "tarjeta";
export type PaymentOrderStatus = "processing" | "success" | "failed";
export type ImportBatchStatus = "running" | "completed" | "completed_with_errors" | "failed";
export type ImportBatchItemStatus =
  | "pending"
  | "uploading"
  | "processing"
  | "completed"
  | "error"
  | "skipped_duplicate";

export interface InvoicesRecord extends BaseSystemFields {
  process_id: string;
  numero_comprobante: string;
  fecha_emision: string;
  tipo_comprobante: string;
  subtipo_comprobante: string;
  moneda: string;
  emisor_nombre: string;
  emisor_cuit: string;
  receptor_nombre: string;
  receptor_cuit: string;
  subtotal: number;
  total: number;
  cae: string;
  cae_vencimiento: string;
  forma_pago: string;
  /** Alícuota real de IVA (ej. 10.5, 21, 0), extraída por Gemini -- puede no
   * venir. Ver utils/bas_config.py:resolver_item_bas (Invoicy). */
  iva_alicuota: number | null;
  drive_file_id: string;
  sheets_saved: boolean;
  status: InvoiceStatus;
  error_message: string;
  /** Solo mientras status="processing" -- número de intento actual de la
   * extracción con IA (hasta 6, ver Invoicy tool_handler). Indicador
   * agregado aproximado: hay 3 llamadas en paralelo, cada una con su
   * propio contador de reintentos -- no es progreso exacto por campo. */
  extraction_attempt: number;
  review_status: ReviewStatus;
  /** relation -> users; "" si nunca se confirmó */
  confirmed_by: string;
  confirmed_at: IsoDateString | "";
  /** "" si nunca se soft-deleteó -- ver components/DeleteRowMenu.tsx. Nunca
   * un borrado físico (BAS no se entera si se pierde el registro). Filtrar
   * siempre `deleted_at = ""` en cualquier listado nuevo. */
  deleted_at: IsoDateString | "";
  /** relation -> users */
  deleted_by: string;
  delete_reason: string;
  /** sha256 del archivo original -- "" en filas anteriores a la importación
   * masiva (ver components/DeleteRowMenu.tsx para el criterio análogo de
   * deleted_at). Motor del dedup: scripts/batch_import.py y
   * routes/batch_import.py en Invoicy. */
  content_hash: string;
}

export interface InvoiceItemsRecord extends BaseSystemFields {
  /** relation -> invoices (cascade delete) */
  invoice: RecordIdString;
  process_id: string;
  linea: number;
  descripcion: string;
  cantidad: number;
  precio_unitario: number;
  precio_total: number;
  categoria: string;
  bas_codigo_item: string;
}

export interface BasProvidersRecord extends BaseSystemFields {
  /** solo digitos normalizados */
  cuit: string;
  bas_codigo: string;
  razon_social: string;
  nuevo: boolean;
  last_verified_at: IsoDateString;
}

export interface BasProcessingStatusRecord extends BaseSystemFields {
  /** relation 1:1 -> invoices (required, unique) */
  invoice: RecordIdString;
  process_id: string;
  proveedor_resuelto: boolean;
  proveedor_codigo: string;
  comprobante_prefijo: string;
  comprobante_numero: number;
  comprobante_registrado: boolean;
  orden_pago_status: OrdenPagoStatus;
  orden_pago_error: string;
  retry_count: number;
  last_attempt_at: IsoDateString;
}

export interface BasCategoryMapRecord extends BaseSystemFields {
  /** ya no unique sola -- unique compuesto con alicuota, ver migración
   * 1783483945_add_alicuota_to_bas_category_map.js. Debe cubrir "Bebidas y
   * Bar", "Insumos", "Limpieza", "Gastos Generales" */
  categoria: string;
  /** alícuota de IVA del CodigoItem (21, 10.5, 5, 0, ...) */
  alicuota: number;
  codigo_item: string;
  confirmado: boolean;
}

export interface ProcessingJobsRecord extends BaseSystemFields {
  process_id: string;
  status: ProcessingJobStatus;
  from_email: string;
  subject: string;
  file_name: string;
  error_message: string;
}

export interface BasPaymentMethodsRecord extends BaseSystemFields {
  /** select cerrado -- cada valor esta acoplado a METODO_PAGO_ARRAY_BAS en el backend */
  metodo_pago: MetodoPago;
  bas_medio_pago_codigo: string;
  /** solo aplica a "transferencia" -- PagosPorBanco exige CuentaBancaria por item */
  bas_cuenta_bancaria: string;
  /** solo aplica a "tarjeta" -- Tarjetas exige Plan/CodigoTarjeta ademas del
   * numero de tarjeta (que se pide por-pago, no aca, ver PaymentOrderPanel). */
  bas_plan_tarjeta: string;
  bas_codigo_tarjeta: string;
  confirmado: boolean;
}

export interface PaymentOrdersRecord extends BaseSystemFields {
  /** relation 1:1 -> invoices (required, unique) */
  invoice: RecordIdString;
  process_id: string;
  metodo_pago: MetodoPago;
  monto: number;
  status: PaymentOrderStatus;
  bas_op_prefijo: string;
  bas_op_numero: number;
  /** error real de BAS, verbatim -- no un mensaje genérico */
  bas_error: string;
  retry_count: number;
  /** relation -> users */
  requested_by: string;
  requested_at: IsoDateString;
  last_attempt_at: IsoDateString;
  /** "" si nunca se soft-deleteó -- mismo criterio que InvoicesRecord.deleted_at. */
  deleted_at: IsoDateString | "";
  /** relation -> users */
  deleted_by: string;
  delete_reason: string;
}

/** Importación masiva de facturas -- ver Invoicy/docs/plan-importacion-masiva-facturas.md. */
export interface ImportBatchesRecord extends BaseSystemFields {
  label: string;
  status: ImportBatchStatus;
  total_files: number;
  total_unique: number;
  total_duplicates: number;
  started_at: IsoDateString;
  finished_at: IsoDateString | "";
  /** relation -> users; "" si lo disparó el script local sin created_by explícito */
  created_by: string;
  /** null/0 = comportamiento normal. Si tiene valor, TODOS los items de
   * este batch se procesaron con este monto en vez del real extraído (BAS,
   * Sheets e invoices.total) -- para importar facturas reales sin impacto
   * contable real. Ver Invoicy/docs/plan-importacion-masiva-facturas.md. */
  monto_override: number | null;
}

export interface ImportBatchItemsRecord extends BaseSystemFields {
  /** relation -> import_batches */
  batch: RecordIdString;
  /** relation -> invoices; "" hasta que status="completed" */
  invoice: RecordIdString | "";
  /** ej. "facturas.zip/facturas/MercadoPago_4.pdf" -- conserva el linaje completo */
  original_path: string;
  file_name: string;
  /** "" si el archivo no vino de un zip */
  zip_source: string;
  content_hash: string;
  file_size: number;
  status: ImportBatchItemStatus;
  error_message: string;
  attempt_count: number;
  /** relation -> import_batch_items (self); item "ganador" cuando el
   * duplicado es del MISMO batch */
  duplicate_of: RecordIdString | "";
  /** relation -> invoices; item "ganador" cuando el duplicado ya existía
   * como factura de una corrida anterior o de una subida suelta previa */
  duplicate_of_invoice: RecordIdString | "";
  process_id: string;
}

export interface ImportBatchItemWithExpand extends ImportBatchItemsRecord {
  expand?: {
    invoice?: InvoicesRecord;
    duplicate_of_invoice?: InvoicesRecord;
  };
}

/** Coleccion de auth "users" — humanos, login del dashboard Next.js. */
export interface UsersRecord extends BaseSystemFields {
  email: string;
  name?: string;
  avatar?: string;
  verified: boolean;
}

/**
 * Coleccion de auth "service_accounts" — usada SOLO por el backend Python
 * (invoice-api-bas) via POCKETBASE_SERVICE_EMAIL/POCKETBASE_SERVICE_PASSWORD.
 * Este dashboard Next.js nunca debe autenticarse contra esta coleccion.
 */
export interface ServiceAccountsRecord extends BaseSystemFields {
  email: string;
}

// ---------------------------------------------------------------------------
// Formas con `expand` que usa el dashboard (back-relations `via`).
// ---------------------------------------------------------------------------

export interface InvoiceWithItemsExpand extends InvoicesRecord {
  expand?: {
    invoice_items_via_invoice?: InvoiceItemsRecord[];
    // bas_processing_status.invoice y payment_orders.invoice son relations
    // `unique` (1:1) -- a diferencia de invoice_items_via_invoice, PocketBase
    // expande esto como un objeto único, NO un array (verificado contra la
    // respuesta real de la API). Un `[0]` sobre estos campos siempre da
    // undefined.
    bas_processing_status_via_invoice?: BasProcessingStatusRecord;
    payment_orders_via_invoice?: PaymentOrdersRecord;
    confirmed_by?: UsersRecord;
  };
}

export interface InvoiceListItemExpand extends InvoicesRecord {
  expand?: {
    bas_processing_status_via_invoice?: BasProcessingStatusRecord;
  };
}

export interface BasProcessingStatusWithInvoiceExpand
  extends BasProcessingStatusRecord {
  expand?: {
    invoice?: InvoicesRecord;
  };
}

export interface PaymentOrdersWithExpand extends PaymentOrdersRecord {
  expand?: {
    invoice?: InvoicesRecord;
    requested_by?: UsersRecord;
  };
}

/** Nombres de coleccion exactos del contrato de schema. No improvisar. */
export const Collections = {
  Invoices: "invoices",
  InvoiceItems: "invoice_items",
  BasProviders: "bas_providers",
  BasProcessingStatus: "bas_processing_status",
  BasCategoryMap: "bas_category_map",
  BasPaymentMethods: "bas_payment_methods",
  PaymentOrders: "payment_orders",
  ProcessingJobs: "processing_jobs",
  ImportBatches: "import_batches",
  ImportBatchItems: "import_batch_items",
  Users: "users",
  ServiceAccounts: "service_accounts",
} as const;
