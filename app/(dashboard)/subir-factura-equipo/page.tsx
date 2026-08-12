import PageHeader from "@/components/PageHeader";
import SubirFacturaForm from "@/components/SubirFacturaForm";

// Misma tarjeta que app/subir-factura (pública), pero adentro del layout con
// sidebar para que el equipo ya logueado no pierda el chrome del dashboard
// al subir una factura desde acá.
export default function SubirFacturaEquipoPage() {
  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-[16px] font-semibold text-foreground">
          Subir factura
        </h1>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="animate-fade-up mx-auto flex max-w-[480px] flex-col items-center gap-4">
          <SubirFacturaForm />
        </div>
      </div>
    </div>
  );
}
