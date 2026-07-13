"use client";

import { useMemo, useState, type ChangeEvent, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getPocketBase } from "@/lib/pocketbase-browser";
import {
  Collections,
  type BasCategoryMapRecord,
  type InvoiceItemsRecord,
  type InvoicesRecord,
} from "@/lib/pocketbase-types";

type InvoiceDraft = Pick<
  InvoicesRecord,
  | "numero_comprobante"
  | "tipo_comprobante"
  | "subtipo_comprobante"
  | "fecha_emision"
  | "moneda"
  | "emisor_nombre"
  | "emisor_cuit"
  | "receptor_nombre"
  | "receptor_cuit"
  | "forma_pago"
  | "subtotal"
  | "total"
  | "cae"
  | "cae_vencimiento"
>;

type ItemDraft = Pick<
  InvoiceItemsRecord,
  "descripcion" | "cantidad" | "precio_unitario" | "precio_total" | "categoria"
>;

function invoiceDraftFrom(invoice: InvoicesRecord): InvoiceDraft {
  return {
    numero_comprobante: invoice.numero_comprobante,
    tipo_comprobante: invoice.tipo_comprobante,
    subtipo_comprobante: invoice.subtipo_comprobante,
    fecha_emision: invoice.fecha_emision,
    moneda: invoice.moneda,
    emisor_nombre: invoice.emisor_nombre,
    emisor_cuit: invoice.emisor_cuit,
    receptor_nombre: invoice.receptor_nombre,
    receptor_cuit: invoice.receptor_cuit,
    forma_pago: invoice.forma_pago,
    subtotal: invoice.subtotal,
    total: invoice.total,
    cae: invoice.cae,
    cae_vencimiento: invoice.cae_vencimiento,
  };
}

function itemDraftFrom(item: InvoiceItemsRecord): ItemDraft {
  return {
    descripcion: item.descripcion,
    cantidad: item.cantidad,
    precio_unitario: item.precio_unitario,
    precio_total: item.precio_total,
    categoria: item.categoria,
  };
}

function isDirty<T extends object>(draft: T, original: T): boolean {
  return (Object.keys(draft) as (keyof T)[]).some((key) => draft[key] !== original[key]);
}

