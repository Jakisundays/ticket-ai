import { requireUserSession } from "@/lib/pocketbase-server";
import PageHeader from "@/components/PageHeader";
import CategoryMapEditor from "@/components/CategoryMapEditor";

export const dynamic = "force-dynamic";

export default async function CategoryMapPage() {
  // Solo confirma que haya sesion antes de montar el editor cliente; el
  // editor mismo habla directo con PocketBase desde el navegador (la API
  // rule de bas_category_map ya permite create/update a usuarios
  // autenticados, no hace falta pasar por el backend Python).
  await requireUserSession();

  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-base font-semibold text-foreground">
          Categorías BAS
        </h1>
      </PageHeader>
      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="animate-fade-up mx-auto flex max-w-[820px] flex-col gap-4">
          <p className="text-[13px] leading-relaxed text-muted-foreground">
            Mapea cada categoría contable de BAS a la categoría interna que
            usa el equipo. Los cambios se guardan por fila.
          </p>
          <CategoryMapEditor />
        </div>
      </div>
    </div>
  );
}
