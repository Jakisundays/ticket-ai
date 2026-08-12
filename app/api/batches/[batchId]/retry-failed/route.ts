import "server-only";

import { NextResponse } from "next/server";
import { getApiUserSession } from "@/lib/pocketbase-server";

const INVOICE_API_BAS_URL =
  process.env.INVOICE_API_BAS_INTERNAL_URL || process.env.NEXT_PUBLIC_INVOICE_API_BAS_URL;
const SECRET_KEY = process.env.SECRET_KEY;

/** Mismo patrón que retry-extraction: proxy server-to-server que agrega el
 * secreto compartido -- nunca expuesto al navegador -- después de confirmar
 * que hay una sesión real de "users". El endpoint real vive en Invoicy bajo
 * /admin/batch-import (no /gemini2), ver routes/batch_import.py. */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ batchId: string }> }
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

  const { batchId } = await params;

  let upstream: Response;
  try {
    upstream = await fetch(
      `${INVOICE_API_BAS_URL}/admin/batch-import/${encodeURIComponent(batchId)}/retry-failed`,
      {
        method: "POST",
        headers: { "X-Invoicy-Secret": SECRET_KEY },
      }
    );
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "No se pudo contactar al backend." },
      { status: 502 }
    );
  }

  const data = await upstream.json().catch(() => ({ error: "Respuesta inválida del backend." }));
  return NextResponse.json(data, { status: upstream.status });
}
