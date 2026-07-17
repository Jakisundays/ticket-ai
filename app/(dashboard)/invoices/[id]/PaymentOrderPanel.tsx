"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Loader2, CheckCircle, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import StatusBadge from "@/components/StatusBadge";
import type {
  BasPaymentMethodsRecord,
  MetodoPago,
  PaymentOrdersRecord,
} from "@/lib/pocketbase-types";
import { formatCurrency, formatDate } from "@/lib/format";

const METODO_LABEL: Record<MetodoPago, string> = {
  efectivo: "Efectivo",
  cheque: "Cheque",
  transferencia: "Transferencia",
};

export default function PaymentOrderPanel({
  processId,
  invoiceTotal,
  moneda,
  paymentMethods,
  existingOrder,
}: {
  processId: string;
  invoiceTotal: number;
  moneda: string;
  paymentMethods: BasPaymentMethodsRecord[];
  existingOrder: PaymentOrdersRecord | null;
}) {
  const router = useRouter();
  const [metodoPago, setMetodoPago] = useState<MetodoPago>(
    existingOrder?.metodo_pago ?? paymentMethods[0]?.metodo_pago ?? "efectivo"
  );
  const [monto, setMonto] = useState<number>(existingOrder?.monto ?? invoiceTotal);
  const [loading, setLoading] = useState(false);
  const [order, setOrder] = useState<PaymentOrdersRecord | null>(existingOrder);

  const selectedMethod = paymentMethods.find((m) => m.metodo_pago === metodoPago);
  const isRetry = order?.status === "failed";

  async function handleSubmit() {
    setLoading(true);
    try {
      const res = await fetch(`/api/payment-orders/${encodeURIComponent(processId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ metodo_pago: metodoPago, monto }),
      });
      const data = await res.json();
      if (data.payment_order) setOrder(data.payment_order);
      if (data.success) {
        toast.success("Orden de pago creada en BAS.");
      } else {
        toast.error(data.error || "BAS rechazó la orden de pago.");
      }
      router.refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo contactar al backend.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="flex flex-col gap-3.5 rounded-xl bg-card p-6 shadow-(--shadow-1)">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[13px] font-semibold text-foreground">Orden de pago</h2>
        <span className="text-xs text-muted-foreground">
          Total a pagar{" "}
          <span className="font-mono font-semibold text-foreground">
            {formatCurrency(invoiceTotal, moneda)}
          </span>
        </span>
      </div>

      {order && (
        <div className="flex flex-wrap items-center gap-2 border-t pt-3 text-sm">
          <span className="text-muted-foreground">Último intento</span>
          <StatusBadge status={order.status} />
          {order.last_attempt_at && (
            <span className="text-xs text-muted-foreground">
              {formatDate(order.last_attempt_at)}
            </span>
          )}
          {order.retry_count > 0 && (
            <span className="text-xs text-muted-foreground">
              · {order.retry_count} reintento{order.retry_count === 1 ? "" : "s"}
            </span>
          )}
        </div>
      )}

      {order?.status === "processing" && (
        <div className="flex items-center gap-2.5 rounded-lg bg-status-info-bg px-3 py-2.5">
          <Loader2 className="size-4 shrink-0 animate-spin text-status-info-fg" />
          <p className="text-[12.5px] text-status-info-fg">Enviando la orden a BAS…</p>
        </div>
      )}

      {order?.status === "failed" && order.bas_error && (
        <div className="flex flex-col gap-2 rounded-lg bg-status-warning-bg p-3.5">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-status-warning-fg">
            <Clock className="size-3.5 shrink-0" />
            BAS rechazó la orden de pago
          </div>
          <p className="rounded-md bg-background/50 px-2.5 py-2 font-mono text-[11.5px] leading-relaxed text-status-warning-fg">
            {order.bas_error}
          </p>
        </div>
      )}

      {order?.status === "success" && (
        <div className="flex items-center gap-2.5 rounded-lg bg-status-success-bg px-3 py-2.5">
          <CheckCircle className="size-4 shrink-0 text-status-success-fg" />
          <p className="text-[12.5px] text-status-success-fg">
            {order.bas_op_prefijo && order.bas_op_numero
              ? `OP ${order.bas_op_prefijo}-${order.bas_op_numero} creada en BAS.`
              : "Confirmada en BAS."}
          </p>
        </div>
      )}

      {paymentMethods.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Todavía no hay métodos de pago configurados. Andá a{" "}
          <a href="/payment-methods" className="text-primary hover:underline">
            Métodos de pago
          </a>{" "}
          para agregar uno.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 border-t pt-3.5 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs font-medium text-muted-foreground">
                Método de pago
              </Label>
              <Select value={metodoPago} onValueChange={(v) => setMetodoPago(v as MetodoPago)}>
                <SelectTrigger className="h-9 w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {paymentMethods.map((m) => (
                    <SelectItem key={m.id} value={m.metodo_pago}>
                      {METODO_LABEL[m.metodo_pago] ?? m.metodo_pago}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {selectedMethod && !selectedMethod.confirmado && (
                <p className="text-xs text-status-warning-fg">
                  Código todavía no confirmado con un pago real en BAS — puede fallar.
                </p>
              )}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs font-medium text-muted-foreground">Monto</Label>
              <Input
                type="number"
                className="h-9 font-mono"
                value={monto}
                onChange={(e) => setMonto(Number(e.target.value))}
              />
            </div>
          </div>

          <Button onClick={handleSubmit} disabled={loading} className="mt-1 h-9 gap-2">
            {loading && <Loader2 className="size-3.5 animate-spin" />}
            {loading ? "Creando…" : isRetry ? "Reintentar" : "Crear orden de pago"}
          </Button>
        </>
      )}
    </section>
  );
}
