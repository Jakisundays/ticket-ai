"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Loader2, Search } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Botón "Volver a buscar en BAS" -- P0-C/P0-G. Llama al proxy del
 * dashboard, que a su vez llama a Invoicy
 * (/gemini2/invoices/{process_id}/recheck-provider): invalida el cache
 * negativo EN MEMORIA del backend (el motivo por el que este botón existe
 * -- sin eso, un proveedor recién dado de alta a mano en BAS seguiría sin
 * aparecer) y vuelve a buscar el proveedor, solo lectura. Nunca crea ni
 * modifica nada en BAS. Ver
 * app/api/invoices/[processId]/recheck-provider/route.ts. */
export default function RecheckProviderButton({ processId }: { processId: string }) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  async function handleRecheck() {
    setLoading(true);
    try {
      const res = await fetch(
        `/api/invoices/${encodeURIComponent(processId)}/recheck-provider`,
        { method: "POST" }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        toast.error(data.error || data.detail || "No se pudo volver a buscar el proveedor.");
        return;
      }
      if (data.encontrado) {
        toast.success(
          data.proveedor?.codigo
            ? `Proveedor encontrado en BAS (${data.proveedor.codigo}).`
            : "Proveedor encontrado en BAS."
        );
      } else {
        toast.message(data.message || "El proveedor todavía no existe en BAS.");
      }
      router.refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo contactar al backend.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Button
      type="button"
      variant="outline"
      onClick={handleRecheck}
      disabled={loading}
      className="h-9 w-full justify-center gap-2"
    >
      {loading ? <Loader2 className="size-3.5 animate-spin" /> : <Search className="size-3.5" />}
      {loading ? "Buscando…" : "Volver a buscar en BAS"}
    </Button>
  );
}
