import "server-only";

import PocketBase, { ClientResponseError } from "pocketbase";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

// NEXT_PUBLIC_* se hornea (inline) en TODO el bundle de Next.js durante el
// build -- también en código server-only como este archivo, no solo en
// componentes de cliente. Eso significa que si este módulo usara
// NEXT_PUBLIC_POCKETBASE_URL directo, quedaría fijo para siempre al valor de
// build time (la URL pública para el navegador, ej. http://localhost:8090),
// que desde ADENTRO del contenedor del dashboard no resuelve a PocketBase
// (localhost ahí es el propio contenedor) -- verificado en runtime: rompe con
// ECONNREFUSED apenas se renderiza una página que lee datos server-side.
// POCKETBASE_INTERNAL_URL es una env var normal (sin prefijo NEXT_PUBLIC_),
// así que Next.js la lee en runtime real desde el proceso Node, no la
// hornea -- debe apuntar al DNS interno de Docker (ver docker-compose.yml).
const POCKETBASE_URL =
  process.env.POCKETBASE_INTERNAL_URL || process.env.NEXT_PUBLIC_POCKETBASE_URL;

if (!POCKETBASE_URL) {
  throw new Error(
    "Falta POCKETBASE_INTERNAL_URL (o NEXT_PUBLIC_POCKETBASE_URL como fallback). Copia .env.example a .env y completa la URL de PocketBase."
  );
}

/**
 * Crea un cliente de PocketBase para usar en Server Components, Server
 * Actions y Route Handlers. Carga la sesion desde la cookie "pb_auth" que
 * el SDK del navegador sincroniza automaticamente (ver lib/pocketbase-browser.ts)
 * cada vez que el authStore cambia (login/logout/refresh).
 *
 * IMPORTANTE: este helper autentica lecturas como usuario humano de la
 * coleccion "users". Nunca lo uses para autenticar contra "service_accounts":
 * esa coleccion es exclusiva del backend Python (invoice-api-bas) y sus
 * credenciales viven solo en ese proceso, nunca en este dashboard.
 */
export async function createServerClient(): Promise<PocketBase> {
  const pb = new PocketBase(POCKETBASE_URL);
  const cookieStore = await cookies();
  pb.authStore.loadFromCookie(cookieStore.toString());
  return pb;
}

/**
 * Garantiza que exista una sesion valida de la coleccion "users" antes de
 * seguir renderizando una pagina del dashboard, y devuelve el cliente ya
 * autenticado listo para usar.
 *
 * proxy.ts (en la raiz del proyecto) ya hace esta misma verificacion de
 * forma optimista para redirigir peticiones sin cookie antes de que lleguen
 * a renderizar nada. Repetirla aca es defensa en profundidad: evita que un
 * Server Component le pegue a PocketBase con un authStore invalido o con
 * una sesion que no sea de "users" (por ejemplo si la cookie quedo corrupta).
 */
export async function requireUserSession(): Promise<PocketBase> {
  const pb = await createServerClient();

  const hasUserSession =
    pb.authStore.isValid && pb.authStore.record?.collectionName === "users";

  if (!hasUserSession) {
    redirect("/login");
  }

  return pb;
}

export { ClientResponseError };
