"use client";

import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";

/** Solo re-dispara el render del Server Component (misma consulta a
 * PocketBase que ya hacia la pagina) -- no agrega ninguna llamada nueva. */
export default function RetryButton() {
  const router = useRouter();

  return (
    <Button variant="outline" size="sm" onClick={() => router.refresh()}>
      Reintentar
    </Button>
  );
}
