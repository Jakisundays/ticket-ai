import "server-only";

import { NextResponse } from "next/server";
import { getApiUserSession } from "@/lib/pocketbase-server";

// Server-only: NUNCA process.env.NEXT_PUBLIC_* acá -- ese valor queda fijo
// para siempre al build time (la URL pública para el navegador), que desde
// ADENTRO del contenedor del dashboard no resuelve al backend Python (mismo
// bug de ECONNREFUSED ya encontrado y arreglado hoy para PocketBase). Con
// fallback a la pública para dev local sin Docker, mismo patrón que
// lib/pocketbase-server.ts.
const INVOICE_API_BAS_URL =
  process.env.INVOICE_API_BAS_INTERNAL_URL || process.env.NEXT_PUBLIC_INVOICE_API_BAS_URL;
const SECRET_KEY = process.env.SECRET_KEY;

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ processId: string }> }
) {
  const session = await getApiUserSession();
  if (!session) {
    return NextResponse.json({ error: "No autenticado." }, { status: 401 });
  }

  if (!INVOICE_API_BAS_URL || !SECRET_KEY) {
    return NextResponse.json(
      { error: "Falta INVOICE_API_BAS_INTERNAL_URL o SECRET_KEY en el servidor." },
      { status: 500 }
    );
  }

  const { processId } = await params;

  let upstream: Response;
  try {
    upstream = await fetch(
      `${INVOICE_API_BAS_URL}/gemini2/invoices/${encodeURIComponent(processId)}/file`,
      { headers: { "X-Invoicy-Secret": SECRET_KEY } }
    );
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "No se pudo contactar al backend." },
      { status: 502 }
    );
  }

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => "");
    return NextResponse.json(
      { error: detail || `El backend respondió ${upstream.status}.` },
      { status: upstream.status }
    );
  }

  // Streameamos el body tal cual llega -- no lo bufferizamos en memoria
  // (las facturas pueden ser PDFs de varios MB).
  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": upstream.headers.get("Content-Type") || "application/octet-stream",
      "Content-Disposition": "inline",
    },
  });
}
