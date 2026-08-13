"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { CheckCircle, Loader2, AlertTriangle, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import StatusBadge from "@/components/StatusBadge";
import FriendlyErrorDetail from "@/components/FriendlyErrorDetail";
import { getFriendlyBasError } from "@/lib/bas-error-messages";
import { formatCurrency, formatDate } from "@/lib/format";
import type { BasProcessingStatusRecord, BasRegistrationStatus } from "@/lib/pocketbase-types";

/**
 * Reemplaza a PaymentOrderPanel (P0-G, 2026-08-12) -- el nuevo alcance
 * termina el flujo automatizado al registrar el ComprobanteCompra en BAS,
 * SIN Orden de Pago, SIN método de pago, SIN banco (la cooperativa/contable
 * crea la Orden de Pago directamente desde BAS). A propósito NO reusa la
 * coreografía de "misión" multi-paso de PaymentOrderPanel/PaymentOrderMission
 * (buscar factura -> crear OP -> aplicar): ese es un flujo de VARIOS pasos
 * visibles porque la Orden de Pago real lo es. Acá el backend hace su
 * propio trabajo interno (buscar-o-registrar + verificar, P0-F) pero desde
 * el frontend es UNA sola llamada -- mostrar 3 pasos falsos sería forzar un
 * patrón que no corresponde a esta acción. Ver
 * app/api/invoices/[processId]/register-comprobante/route.ts.
 *
 * El botón de registrar se muestra tanto en "ready_to_register" como en
 * "awaiting_service_selection" (fix de un hallazgo crítico de la revisión
 * final de P0-G, 2026-08-12): "ready_to_register" solo lo escribe el pase
 * automático de la ingesta, una sola vez -- nunca se re-evalúa después de
 * que un humano corrige manualmente el Servicio/Ítem en el selector de
 * InvoiceReviewForm. El backend (registrar_comprobante en Invoicy) no
 * depende de ese estado guardado: re-resuelve y valida proveedor + cada
 * ítem en fresco en cada llamada, así que dejarlo disparar también desde
 * "awaiting_service_selection" es seguro -- si de verdad falta algo, el
 * endpoint responde 422 con el motivo y el estado se actualiza solo.
 *
 * "Ver respuesta técnica" (2026-08-13): antes del reemplazo de
 * PaymentOrderPanel se podía ver el JSON crudo de cada paso de la Orden de
 * Pago -- acá no hay pasos que mostrar (ver arriba), pero sí se perdió la
 * visibilidad de "qué respondió BAS" en general. Se guarda el body
 * COMPLETO de la respuesta de register-comprobante (éxito, error o
 * already_resolved) en `lastResponse` y se muestra colapsado, mismo patrón
 * ya usado en PaymentOrderMission ("Ver detalle técnico"). Solo existe
 * para llamadas hechas EN VIVO durante esta sesión del navegador -- el
 * body crudo de BAS nunca se persiste en PocketBase (bas_processing_status
 * solo guarda campos estructurados), así que un registro ya hecho en una
 * sesión anterior no tiene un JSON técnico que mostrar al recargar.
 */
