import type { ReactNode } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronRight, ChevronLeft, AlertTriangle } from "lucide-react";
import { createServerClient, ClientResponseError } from "@/lib/pocketbase-server";
import { cn } from "@/lib/utils";
import StatusBadge from "@/components/StatusBadge";
import ReopenButton from "./ReopenButton";
import FriendlyErrorDetail from "@/components/FriendlyErrorDetail";
import { getFriendlyBasError } from "@/lib/bas-error-messages";
import { getFriendlyExtractionError } from "@/lib/extraction-error-messages";
import InvoiceFileViewer from "./InvoiceFileViewer";
import InvoiceReviewForm from "./InvoiceReviewForm";
import PaymentOrderPanel from "./PaymentOrderPanel";
import ExtractionProgress from "./ExtractionProgress";
import RetryExtractionButton from "./RetryExtractionButton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Collections,
  type BasCategoryMapRecord,
  type BasPaymentMethodsRecord,
  type InvoiceItemsRecord,
  type InvoiceWithItemsExpand,
} from "@/lib/pocketbase-types";
import { formatCurrency, formatDate, formatRelativeDateTime, driveFileUrl } from "@/lib/format";
import PageHeader from "@/components/PageHeader";

export const dynamic = "force-dynamic";

export default async function InvoiceDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const pb = await createServerClient();

  let invoice: InvoiceWithItemsExpand;
  try {
    invoice = await pb
      .collection<InvoiceWithItemsExpand>(Collections.Invoices)
      .getOne(id, {
        expand:
          "invoice_items_via_invoice,bas_processing_status_via_invoice,confirmed_by,payment_orders_via_invoice",
      });
  } catch (error) {
    if (error instanceof ClientResponseError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  const items = invoice.expand?.invoice_items_via_invoice ?? [];
  // Relation `unique` (1:1) -- PocketBase expande esto como un objeto único,
  // no un array (a diferencia de invoice_items_via_invoice).
  const basStatus = invoice.expand?.bas_processing_status_via_invoice;
  const isConfirmed = invoice.review_status === "confirmed";

  // Todavía no hay nada que revisar -- ni éxito ni fracaso -- mientras la
  // extracción con IA está corriendo (o reintentando) del lado de Invoicy.
  // Chequea status ANTES que isConfirmed a propósito: una factura
  // processing/error nunca puede estar confirmed (el formulario de
  // confirmación solo existe cuando status="completed").
  if (invoice.status === "processing") {
    return (
      <div className="flex h-full flex-col">
        <PageHeader>
          <Link
            href="/queue"
            className="text-sm font-medium text-muted-foreground hover:text-foreground"
          >
            Cola de revisión
          </Link>
          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground/50" />
          <span className="truncate font-mono text-sm font-semibold text-foreground">
            {invoice.numero_comprobante || invoice.process_id}
          </span>
          <StatusBadge status="processing" />
        </PageHeader>
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <div className="h-[42vh] shrink-0 border-b p-3 lg:h-auto lg:w-2/5 lg:min-w-[280px] lg:max-w-[560px] lg:border-r lg:border-b-0 lg:p-5">
            <div className="sticky top-16 flex h-full flex-col gap-3.5 rounded-xl bg-sidebar p-4 shadow-(--shadow-2)">
              <span className="overline px-0.5 text-[11px] text-sidebar-foreground">
                Comprobante original
              </span>
              <InvoiceFileViewer processId={invoice.process_id} />
            </div>
          </div>
          <ExtractionProgress
            invoiceId={invoice.id}
            initialAttempt={invoice.extraction_attempt || 1}
          />
        </div>
      </div>
    );
  }

  // Se agotaron los reintentos de extracción (ver Invoicy tool_handler,
  // max_retries=6) -- no hay datos reales para revisar/confirmar, así que
  // esta rama no reusa InvoiceReviewForm. El archivo original queda
  // disponible (se adjunta desde el arranque del procesamiento, no solo si
  // termina bien) para que el motivo del fallo se pueda revisar a ojo.
  if (invoice.status === "error") {
    return (
      <div className="flex h-full flex-col">
        <PageHeader>
          <Link
            href="/invoices"
            className="text-sm font-medium text-muted-foreground hover:text-foreground"
          >
            Facturas
          </Link>
          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground/50" />
          <span className="truncate font-mono text-sm font-semibold text-foreground">
            {invoice.numero_comprobante || invoice.process_id}
          </span>
          <StatusBadge status="error" />
        </PageHeader>
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <div className="h-[42vh] shrink-0 border-b p-3 lg:h-auto lg:w-2/5 lg:min-w-[280px] lg:max-w-[560px] lg:border-r lg:border-b-0 lg:p-5">
            <div className="sticky top-16 flex h-full flex-col gap-3.5 rounded-xl bg-sidebar p-4 shadow-(--shadow-2)">
              <span className="overline px-0.5 text-[11px] text-sidebar-foreground">
                Comprobante original
              </span>
              <InvoiceFileViewer processId={invoice.process_id} />
            </div>
          </div>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col items-center justify-center gap-4 overflow-y-auto px-7 py-5 text-center">
            <span className="flex size-11 items-center justify-center rounded-full bg-status-destructive-bg text-status-destructive-fg">
              <AlertTriangle className="size-5" />
            </span>
            <div className="flex flex-col gap-1.5">
              <p className="text-[15px] font-semibold text-foreground">
                No pudimos extraer los datos de esta factura
              </p>
              <p className="max-w-md text-[13.5px] text-muted-foreground">
                {invoice.extraction_attempt
                  ? `Se agotaron los ${invoice.extraction_attempt} intentos de extracción.`
                  : "La extracción falló antes de completar ningún intento."}
              </p>
            </div>
            {invoice.error_message && (
              <FriendlyErrorDetail
                error={getFriendlyExtractionError(invoice.error_message)}
                className="max-w-md rounded-md bg-status-destructive-bg px-3 py-2.5 text-left text-[11.5px] leading-relaxed text-status-destructive-fg"
              />
            )}
            <div className="mt-1 flex items-center gap-2.5">
              <RetryExtractionButton processId={invoice.process_id} />
              <Link
                href="/subir-factura"
                className="text-[13px] font-medium text-muted-foreground hover:text-foreground hover:underline"
              >
                Subir de nuevo
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!isConfirmed) {
    const [categoriesResult, queueResult] = await Promise.all([
      pb.collection<BasCategoryMapRecord>(Collections.BasCategoryMap).getFullList({ sort: "categoria" }),
      pb.collection<{ id: string }>(Collections.Invoices).getFullList({
        filter: 'status = "completed" && review_status != "confirmed"',
        sort: "+created",
        fields: "id",
      }),
    ]);
    const queueIds = queueResult.map((row) => row.id);
    const position = queueIds.indexOf(invoice.id);
    const prevInvoiceId = position > 0 ? queueIds[position - 1] : null;
    const nextInvoiceId = position >= 0 && position < queueIds.length - 1 ? queueIds[position + 1] : null;

    return (
      <div className="flex h-full flex-col">
        <PageHeader>
          <Link
            href="/queue"
            className="text-sm font-medium text-muted-foreground hover:text-foreground"
          >
            Cola de revisión
          </Link>
          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground/50" />
          <span className="truncate font-mono text-sm font-semibold text-foreground">
            {invoice.numero_comprobante || invoice.process_id}
          </span>
          <StatusBadge status="needs_review" />
          <div className="flex-1" />
          {position >= 0 && (
            <span className="hidden text-sm text-muted-foreground sm:inline">
              {position + 1} de {queueIds.length} en la cola
            </span>
          )}
          <div className="flex items-center gap-1.5">
            <NavButton href={prevInvoiceId ? `/invoices/${prevInvoiceId}` : null} label="Factura anterior">
              <ChevronLeft className="size-4" />
            </NavButton>
            <NavButton href={nextInvoiceId ? `/invoices/${nextInvoiceId}` : null} label="Factura siguiente">
              <ChevronRight className="size-4" />
            </NavButton>
          </div>
        </PageHeader>

        <div className="animate-fade-up flex min-h-0 flex-1 flex-col lg:flex-row">
          <div className="h-[42vh] shrink-0 border-b p-3 lg:h-auto lg:w-2/5 lg:min-w-[280px] lg:max-w-[560px] lg:border-r lg:border-b-0 lg:p-5">
            <div className="sticky top-16 flex h-full flex-col gap-3.5 rounded-xl bg-sidebar p-4 shadow-(--shadow-2)">
              <span className="overline px-0.5 text-[11px] text-sidebar-foreground">
                Comprobante original
              </span>
              <InvoiceFileViewer processId={invoice.process_id} />
            </div>
          </div>
          <div className="min-h-0 min-w-0 flex-1">
            <InvoiceReviewForm
              invoice={invoice}
              items={items}
              categories={categoriesResult}
              prevInvoiceId={prevInvoiceId}
              nextInvoiceId={nextInvoiceId}
            />
          </div>
        </div>
      </div>
    );
  }

  const driveUrl = driveFileUrl(invoice.drive_file_id);
  const confirmedByEmail = invoice.expand?.confirmed_by?.email;
  const paymentMethods = await pb
    .collection<BasPaymentMethodsRecord>(Collections.BasPaymentMethods)
    .getFullList({ sort: "metodo_pago" });

  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <Link
          href="/invoices"
          className="text-sm font-medium text-muted-foreground hover:text-foreground"
        >
          Facturas
        </Link>
        <ChevronRight className="size-3.5 shrink-0 text-muted-foreground/50" />
        <span className="truncate font-mono text-sm font-semibold text-foreground">
          {invoice.numero_comprobante || invoice.process_id}
        </span>
        <StatusBadge status="confirmed" />
      </PageHeader>

      <div className="animate-fade-up flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="h-[42vh] shrink-0 border-b p-3 lg:h-auto lg:w-2/5 lg:min-w-[280px] lg:max-w-[560px] lg:border-r lg:border-b-0 lg:p-5">
          <div className="sticky top-16 flex h-full flex-col gap-3.5 rounded-xl bg-sidebar p-4 shadow-(--shadow-2)">
            <span className="overline px-0.5 text-[11px] text-sidebar-foreground">
              Comprobante original
            </span>
            <InvoiceFileViewer processId={invoice.process_id} />
          </div>
        </div>
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-4 md:px-7 md:py-7">
        <div className="mx-auto flex max-w-[1040px] flex-col gap-6">
        <div className="flex flex-wrap items-start gap-6">
          {/* Columna izquierda: datos de solo lectura */}
          <div className="flex min-w-0 flex-1 basis-[420px] flex-col gap-5">
            <section className="rounded-xl bg-card p-6 shadow-(--shadow-1)">
              <h2 className="mb-3.5 text-[13px] font-semibold text-foreground">
                Datos de la factura
              </h2>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Estado">
                  <StatusBadge status={invoice.status} />
                </Field>
                <Field label="Tipo">
                  {invoice.tipo_comprobante} {invoice.subtipo_comprobante}
                </Field>
                <Field label="Emisión">{formatDate(invoice.fecha_emision)}</Field>
                <Field label="Emisor" tabular>
                  {invoice.emisor_nombre} ({invoice.emisor_cuit})
                </Field>
                <Field label="Receptor" tabular>
                  {invoice.receptor_nombre} ({invoice.receptor_cuit})
                </Field>
                <Field label="Forma de pago">{invoice.forma_pago || "—"}</Field>
                <Field label="Subtotal" tabular>
                  {formatCurrency(invoice.subtotal, invoice.moneda)}
                </Field>
                <Field label="Total" tabular>
                  {formatCurrency(invoice.total, invoice.moneda)}
                </Field>
                <Field label="Alícuota IVA" tabular>
                  {invoice.iva_alicuota !== null && invoice.iva_alicuota !== undefined
                    ? `${invoice.iva_alicuota}%`
                    : "—"}
                </Field>
                <Field label="CAE" tabular>
                  {invoice.cae || "—"}
                  {invoice.cae_vencimiento
                    ? ` (vence ${formatDate(invoice.cae_vencimiento)})`
                    : ""}
                </Field>
                <Field label="Guardado en Sheets">{invoice.sheets_saved ? "Sí" : "No"}</Field>
                <Field label="Drive">
                  {driveUrl ? (
                    <a
                      href={driveUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="text-primary hover:underline"
                    >
                      Ver archivo
                    </a>
                  ) : (
                    "—"
                  )}
                </Field>
                <Field label="process_id">
                  <code className="font-mono text-xs text-muted-foreground">
                    {invoice.process_id}
                  </code>
                </Field>
                <Field label="Subida el">{formatRelativeDateTime(invoice.created)}</Field>
                <Field label="Confirmada el">
                  {invoice.confirmed_at ? formatDate(invoice.confirmed_at) : "—"}
                  {confirmedByEmail ? ` · ${confirmedByEmail}` : ""}
                </Field>
              </div>
            </section>

            <section className="rounded-xl bg-card p-6 shadow-(--shadow-1)">
              <h2 className="mb-3.5 text-[13px] font-semibold text-foreground">Estado BAS</h2>
              {basStatus ? (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <Field label="Proveedor resuelto">
                    {basStatus.proveedor_resuelto ? "Sí" : "No"}
                    {basStatus.proveedor_codigo ? ` (${basStatus.proveedor_codigo})` : ""}
                  </Field>
                  <Field label="Comprobante registrado">
                    {basStatus.comprobante_registrado ? "Sí" : "No"}
                    {basStatus.comprobante_prefijo
                      ? ` (${basStatus.comprobante_prefijo}-${basStatus.comprobante_numero})`
                      : ""}
                  </Field>
                  <Field label="Orden de pago (intento automático)">
                    <StatusBadge status={basStatus.orden_pago_status} />
                  </Field>
                  {basStatus.orden_pago_error && (
                    <Field label="Error">
                      <FriendlyErrorDetail
                        error={getFriendlyBasError(basStatus.orden_pago_error)}
                        className="text-destructive"
                      />
                    </Field>
                  )}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Todavía no hay estado de procesamiento BAS para esta factura.
                </p>
              )}
            </section>
          </div>

          {/* Columna derecha: estado + orden de pago */}
          <div className="flex w-full min-w-[300px] max-w-[400px] flex-1 basis-[320px] flex-col gap-4">
            <section className="flex flex-col gap-3.5 rounded-xl bg-card p-6 shadow-(--shadow-1)">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Estado de la factura</span>
                <StatusBadge status="confirmed" />
              </div>
              <ReopenButton invoiceId={invoice.id} />
            </section>

            <PaymentOrderPanel
              processId={invoice.process_id}
              invoiceTotal={invoice.total}
              moneda={invoice.moneda}
              paymentMethods={paymentMethods}
              existingOrder={invoice.expand?.payment_orders_via_invoice ?? null}
            />
          </div>
        </div>

        {/* Ítems: ancho completo -- antes vivía apretada en la columna
            izquierda (compartiendo espacio con la columna derecha de 320-400px),
            lo que forzaba scroll horizontal en sus 7 columnas. */}
        <section className="rounded-xl bg-card p-6 shadow-(--shadow-1)">
          <h2 className="mb-3.5 text-[13px] font-semibold text-foreground">Ítems</h2>
          <ItemsTable items={items} moneda={invoice.moneda} />
        </section>
        </div>
        </div>
      </div>
    </div>
  );
}

function ItemsTable({ items, moneda }: { items: InvoiceItemsRecord[]; moneda: string }) {
  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead>Descripción</TableHead>
          <TableHead className="text-right">Cantidad</TableHead>
          <TableHead className="text-right">Precio unit.</TableHead>
          <TableHead className="text-right">Total</TableHead>
          <TableHead>Categoría</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items
          .slice()
          .sort((a, b) => (a.linea ?? 0) - (b.linea ?? 0))
          .map((item) => (
            <TableRow key={item.id} className="hover:bg-transparent">
              <TableCell className="whitespace-normal">{item.descripcion}</TableCell>
              <TableCell className="text-right">{item.cantidad}</TableCell>
              <TableCell className="text-right font-mono">
                {formatCurrency(item.precio_unitario, moneda)}
              </TableCell>
              <TableCell className="text-right font-mono font-medium text-foreground">
                {formatCurrency(item.precio_total, moneda)}
              </TableCell>
              <TableCell>{item.categoria || "—"}</TableCell>
            </TableRow>
          ))}
        {items.length === 0 && (
          <TableRow className="hover:bg-transparent">
            <TableCell colSpan={5} className="py-6 text-center text-muted-foreground">
              Sin items.
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}

function Field({
  label,
  children,
  tabular = false,
}: {
  label: string;
  children: ReactNode;
  tabular?: boolean;
}) {
  return (
    <div>
      <dt className="overline text-[11px] text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "mt-0.5 text-[13.5px] font-medium text-foreground",
          tabular && "tabular"
        )}
      >
        {children}
      </dd>
    </div>
  );
}

/** Botones circulares de navegación prev/next del header de revisión --
 * deshabilitados (sin Link, sin hover) cuando no hay factura hacia ese lado
 * en la cola. */
function NavButton({
  href,
  label,
  children,
}: {
  href: string | null;
  label: string;
  children: ReactNode;
}) {
  const base =
    "flex size-[34px] items-center justify-center rounded-lg border-[1.5px] border-input text-muted-foreground transition-colors duration-(--dur-fast) ease-(--ease-out)";

  if (!href) {
    return (
      <span className={cn(base, "pointer-events-none opacity-40")} aria-hidden="true">
        {children}
      </span>
    );
  }

  return (
    <Link href={href} aria-label={label} className={cn(base, "hover:bg-accent hover:text-ring")}>
      {children}
    </Link>
  );
}
