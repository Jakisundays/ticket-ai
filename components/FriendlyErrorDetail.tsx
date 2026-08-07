export interface FriendlyErrorDisplay {
  friendly: string;
  technical: string;
}

/**
 * Mensaje amigable de un error (BAS o de extracción) + un <details> nativo
 * para revelar el texto técnico crudo (útil para copiar/pegar en un reporte
 * a soporte). Sin estado de React a propósito -- <details>/<summary>
 * alcanza, no hace falta un modal ni un componente "collapsible" nuevo para
 * una línea de texto. Server-safe (nada de "use client").
 *
 * Agnóstico del origen del error a propósito -- recibe {friendly, technical}
 * ya resuelto en vez de un string crudo, así lo puede usar tanto un error de
 * BAS (getFriendlyBasError) como uno de extracción (getFriendlyExtractionError)
 * sin duplicar este JSX en cada lugar.
 */
export default function FriendlyErrorDetail({
  error,
  className,
}: {
  error: FriendlyErrorDisplay | null | undefined;
  className?: string;
}) {
  if (!error) return null;

  return (
    <div className={className}>
      <p>{error.friendly}</p>
      <details className="mt-1.5">
        <summary className="cursor-pointer text-[11px] font-medium underline decoration-dotted underline-offset-2 select-none">
          Ver detalle técnico
        </summary>
        <pre className="mt-1.5 max-w-full overflow-x-auto rounded bg-black/5 p-2 text-[11px] leading-relaxed whitespace-pre-wrap">
          {error.technical}
        </pre>
      </details>
    </div>
  );
}
