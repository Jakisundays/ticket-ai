import "server-only";

import { NextResponse } from "next/server";
import { getApiUserSession } from "@/lib/pocketbase-server";

const INVOICE_API_BAS_URL =
  process.env.INVOICE_API_BAS_INTERNAL_URL || process.env.NEXT_PUBLIC_INVOICE_API_BAS_URL;
const SECRET_KEY = process.env.SECRET_KEY;

/** Mismo patrón que app/api/payment-orders/[processId]/route.ts: proxy
 * server-to-server que agrega el secreto compartido -- nunca expuesto al
 * navegador -- después de confirmar que hay una sesión real de "users".
 * Soft-delete SIEMPRE (ver Invoicy DELETE /gemini2/invoices/{process_id}):
 * si la factura tiene una Orden de Pago ya exitosa activa, el backend
 * responde 409 -- ese caso hay que resolverlo eliminando la Orden de Pago
 * primero (desde Órdenes de pago), no se cascadea automáticamente. */
export async function DELETE(
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

  // deleted_by lo resuelve el server, NO el cliente -- nunca confiar en un
  // id de usuario que venga en el body de un request del navegador (mismo
  // criterio que requested_by en payment-orders/[processId]/route.ts).
  const deletedBy = session.authStore.record?.id;
  if (!deletedBy) {
    return NextResponse.json({ error: "No se pudo resolver el usuario de la sesión." }, { status: 401 });
  }

  const { processId } = await params;
  const body = await request.json().catch(() => ({}));
  const reason = typeof body?.reason === "string" ? body.reason : undefined;

  let upstream: Response;
  try {
    upstream = await fetch(
      `${INVOICE_API_BAS_URL}/gemini2/invoices/${encodeURIComponent(processId)}`,
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json", "X-Invoicy-Secret": SECRET_KEY },
        body: JSON.stringify({ deleted_by: deletedBy, reason }),
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
