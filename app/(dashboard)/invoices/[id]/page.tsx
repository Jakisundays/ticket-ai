import type { ReactNode } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { createServerClient, ClientResponseError } from "@/lib/pocketbase-server";
import StatusBadge from "@/components/StatusBadge";
import ReopenButton from "./ReopenButton";
import InvoiceFileViewer from "./InvoiceFileViewer";
import InvoiceReviewForm from "./InvoiceReviewForm";
import PaymentOrderPanel from "./PaymentOrderPanel";
import {
  Collections,
  type BasCategoryMapRecord,
  type BasPaymentMethodsRecord,
  type InvoiceItemsRecord,
  type InvoiceWithItemsExpand,
} from "@/lib/pocketbase-types";
import { formatCurrency, formatDate, driveFileUrl } from "@/lib/format";

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
      <div className="flex h-[calc(100vh-8rem)] flex-col">
        <div className="mb-4">
          <Link href="/queue" className="text-sm text-gray-500 hover:underline">
            ← Cola de revisión
          </Link>
          <h1 className="mt-1 text-lg font-semibold text-gray-900">
            {invoice.numero_comprobante || invoice.process_id}
          </h1>
        </div>
        {invoice.status === "error" && invoice.error_message && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {invoice.error_message}
          </div>
        )}
        <div className="grid flex-1 grid-cols-1 gap-4 overflow-hidden lg:grid-cols-2">
          <InvoiceFileViewer processId={invoice.process_id} />
          <InvoiceReviewForm
            invoice={invoice}
            items={items}
            categories={categoriesResult}
            prevInvoiceId={prevInvoiceId}
            nextInvoiceId={nextInvoiceId}
          />
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
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href="/invoices" className="text-sm text-gray-500 hover:underline">
            ← Facturas
          </Link>
          <h1 className="mt-1 text-lg font-semibold text-gray-900">
            {invoice.numero_comprobante || invoice.process_id}
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status="confirmed" />
          <ReopenButton invoiceId={invoice.id} />
        </div>
      </div>

      <p className="text-sm text-gray-500">
        Confirmada{confirmedByEmail ? ` por ${confirmedByEmail}` : ""}
        {invoice.confirmed_at ? ` el ${formatDate(invoice.confirmed_at)}` : ""}.
      </p>

      <section className="grid grid-cols-1 gap-4 rounded-lg border border-gray-200 bg-white p-4 text-sm sm:grid-cols-3">
        <Field label="Estado">
          <StatusBadge status={invoice.status} />
        </Field>
        <Field label="Tipo">
          {invoice.tipo_comprobante} {invoice.subtipo_comprobante}
        </Field>
        <Field label="Emisión">{formatDate(invoice.fecha_emision)}</Field>
        <Field label="Emisor">
          {invoice.emisor_nombre} ({invoice.emisor_cuit})
        </Field>
        <Field label="Receptor">
          {invoice.receptor_nombre} ({invoice.receptor_cuit})
        </Field>
        <Field label="Forma de pago">{invoice.forma_pago || "—"}</Field>
        <Field label="Subtotal">
          {formatCurrency(invoice.subtotal, invoice.moneda)}
        </Field>
        <Field label="Total">
          {formatCurrency(invoice.total, invoice.moneda)}
        </Field>
        <Field label="CAE">
          {invoice.cae || "—"}
          {invoice.cae_vencimiento
            ? ` (vence ${formatDate(invoice.cae_vencimiento)})`
            : ""}
        </Field>
        <Field label="Guardado en Sheets">
          {invoice.sheets_saved ? "Sí" : "No"}
        </Field>
        <Field label="Drive">
          {driveUrl ? (
            <a
              href={driveUrl}
              target="_blank"
              rel="noreferrer"
              className="text-blue-600 hover:underline"
            >
              Ver archivo
            </a>
          ) : (
            "—"
          )}
        </Field>
        <Field label="process_id">
          <code className="text-xs text-gray-500">{invoice.process_id}</code>
        </Field>
      </section>

      <section className="rounded-lg border border-gray-200 bg-white p-4">
        <h2 className="mb-3 text-sm font-semibold text-gray-900">Estado BAS</h2>
        {basStatus ? (
          <div className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-3">
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
                <span className="text-red-600">{basStatus.orden_pago_error}</span>
              </Field>
            )}
          </div>
        ) : (
          <p className="text-sm text-gray-400">
            Todavía no hay estado de procesamiento BAS para esta factura.
          </p>
        )}
      </section>

      <section className="rounded-lg border border-gray-200 bg-white p-4">
        <h2 className="mb-3 text-sm font-semibold text-gray-900">Ítems</h2>
        <ItemsTable items={items} moneda={invoice.moneda} />
      </section>

      <PaymentOrderPanel
        processId={invoice.process_id}
        invoiceTotal={invoice.total}
        paymentMethods={paymentMethods}
        existingOrder={invoice.expand?.payment_orders_via_invoice ?? null}
      />
    </div>
  );
}

function ItemsTable({ items, moneda }: { items: InvoiceItemsRecord[]; moneda: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-200 text-sm">
        <thead className="bg-gray-50 text-left text-xs font-medium uppercase text-gray-500">
          <tr>
            <th className="px-3 py-2">#</th>
            <th className="px-3 py-2">Descripción</th>
            <th className="px-3 py-2">Cantidad</th>
            <th className="px-3 py-2">Precio unit.</th>
            <th className="px-3 py-2">Total</th>
            <th className="px-3 py-2">Categoría</th>
            <th className="px-3 py-2">Código BAS</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {items
            .slice()
            .sort((a, b) => (a.linea ?? 0) - (b.linea ?? 0))
            .map((item) => (
              <tr key={item.id}>
                <td className="px-3 py-2 text-gray-500">{item.linea}</td>
                <td className="px-3 py-2">{item.descripcion}</td>
                <td className="px-3 py-2">{item.cantidad}</td>
                <td className="px-3 py-2">{formatCurrency(item.precio_unitario, moneda)}</td>
                <td className="px-3 py-2">{formatCurrency(item.precio_total, moneda)}</td>
                <td className="px-3 py-2">{item.categoria || "—"}</td>
                <td className="px-3 py-2">{item.bas_codigo_item || "—"}</td>
              </tr>
            ))}
          {items.length === 0 && (
            <tr>
              <td colSpan={7} className="px-3 py-6 text-center text-gray-400">
                Sin items.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase text-gray-400">{label}</dt>
      <dd className="mt-0.5 text-gray-900">{children}</dd>
    </div>
  );
}
