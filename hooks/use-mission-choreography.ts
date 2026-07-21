"use client";

import { useEffect, useRef, useState } from "react";
import { MISSION_STEPS, inferMissionOutcome, type MissionOutcome } from "@/lib/payment-order-mission";

/** Cada paso "coreografiado" avanza cada 750ms mientras esperamos la única
 * respuesta real del backend -- nunca completa el ÚLTIMO paso por timer, solo
 * la respuesta real lo hace (ver run()). */
const STEP_ADVANCE_MS = 750;
/** Si el paso activo lleva más que esto sin resolver, mostramos un hint de
 * "puede tardar" en vez de dejarlo mudo. */
const SLOW_HINT_MS = 4000;

/**
 * Orquesta la animación de "avance optimista" del checklist mientras se
 * espera una única respuesta HTTP real, y resuelve el resultado final con
 * inferMissionOutcome(). Compartido entre PaymentOrderPanel.tsx (fetch real)
 * y app/(dashboard)/demo (respuestas simuladas) -- misma coreografía, misma
 * lógica de inferencia de paso fallido, dos fuentes de datos distintas.
 */
export function useMissionChoreography(initialOutcome: MissionOutcome | null = null) {
  const [loading, setLoading] = useState(false);
  const [liveStepIndex, setLiveStepIndex] = useState(0);
  const [slowHint, setSlowHint] = useState(false);
  const [lastOutcome, setLastOutcome] = useState<MissionOutcome | null>(initialOutcome);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  function clearTimers() {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }
  useEffect(() => () => clearTimers(), []);

  /**
   * `resolve` hace el trabajo real (fetch, o una promesa simulada en la
   * demo) y devuelve {status, data} -- el mismo shape que
   * inferMissionOutcome() ya sabe interpretar.
   */
  async function run(
    resolve: () => Promise<{ status: number; data: unknown }>
  ): Promise<MissionOutcome> {
    clearTimers();
    setLoading(true);
    setSlowHint(false);
    setLiveStepIndex(0);
    setLastOutcome(null);

    for (let i = 1; i <= MISSION_STEPS.length - 2; i++) {
      timers.current.push(setTimeout(() => setLiveStepIndex(i), i * STEP_ADVANCE_MS));
    }
    timers.current.push(setTimeout(() => setSlowHint(true), SLOW_HINT_MS));

    try {
      const { status, data } = await resolve();
      clearTimers();
      const outcome = inferMissionOutcome(status, data);
      setLastOutcome(outcome);
      return outcome;
    } catch (error) {
      clearTimers();
      const outcome: MissionOutcome = {
        success: false,
        failedStepIndex: null,
        detailText: error instanceof Error ? error.message : "No se pudo contactar al backend.",
      };
      setLastOutcome(outcome);
      return outcome;
    } finally {
      setLoading(false);
    }
  }

  return { loading, liveStepIndex, slowHint, lastOutcome, setLastOutcome, run };
}
