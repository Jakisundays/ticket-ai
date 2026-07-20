"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { getPocketBase } from "@/lib/pocketbase-browser";
import { Collections } from "@/lib/pocketbase-types";

/**
 * No renderiza nada -- solo se suscribe en vivo (PocketBase realtime, no
 * polling) a TODA la colección "invoices" mientras la cola está montada, y
 * dispara router.refresh() ante cualquier create/update. Eso hace que el
 * Server Component de page.tsx vuelva a correr su query y la lista se
 * actualice sola: una factura nueva (create, status="pending" desde
 * /website-upload/init) aparece de inmediato, y una que cambia de
 * processing a completed/error también, sin que nadie recargue la página.
 *
 * Debounce de 500ms: varias subidas simultáneas o los upserts sucesivos de
 * un mismo process_id (pending -> processing -> completed) pueden disparar
 * varios eventos muy seguidos -- no queremos un refresh por cada uno.
 */
export default function QueueRealtime() {
  const router = useRouter();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const pb = getPocketBase();

    pb.collection(Collections.Invoices)
      .subscribe("*", () => {
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => router.refresh(), 500);
      })
      .catch(() => {
        // Best-effort, mismo criterio que ExtractionProgress: si la
        // suscripción no engancha, el usuario igual puede recargar a mano.
      });

    return () => {
      if (timer.current) clearTimeout(timer.current);
      pb.collection(Collections.Invoices).unsubscribe("*");
    };
  }, [router]);

  return null;
}
