import SubirFacturaForm from "@/components/SubirFacturaForm";

// Página pública a propósito -- vive afuera de app/(dashboard) porque ese
// layout exige sesión (requireUserSession en app/(dashboard)/layout.tsx) y
// este link se comparte con gente sin cuenta en el dashboard. La versión
// para el equipo, con sidebar, está en app/(dashboard)/subir-factura-equipo.
export default function SubirFacturaPage() {
  return (
    <main className="flex flex-1 flex-col items-center px-6 py-12">
      <div className="mb-8 flex w-full max-w-[480px] items-center gap-2">
        <div className="flex size-6 items-center justify-center rounded-sm bg-primary text-[10px] font-bold text-primary-foreground">
          TA
        </div>
        <span className="text-[13.5px] font-semibold text-foreground/70">
          Ticket AI · Dinardi
        </span>
      </div>

      <SubirFacturaForm />

      <p className="mt-6 text-center text-[13px] text-muted-foreground">
        ¿Preferís otro medio? También podés enviarla por email a{" "}
        <a
          href="mailto:sistemas@dinardi.com"
          className="text-primary hover:underline"
        >
          sistemas@dinardi.com
        </a>
      </p>
    </main>
  );
}
