"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { getPocketBase } from "@/lib/pocketbase-browser";
import { Collections } from "@/lib/pocketbase-types";

/**
 * Reabrir puede desbloquear una factura que ya tiene una orden de pago
 * intentada -- un click accidental no debería reabrir en silencio.
 */
export default function ReopenButton({ invoiceId }: { invoiceId: string }) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  async function handleReopen() {
    if (
      !window.confirm(
        "¿Reabrir esta factura? Vas a poder editarla de nuevo. Si ya se creó una orden de pago, revisala antes de reabrir."
      )
    ) {
      return;
    }
    setLoading(true);
    try {
      const pb = getPocketBase();
      // Reabrir tiene que venir SOLA (review_status únicamente) -- el hook
      // rechaza cualquier otro campo en el mismo request.
      await pb.collection(Collections.Invoices).update(invoiceId, {
        review_status: "needs_review",
      });
      router.refresh();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "No se pudo reabrir la factura.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <button
      type="button"
      onClick={handleReopen}
      disabled={loading}
      className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40"
    >
      {loading ? "Reabriendo…" : "Reabrir para editar"}
    </button>
  );
}
