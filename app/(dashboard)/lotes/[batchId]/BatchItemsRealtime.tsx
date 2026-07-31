"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { getPocketBase } from "@/lib/pocketbase-browser";
import { Collections } from "@/lib/pocketbase-types";
import type { ImportBatchItemsRecord } from "@/lib/pocketbase-types";

/**
 * Se suscribe a TODA la colección "import_batch_items" (sin filtro de
 * suscripción server-side -- esa opción del SDK no tiene precedente de uso
 * verificado en este proyecto, ver docs/plan-importacion-masiva-facturas.md
 * sección 7) y descarta en el callback los eventos que no sean de este
 * batch. A esta escala (una corrida activa a la vez, decenas de items en
 * total en toda la colección) el costo de filtrar client-side es
 * insignificante. Mismo patrón de debounce + router.refresh() que
 * QueueRealtime.tsx.
 */
export default function BatchItemsRealtime({ batchId }: { batchId: string }) {
  const router = useRouter();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const pb = getPocketBase();

    pb.collection<ImportBatchItemsRecord>(Collections.ImportBatchItems)
      .subscribe("*", (e) => {
        if (e.record.batch !== batchId) return;
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => router.refresh(), 500);
      })
      .catch(() => {
        // Best-effort, mismo criterio que QueueRealtime.tsx.
      });

    // También la colección padre -- para que el banner de resumen (status
    // del batch en sí, no solo sus items) se actualice al cerrarse.
    pb.collection(Collections.ImportBatches)
      .subscribe(batchId, () => {
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => router.refresh(), 500);
      })
      .catch(() => {});

    return () => {
      if (timer.current) clearTimeout(timer.current);
      pb.collection(Collections.ImportBatchItems).unsubscribe("*");
      pb.collection(Collections.ImportBatches).unsubscribe(batchId);
    };
  }, [router, batchId]);

  return null;
}
