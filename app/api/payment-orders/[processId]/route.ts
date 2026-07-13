import "server-only";

import { NextResponse } from "next/server";
import { getApiUserSession } from "@/lib/pocketbase-server";

const INVOICE_API_BAS_URL =
  process.env.INVOICE_API_BAS_INTERNAL_URL || process.env.NEXT_PUBLIC_INVOICE_API_BAS_URL;
const SECRET_KEY = process.env.SECRET_KEY;

export async function POST(
  request: Request,
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
  const body = await request.json().catch(() => null);
  if (!body || typeof body.metodo_pago !== "string") {
    return NextResponse.json({ error: "Falta metodo_pago." }, { status: 400 });
  }

  // requested_by lo resuelve el server, NO el cliente -- nunca confiar en un
  // id de usuario que venga en el body de un request del navegador.
  const requestedBy = session.authStore.record?.id;
  if (!requestedBy) {
    return NextResponse.json({ error: "No se pudo resolver el usuario de la sesión." }, { status: 401 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      `${INVOICE_API_BAS_URL}/gemini2/payment-orders/${encodeURIComponent(processId)}/create`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Invoicy-Secret": SECRET_KEY },
        body: JSON.stringify({
          metodo_pago: body.metodo_pago,
          monto: typeof body.monto === "number" ? body.monto : undefined,
          requested_by: requestedBy,
        }),
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
