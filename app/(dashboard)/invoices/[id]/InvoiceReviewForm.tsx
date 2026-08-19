"use client";

import { useEffect, useMemo, useState, type ChangeEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import { getPocketBase } from "@/lib/pocketbase-browser";
import {
  Collections,
  type BasCategoryMapRecord,
  type BasItemsRecord,
  type InvoiceItemsRecord,
  type InvoiceWithItemsExpand,
} from "@/lib/pocketbase-types";
import { formatRelativeDateTime } from "@/lib/format";
import { validarFacturaParaConfirmar, fechaComoInputDate } from "@/lib/invoice-validation";
import { cn } from "@/lib/utils";
import StatusBadge from "@/components/StatusBadge";
import RecheckProviderButton from "./RecheckProviderButton";
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
  InvoiceWithItemsExpand,
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
  | "iva_alicuota"
>;

type ItemDraft = Pick<
  InvoiceItemsRecord,
  "descripcion" | "cantidad" | "precio_unitario" | "precio_total" | "categoria" | "bas_codigo_item"
>;

function invoiceDraftFrom(invoice: InvoiceWithItemsExpand): InvoiceDraft {
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
    iva_alicuota: invoice.iva_alicuota,
  };
}

function itemDraftFrom(item: InvoiceItemsRecord): ItemDraft {
  return {
    descripcion: item.descripcion,
    cantidad: item.cantidad,
    precio_unitario: item.precio_unitario,
    precio_total: item.precio_total,
    categoria: item.categoria,
    bas_codigo_item: item.bas_codigo_item,
  };
}

function isDirty<T extends object>(draft: T, original: T): boolean {
  return (Object.keys(draft) as (keyof T)[]).some((key) => draft[key] !== original[key]);
}

/** Colapsa duplicados por `keyFn`, quedándose con la primera aparición --
 * conserva el orden ya recibido (ej. el `sort` que aplicó el fetch server-side).
 * Usado para los <Select> de abajo: bas_category_map tiene varias filas por
 * categoría a propósito (una por alícuota, ver migración
 * 1783483945_add_alicuota_to_bas_category_map.js), pero este selector de
 * categoría es solo informativo -- el Servicio/Ítem BAS es el que se registra
 * de verdad -- así que mostrar cada nombre una sola vez es lo correcto acá,
 * sin tocar la colección. Se aplica también a bas_items por consistencia,
 * aunque ahí `codigo` ya es único a nivel de índice de PocketBase. */
function dedupeBy<T, K>(items: T[], keyFn: (item: T) => K): T[] {
  return Array.from(new Map(items.map((item) => [keyFn(item), item])).values());
}

