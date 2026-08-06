export interface BasErrorDisplay {
  friendly: string;
  technical: string;
}

/**
 * Mapea el texto crudo de un error de BAS (bas_error persistido en
 * payment_orders/bas_processing_status, o el detailText que arma
 * inferMissionOutcome) a un mensaje amigable + el texto técnico original
 * intacto, para un toggle "Ver detalle técnico" en la UI.
 *
 * REGLA para agregar un caso nuevo acá: solo errores que ya se vieron
 * REALES en producción, con el texto exacto capturado (no heurísticas por
 * "parece que podría pasar esto"). Si no hay un string real todavía, que
 * caiga al genérico de abajo -- sigue mostrando el detalle técnico
 * completo, así que no se pierde nada, y evita falsos positivos por
 * matchear de más con un patrón especulativo. Un caso nuevo se agrega
 * recién cuando aparece en producción, con el string real como referencia.
 *
 * BAS no expone códigos de error estructurados -- solo texto libre con el
 * nombre del stored procedure entre paréntesis (ej. "(SP_GENEROASI)
 * (SP_ICR_COMPROB_COMPRA)") -- por eso el matching es por substring, del
 * más específico al más genérico. Los 3 casos de abajo están anclados a
 * strings reales capturados contra BAS (2026-08-04/05).
 *
 * A propósito NO se afirma una causa raíz para el caso de IVA/alícuota: el
 * límite real de BAS (ImporteIva > $1.00 rechaza el comprobante) está
 * confirmado con evidencia real, pero la resolución depende de soporte de
 * BAS -- mismo criterio que llevó a sacar validar_monto_aplicable_vs_neto
 * (Invoicy, docs/incidente-2026-08-04-pagos-solo-neto.md): no reemplazar
 * un dato real por una suposición, tampoco en el texto amigable.
 */
export function getFriendlyBasError(raw: string | null | undefined): BasErrorDisplay | null {
  const texto = (raw || "").trim();
  if (!texto) return null;

  if (texto.includes("no son consistentes") && texto.includes("SP_ICR_COMPROB_COMPRA")) {
    return {
      friendly:
        "BAS rechazó el comprobante por una inconsistencia en los importes de IVA. Ya estamos en contacto con soporte de BAS para resolverlo.",
      technical: texto,
    };
  }
  if (texto.includes("saldo del vencimiento no puede ser negativo")) {
    return {
      friendly:
        "BAS no pudo aplicar el pago contra el comprobante -- el saldo registrado no coincide con el monto que se intentó pagar.",
      technical: texto,
    };
  }
  if (texto.includes("Ya existe otro comprobante") && texto.includes("SP_VALIDA_TRANSAC")) {
    return {
      friendly: "Este comprobante ya fue registrado antes en BAS (número duplicado).",
      technical: texto,
    };
  }
  // Token vencido/401 y timeout de red van a caer acá hasta que aparezca un
  // caso real en producción con el string exacto -- no vale la pena
  // adivinar el texto todavía (401 además se reintenta solo dentro de
  // BasClient._request, así que rara vez llega hasta esta capa).
  return { friendly: "Ocurrió un error al procesar en BAS.", technical: texto };
}
