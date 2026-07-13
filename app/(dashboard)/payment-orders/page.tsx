import Link from "next/link";
import { createServerClient } from "@/lib/pocketbase-server";
import StatusBadge from "@/components/StatusBadge";
import { Collections, type PaymentOrdersWithExpand } from "@/lib/pocketbase-types";
import { formatCurrency, formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

// Lee "payment_orders" (creado por una acción humana deliberada, dry_run=False
// contra BAS real) -- NO "bas_processing_status" (la sonda automática de la
// ingesta, siempre dry_run=True). Antes de esta página, /payment-orders leía
// la colección equivocada -- mostraba el intento automático informativo, no
// las órdenes de pago reales que alguien pidió crear.
export default async function PaymentOrdersPage() {
  const pb = await createServerClient();

  const rows = await pb
    .collection<PaymentOrdersWithExpand>(Collections.PaymentOrders)
    .getFullList({
      expand: "invoice,requested_by",
      sort: "-last_attempt_at",
    });

  return (
    <div>
      <h1 className="mb-1 text-lg font-semibold text-gray-900">Órdenes de pago</h1>
      <p className="mb-4 text-sm text-gray-500">
        Intentos reales de crear una Orden de Pago en BAS, disparados desde la revisión de una
        factura confirmada.
      </p>

      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50 text-left text-xs font-medium uppercase text-gray-500">
            <tr>
              <th className="px-4 py-2">Comprobante</th>
              <th className="px-4 py-2">Método</th>
              <th className="px-4 py-2">Monto</th>
              <th className="px-4 py-2">Estado</th>
              <th className="px-4 py-2">Error</th>
              <th className="px-4 py-2">Pedido por</th>
              <th className="px-4 py-2">Último intento</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((row) => (
              <tr key={row.id}>
                <td className="px-4 py-2 font-medium text-gray-900">
                  {row.expand?.invoice?.numero_comprobante || row.process_id}
                </td>
                <td className="px-4 py-2 text-gray-600 capitalize">{row.metodo_pago}</td>
                <td className="px-4 py-2 text-gray-900">
                  {formatCurrency(row.monto, row.expand?.invoice?.moneda)}
                </td>
                <td className="px-4 py-2">
                  <StatusBadge status={row.status} />
                </td>
                <td className="max-w-xs truncate px-4 py-2 text-red-600" title={row.bas_error}>
                  {row.bas_error || "—"}
                </td>
                <td className="px-4 py-2 text-gray-600">{row.expand?.requested_by?.email || "—"}</td>
                <td className="px-4 py-2 text-gray-600">{formatDate(row.last_attempt_at)}</td>
                <td className="px-4 py-2">
                  {row.expand?.invoice && (
                    <Link href={`/invoices/${row.expand.invoice.id}`} className="text-blue-600 hover:underline">
                      Ver factura
                    </Link>
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-6 text-center text-gray-400">
                  Todavía no se pidió ninguna orden de pago.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
