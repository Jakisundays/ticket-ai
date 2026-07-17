import { requireUserSession } from "@/lib/pocketbase-server";
import PageHeader from "@/components/PageHeader";
import PaymentMethodsEditor from "@/components/PaymentMethodsEditor";

export const dynamic = "force-dynamic";

export default async function PaymentMethodsPage() {
  // Igual que category-map: solo confirma sesión, el editor habla directo
  // con PocketBase (bas_payment_methods permite create/update a "users").
  await requireUserSession();

  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-base font-semibold text-foreground">Métodos de pago</h1>
      </PageHeader>
      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="animate-fade-up mx-auto flex max-w-[760px] flex-col gap-4">
          <p className="text-sm leading-relaxed text-muted-foreground">
            Código real de <code className="font-mono text-xs">MedioPago</code>{" "}
            en BAS, por método. No hay endpoint que exponga este catálogo — se completa a mano a
            medida que se confirma con pagos reales. &quot;Confirmado&quot; queda en true recién
            con una Orden de Pago real verificada con ese método.
          </p>
          <PaymentMethodsEditor />
        </div>
      </div>
    </div>
  );
}
