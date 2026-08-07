import type { FriendlyErrorDisplay } from "@/components/FriendlyErrorDetail";

/**
 * Mensaje amigable para errores de extracción (OCR/Gemini), a diferencia de
 * los errores de BAS (ver getFriendlyBasError): acá no hay un vocabulario
 * fijo de stored procedures para matchear por patrón -- son fallas de
 * lectura de imagen/PDF, texto libre e impredecible (timeouts, imagen
 * malformada, etc.). Un mensaje genérico + el crudo siempre disponible como
 * detalle técnico alcanza; no vale la pena una tabla de patrones que va a
 * quedar perpetuamente incompleta.
 */
export function getFriendlyExtractionError(raw: string | null | undefined): FriendlyErrorDisplay | null {
  const texto = (raw || "").trim();
  if (!texto) return null;
  return {
    friendly: "No pudimos leer o procesar este comprobante automáticamente.",
    technical: texto,
  };
}
