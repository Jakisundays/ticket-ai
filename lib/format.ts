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
