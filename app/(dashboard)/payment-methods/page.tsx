import { requireUserSession } from "@/lib/pocketbase-server";
import PaymentMethodsEditor from "@/components/PaymentMethodsEditor";

export const dynamic = "force-dynamic";

export default async function PaymentMethodsPage() {
  // Igual que category-map: solo confirma sesión, el editor habla directo
  // con PocketBase (bas_payment_methods permite create/update a "users").
  await requireUserSession();

  return (
    <div>
      <h1 className="mb-1 text-lg font-semibold text-gray-900">Métodos de pago</h1>
      <p className="mb-4 text-sm text-gray-500">
        Código real de <code className="text-xs">MedioPago</code> en BAS, por método. No hay
        endpoint que exponga este catálogo — se completa a mano a medida que se confirma con
        pagos reales. &quot;Confirmado&quot; queda en true recién con una Orden de Pago real
        verificada con ese método.
      </p>
      <PaymentMethodsEditor />
    </div>
  );
}