export default function InvoiceReviewForm({
  invoice,
  items,
  categories,
  basItems,
  prevInvoiceId,
  nextInvoiceId,
}: {
  invoice: InvoiceWithItemsExpand;
  items: InvoiceItemsRecord[];
  categories: BasCategoryMapRecord[];
  basItems: BasItemsRecord[];
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

  // validarFacturaParaConfirmar solo chequea completitud por ítem
  // (precio_total presente y positivo) -- ver lib/invoice-validation.ts. Ya
  // no valida que la suma de ítems cierre contra subtotal/total ni que
  // cantidad*precio_unitario cierre contra precio_total: Gemini es
  // inconsistente sobre si desglosa impuestos (IVA, Ingresos Brutos, tasas
  // municipales) como líneas de ítem propias o no, y ese cruce terminaba
  // bloqueando facturas reales que BAS aceptaría sin problema (2026-08-05,
  // mismo criterio que la baja de validar_monto_aplicable_vs_neto en
  // Invoicy, ver docs/incidente-2026-08-04-pagos-solo-neto.md).
  const itemsForValidation = useMemo(
    () =>
      items.map((item) => ({
        id: item.id,
        precio_total: itemDrafts[item.id].precio_total,
      })),
    [items, itemDrafts]
  );
  const validation = useMemo(
    () => validarFacturaParaConfirmar(invoiceDraft, itemsForValidation),
    [invoiceDraft, itemsForValidation]
  );

  // P0-G: bloquear Confirmar si falta proveedor o algún CodigoItem válido.
  // A propósito NO vive esto en lib/invoice-validation.ts (ese módulo es
  // puramente sobre datos de la factura -- CUIT/fecha/CAE/etc, ver su
  // comentario de cabecera) -- proveedor/CodigoItem son ejes de BAS
  // (P0-B/P0-E), no de completitud de datos. `basItemCodes` es el ÚNICO
  // catálogo válido (P0-E): el <Select> de abajo solo ofrece estos
  // códigos, así que un bas_codigo_item que no esté acá es, por
  // definición, inválido o desactualizado (ej. quedó de antes de que el
  // ítem se desactivara en BAS).
  const basItemCodes = useMemo(() => new Set(basItems.map((i) => i.codigo)), [basItems]);
  // Ver dedupeBy: bas_category_map trae varias filas por categoría (una por
  // alícuota) a propósito -- el selector de abajo solo necesita el nombre.
  const uniqueCategories = useMemo(() => dedupeBy(categories, (c) => c.categoria), [categories]);
  const uniqueBasItems = useMemo(() => dedupeBy(basItems, (b) => b.codigo), [basItems]);
  const proveedorResuelto =
    invoice.bas_registration_status === "awaiting_service_selection" ||
    invoice.bas_registration_status === "ready_to_register" ||
    invoice.bas_registration_status === "registered";
  const itemsSinCodigoValido = items.filter((item) => {
    const codigo = itemDrafts[item.id]?.bas_codigo_item;
    return !codigo || !basItemCodes.has(codigo);
  });
  const puedeConfirmar = validation.isValid && proveedorResuelto && itemsSinCodigoValido.length === 0;

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
      // Se queda en la factura recién confirmada (pedido explícito) en vez
      // de avanzar a la siguiente de la cola -- nextInvoiceId/prevInvoiceId
      // siguen existiendo para la navegación con flechas/atajos más abajo,
      // esto solo cambia el destino después de confirmar.
      router.push(`/invoices/${invoice.id}`);
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
        if (status !== "saving" && puedeConfirmar) handleConfirm();
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
  }, [status, nextInvoiceId, prevInvoiceId, puedeConfirmar]);

  const basProvider = invoice.expand?.bas_provider;

  return (
    <div className="flex h-full flex-col gap-6 overflow-y-auto px-7 py-5">
      {/* P0-G: proveedor en BAS -- ver reglas 1/2/3 del alcance (mostrar si
          se encontró, mostrar awaiting_provider_match, botón de recheck). */}
      <section className="rounded-xl bg-card p-6 shadow-(--shadow-1)">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <h2 className="text-[13px] font-semibold text-foreground">Proveedor en BAS</h2>
            <StatusBadge status={invoice.bas_registration_status || undefined} />
          </div>
          {!proveedorResuelto && <RecheckProviderButton processId={invoice.process_id} />}
        </div>
        {proveedorResuelto && basProvider ? (
          <p className="mt-2.5 flex items-center gap-1.5 text-[13px] text-foreground">
            <CheckCircle2 className="size-3.5 shrink-0 text-status-success-fg" />
            {basProvider.razon_social}{" "}
            <span className="font-mono text-muted-foreground">({basProvider.bas_codigo})</span>
          </p>
        ) : invoice.bas_registration_status === "awaiting_provider_match" ? (
          <p className="mt-2.5 flex items-start gap-1.5 text-[12.5px] text-status-warning-fg">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            El proveedor con CUIT {invoice.emisor_cuit} todavía no existe en BAS. Dalo de alta
            manualmente en BAS y presioná &quot;Volver a buscar en BAS&quot;.
          </p>
        ) : (
          <p className="mt-2.5 text-[12.5px] text-muted-foreground">
            Todavía no se resolvió el proveedor para esta factura.
          </p>
        )}
      </section>

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
          <NumberField label="Total" value={invoiceDraft.total} onChange={(v) => setInvoiceField("total", v)} error={validation.fieldErrors.total} />
          <NumberField label="Alícuota IVA (%)" value={invoiceDraft.iva_alicuota} onChange={(v) => setInvoiceField("iva_alicuota", v)} />
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
              <TableHead>Servicio/Ítem BAS</TableHead>
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
                        {uniqueCategories.map((cat) => (
                          <SelectItem key={cat.id} value={cat.categoria}>
                            {cat.categoria}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell className="p-1.5 align-middle">
                    {/* Única fuente válida de CodigoItem (P0-E/P0-G) --
                        poblado EXCLUSIVAMENTE desde bas_items
                        (activo+elegible_compras, filtrado server-side en
                        page.tsx), nunca una lista hardcodeada. Cualquier
                        valor elegido acá ya viene validado por
                        construcción -- el hook de PocketBase
                        (invoice_items.pb.js) además lo re-valida server-side
                        como segunda línea de defensa. */}
                    <Select
                      value={basItemCodes.has(draft.bas_codigo_item) ? draft.bas_codigo_item : ""}
                      onValueChange={(v) => setItemField(item.id, "bas_codigo_item", v)}
                    >
                      <SelectTrigger
                        className={cn(
                          "h-8 w-full min-w-[220px]",
                          !basItemCodes.has(draft.bas_codigo_item) &&
                            "border-status-warning-fg/60 text-status-warning-fg"
                        )}
                      >
                        <SelectValue placeholder="Elegir ítem…" />
                      </SelectTrigger>
                      <SelectContent>
                        {uniqueBasItems.map((basItem) => (
                          <SelectItem key={basItem.id} value={basItem.codigo}>
                            {basItem.descripcion}{" "}
                            <span className="font-mono text-muted-foreground">({basItem.codigo})</span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
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
              El Servicio/Ítem BAS es el que efectivamente se registra en BAS -- la categoría es
              solo informativa.
            </p>
            {validation.itemsSummaryError && (
              <p className="flex items-center gap-1.5 rounded-sm bg-status-destructive-bg px-2.5 py-1 text-xs font-medium text-status-destructive-fg">
                <AlertTriangle className="size-3.5 shrink-0" />
                {validation.itemsSummaryError}
              </p>
            )}
          </div>
        )}
        {itemsSinCodigoValido.length > 0 && (
          <p className="mt-2 flex items-start gap-1.5 rounded-sm bg-status-warning-bg px-2.5 py-2 text-xs font-medium text-status-warning-fg">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            {itemsSinCodigoValido.length === 1
              ? "Falta elegir un Servicio/Ítem BAS válido en 1 línea."
              : `Falta elegir un Servicio/Ítem BAS válido en ${itemsSinCodigoValido.length} líneas.`}
          </p>
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
            disabled={status === "saving" || !puedeConfirmar}
            title={
              !validation.isValid
                ? "Completá los datos obligatorios marcados arriba antes de confirmar."
                : !proveedorResuelto
                  ? "El proveedor todavía no existe en BAS -- dalo de alta y volvé a buscar antes de confirmar."
                  : itemsSinCodigoValido.length > 0
                    ? "Elegí un Servicio/Ítem BAS válido en todas las líneas antes de confirmar."
                    : undefined
            }
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
  error,
}: {
  label: string;
  value: number | null;
  onChange: (value: number) => void;
  error?: string | null;
}) {
  return (
    <Field label={label} error={error}>
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
