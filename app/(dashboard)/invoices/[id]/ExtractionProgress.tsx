"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { getPocketBase } from "@/lib/pocketbase-browser";
import { Collections, type InvoicesRecord } from "@/lib/pocketbase-types";

/** Cuántos intentos hace Invoicy antes de rendirse -- ver
 * Invoicy/routes/process_invoice_google_2.py, InvoiceOrchestrator.tool_handler
 * (max_retries=6). Mantenido a mano en sync con ese valor, no hay un
 * endpoint que lo exponga. */
const MAX_EXTRACTION_ATTEMPTS = 6;

/**
 * Vive solo mientras invoice.status === "processing" (ver page.tsx). Se
 * suscribe en vivo (PocketBase realtime, no polling) al mismo record para
 * mostrar en qué intento de extracción va, y dispara router.refresh() en
 * cuanto el status cambia -- eso hace que el Server Component vuelva a
 * renderizar y el usuario caiga en la rama correcta (needs_review si
 * terminó bien, error si se agotaron los reintentos) sin recargar la
 * página a mano.
 */
export default function ExtractionProgress({
  invoiceId,
  initialAttempt,
}: {
  invoiceId: string;
  initialAttempt: number;
}) {
  const router = useRouter();
  const [attempt, setAttempt] = useState(initialAttempt);

  useEffect(() => {
    const pb = getPocketBase();
    let isMounted = true;

    pb.collection<InvoicesRecord>(Collections.Invoices)
      .subscribe(invoiceId, (e) => {
        if (!isMounted) return;
        if (e.record.status !== "processing") {
          router.refresh();
          return;
        }
        setAttempt(e.record.extraction_attempt || 1);
      })
      .catch(() => {
        // Best-effort: si la suscripción realtime no engancha (red rara,
        // el token de auth vencido en el medio, etc.) el usuario todavía
        // puede refrescar a mano -- no hay un estado de error propio acá
        // a propósito, para no sumar ruido sobre un simple "está
        // procesando".
      });

    return () => {
      isMounted = false;
      pb.collection(Collections.Invoices).unsubscribe(invoiceId);
    };
  }, [invoiceId, router]);

  const isRetrying = attempt > 1;

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 px-7 py-5 text-center">
      <span className="flex size-11 items-center justify-center rounded-full bg-status-info-bg text-status-info-fg">
        <Loader2 className="size-5 animate-spin" />
      </span>
      <div className="flex flex-col gap-1.5">
        <p className="text-[15px] font-semibold text-foreground">
          {isRetrying ? "Reintentando la extracción…" : "Extrayendo los datos de la factura…"}
        </p>
        <p className="max-w-sm text-[13.5px] text-muted-foreground">
          {isRetrying
            ? `Intento ${attempt} de ${MAX_EXTRACTION_ATTEMPTS}. Esto puede tardar un rato — no hace falta que hagas nada, la página se va a actualizar sola.`
            : "Puede tardar hasta un par de minutos, sobre todo si es la primera vez que vemos a este proveedor. La página se actualiza sola cuando termina."}
        </p>
      </div>
    </div>
  );
}
