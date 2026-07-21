import { Check, CircleAlert, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MissionStepState, MissionStepView } from "@/lib/payment-order-mission";

export type { MissionStepState, MissionStepView } from "@/lib/payment-order-mission";

const ICON_WRAP: Record<MissionStepState, string> = {
  pending: "bg-status-neutral-bg text-status-neutral-fg",
  active: "bg-status-info-bg text-status-info-fg",
  done: "bg-status-success-bg text-status-success-fg",
  error: "bg-status-destructive-bg text-status-destructive-fg",
  skipped: "bg-status-neutral-bg text-status-neutral-fg opacity-40",
};

const LABEL_CLASS: Record<MissionStepState, string> = {
  pending: "text-muted-foreground",
  active: "text-foreground font-medium",
  done: "text-foreground",
  error: "text-status-destructive-fg font-medium",
  skipped: "text-muted-foreground opacity-50",
};

function StepIcon({ state }: { state: MissionStepState }) {
  if (state === "active") return <Loader2 className="size-3 animate-spin" />;
  if (state === "done") return <Check className="size-3" strokeWidth={3} />;
  if (state === "error") return <X className="size-3" strokeWidth={3} />;
  return <span className="size-1.5 rounded-full bg-current" />;
}

export default function PaymentOrderMission({
  steps,
  slowHint,
}: {
  steps: MissionStepView[];
  /** Aparece bajo el paso activo si viene tardando -- ver PaymentOrderPanel. */
  slowHint?: boolean;
}) {
  const total = steps.length;
  const done = steps.filter((s) => s.state === "done").length;
  const hasError = steps.some((s) => s.state === "error");
  const progressPct = Math.round((done / total) * 100);

  return (
    <div className="flex flex-col gap-3 border-t pt-3.5">
      <div className="h-1 w-full overflow-hidden rounded-full bg-status-neutral-bg">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-(--dur-slow) ease-(--ease-out-soft)",
            hasError ? "bg-status-destructive-fg" : "bg-status-info-fg"
          )}
          style={{ width: `${progressPct}%` }}
        />
      </div>

      <div className="flex flex-col">
        {steps.map((step, i) => {
          const isLast = i === steps.length - 1;
          const lineDone = step.state === "done" || (hasError && i < steps.findIndex((s) => s.state === "error"));
          const showDetail = (step.state === "active" || step.state === "error") && (step.detail || (step.state === "active" && slowHint));

          return (
            <div key={step.key} className="relative flex gap-3 pb-4 last:pb-0">
              {!isLast && (
                <span
                  className={cn(
                    "absolute top-5 bottom-0 left-[9px] w-px transition-colors duration-(--dur-medium)",
                    lineDone ? "bg-status-success-fg/40" : "bg-border"
                  )}
                />
              )}
              <span
                key={step.state}
                className={cn(
                  "animate-in zoom-in-75 fade-in flex size-5 shrink-0 items-center justify-center rounded-full duration-(--dur-fast)",
                  ICON_WRAP[step.state]
                )}
              >
                <StepIcon state={step.state} />
              </span>
              <div className="flex-1 pt-0.5">
                <p className={cn("text-[12.5px] leading-tight", LABEL_CLASS[step.state])}>{step.label}</p>
                {showDetail && (
                  <p
                    className={cn(
                      "animate-in fade-in mt-1.5 rounded-md px-2.5 py-2 font-mono text-[11.5px] leading-relaxed duration-(--dur-medium)",
                      step.state === "error"
                        ? "bg-status-destructive-bg text-status-destructive-fg"
                        : "bg-status-neutral-bg text-muted-foreground"
                    )}
                  >
                    {step.detail ?? "Esto puede tardar unos segundos más…"}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function ConnectionLostBanner({ text }: { text?: string | null }) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg bg-status-warning-bg px-3 py-2.5">
      <CircleAlert className="mt-0.5 size-4 shrink-0 text-status-warning-fg" />
      <div className="flex flex-col gap-1">
        <p className="text-[12.5px] font-medium text-status-warning-fg">
          No pudimos confirmar qué pasó
        </p>
        <p className="text-[11.5px] text-status-warning-fg/90">
          {text || "Se perdió la conexión con el backend antes de terminar."} Probá reintentar —
          si la orden ya se creó del lado de BAS, no se duplica.
        </p>
      </div>
    </div>
  );
}
