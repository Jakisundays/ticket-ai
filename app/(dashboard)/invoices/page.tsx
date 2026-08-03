import { AlertCircle } from "lucide-react";
import { createServerClient, ClientResponseError } from "@/lib/pocketbase-server";
import {
  Collections,
  type InvoiceListItemExpand,
} from "@/lib/pocketbase-types";
import { formatCurrency, formatDate, driveFileUrl } from "@/lib/format";
import PageHeader from "@/components/PageHeader";
import EmptyState from "@/components/EmptyState";
import InvoicesTable, { type InvoiceRow } from "./InvoicesTable";
import RetryButton from "./RetryButton";

// Esta pagina lee cookies (via createServerClient) y siempre debe reflejar
// el estado mas reciente de PocketBase, asi que no tiene sentido cachearla
// ni pre-renderizarla de forma estatica.
export const dynamic = "force-dynamic";

export default async function InvoicesPage() {
  const pb = await createServerClient();

  // `items === null` distingue "fallo la consulta" de "la consulta funciono
  // pero no hay facturas" -- son dos estados visuales distintos (error vs
  // vacio) aunque ambos partan del mismo `getList`.
  // "Facturas" es el historial de lo ya REVISADO y aprobado -- no de todo lo
  // que entró al sistema. Antes no filtraba nada, así que una factura
  // recién subida (pending/processing/completed-sin-confirmar) aparecía acá
  // Y en la cola al mismo tiempo, lo cual confundía el propósito de cada
  // pantalla. Ahora: review_status="confirmed" es el único gate de entrada
  // -- coincide exactamente con el único lugar que lo setea
  // (InvoiceReviewForm.tsx, al confirmar). Todo lo anterior a eso
  // (pending/processing/error, o completed sin confirmar) vive solo en
  // /queue.
  //
  // Además, una vez que se crea una Orden de Pago (real, dry_run=False) para
  // la factura, esta deja de listarse acá y pasa a vivir solo en /payment-orders
  // -- cada factura tiene una sola pantalla "hogar" según su etapa, en vez de
  // aparecer en Facturas Y en Órdenes de pago al mismo tiempo. El filtro de
  // back-relation `payment_orders_via_invoice.id = ""` tiene una
  // particularidad real de PocketBase: cuando NO hay ninguna fila
  // relacionada, se evalúa como verdadero igual (JOIN con NULL) -- por eso
  // no alcanza con `payment_orders_via_invoice.deleted_at != ""` solo (eso
  // excluiría también a las facturas sin ninguna orden). La combinación con
  // `||` cubre los dos casos que SÍ queremos mostrar: "no tiene ninguna
  // orden" O "la que tiene está soft-eliminada" -- verificado con datos
  // reales antes de escribir esto (ver docs/plan-validaciones-pre-bas.md
  // para el patrón de verificar filtros de PocketBase empíricamente en vez
  // de asumir la sintaxis).
  let items: InvoiceListItemExpand[] | null = null;
  try {
    const result = await pb
      .collection<InvoiceListItemExpand>(Collections.Invoices)
      .getList(1, 50, {
        filter:
          'review_status = "confirmed" && deleted_at = "" && (payment_orders_via_invoice.deleted_at != "" || payment_orders_via_invoice.id = "")',
        sort: "-created",
        expand: "bas_processing_status_via_invoice",
      });
    items = result.items;
  } catch (error) {
    if (!(error instanceof ClientResponseError)) throw error;
    items = null;
  }

  const rows: InvoiceRow[] =
    items?.map((invoice) => {
      // Relation `unique` (1:1) -- PocketBase expande esto como un objeto
      // unico, no un array (a diferencia de invoice_items).
      const basStatus = invoice.expand?.bas_processing_status_via_invoice;

      return {
        id: invoice.id,
        processId: invoice.process_id,
        numero: invoice.numero_comprobante || invoice.process_id,
        emisorNombre: invoice.emisor_nombre,
        fecha: formatDate(invoice.fecha_emision),
        monto: formatCurrency(invoice.total, invoice.moneda),
        status: invoice.status,
        reviewStatus: invoice.review_status || "needs_review",
        sheetsSaved: invoice.sheets_saved,
        driveUrl: driveFileUrl(invoice.drive_file_id),
        basStatus: basStatus ? basStatus.orden_pago_status : null,
        // Antes: false para status="error" ("decision de producto
        // pendiente"). Ya no aplica -- invoices/[id]/page.tsx ahora tiene
        // una vista dedicada para status="error" (motivo + reintentar) y
        // para status="processing" (progreso en vivo), así que toda fila
        // tiene a dónde ir.
        clickable: true,
      };
    }) ?? [];

  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-[16px] font-semibold text-foreground">
          Facturas
        </h1>
        <span className="ml-auto hidden truncate text-[13px] text-muted-foreground md:block">
          Últimos 50 comprobantes confirmados
        </span>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="animate-fade-up mx-auto flex max-w-[1180px] flex-col gap-4">
          {items === null ? (
            <ErrorState />
          ) : (
            <InvoicesTable rows={rows} />
          )}
        </div>
      </div>
    </div>
  );
}

function ErrorState() {
  return (
    <div className="rounded-xl bg-card shadow-(--shadow-1)">
      <EmptyState
        icon={AlertCircle}
        iconTone="destructive"
        title="No pudimos cargar las facturas"
        description="Revisá tu conexión e intentá de nuevo."
        action={<RetryButton />}
      />
    </div>
  );
}
