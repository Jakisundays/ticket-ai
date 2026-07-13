import { requireUserSession } from "@/lib/pocketbase-server";
import CategoryMapEditor from "@/components/CategoryMapEditor";

export const dynamic = "force-dynamic";

export default async function CategoryMapPage() {
  // Solo confirma que haya sesion antes de montar el editor cliente; el
  // editor mismo habla directo con PocketBase desde el navegador (la API
  // rule de bas_category_map ya permite create/update a usuarios
  // autenticados, no hace falta pasar por el backend Python).
  await requireUserSession();

  return (
    <div>
      <h1 className="mb-1 text-lg font-semibold text-gray-900">
        Mapa de categorías BAS
      </h1>
      <p className="mb-4 text-sm text-gray-500">
        Cada categoría de item de factura se mapea a un código de BAS ERP. Los
        cambios se guardan directo en PocketBase.
      </p>
      <CategoryMapEditor />
    </div>
  );
}
