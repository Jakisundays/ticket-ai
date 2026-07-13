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
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-gray-200 bg-gray-50">
      <div className="flex items-center justify-between border-b border-gray-200 bg-white px-3 py-2">
        <span className="text-xs font-medium uppercase tracking-wide text-gray-500">
          Archivo original
        </span>
        <a
          href={src}
          target="_blank"
          rel="noreferrer"
          className="text-xs text-blue-600 hover:underline"
        >
          Abrir en pestaña nueva
        </a>
      </div>
      <iframe
        src={src}
        title="Archivo original de la factura"
        className="min-h-[480px] flex-1 bg-white"
      />
    </div>
  );
}
