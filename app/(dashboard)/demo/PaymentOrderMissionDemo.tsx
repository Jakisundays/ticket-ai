"use client";

import { useState } from "react";
import Link from "next/link";
import { CheckCircle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import StatusBadge from "@/components/StatusBadge";
import PaymentOrderMission, { ConnectionLostBanner } from "../invoices/[id]/PaymentOrderMission";
import { computeMissionSteps } from "@/lib/payment-order-mission";
import { useMissionChoreography } from "@/hooks/use-mission-choreography";
import { formatCurrency } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Escenarios con el MISMO shape que devuelve de verdad
 * POST /api/payment-orders/[processId] (ver app/api/payment-orders/[processId]/route.ts
 * y, del lado de Invoicy, routes/process_invoice_google_2.py:crear_orden_pago). Alimentan
 * el mismo inferMissionOutcome() que usa producción -- no es una simulación aparte del
 * checklist, es el checklist real corriendo con una respuesta enlatada en vez de un fetch.
 */
const SCENARIOS: {
  key: string;
  title: string;
  resolvedTag?: string;
  delayMs: number;
  status: number;
  data: unknown;
}[] = [
  {
    key: "success",
    title: "Éxito",
    delayMs: 3900,
    status: 200,
    data: { success: true, error: null },
  },
  {
    key: "cuenta0",
    title: "Cuenta 0",
    resolvedTag: "RESUELTO 18-JUL",
    delayMs: 3000,
    status: 200,
    data: {
      success: false,
      error:
        "BasApiError 409 en /api/ComprobantesCompra: No se pudo establecer la moneda correspondiente a la cuenta 0. (SP_ICR_VALIDA_CODTAB)(SP_ICR_COMPROB_COMPRA) — [histórico: SUPERCOO ya tiene CuentasCorrientes configurada desde el 2026-07-18, este error no debería volver a aparecer para este proveedor]",
    },
  },
  {
    key: "verificacionFallo",
    title: "Registro sin confirmar",
    delayMs: 3300,
    status: 200,
    data: {
      success: false,
      error:
        "BasApiError 409 en /api/ConsultaComprobantesExternos: El comprobante MA 0001-00012345 se registró (POST 201) pero la verificación posterior (GET /api/ConsultaComprobantes) no lo encontró -- no se creó la orden de pago para evitar aplicarla contra una factura que podría no existir.",
    },
  },
  {
    key: "noConfirmada",
    title: "Factura no confirmada",
    delayMs: 700,
    status: 409,
    data: { detail: "La factura todavía no fue confirmada." },
  },
  {
    key: "ordenFallo",
    title: "Falla la orden de pago",
    delayMs: 4200,
    status: 200,
    data: {
      success: false,
      error:
        "Orden de pago falló: El comprobante MA 0001-00012345 no existe para aplicarlo. (SP_ICR_COMPROB_APL)(SP_ICR_COMPROB_CAJA)",
    },
  },
  {
    key: "conexion",
    title: "Conexión perdida",
    delayMs: 2600,
    status: 502,
    data: { error: "Failed to fetch" },
  },
];

const MOCK_INVOICE = {
  proveedor: "SUPERCOOP",
  cuit: "30-52570593-1",
  comprobante: "MA 0001-00012345",
  fecha: "15/07/2026",
  items: [
    { qty: "2 x", label: "Nafta Súper 95 (20 L c/u)", amount: 45000 },
    { qty: "1 x", label: "Aceite lubricante 4L", amount: 12500 },
  ],
  gravado: 57500,
  iva: 12075,
  total: 69575,
  cae: "75083266482093",
  caeVto: "25/07/2026",
};

export default function PaymentOrderMissionDemo() {
  const [scenarioKey, setScenarioKey] = useState(SCENARIOS[0].key);
  const { loading, liveStepIndex, slowHint, lastOutcome, run } = useMissionChoreography();

  const scenario = SCENARIOS.find((s) => s.key === scenarioKey) ?? SCENARIOS[0];
  const stepViews = computeMissionSteps(loading, liveStepIndex, loading ? null : lastOutcome);
  const showConnectionLost =
    !loading && lastOutcome !== null && !lastOutcome.success && lastOutcome.failedStepIndex === null;
  const isRetry = lastOutcome !== null && !lastOutcome.success;

  async function handleRun() {
    await run(
      () =>
        new Promise((resolve) =>
          setTimeout(() => resolve({ status: scenario.status, data: scenario.data }), scenario.delayMs)
        )
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Factura (datos de ejemplo)</CardTitle>
          <StatusBadge status="confirmed" />
        </CardHeader>
        <CardContent className="flex flex-col gap-3.5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex flex-col gap-0.5">
              <span className="text-[10.5px] tracking-[0.04em] text-muted-foreground uppercase">
                Proveedor
              </span>
              <span className="text-sm font-semibold text-foreground">{MOCK_INVOICE.proveedor}</span>
              <span className="text-xs text-muted-foreground">CUIT {MOCK_INVOICE.cuit}</span>
            </div>
            <div className="flex flex-col items-end gap-0.5 text-right">
              <span className="text-[10.5px] tracking-[0.04em] text-muted-foreground uppercase">
                Comprobante
              </span>
              <span className="font-mono text-sm font-semibold text-foreground">
                {MOCK_INVOICE.comprobante}
              </span>
              <span className="text-xs text-muted-foreground">{MOCK_INVOICE.fecha}</span>
            </div>
          </div>

          <div className="flex flex-col gap-1.5 border-t pt-3">
            {MOCK_INVOICE.items.map((item) => (
              <div key={item.label} className="flex justify-between gap-3 text-[12.5px] text-muted-foreground">
                <span>
                  <span className="mr-1.5 text-muted-foreground/70">{item.qty}</span>
                  {item.label}
                </span>
                <span className="font-mono text-foreground">{formatCurrency(item.amount, "ARS")}</span>
              </div>
            ))}
          </div>

          <div className="flex flex-col gap-1 border-t pt-2.5 text-xs">
            <div className="flex justify-between text-muted-foreground">
              <span>Gravado</span>
              <span className="font-mono">{formatCurrency(MOCK_INVOICE.gravado, "ARS")}</span>
            </div>
            <div className="flex justify-between text-muted-foreground">
              <span>IVA 21%</span>
              <span className="font-mono">{formatCurrency(MOCK_INVOICE.iva, "ARS")}</span>
            </div>
            <div className="flex justify-between text-sm font-semibold text-foreground">
              <span>Total</span>
              <span className="font-mono">{formatCurrency(MOCK_INVOICE.total, "ARS")}</span>
            </div>
          </div>

          <div className="flex justify-between border-t pt-2.5 font-mono text-[10.5px] text-muted-foreground/80">
            <span>CAE {MOCK_INVOICE.cae}</span>
            <span>Vto. {MOCK_INVOICE.caeVto}</span>
          </div>
        </CardContent>
      </Card>

      <section className="flex flex-col gap-3.5 rounded-xl bg-card p-6 shadow-(--shadow-1)">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-[13px] font-semibold text-foreground">Orden de pago</h2>
          <span className="text-xs text-muted-foreground">
            Total a pagar{" "}
            <span className="font-mono font-semibold text-foreground">
              {formatCurrency(MOCK_INVOICE.total, "ARS")}
            </span>
          </span>
        </div>

        {stepViews && <PaymentOrderMission steps={stepViews} slowHint={loading && slowHint} />}

        {showConnectionLost && <ConnectionLostBanner text={lastOutcome?.detailText} />}

        {!loading && lastOutcome?.success && (
          <div className="animate-scale-in flex items-center gap-2.5 rounded-lg bg-status-success-bg px-3 py-2.5">
            <CheckCircle className="size-4 shrink-0 text-status-success-fg" />
            <p className="text-[12.5px] text-status-success-fg">OP 0001-00089 creada en BAS.</p>
          </div>
        )}

        <Button onClick={handleRun} disabled={loading} className="mt-1 h-9 gap-2 border-t">
          {loading && <Loader2 className="size-3.5 animate-spin" />}
          {loading ? "Creando…" : isRetry ? "Reintentar" : "Crear orden de pago"}
        </Button>
      </section>

      <Card>
        <CardHeader>
          <CardTitle className="text-[11px] font-semibold tracking-[0.06em] text-muted-foreground uppercase">
            Simular resultado
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {SCENARIOS.map((s) => (
              <button
                key={s.key}
                type="button"
                onClick={() => setScenarioKey(s.key)}
                disabled={loading}
                className={cn(
                  "rounded-lg border px-3 py-2 text-left text-[11.5px] transition-colors",
                  scenarioKey === s.key
                    ? "border-status-info-fg bg-status-info-bg text-foreground"
                    : "border-border text-muted-foreground hover:text-foreground"
                )}
              >
                {s.title}
                {s.resolvedTag && (
                  <span className="ml-1.5 text-[9.5px] font-bold tracking-[0.03em] text-status-success-fg uppercase">
                    ✓ {s.resolvedTag}
                  </span>
                )}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      <p className="text-xs leading-relaxed text-muted-foreground">
        Esta página corre el <b className="font-medium text-foreground">mismo</b>{" "}
        <code className="font-mono text-[11px]">PaymentOrderMission</code> y el mismo hook{" "}
        <code className="font-mono text-[11px]">useMissionChoreography</code> que el botón real de{" "}
        <Link href="/invoices" className="text-primary hover:underline">
          Facturas
        </Link>
        . El selector de arriba reemplaza el <code className="font-mono text-[11px]">fetch</code>{" "}
        real por una respuesta enlatada con el mismo formato que devuelve{" "}
        <code className="font-mono text-[11px]">POST /api/payment-orders/[processId]</code> —
        la lógica que decide en qué paso se cortó cada error es la de producción, no una copia.
      </p>
    </div>
  );
}
