"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Mismo patrón que ReopenButton.tsx: loading local, toast en error,
 * router.refresh() en éxito (el cambio real lo va a mostrar
 * BatchItemsRealtime cuando lleguen los updates, esto es solo para
 * reflejar el estado "running" del batch de inmediato). */
export default function RetryFailedButton({ batchId }: { batchId: string }) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  async function handleClick() {
    setLoading(true);
    try {
      const res = await fetch(`/api/batches/${batchId}/retry-failed`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.error || data?.detail || "No se pudo reintentar.");
      }
      toast.success(
        data?.reintentados
          ? `Reintentando ${data.reintentados} archivo${data.reintentados === 1 ? "" : "s"}…`
          : "No había nada para reintentar."
      );
      router.refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo reintentar.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={handleClick}
      disabled={loading}
      className="h-8 gap-1.5"
    >
      <RotateCcw className="size-3.5" />
      {loading ? "Reintentando…" : "Reintentar fallidos"}
    </Button>
  );
}
