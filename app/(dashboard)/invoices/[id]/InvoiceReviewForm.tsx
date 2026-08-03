"use client";

import { useEffect, useMemo, useState, type ChangeEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { AlertTriangle, Loader2 } from "lucide-react";
import { getPocketBase } from "@/lib/pocketbase-browser";
import {
  Collections,
  type BasCategoryMapRecord,
  type InvoiceItemsRecord,
  type InvoicesRecord,
} from "@/lib/pocketbase-types";
import { formatRelativeDateTime } from "@/lib/format";
import { validarFacturaParaConfirmar, fechaComoInputDate } from "@/lib/invoice-validation";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** Tags donde j/k NO deben navegar (el usuario está escribiendo). Cmd/Ctrl+Enter
 * para confirmar sí debe funcionar incluso con foco en un campo. */
const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

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
    setItemDrafts((prev) => {
      const current = { ...prev[itemId], [field]: value };
      // Auto-recalcula el total del ítem al tocar cantidad o precio unitario
      // -- reduce un error de corrección común. precio_total sigue siendo
      // editable a mano después (ej. si hay un descuento); ese último valor
      // manual es el que se guarda, esto solo autocompleta el caso simple.
      if (field === "cantidad" || field === "precio_unitario") {
        current.precio_total = Number((current.cantidad * current.precio_unitario).toFixed(2));
      }
      return { ...prev, [itemId]: current };
    });
  }

  // Gemini es inconsistente sobre si desglosa impuestos (IVA, Ingresos
  // Brutos, tasas municipales, etc.) como líneas de ítem propias o no --
  // depende de cómo esté impresa la factura real (caso real: Litoral Gas,
  // 15 ítems donde 2 son líneas de impuesto, sumando exacto contra `total`
  // bruto; vs. una factura de un solo producto donde el ítem viene neto,
  // sumando exacto contra `subtotal`). Ambos son resultados de extracción
  // válidos -- lo único que indica un error real es que la suma de ítems no
  // cierre contra NINGUNO de los dos. Ver lib/invoice-validation.ts para el
  // detalle -- son las mismas 6 reglas que ya bloquean el pago real
  // (Invoicy/utils/validaciones_pre_bas.py) y que la barrera real de
  // confirmación (ticket-ai-infra/pocketbase/pb_hooks/invoices.pb.js).
  const itemsForValidation = useMemo(
    () =>
      items.map((item) => {
        const draft = itemDrafts[item.id];
        return {
          id: item.id,
          cantidad: draft.cantidad,
          precio_unitario: draft.precio_unitario,
          precio_total: draft.precio_total,
        };
      }),
    [items, itemDrafts]
  );
  const validation = useMemo(
    () => validarFacturaParaConfirmar(invoiceDraft, itemsForValidation),
    [invoiceDraft, itemsForValidation]
  );
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

      toast.success("Factura confirmada.");
      // Avanza directo a la siguiente pendiente en vez de quedarse en esta
      // (ahora bloqueada) -- mantiene al revisor en flujo, no lo devuelve a
      // la lista entre cada factura. Si era la última, vuelve a la cola.
      router.push(nextInvoiceId ? `/invoices/${nextInvoiceId}` : "/queue");
    } catch (err) {
      setStatus("error");
      const message = err instanceof Error ? err.message : "No se pudo confirmar la factura.";
      setError(message);
      toast.error(message);
    }
  }

  // Navegación por teclado en la cola: j/k (o flechas) para moverse entre
  // pendientes, Cmd/Ctrl+Enter para confirmar y avanzar. Nunca se anima --
  // son acciones de teclado, se usan demasiado seguido para justificar una
  // transición (ver principios de motion de Emil Kowalski).
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      // Algunos entornos (automatización, teclados no-US) reportan la tecla
      // Enter como "Return" en vez de "Enter", o solo son fiables via
      // `code` (posición física) -- se chequean las tres formas.
      const isEnterKey =
        event.key === "Enter" || event.key === "Return" || event.code === "Enter" || event.code === "NumpadEnter";
      const isConfirmShortcut = (event.metaKey || event.ctrlKey) && isEnterKey;
      if (isConfirmShortcut) {
        event.preventDefault();
        if (status !== "saving" && validation.isValid) handleConfirm();
        return;
      }

      const isTyping = TYPING_TAGS.has((event.target as HTMLElement)?.tagName);
      if (isTyping) return;

      if (event.key === "j" || event.key === "ArrowDown") {
        if (nextInvoiceId) {
          event.preventDefault();
          router.push(`/invoices/${nextInvoiceId}`);
        }
      } else if (event.key === "k" || event.key === "ArrowUp") {
        if (prevInvoiceId) {
          event.preventDefault();
          router.push(`/invoices/${prevInvoiceId}`);
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, nextInvoiceId, prevInvoiceId, validation.isValid]);

  return (
    <div className="flex h-full flex-col gap-6 overflow-y-auto px-7 py-5">
      <section className="rounded-xl bg-card p-6 shadow-(--shadow-1)">
        <h2 className="mb-4 text-[13px] font-semibold text-foreground">Datos de la factura</h2>
        {!validation.isValid && (
          <p className="mb-4 flex items-start gap-1.5 rounded-sm bg-status-destructive-bg px-2.5 py-2 text-xs font-medium text-status-destructive-fg">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            <span>
              Faltan {validation.messages.length} dato{validation.messages.length === 1 ? "" : "s"} obligatorio
              {validation.messages.length === 1 ? "" : "s"} para poder generar el pago después: {validation.messages.join(" · ")}.
            </span>
          </p>
        )}
        <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
          <Field label="Subida el">
            <p className="flex h-9 items-center text-[13.5px] font-medium text-foreground">
              {formatRelativeDateTime(invoice.created)}
            </p>
          </Field>
          <TextField label="Número de comprobante" value={invoiceDraft.numero_comprobante} onChange={(v) => setInvoiceField("numero_comprobante", v)} mono error={validation.fieldErrors.numero_comprobante} />
          <DateField label="Fecha de emisión" value={invoiceDraft.fecha_emision} onChange={(v) => setInvoiceField("fecha_emision", v)} error={validation.fieldErrors.fecha_emision} />
          <TextField label="Tipo" value={invoiceDraft.tipo_comprobante} onChange={(v) => setInvoiceField("tipo_comprobante", v)} />
          <TextField label="Subtipo" value={invoiceDraft.subtipo_comprobante} onChange={(v) => setInvoiceField("subtipo_comprobante", v)} />
          <TextField label="Emisor" value={invoiceDraft.emisor_nombre} onChange={(v) => setInvoiceField("emisor_nombre", v)} />
          <TextField label="CUIT emisor" value={invoiceDraft.emisor_cuit} onChange={(v) => setInvoiceField("emisor_cuit", v)} mono error={validation.fieldErrors.emisor_cuit} />
          <TextField label="Receptor" value={invoiceDraft.receptor_nombre} onChange={(v) => setInvoiceField("receptor_nombre", v)} />
          <TextField label="CUIT receptor" value={invoiceDraft.receptor_cuit} onChange={(v) => setInvoiceField("receptor_cuit", v)} mono />
          <TextField label="Forma de pago" value={invoiceDraft.forma_pago} onChange={(v) => setInvoiceField("forma_pago", v)} />
          <TextField label="Moneda" value={invoiceDraft.moneda} onChange={(v) => setInvoiceField("moneda", v)} error={validation.fieldErrors.moneda} />
          <NumberField label="Subtotal" value={invoiceDraft.subtotal} onChange={(v) => setInvoiceField("subtotal", v)} />
          <NumberField label="Total" value={invoiceDraft.total} onChange={(v) => setInvoiceField("total", v)} />
          <TextField label="CAE" value={invoiceDraft.cae} onChange={(v) => setInvoiceField("cae", v)} mono error={validation.fieldErrors.cae} />
          <DateField label="Vencimiento CAE" value={invoiceDraft.cae_vencimiento} onChange={(v) => setInvoiceField("cae_vencimiento", v)} error={validation.fieldErrors.cae_vencimiento} />
        </div>
      </section>

      <section className="rounded-xl bg-card p-6 shadow-(--shadow-1)">
        <h2 className="mb-4 text-[13px] font-semibold text-foreground">Ítems</h2>
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Descripción</TableHead>
              <TableHead className="text-right">Cantidad</TableHead>
              <TableHead className="text-right">Precio unit.</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead>Categoría</TableHead>
              <TableHead>Código BAS</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => {
              const draft = itemDrafts[item.id];
              const itemError = validation.itemErrors[item.id];
              return (
                <TableRow key={item.id} className="hover:bg-transparent">
                  <TableCell className="p-1.5 align-middle">
                    <Input
                      type="text"
                      value={draft.descripcion}
                      onChange={(e) => setItemField(item.id, "descripcion", e.target.value)}
                      className="h-8 min-w-[160px]"
                    />
                  </TableCell>
                  <TableCell className="p-1.5 align-middle">
                    <Input
                      type="number"
                      value={draft.cantidad}
                      onChange={(e) => setItemField(item.id, "cantidad", Number(e.target.value))}
                      className="h-8 w-16 text-right"
                    />
                  </TableCell>
                  <TableCell className="p-1.5 align-middle">
                    <Input
                      type="number"
                      step="0.01"
                      value={draft.precio_unitario}
                      onChange={(e) => setItemField(item.id, "precio_unitario", Number(e.target.value))}
                      className="h-8 w-24 text-right font-mono"
                    />
                  </TableCell>
                  <TableCell className="p-1.5 align-middle">
                    <Input
                      type="number"
                      step="0.01"
                      value={draft.precio_total}
                      onChange={(e) => setItemField(item.id, "precio_total", Number(e.target.value))}
                      title={itemError}
                      className={cn(
                        "h-8 w-24 text-right font-mono",
                        itemError && "border-destructive focus-visible:ring-destructive/40"
                      )}
                    />
                  </TableCell>
                  <TableCell className="p-1.5 align-middle">
                    <Select
                      value={draft.categoria}
                      onValueChange={(v) => setItemField(item.id, "categoria", v)}
                    >
                      <SelectTrigger className="h-8 w-full min-w-[140px]">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {categories.map((cat) => (
                          <SelectItem key={cat.id} value={cat.categoria}>
                            {cat.categoria}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell className="p-1.5 align-middle text-muted-foreground">
                    {item.bas_codigo_item || "—"}
                  </TableCell>
                </TableRow>
              );
            })}
            {items.length === 0 && (
              <TableRow className="hover:bg-transparent">
                <TableCell colSpan={6} className="py-6 text-center text-muted-foreground">
                  Sin ítems.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
        {items.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              La categoría define el código BAS automáticamente al guardar.
            </p>
            {validation.itemsSummaryError && (
              <p className="flex items-center gap-1.5 rounded-sm bg-status-destructive-bg px-2.5 py-1 text-xs font-medium text-status-destructive-fg">
                <AlertTriangle className="size-3.5 shrink-0" />
                {validation.itemsSummaryError}
              </p>
            )}
          </div>
        )}
      </section>

      <div className="sticky bottom-0 -mx-7 flex flex-wrap items-center justify-between gap-3 border-t bg-background/95 px-7 py-3.5 backdrop-blur">
        <span className="hidden items-center gap-3 text-xs text-muted-foreground lg:flex">
          <span className="inline-flex items-center gap-1">
            <Kbd>j</Kbd>
            <Kbd>k</Kbd>
            factura ant./sig.
          </span>
          <span className="inline-flex items-center gap-1">
            <Kbd>⌘</Kbd>
            <Kbd>⏎</Kbd>
            confirmar
          </span>
        </span>
        <div className="flex items-center gap-3">
          {error && <span className="text-sm text-destructive">{error}</span>}
          <Button
            type="button"
            onClick={handleConfirm}
            disabled={status === "saving" || !validation.isValid}
            title={!validation.isValid ? "Completá los datos obligatorios marcados arriba antes de confirmar." : undefined}
            className="h-9 gap-2 px-4"
          >
            {status === "saving" && <Loader2 className="size-3.5 animate-spin" />}
            {status === "saving" ? "Confirmando…" : "Confirmar factura"}
            {status !== "saving" && (
              <span className="font-mono text-[11px] font-medium opacity-70">⌘⏎</span>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[11px] font-medium text-muted-foreground">
      {children}
    </kbd>
  );
}

function TextField({
  label,
  value,
  onChange,
  mono = false,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  mono?: boolean;
  error?: string | null;
}) {
  return (
    <Field label={label} error={error}>
      <Input
        type="text"
        value={value ?? ""}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
        className={cn(
          "h-9",
          mono && "font-mono text-[13px]",
          error && "border-destructive focus-visible:ring-destructive/40"
        )}
      />
    </Field>
  );
}

function DateField({
  label,
  value,
  onChange,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string | null;
}) {
  // Input nativo type="date" -- calendario del navegador en vez de texto
  // libre, y el formato que devuelve el picker (YYYY-MM-DD) ya es uno de
  // los 3 que acepta la validación, así que editar acá normaliza a ISO de
  // paso. fechaComoInputDate convierte facturas viejas en DD/MM/YYYY o
  // DD-MM-YYYY (Gemini no estaba forzado a un formato único antes de esto)
  // para que el picker las muestre en vez de aparecer vacío -- si no es
  // parseable, queda vacío a propósito (ya se marca aparte con `error`, no
  // se inventa una fecha).
  return (
    <Field label={label} error={error}>
      <Input
        type="date"
        value={fechaComoInputDate(value)}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
        className={cn("h-9", error && "border-destructive focus-visible:ring-destructive/40")}
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
      <Input
        type="number"
        step="0.01"
        value={value ?? 0}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(Number(e.target.value))}
        className="h-9 text-right font-mono"
      />
    </Field>
  );
}

function Field({
  label,
  children,
  error,
}: {
  label: string;
  children: ReactNode;
  error?: string | null;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="overline text-[11px] text-muted-foreground">{label}</span>
      {children}
      {error && <span className="text-[11.5px] font-medium text-destructive">{error}</span>}
    </label>
  );
}
