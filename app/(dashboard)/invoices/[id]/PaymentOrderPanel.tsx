"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { CheckCircle, Loader2 } from "lucide-react";
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
import PaymentOrderMission, {
  ConnectionLostBanner,
  type MissionStepView,
} from "./PaymentOrderMission";
import {
  MISSION_STEPS,
  inferMissionOutcome,
  type MissionOutcome,
} from "@/lib/payment-order-mission";
import type {
  BasPaymentMethodsRecord,
  MetodoPago,
  PaymentOrdersRecord,
} from "@/lib/pocketbase-types";
import { formatCurrency, formatDate } from "@/lib/format";

/** Cada paso "coreografiado" avanza cada 750ms mientras esperamos la única
 * respuesta real del backend -- nunca completa el ÚLTIMO paso por timer, solo
 * la respuesta real lo hace (ver handleSubmit). */
const STEP_ADVANCE_MS = 750;
/** Si el paso activo lleva más que esto sin resolver, mostramos un hint de
 * "puede tardar" en vez de dejarlo mudo. */
const SLOW_HINT_MS = 4000;

function outcomeFromOrder(order: PaymentOrdersRecord | null): MissionOutcome | null {
  if (!order || order.status === "processing") return null;
  return inferMissionOutcome(200, { success: order.status === "success", error: order.bas_error || undefined });
}

function stepViewsFor(
  loading: boolean,
  liveStepIndex: number,
  outcome: MissionOutcome | null
): MissionStepView[] | null {
  if (loading) {
    return MISSION_STEPS.map(
      (step, i): MissionStepView => ({
        ...step,
        state: i < liveStepIndex ? "done" : i === liveStepIndex ? "active" : "pending",
      })
    );
  }
  if (!outcome || (!outcome.success && outcome.failedStepIndex === null)) return null;
  return MISSION_STEPS.map((step, i): MissionStepView => {
    if (outcome.success) return { ...step, state: "done" };
    const failedAt = outcome.failedStepIndex as number;
    if (i < failedAt) return { ...step, state: "done" };
    if (i === failedAt) return { ...step, state: "error", detail: outcome.detailText };
    return { ...step, state: "skipped" };
  });
}

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
  const [liveStepIndex, setLiveStepIndex] = useState(0);
  const [slowHint, setSlowHint] = useState(false);
  const [lastOutcome, setLastOutcome] = useState<MissionOutcome | null>(outcomeFromOrder(existingOrder));
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  function clearTimers() {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }
  useEffect(() => () => clearTimers(), []);

  const selectedMethod = paymentMethods.find((m) => m.metodo_pago === metodoPago);
  const isStaleProcessing = order?.status === "processing" && !loading;
  const isRetry = order?.status === "failed" || (lastOutcome !== null && !lastOutcome.success) || isStaleProcessing;
  const stepViews = stepViewsFor(loading, liveStepIndex, loading ? null : lastOutcome);
  const showConnectionLost = !loading && lastOutcome !== null && !lastOutcome.success && lastOutcome.failedStepIndex === null;

  async function handleSubmit() {
    clearTimers();
    setLoading(true);
    setSlowHint(false);
    setLiveStepIndex(0);
    setLastOutcome(null);

    // Coreografía optimista: avanza un paso genuino cada STEP_ADVANCE_MS
    // mientras esperamos la única respuesta del backend. Se detiene en el
    // anteúltimo paso a propósito -- "Confirmando en BAS" solo lo completa
    // la respuesta real (ver más abajo), nunca un timer.
    for (let i = 1; i <= MISSION_STEPS.length - 2; i++) {
      timers.current.push(setTimeout(() => setLiveStepIndex(i), i * STEP_ADVANCE_MS));
    }
    timers.current.push(setTimeout(() => setSlowHint(true), SLOW_HINT_MS));

    try {
      const res = await fetch(`/api/payment-orders/${encodeURIComponent(processId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ metodo_pago: metodoPago, monto }),
      });
      const data = await res.json().catch(() => ({}));
      clearTimers();
      if (data.payment_order) setOrder(data.payment_order);
      const outcome = inferMissionOutcome(res.status, data);
      setLastOutcome(outcome);
      if (outcome.success) {
        toast.success("Orden de pago creada en BAS.");
      } else {
        // El detalle completo (a veces un blob JSON largo de BAS) ya se
        // muestra inline en el paso que falló -- ver PaymentOrderMission.
        // Repetirlo acá infla el toast (llegó a medir 346px de alto en una
        // prueba real) y termina tapando el panel entero.
        toast.error("BAS rechazó la orden de pago.");
      }
      router.refresh();
    } catch (error) {
      clearTimers();
      setLastOutcome({
        success: false,
        failedStepIndex: null,
        detailText: error instanceof Error ? error.message : "No se pudo contactar al backend.",
      });
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

      {isStaleProcessing && (
        <div className="flex items-center gap-2.5 rounded-lg bg-status-neutral-bg px-3 py-2.5">
          <Loader2 className="size-4 shrink-0 text-status-neutral-fg" />
          <p className="text-[12.5px] text-status-neutral-fg">
            El último intento quedó &quot;procesando&quot; — probablemente se cortó antes de
            terminar. Reintentá.
          </p>
        </div>
      )}

      {stepViews && <PaymentOrderMission steps={stepViews} slowHint={loading && slowHint} />}

      {showConnectionLost && <ConnectionLostBanner text={lastOutcome?.detailText} />}

      {!loading && lastOutcome?.success && (
        <div className="animate-scale-in flex items-center gap-2.5 rounded-lg bg-status-success-bg px-3 py-2.5">
          <CheckCircle className="size-4 shrink-0 text-status-success-fg" />
          <p className="text-[12.5px] text-status-success-fg">
            {order?.bas_op_prefijo && order?.bas_op_numero
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
