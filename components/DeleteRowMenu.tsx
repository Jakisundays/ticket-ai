"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

type DeleteEndpoint = "invoice" | "payment-order";

/**
 * Menú "..." con la acción "Eliminar" para una fila de Cola de revisión,
 * Facturas u Órdenes de pago. Nunca hace un borrado físico -- pega a
 * DELETE /api/invoices/[processId] o /api/payment-orders/[processId], que
 * proxean a los endpoints de soft-delete de Invoicy (ver
 * routes/process_invoice_google_2.py: eliminar_invoice/eliminar_payment_order).
 *
 * Botón directo (no dropdown): la única acción disponible acá es "Eliminar"
 * -- ver detalle ya lo resuelve el click en la fila/link de cada tabla, y
 * este dominio no tiene un "editar"/"duplicar" genérico que justifique un
 * menú. Un dropdown de un solo ítem solo agregaba un click de más.
 *
 * `requireReason`: variante reforzada para una Orden de Pago con
 * status="success" (ya real en BAS, ver docstring de eliminar_payment_order
 * en Invoicy) -- exige un motivo no vacío Y tipear `confirmText` (el número
 * de comprobante) antes de habilitar "Eliminar". El resto de los casos usa
 * la confirmación simple de siempre (mismo patrón que ReopenButton.tsx).
 */
export default function DeleteRowMenu({
  processId,
  endpoint,
  requireReason = false,
  confirmText,
  itemLabel = "este registro",
  className,
}: {
  processId: string;
  endpoint: DeleteEndpoint;
  requireReason?: boolean;
  confirmText?: string;
  itemLabel?: string;
  className?: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [reason, setReason] = useState("");
  const [typedConfirm, setTypedConfirm] = useState("");

  const apiPath =
    endpoint === "invoice"
      ? `/api/invoices/${encodeURIComponent(processId)}`
      : `/api/payment-orders/${encodeURIComponent(processId)}`;

  const puedeConfirmar =
    !loading && (!requireReason || (reason.trim().length > 0 && typedConfirm === confirmText));

  async function handleDelete() {
    setLoading(true);
    try {
      const res = await fetch(apiPath, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: reason.trim() || undefined }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.error || data?.detail || "No se pudo eliminar.");
      }
      setOpen(false);
      setReason("");
      setTypedConfirm("");
      router.refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo eliminar.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={className} onClick={(e) => e.stopPropagation()}>
      <Button
        type="button"
        variant="ghost"
        className="size-8 justify-center p-0 text-muted-foreground hover:bg-status-destructive-bg hover:text-status-destructive-fg"
        aria-label="Eliminar"
        onClick={() => setOpen(true)}
      >
        <Trash2 className="size-4" />
      </Button>

      <AlertDialog open={open} onOpenChange={(next) => !loading && setOpen(next)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {requireReason ? "Esta Orden de Pago ya se aplicó en BAS" : `¿Eliminar ${itemLabel}?`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {requireReason
                ? "El comprobante y la plata ya son reales del lado de BAS -- eliminar acá solo oculta el registro en este sistema, no revierte nada en BAS. Escribí el motivo y confirmá para continuar."
                : "El registro queda archivado (no se borra físicamente) y deja de aparecer en las listas."}
            </AlertDialogDescription>
          </AlertDialogHeader>

          {requireReason && (
            <div className="flex flex-col gap-3 py-1">
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-muted-foreground">
                  Motivo de la eliminación
                </label>
                <Textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Ej: factura duplicada, se cargó dos veces por error"
                  disabled={loading}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-muted-foreground">
                  Escribí <span className="font-mono text-foreground">{confirmText}</span> para
                  confirmar
                </label>
                <Input
                  value={typedConfirm}
                  onChange={(e) => setTypedConfirm(e.target.value)}
                  disabled={loading}
                  autoComplete="off"
                />
              </div>
            </div>
          )}

          <AlertDialogFooter>
            <AlertDialogCancel disabled={loading}>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                handleDelete();
              }}
              disabled={!puedeConfirmar}
            >
              {loading ? "Eliminando…" : "Eliminar"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
