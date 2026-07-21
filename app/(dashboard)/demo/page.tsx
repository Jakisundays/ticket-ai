import { requireUserSession } from "@/lib/pocketbase-server";
import PageHeader from "@/components/PageHeader";
import PaymentOrderMissionDemo from "./PaymentOrderMissionDemo";

export const dynamic = "force-dynamic";

export default async function DemoPage() {
  await requireUserSession();

  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-base font-semibold text-foreground">
          Demo: Crear orden de pago
        </h1>
      </PageHeader>
      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="animate-fade-up mx-auto flex max-w-[480px] flex-col gap-4">
          <p className="text-sm leading-relaxed text-muted-foreground">
            Réplica en vivo del checklist gamificado que corre al crear una orden de pago real
            (ver <code className="font-mono text-xs">Facturas</code> → una factura confirmada).
            Sin datos ni escrituras reales — sirve para mostrar el flujo, probar cómo se ve cada
            error, o entrenar a alguien nuevo sin arriesgar nada contra BAS.
          </p>
          <PaymentOrderMissionDemo />
        </div>
      </div>
    </div>
  );
}
