import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import PocketBase from "pocketbase";

/**
 * Gate de sesion para todas las rutas del dashboard.
 *
 * NOTA: en Next.js 16 el archivo `middleware.ts` fue renombrado a
 * `proxy.ts` y ahora corre siempre en runtime Node.js (ya no Edge), lo que
 * nos permite usar el SDK oficial de "pocketbase" directo aca en vez de
 * decodificar el JWT a mano.
 *
 * Esto es una verificacion OPTIMISTA (solo mira si el JWT de la cookie
 * "pb_auth" existe, no esta vencido, y pertenece a la coleccion "users").
 * No hace ningun request de red a PocketBase. La autorizacion real de cada
 * operacion la sigue haciendo PocketBase server-side via sus API rules
 * (lectura abierta a "users", escritura reservada a "service_accounts",
 * etc.) — este proxy solo evita que alguien sin cookie llegue a ver el
 * HTML del dashboard.
 */

// "/login" es publica pero SOLO tiene sentido para alguien sin sesion -- si
// ya esta logueado, lo mandamos derecho al dashboard (ver el segundo if).
const AUTH_PATH = "/login";

// "/subir-factura" es el formulario público de subida (sin login, protegido
// por rate limiting en el backend en vez de por sesión -- ver
// app/subir-factura/page.tsx). A diferencia de "/login", tiene que quedar
// visible SIEMPRE, esté o no logueado quien lo visite -- no es una pantalla
// de auth, es contenido público real.
const ALWAYS_PUBLIC_PATHS = new Set(["/subir-factura"]);

const POCKETBASE_URL = process.env.NEXT_PUBLIC_POCKETBASE_URL;

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const isAuthPath = pathname === AUTH_PATH;
  const isPublicPath = isAuthPath || ALWAYS_PUBLIC_PATHS.has(pathname);

  const pb = new PocketBase(POCKETBASE_URL);
  pb.authStore.loadFromCookie(request.headers.get("cookie") ?? "");

  // Nunca tratamos como valida una sesion que no sea de "users": la
  // coleccion "service_accounts" es exclusiva del backend Python y jamas
  // deberia poder loguearse en este dashboard.
  const hasUserSession =
    pb.authStore.isValid && pb.authStore.record?.collectionName === "users";

  if (!hasUserSession && !isPublicPath) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("from", pathname);
    return NextResponse.redirect(loginUrl);
  }

  if (hasUserSession && isAuthPath) {
    return NextResponse.redirect(new URL("/queue", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
