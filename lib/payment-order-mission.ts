/**
 * Los 6 pasos reales que corre POST /api/payment-orders/{processId} del lado
 * de Invoicy (routes/process_invoice_google_2.py:2572-2746, repo hermano) en
 * ese orden exacto. El backend no expone un campo "step" -- la respuesta es
 * plana (ver inferMissionOutcome) -- así que este es el único mapa de la
 * secuencia real, mantenido a mano contra ese archivo.
 *
 * "verificar" (paso 3) es una re-consulta GET independiente que
 * crear_orden_de_pago_desde_factura hace en utils/bas.py DESPUÉS de crear el
 * comprobante de compra y ANTES de intentar la orden de pago -- no confía en
 * el 201 del POST solo, corta acá si el GET no confirma que la factura
 * quedó persistida.
 */
export const MISSION_STEPS = [
  { key: "validar", label: "Validando factura y método de pago" },
  { key: "proveedor", label: "Verificando proveedor y medio de pago en BAS" },
  { key: "comprobante", label: "Registrando comprobante de compra" },
  { key: "verificar", label: "Confirmando que la factura quedó registrada" },
  { key: "orden", label: "Creando orden de pago" },
  { key: "confirmar", label: "Confirmando en BAS" },
] as const;

export type MissionStepKey = (typeof MISSION_STEPS)[number]["key"];

export type MissionStepState = "pending" | "active" | "done" | "error" | "skipped";

export interface MissionStepView {
  key: string;
  label: string;
  state: MissionStepState;
  /** Texto real del backend/BAS -- solo se muestra en el paso activo o el que falló. */
  detail?: string | null;
}

export interface MissionOutcome {
  success: boolean;
  /** Índice en MISSION_STEPS donde ocurrió el fallo. null si success=true,
   * o si la respuesta no permite ubicar el paso (ver "conexión perdida"). */
  failedStepIndex: number | null;
  /** Texto real del backend/BAS para ese paso -- nunca un mensaje genérico. */
  detailText: string | null;
}

/**
 * Traduce (loading + índice coreografiado) o (resultado final) a la lista de
 * estados por paso que renderiza <PaymentOrderMission>. Pura -- sin esto,
 * PaymentOrderPanel.tsx y la demo de /demo terminarían con dos copias
 * ligeramente distintas de la misma lógica.
 */
export function computeMissionSteps(
  loading: boolean,
  liveStepIndex: number,
  outcome: MissionOutcome | null
): MissionStepView[] | null {
  if (loading) {
    return MISSION_STEPS.map(
      (step, i): MissionStepView => ({
        ...step,
        state: i < liveStepIndex ? "done" : i === liveStepIndex ? "active" : "pending",
      })
    );
  }
  if (!outcome || (!outcome.success && outcome.failedStepIndex === null)) return null;
  return MISSION_STEPS.map((step, i): MissionStepView => {
    if (outcome.success) return { ...step, state: "done" };
    const failedAt = outcome.failedStepIndex as number;
    if (i < failedAt) return { ...step, state: "done" };
    if (i === failedAt) return { ...step, state: "error", detail: outcome.detailText };
    return { ...step, state: "skipped" };
  });
}

/**
 * Traduce la respuesta cruda de POST /api/payment-orders/{processId} al paso
 * exacto donde ocurrió el fallo. Hace falta reconstruirlo porque el backend
 * responde con DOS formas incompatibles según en qué momento falla:
 *
 *  1) Validaciones previas (antes de tocar BAS) -- FastAPI HTTPException:
 *     status 404/409/422, body {detail: "..."}.
 *  2) Ya se creó el row "processing" y se llamó a BAS -- siempre HTTP 200,
 *     body {success, error, payment_order, ...}. Acá `error` es un string
 *     con uno de tres formas fijas (armadas en utils/bas.py /
 *     process_invoice_google_2.py):
 *       - "BasApiError {code} en /api/ComprobantesCompra: ..." -> paso 2
 *       - "BasApiError 409 en /api/ConsultaComprobantesExternos: ... la
 *         verificación posterior ... no lo encontró" -> paso 3 (el GET de
 *         verificación post-registro, ver crear_orden_de_pago_desde_factura)
 *       - "Orden de pago falló: ..." -> paso 4
 *     Son la única forma de distinguir en qué paso se cortó, porque BAS no
 *     expone nombres de stored procedure en la respuesta de la API REST.
 */
export function inferMissionOutcome(status: number, data: unknown): MissionOutcome {
  const body = (data ?? {}) as Record<string, unknown>;

  if (status === 200 && body.success === true) {
    return { success: true, failedStepIndex: null, detailText: null };
  }

  if (status === 200 && typeof body.error === "string") {
    const text = body.error;
    if (text.startsWith("Orden de pago falló:")) {
      return { success: false, failedStepIndex: 4, detailText: text };
    }
    if (text.includes("verificación posterior")) {
      return { success: false, failedStepIndex: 3, detailText: text };
    }
    // Cualquier otro BasApiError que llega hasta acá viene de
    // crear_comprobante_compra (crear_orden_de_pago_desde_factura llama a
    // ese método primero y no envuelve sus excepciones -- las otras dos
    // formas ya se descartaron arriba).
    return { success: false, failedStepIndex: 2, detailText: text };
  }

  const detail = typeof body.detail === "string" ? body.detail : null;

  if (status === 409) {
    return { success: false, failedStepIndex: 0, detailText: detail ?? "La factura todavía no fue confirmada." };
  }

  if (status === 422) {
    const esProveedorOMedioPago =
      !!detail &&
      (detail.includes("proveedor_codigo") ||
        detail.includes("código BAS configurado") ||
        detail.includes("cuenta_bancaria"));
    return {
      success: false,
      failedStepIndex: esProveedorOMedioPago ? 1 : 0,
      detailText: detail ?? "Faltan datos para crear la orden de pago.",
    };
  }

  if (status === 404) {
    return { success: false, failedStepIndex: 0, detailText: detail ?? "No se encontró la factura." };
  }

  // 502 del proxy (sin red), 500 inesperado, o cualquier forma no
  // reconocida: no sabemos en qué paso real se cortó, así que no acusamos
  // a ninguno -- el panel muestra un estado de "conexión perdida" aparte.
  const fallbackText =
    detail ?? (typeof body.error === "string" ? body.error : "No se pudo contactar al backend.");
  return { success: false, failedStepIndex: null, detailText: fallbackText };
}
