"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { getPocketBase } from "@/lib/pocketbase-browser";
import { Collections } from "@/lib/pocketbase-types";

/**
 * Calcado de app/(dashboard)/queue/QueueRealtime.tsx: se suscribe en vivo
 * (PocketBase Realtime, no polling) a TODA la colección "import_batches"
 * mientras la lista está montada, y dispara router.refresh() ante cualquier
 * create/update -- así un lote nuevo (creado por scripts/batch_import.py) o
 * uno que cambia de status aparece solo, sin recargar la página.
 */
export default function BatchesRealtime() {
  const router = useRouter();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const pb = getPocketBase();

    pb.collection(Collections.ImportBatches)
      .subscribe("*", () => {
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => router.refresh(), 500);
      })
      .catch(() => {
        // Best-effort, mismo criterio que QueueRealtime.tsx: si la
        // suscripción no engancha, el usuario igual puede recargar a mano.
      });

    return () => {
      if (timer.current) clearTimeout(timer.current);
      pb.collection(Collections.ImportBatches).unsubscribe("*");
    };
  }, [router]);

  return null;
}
