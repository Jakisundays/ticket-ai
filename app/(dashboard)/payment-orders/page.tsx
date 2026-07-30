import { createServerClient } from "@/lib/pocketbase-server";
import { Collections, type PaymentOrdersWithExpand } from "@/lib/pocketbase-types";
import { formatCurrency, formatDate } from "@/lib/format";
import PageHeader from "@/components/PageHeader";
import PaymentOrdersTable, { type PaymentOrderRow } from "./PaymentOrdersTable";

export const dynamic = "force-dynamic";

// Lee "payment_orders" (creado por una acción humana deliberada, dry_run=False
// contra BAS real) -- NO "bas_processing_status" (la sonda automática de la
// ingesta, siempre dry_run=True). Antes de esta página, /payment-orders leía
// la colección equivocada -- mostraba el intento automático informativo, no
// las órdenes de pago reales que alguien pidió crear.
export default async function PaymentOrdersPage() {
  const pb = await createServerClient();

  const orders = await pb
    .collection<PaymentOrdersWithExpand>(Collections.PaymentOrders)
    .getFullList({
      filter: 'deleted_at = ""',
      expand: "invoice,requested_by",
      sort: "-last_attempt_at",
    });

  // Aplanado a valores serializables antes de cruzar al Client Component --
  // ver la advertencia en PaymentOrdersTable.tsx sobre no pasarle nunca
  // referencias de íconos/funciones desde acá.
  const rows: PaymentOrderRow[] = orders.map((row) => ({
    id: row.id,
    processId: row.process_id,
    numero: row.expand?.invoice?.numero_comprobante || row.process_id,
    proveedor: row.expand?.invoice?.emisor_nombre || "—",
    metodo: row.metodo_pago,
    moneda: row.expand?.invoice?.moneda || "",
    monto: formatCurrency(row.monto, row.expand?.invoice?.moneda),
    fecha: formatDate(row.last_attempt_at),
    status: row.status,
    hasError: row.status === "failed" && !!row.bas_error,
    errorMensaje: row.bas_error || "",
    invoiceHref: row.expand?.invoice ? `/invoices/${row.expand.invoice.id}` : null,
    requestedByEmail: row.expand?.requested_by?.email || "—",
    retryCount: row.retry_count || 0,
  }));

  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-base font-semibold text-foreground">Órdenes de pago</h1>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="animate-fade-up mx-auto flex max-w-[1100px] flex-col gap-4">
          <PaymentOrdersTable rows={rows} />
        </div>
      </div>
    </div>
  );
}