export default function InvoiceReviewForm({
  invoice,
  items,
  categories,
  prevInvoiceId,
  nextInvoiceId,
}: {
  invoice: InvoicesRecord;
  items: InvoiceItemsRecord[];
  categories: BasCategoryMapRecord[];
  prevInvoiceId: string | null;
  nextInvoiceId: string | null;
}) {
  const router = useRouter();
  const [invoiceDraft, setInvoiceDraft] = useState<InvoiceDraft>(() => invoiceDraftFrom(invoice));
  const [itemDrafts, setItemDrafts] = useState<Record<string, ItemDraft>>(() =>
    Object.fromEntries(items.map((item) => [item.id, itemDraftFrom(item)]))
  );
  const [status, setStatus] = useState<"idle" | "saving" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  const originalInvoiceDraft = useMemo(() => invoiceDraftFrom(invoice), [invoice]);

  function setInvoiceField<K extends keyof InvoiceDraft>(field: K, value: InvoiceDraft[K]) {
    setInvoiceDraft((prev) => ({ ...prev, [field]: value }));
  }

  function setItemField<K extends keyof ItemDraft>(itemId: string, field: K, value: ItemDraft[K]) {
    setItemDrafts((prev) => ({ ...prev, [itemId]: { ...prev[itemId], [field]: value } }));
  }

  async function handleConfirm() {
    setStatus("saving");
    setError(null);
    try {
      const pb = getPocketBase();
      const userId = pb.authStore.record?.id;
      if (!userId) throw new Error("Sesión inválida.");

      // Los items primero: el hook de invoice_items valida el candado contra
      // la factura padre en su estado ACTUAL (todavía needs_review acá,
      // recién se confirma después) -- si se invirtiera el orden, estos
      // updates llegarían después de que la factura ya esté confirmed y el
      // propio hook los rechazaría.
      const dirtyItemIds = items
        .map((item) => item.id)
        .filter((id) => isDirty(itemDrafts[id], itemDraftFrom(items.find((i) => i.id === id)!)));
      await Promise.all(
        dirtyItemIds.map((id) =>
          pb.collection(Collections.InvoiceItems).update(id, itemDrafts[id])
        )
      );

      // Un solo write atómico: guarda los campos de factura que cambiaron Y
      // confirma, en la misma request -- evita una ventana de "guardado pero
      // no confirmado" si la red se corta entre dos llamadas separadas.
      const dirtyInvoiceFields = Object.fromEntries(
        (Object.keys(invoiceDraft) as (keyof InvoiceDraft)[])
          .filter((key) => invoiceDraft[key] !== originalInvoiceDraft[key])
          .map((key) => [key, invoiceDraft[key]])
      );
      await pb.collection(Collections.Invoices).update(invoice.id, {
        ...dirtyInvoiceFields,
        review_status: "confirmed",
        confirmed_by: userId,
        confirmed_at: new Date().toISOString(),
      });

      router.refresh();
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : "No se pudo confirmar la factura.");
    }
  }

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto pb-4">
      <section className="rounded-lg border border-gray-200 bg-white p-4">
        <h2 className="mb-3 text-sm font-semibold text-gray-900">Datos de la factura</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <TextField label="Número de comprobante" value={invoiceDraft.numero_comprobante} onChange={(v) => setInvoiceField("numero_comprobante", v)} />
          <TextField label="Fecha de emisión" value={invoiceDraft.fecha_emision} onChange={(v) => setInvoiceField("fecha_emision", v)} />
          <TextField label="Tipo" value={invoiceDraft.tipo_comprobante} onChange={(v) => setInvoiceField("tipo_comprobante", v)} />
          <TextField label="Subtipo" value={invoiceDraft.subtipo_comprobante} onChange={(v) => setInvoiceField("subtipo_comprobante", v)} />
          <TextField label="Emisor" value={invoiceDraft.emisor_nombre} onChange={(v) => setInvoiceField("emisor_nombre", v)} />
          <TextField label="CUIT emisor" value={invoiceDraft.emisor_cuit} onChange={(v) => setInvoiceField("emisor_cuit", v)} />
          <TextField label="Receptor" value={invoiceDraft.receptor_nombre} onChange={(v) => setInvoiceField("receptor_nombre", v)} />
          <TextField label="CUIT receptor" value={invoiceDraft.receptor_cuit} onChange={(v) => setInvoiceField("receptor_cuit", v)} />
          <TextField label="Forma de pago" value={invoiceDraft.forma_pago} onChange={(v) => setInvoiceField("forma_pago", v)} />
          <TextField label="Moneda" value={invoiceDraft.moneda} onChange={(v) => setInvoiceField("moneda", v)} />
          <NumberField label="Subtotal" value={invoiceDraft.subtotal} onChange={(v) => setInvoiceField("subtotal", v)} />
          <NumberField label="Total" value={invoiceDraft.total} onChange={(v) => setInvoiceField("total", v)} />
          <TextField label="CAE" value={invoiceDraft.cae} onChange={(v) => setInvoiceField("cae", v)} />
          <TextField label="Vencimiento CAE" value={invoiceDraft.cae_vencimiento} onChange={(v) => setInvoiceField("cae_vencimiento", v)} />
        </div>
      </section>

      <section className="rounded-lg border border-gray-200 bg-white p-4">
        <h2 className="mb-3 text-sm font-semibold text-gray-900">Ítems</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="text-left text-xs font-medium uppercase text-gray-500">
              <tr>
                <th className="py-1.5 pr-2">Descripción</th>
                <th className="py-1.5 pr-2">Cantidad</th>
                <th className="py-1.5 pr-2">Precio unit.</th>
                <th className="py-1.5 pr-2">Total</th>
                <th className="py-1.5 pr-2">Categoría</th>
                <th className="py-1.5">Código BAS</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {items.map((item) => {
                const draft = itemDrafts[item.id];
                return (
                  <tr key={item.id}>
                    <td className="py-1.5 pr-2">
                      <input
                        type="text"
                        value={draft.descripcion}
                        onChange={(e) => setItemField(item.id, "descripcion", e.target.value)}
                        className="w-full rounded-md border border-gray-300 px-2 py-1 text-sm"
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <input
                        type="number"
                        value={draft.cantidad}
                        onChange={(e) => setItemField(item.id, "cantidad", Number(e.target.value))}
                        className="w-20 rounded-md border border-gray-300 px-2 py-1 text-sm"
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <input
                        type="number"
                        value={draft.precio_unitario}
                        onChange={(e) => setItemField(item.id, "precio_unitario", Number(e.target.value))}
                        className="w-24 rounded-md border border-gray-300 px-2 py-1 text-sm"
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <input
                        type="number"
                        value={draft.precio_total}
                        onChange={(e) => setItemField(item.id, "precio_total", Number(e.target.value))}
                        className="w-24 rounded-md border border-gray-300 px-2 py-1 text-sm"
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <select
                        value={draft.categoria}
                        onChange={(e) => setItemField(item.id, "categoria", e.target.value)}
                        className="rounded-md border border-gray-300 px-2 py-1 text-sm"
                      >
                        {categories.map((cat) => (
                          <option key={cat.id} value={cat.categoria}>
                            {cat.categoria}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="py-1.5 text-gray-500">{item.bas_codigo_item || "—"}</td>
                  </tr>
                );
              })}
              {items.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-6 text-center text-gray-400">
                    Sin ítems.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {items.length > 0 && (
          <p className="mt-2 text-xs text-gray-400">
            La categoría define el código BAS automáticamente al guardar.
          </p>
        )}
      </section>

      <div className="sticky bottom-0 -mx-4 flex items-center justify-between gap-3 border-t border-gray-200 bg-white/95 px-4 py-3 backdrop-blur">
        <div className="flex items-center gap-3 text-sm">
          {prevInvoiceId && (
            <Link href={`/invoices/${prevInvoiceId}`} className="text-gray-500 hover:text-gray-900">
              ← Anterior
            </Link>
          )}
          {nextInvoiceId && (
            <Link href={`/invoices/${nextInvoiceId}`} className="text-gray-500 hover:text-gray-900">
              Siguiente →
            </Link>
          )}
        </div>
        <div className="flex items-center gap-3">
          {error && <span className="text-sm text-red-600">{error}</span>}
          <button
            type="button"
            onClick={handleConfirm}
            disabled={status === "saving"}
            className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-40"
          >
            {status === "saving" ? "Guardando…" : "Guardar y confirmar"}
          </button>
        </div>
      </div>
    </div>
  );
}

function TextField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label}>
      <input
        type="text"
        value={value ?? ""}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
        className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
      />
    </Field>
  );
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <Field label={label}>
      <input
        type="number"
        value={value ?? 0}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(Number(e.target.value))}
        className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
      />
    </Field>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-gray-500">{label}</span>
      {children}
    </label>
  );
}