export default function ComprobanteRegistrationPanel({
  processId,
  basRegistrationStatus,
  basProcessingStatus,
  moneda,
}: {
  processId: string;
  basRegistrationStatus: BasRegistrationStatus;
  basProcessingStatus: BasProcessingStatusRecord | null;
  moneda: string;
}) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [lastError, setLastError] = useState<string | null>(
    basRegistrationStatus === "register_failed" ? basProcessingStatus?.bas_last_error || null : null
  );
  // Body completo de la última respuesta EN VIVO de register-comprobante --
  // ver docstring de arriba. null hasta que se haga una llamada real en
  // esta sesión (nunca se hidrata desde props: ese dato no se persiste).
  const [lastResponse, setLastResponse] = useState<Record<string, unknown> | null>(null);

  async function handleRegister() {
    setLoading(true);
    setLastError(null);
    try {
      const res = await fetch(
        `/api/invoices/${encodeURIComponent(processId)}/register-comprobante`,
        { method: "POST" }
      );
      const data = await res.json().catch(() => ({}));
      setLastResponse(data);
      if (!res.ok || !data.success) {
        const detail = data.error || data.detail?.mensaje || data.detail || "No se pudo registrar el comprobante.";
        setLastError(typeof detail === "string" ? detail : JSON.stringify(detail));
        toast.error("BAS rechazó el registro del comprobante.");
      } else if (data.already_resolved) {
        toast.message("El comprobante ya estaba registrado; no se reintenta.");
      } else {
        toast.success("Comprobante registrado en BAS.");
      }
      router.refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : "No se pudo contactar al backend.";
      setLastError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }

  const friendlyError = getFriendlyBasError(lastError);

  return (
    <section className="flex flex-col gap-3.5 rounded-xl bg-card p-6 shadow-(--shadow-1)">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[13px] font-semibold text-foreground">Registro en BAS</h2>
        {/* Mientras loading=true, el badge pasa a "Procesando" -- basRegistrationStatus
            queda desactualizado hasta que router.refresh() vuelva a traer el
            estado real, así que mostrar el viejo acá confundiría más de lo
            que ayuda. Es lo primero que se ve del panel, de un vistazo. */}
        <StatusBadge status={loading ? "processing" : basRegistrationStatus || undefined} />
      </div>

      {basRegistrationStatus === "registered" && (
        <div className="flex flex-col gap-2 border-t pt-3.5">
          <div className="animate-scale-in flex items-center gap-2.5 rounded-lg bg-status-success-bg px-3 py-2.5">
            <CheckCircle className="size-4 shrink-0 text-status-success-fg" />
            <p className="text-[12.5px] text-status-success-fg">
              {basProcessingStatus?.comprobante_prefijo && basProcessingStatus?.comprobante_numero
                ? `Comprobante ${basProcessingStatus.comprobante_prefijo}-${basProcessingStatus.comprobante_numero} registrado en BAS.`
                : "Comprobante registrado en BAS."}
            </p>
          </div>
          <dl className="grid grid-cols-1 gap-2 text-[12.5px] sm:grid-cols-2">
            {basProcessingStatus?.bas_id_transaccion != null && (
              <Field label="IdTransaccion">
                <span className="font-mono">{basProcessingStatus.bas_id_transaccion}</span>
              </Field>
            )}
            {basProcessingStatus?.comprobante_total_registrado != null && (
              <Field label="Total registrado">
                <span className="font-mono">
                  {formatCurrency(basProcessingStatus.comprobante_total_registrado, moneda)}
                </span>
              </Field>
            )}
            {basProcessingStatus?.comprobante_registrado_at && (
              <Field label="Registrado el">{formatDate(basProcessingStatus.comprobante_registrado_at)}</Field>
            )}
          </dl>
        </div>
      )}

      {basRegistrationStatus === "register_failed" && (
        <div className="flex flex-col gap-3 border-t pt-3.5">
          <FriendlyErrorDetail
            error={friendlyError}
            className="rounded-lg bg-status-destructive-bg px-3 py-2.5 text-[12.5px] text-status-destructive-fg"
          />
          <Button onClick={handleRegister} disabled={loading} className="h-9 gap-2">
            {loading && <Loader2 className="size-3.5 animate-spin" />}
            {loading ? "Reintentando…" : "Reintentar registro"}
          </Button>
        </div>
      )}

      {(basRegistrationStatus === "ready_to_register" ||
        basRegistrationStatus === "awaiting_service_selection") && (
        <div className="flex flex-col gap-3 border-t pt-3.5">
          <p className="text-[12.5px] text-muted-foreground">
            {basRegistrationStatus === "ready_to_register"
              ? "Proveedor e ítems ya resueltos -- lista para registrarse en BAS."
              : "Proveedor resuelto y factura confirmada -- al registrar, el backend vuelve a validar el proveedor y cada ítem contra BAS antes de enviar nada."}
          </p>
          <Button onClick={handleRegister} disabled={loading} className="h-9 gap-2">
            {loading && <Loader2 className="size-3.5 animate-spin" />}
            {loading ? "Registrando…" : "Registrar comprobante en BAS"}
          </Button>
        </div>
      )}

      {(basRegistrationStatus === "awaiting_provider_match" || !basRegistrationStatus) && (
        <div className="flex items-start gap-2.5 rounded-lg bg-status-warning-bg px-3 py-2.5">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-status-warning-fg" />
          <p className="text-[12.5px] text-status-warning-fg">
            {basRegistrationStatus === "awaiting_provider_match"
              ? "Todavía falta que el proveedor exista en BAS."
              : "Todavía no se resolvió el proveedor ni los ítems para esta factura."}{" "}
            Reabrí la factura para resolverlo antes de poder registrar el comprobante.
          </p>
        </div>
      )}

      {basProcessingStatus?.last_attempt_at && basRegistrationStatus !== "registered" && (
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Clock className="size-3 shrink-0" />
          Último intento: {formatDate(basProcessingStatus.last_attempt_at)}
        </p>
      )}

      {lastResponse && (
        <details className="border-t pt-2.5">
          <summary className="cursor-pointer text-[11px] font-medium text-muted-foreground underline decoration-dotted underline-offset-2 select-none">
            Ver respuesta técnica
          </summary>
          <pre className="mt-2 max-h-80 max-w-full overflow-auto rounded-md bg-status-neutral-bg p-2.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-muted-foreground">
            {JSON.stringify(lastResponse, null, 2)}
          </pre>
        </details>
      )}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[10.5px] font-medium text-muted-foreground uppercase tracking-wide">{label}</dt>
      <dd className="mt-0.5 font-medium text-foreground">{children}</dd>
    </div>
  );
}
