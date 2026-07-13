import Link from "next/link";
import { createServerClient } from "@/lib/pocketbase-server";
import StatusBadge from "@/components/StatusBadge";
import {
  Collections,
  type InvoiceListItemExpand,
} from "@/lib/pocketbase-types";
import { formatCurrency, formatDate, driveFileUrl } from "@/lib/format";

// Esta pagina lee cookies (via createServerClient) y siempre debe reflejar
// el estado mas reciente de PocketBase, asi que no tiene sentido cachearla
// ni pre-renderizarla de forma estatica.
export const dynamic = "force-dynamic";

export default async function InvoicesPage() {
  const pb = await createServerClient();

  const result = await pb
    .collection<InvoiceListItemExpand>(Collections.Invoices)
    .getList(1, 50, {
      sort: "-created",
      expand: "bas_processing_status_via_invoice",
    });

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold text-gray-900">Facturas</h1>
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50 text-left text-xs font-medium uppercase text-gray-500">
            <tr>
              <th className="px-4 py-2">Comprobante</th>
              <th className="px-4 py-2">Emisión</th>
              <th className="px-4 py-2">Emisor</th>
              <th className="px-4 py-2">Total</th>
              <th className="px-4 py-2">Estado</th>
              <th className="px-4 py-2">Revisión</th>
              <th className="px-4 py-2">Sheets</th>
              <th className="px-4 py-2">Drive</th>
              <th className="px-4 py-2">Estado BAS</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {result.items.map((invoice) => {
              // Relation `unique` (1:1) -- PocketBase expande esto como un
              // objeto único, no un array (a diferencia de invoice_items).
              const basStatus =
                invoice.expand?.bas_processing_status_via_invoice;
              const driveUrl = driveFileUrl(invoice.drive_file_id);

              return (
                <tr key={invoice.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2">
                    <Link
                      href={`/invoices/${invoice.id}`}
                      className="font-medium text-gray-900 hover:underline"
                    >
                      {invoice.numero_comprobante || invoice.process_id}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-gray-600">
                    {formatDate(invoice.fecha_emision)}
                  </td>
                  <td className="px-4 py-2 text-gray-600">
                    {invoice.emisor_nombre}
                  </td>
                  <td className="px-4 py-2 text-gray-900">
                    {formatCurrency(invoice.total, invoice.moneda)}
                  </td>
                  <td className="px-4 py-2">
                    <StatusBadge status={invoice.status} />
                  </td>
                  <td className="px-4 py-2">
                    <StatusBadge status={invoice.review_status || "needs_review"} />
                  </td>
                  <td className="px-4 py-2">
                    {invoice.sheets_saved ? "Sí" : "No"}
                  </td>
                  <td className="px-4 py-2">
                    {driveUrl ? (
                      <a
                        href={driveUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="text-blue-600 hover:underline"
                      >
                        Ver
                      </a>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    {basStatus ? (
                      <StatusBadge status={basStatus.orden_pago_status} />
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
            {result.items.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-gray-400">
                  No hay facturas todavía.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
