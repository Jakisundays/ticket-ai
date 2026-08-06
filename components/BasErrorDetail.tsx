import { getFriendlyBasError } from "@/lib/bas-error-messages";

/**
 * Mensaje amigable de un error de BAS + un <details> nativo para revelar el
 * texto técnico crudo (útil para copiar/pegar en un reporte a soporte de
 * BAS). Sin estado de React a propósito -- <details>/<summary> alcanza,
 * no hace falta un modal ni un componente "collapsible" nuevo para una
 * línea de texto. Server-safe (nada de "use client").
 */
export default function BasErrorDetail({
  raw,
  className,
}: {
  raw: string | null | undefined;
  className?: string;
}) {
  const parsed = getFriendlyBasError(raw);
  if (!parsed) return null;

  return (
    <div className={className}>
      <p>{parsed.friendly}</p>
      <details className="mt-1.5">
        <summary className="cursor-pointer text-[11px] font-medium underline decoration-dotted underline-offset-2 select-none">
          Ver detalle técnico
        </summary>
        <pre className="mt-1.5 max-w-full overflow-x-auto rounded bg-black/5 p-2 text-[11px] leading-relaxed whitespace-pre-wrap">
          {parsed.technical}
        </pre>
      </details>
    </div>
  );
}
