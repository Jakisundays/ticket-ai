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
  let items: InvoiceListItemExpand[] | null = null;
  try {
    const result = await pb
      .collection<InvoiceListItemExpand>(Collections.Invoices)
      .getList(1, 50, {
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
        numero: invoice.numero_comprobante || invoice.process_id,
        emisorNombre: invoice.emisor_nombre,
        fecha: formatDate(invoice.fecha_emision),
        monto: formatCurrency(invoice.total, invoice.moneda),
        status: invoice.status,
        reviewStatus: invoice.review_status || "needs_review",
        sheetsSaved: invoice.sheets_saved,
        driveUrl: driveFileUrl(invoice.drive_file_id),
        basStatus: basStatus ? basStatus.orden_pago_status : null,
        // REGLA DE ORO: el listado no tiene ninguna accion disponible para
        // facturas en estado "error" de extraccion -- fuera de alcance a
        // proposito, decision de producto pendiente.
        clickable: invoice.status !== "error",
      };
    }) ?? [];

  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-[16px] font-semibold text-foreground">
          Facturas
        </h1>
        <span className="ml-auto hidden truncate text-[13px] text-muted-foreground md:block">
          Últimos 50 comprobantes recibidos
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
