export function formatCurrency(
  amount: number | null | undefined,
  currency?: string | null
): string {
  if (amount === null || amount === undefined || Number.isNaN(amount)) {
    return "—";
  }

  try {
    return new Intl.NumberFormat("es-AR", {
      style: "currency",
      currency: currency || "ARS",
      currencyDisplay: "narrowSymbol",
    }).format(amount);
  } catch {
    // `moneda` es texto libre en el schema y puede no ser un codigo ISO 4217
    // valido; en ese caso mostramos el numero simple en vez de romper.
    return amount.toFixed(2);
  }
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("es-AR", { dateStyle: "medium" }).format(
    date
  );
}

// Argentina no observa horario de verano desde 2009 -- offset fijo UTC-3,
// valido todo el año. Mismo supuesto que usa app/(dashboard)/page.tsx para
// calcular los limites de mes en hora AR.
const AR_OFFSET_MS = -3 * 60 * 60 * 1000;

/** Dia calendario (año-mes-dia) en hora de Argentina para un instante dado,
 * como clave de comparacion simple -- sumar el offset fijo y leer los
 * getters *UTC* “engaña” a Date para que devuelva los componentes de pared
 * AR sin depender de la zona horaria del proceso Node. */
function claveDiaAr(date: Date): string {
  const ar = new Date(date.getTime() + AR_OFFSET_MS);
  return `${ar.getUTCFullYear()}-${ar.getUTCMonth()}-${ar.getUTCDate()}`;
}

/** "Hoy, 10:42" / "Ayer, 17:03" / "15 jul, 09:02" -- fecha relativa para la
 * tabla de "Actividad reciente" de Inicio. La hora se formatea con
 * Intl.DateTimeFormat("es-AR", ...) igual que el resto de este archivo (sin
 * forzar timeZone, mismo criterio que formatDate); la comparación de "hoy"
 * vs. "ayer" sí se hace explícitamente en hora de Argentina (UTC-3) para que
 * el bucket no dependa de en qué huso corre el proceso Node -- mismo
 * espíritu que calcularAntiguedad en app/(dashboard)/queue/page.tsx (función
 * de módulo pura, no hook). */
export function formatRelativeDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  const hora = new Intl.DateTimeFormat("es-AR", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);

  const hoy = claveDiaAr(new Date());
  const ayer = claveDiaAr(new Date(Date.now() - 86400000));
  const clave = claveDiaAr(date);

  if (clave === hoy) return `Hoy, ${hora}`;
  if (clave === ayer) return `Ayer, ${hora}`;

  const fecha = new Intl.DateTimeFormat("es-AR", {
    day: "numeric",
    month: "short",
  }).format(date);
  return `${fecha}, ${hora}`;
}

export function driveFileUrl(fileId: string | null | undefined): string | null {
  if (!fileId) return null;
  return `https://drive.google.com/file/d/${fileId}/view`;
}

/** Proxy interno (Route Handler) del archivo original de una factura -- ver
 * app/api/invoices/[processId]/file/route.ts. A diferencia de driveFileUrl
 * (deep-link directo a Drive, requiere sesión de Google), esto funciona para
 * cualquier usuario logueado en el dashboard sin tocar permisos de Drive. */
export function invoiceFileProxyUrl(processId: string): string {
  return `/api/invoices/${encodeURIComponent(processId)}/file`;
}
