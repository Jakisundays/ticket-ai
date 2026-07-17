import { ExternalLink } from "lucide-react";
import { invoiceFileProxyUrl } from "@/lib/format";

/**
 * Panel izquierdo del split-view de revisión: el archivo original (imagen o
 * PDF) de la factura, vía el proxy server-side (ver app/api/invoices/[processId]/file)
 * -- el archivo vive en Drive con scope drive.file y sin permissions().create(),
 * así que un <img>/<iframe> apuntando directo a drive.google.com no funciona
 * para un usuario del dashboard.
 *
 * Server Component a propósito: un <iframe>/<img> no necesita JS de cliente,
 * el navegador hace el request de imagen/PDF él solo.
 */
export default function InvoiceFileViewer({ processId }: { processId: string }) {
  const src = invoiceFileProxyUrl(processId);

  return (
    <div className="flex h-full flex-col gap-3 rounded-lg bg-card p-3">
      <div className="flex items-center justify-between px-1">
        <span className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
          Archivo original
        </span>
        <a
          href={src}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:text-primary/80"
        >
          Abrir en pestaña nueva
          <ExternalLink className="size-3.5" />
        </a>
      </div>
      <iframe
        src={src}
        title="Archivo original de la factura"
        className="min-h-0 flex-1 rounded-lg border bg-muted"
      />
    </div>
  );
}
