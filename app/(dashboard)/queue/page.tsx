import Link from "next/link";
import { createServerClient } from "@/lib/pocketbase-server";
import { Collections, type InvoicesRecord } from "@/lib/pocketbase-types";
import { formatCurrency, formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function QueuePage() {
  const pb = await createServerClient();

  // status = "completed": una factura todavía en processing/error no es
  // asunto de la cola de revisión humana todavía (eso lo cubre /invoices).
  // review_status != "confirmed" (no "= needs_review"): filas legacy sin
  // review_status seteado (string vacío) también cuentan como pendientes.
  const result = await pb
    .collection<InvoicesRecord>(Collections.Invoices)
    .getList(1, 100, {
      filter: 'status = "completed" && review_status != "confirmed"',
      sort: "+created",
    });

  return (
    <div>
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-lg font-semibold text-gray-900">Cola de revisión</h1>
        <span className="text-sm text-gray-500">
          {result.totalItems} pendiente{result.totalItems === 1 ? "" : "s"}
        </span>
      </div>

      {result.items.length === 0 ? (
        <EmptyState />
      ) : (
        <ul className="space-y-2">
          {result.items.map((invoice) => (
            <li key={invoice.id}>
              <Link
                href={`/invoices/${invoice.id}`}
                className="flex items-center justify-between gap-4 rounded-lg border border-gray-200 bg-white p-4 transition-colors hover:border-gray-300 hover:bg-gray-50"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium text-gray-900">
                    {invoice.numero_comprobante || invoice.process_id}
                  </p>
                  <p className="truncate text-sm text-gray-500">
                    {invoice.emisor_nombre || "Emisor sin identificar"} · {formatDate(invoice.fecha_emision)}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-4">
                  <span className="text-sm font-medium text-gray-900">
                    {formatCurrency(invoice.total, invoice.moneda)}
                  </span>
                  <span className="text-sm text-gray-400">Revisar →</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-gray-300 bg-white px-6 py-16 text-center">
      <p className="text-sm font-medium text-gray-900">No hay facturas pendientes de revisión.</p>
      <p className="mt-1 text-sm text-gray-500">
        Las facturas extraídas por WhatsApp, email o el{" "}
        <Link href="/subir-factura" className="text-blue-600 hover:underline">
          formulario web
        </Link>{" "}
        van a aparecer acá apenas terminen de procesarse.
      </p>
    </div>
  );
}
