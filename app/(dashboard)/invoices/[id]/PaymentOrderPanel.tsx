"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
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
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import type {
  BasPaymentMethodsRecord,
  MetodoPago,
  PaymentOrdersRecord,
} from "@/lib/pocketbase-types";
import { formatDate } from "@/lib/format";

const METODO_LABEL: Record<MetodoPago, string> = {
  efectivo: "Efectivo",
  cheque: "Cheque",
  transferencia: "Transferencia",
};

const STATUS_LABEL: Record<string, string> = {
  processing: "En proceso",
  success: "Creada",
  failed: "Falló",
};

const STATUS_VARIANT: Record<string, "default" | "destructive" | "secondary"> = {
  processing: "secondary",
  success: "default",
  failed: "destructive",
};

export default function PaymentOrderPanel({
  processId,
  invoiceTotal,
  paymentMethods,
  existingOrder,
}: {
  processId: string;
  invoiceTotal: number;
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
    <section className="rounded-lg border border-gray-200 bg-white p-4">
      <h2 className="mb-3 text-sm font-semibold text-gray-900">Orden de pago</h2>

      {order && (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-gray-500">Último intento:</span>
          <Badge variant={STATUS_VARIANT[order.status] ?? "secondary"}>
            {STATUS_LABEL[order.status] ?? order.status}
          </Badge>
          {order.last_attempt_at && (
            <span className="text-gray-400">{formatDate(order.last_attempt_at)}</span>
          )}
          {order.retry_count > 0 && (
            <span className="text-gray-400">
              · {order.retry_count} reintento{order.retry_count === 1 ? "" : "s"}
            </span>
          )}
        </div>
      )}

      {order?.status === "failed" && order.bas_error && (
        <Alert variant="destructive" className="mb-4">
          <AlertTitle>BAS rechazó la orden de pago</AlertTitle>
          <AlertDescription>{order.bas_error}</AlertDescription>
        </Alert>
      )}

      {order?.status === "success" && (
        <Alert className="mb-4">
          <AlertTitle>Orden de pago creada</AlertTitle>
          <AlertDescription>
            {order.bas_op_prefijo && order.bas_op_numero
              ? `OP ${order.bas_op_prefijo}-${order.bas_op_numero}`
              : "Confirmada en BAS."}
          </AlertDescription>
        </Alert>
      )}

      {paymentMethods.length === 0 ? (
        <p className="text-sm text-gray-400">
          Todavía no hay métodos de pago configurados. Andá a{" "}
          <a href="/payment-methods" className="text-blue-600 hover:underline">
            Métodos de pago
          </a>{" "}
          para agregar uno.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <Label className="mb-1.5 block text-xs font-medium text-gray-500">
                Método de pago
              </Label>
              <Select value={metodoPago} onValueChange={(v) => setMetodoPago(v as MetodoPago)}>
                <SelectTrigger className="w-full">
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
                <p className="mt-1 text-xs text-amber-600">
                  Código todavía no confirmado con un pago real en BAS — puede fallar.
                </p>
              )}
            </div>
            <div>
              <Label className="mb-1.5 block text-xs font-medium text-gray-500">Monto</Label>
              <Input
                type="number"
                value={monto}
                onChange={(e) => setMonto(Number(e.target.value))}
              />
            </div>
          </div>

          <Button onClick={handleSubmit} disabled={loading} className="mt-4">
            {loading ? "Creando…" : order?.status === "failed" ? "Reintentar" : "Crear orden de pago"}
          </Button>
        </>
      )}
    </section>
  );
}
