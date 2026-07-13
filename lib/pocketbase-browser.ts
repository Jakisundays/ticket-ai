import "client-only";

import PocketBase from "pocketbase";

const POCKETBASE_URL = process.env.NEXT_PUBLIC_POCKETBASE_URL;

if (!POCKETBASE_URL) {
  throw new Error(
    "NEXT_PUBLIC_POCKETBASE_URL no esta configurada. Copia .env.example a .env y completa la URL de PocketBase."
  );
}

let browserClient: PocketBase | null = null;

/**
 * Cliente de PocketBase para el navegador. Se usa SOLO en dos lugares:
 *
 *  1. El formulario de login (autentica contra la coleccion "users";
 *     JAMAS contra "service_accounts", que es exclusiva del backend Python).
 *  2. La pantalla de bas_category_map, que lee y escribe directo contra
 *     PocketBase porque la API rule de esa coleccion ya permite
 *     create/update a usuarios autenticados.
 *
 * La sesion se guarda en la cookie "pb_auth" (nombre por defecto del SDK).
 * A proposito la exportamos como NO httpOnly: este mismo cliente necesita
 * poder leerla en cada carga de pagina para inicializar su authStore. El
 * proxy del servidor (proxy.ts) valida esa misma cookie de forma
 * independiente antes de dejar pasar a cualquier ruta del dashboard, y
 * cada request real contra PocketBase se re-valida server-side via sus
 * API rules — la cookie legible por JS no es, por si sola, la unica
 * barrera de seguridad.
 */
export function getPocketBase(): PocketBase {
  if (browserClient) {
    return browserClient;
  }

  browserClient = new PocketBase(POCKETBASE_URL);
  browserClient.authStore.loadFromCookie(document.cookie);

  browserClient.authStore.onChange(() => {
    if (!browserClient) return;
    document.cookie = browserClient.authStore.exportToCookie({
      httpOnly: false,
      secure: window.location.protocol === "https:",
      sameSite: "Lax",
      path: "/",
    });
  });

  return browserClient;
}
