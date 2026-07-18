"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Llama al proxy del dashboard, que a su vez llama a Invoicy
 * (/gemini2/invoices/{process_id}/retry-extraction) -- re-descarga el
 * archivo original ya guardado en PocketBase y vuelve a correr la
 * extracción completa, mismo process_id. Ver
 * app/api/invoices/[processId]/retry-extraction/route.ts. */
export default function RetryExtractionButton({ processId }: { processId: string }) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  async function handleRetry() {
    setLoading(true);
    try {
      const res = await fetch(
        `/api/invoices/${encodeURIComponent(processId)}/retry-extraction`,
        { method: "POST" }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        toast.error(data.error || data.detail || "No se pudo reintentar la extracción.");
        setLoading(false);
        return;
      }
      toast.success("Reintentando la extracción.");
      // El backend responde apenas encola el reintento (mismo patrón
      // fire-and-forget que la subida original) -- router.refresh() ahora
      // mismo ya debería mostrar status="processing" (el placeholder se
      // crea antes de arrancar la extracción, ver _procesar_imagen_o_pdf).
      router.refresh();
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "No se pudo contactar al backend."
      );
      setLoading(false);
    }
  }

  return (
    <Button onClick={handleRetry} disabled={loading} className="gap-2">
      {loading ? (
        <Loader2 className="size-3.5 animate-spin" />
      ) : (
        <RotateCcw className="size-3.5" />
      )}
      {loading ? "Reintentando…" : "Reintentar extracción"}
    </Button>
  );
}
