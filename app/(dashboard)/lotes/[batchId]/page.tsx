import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { notFound } from "next/navigation";
import { createServerClient } from "@/lib/pocketbase-server";
import {
  Collections,
  type ImportBatchesRecord,
  type ImportBatchItemWithExpand,
} from "@/lib/pocketbase-types";
import { formatDate } from "@/lib/format";
import PageHeader from "@/components/PageHeader";
import StatusBadge from "@/components/StatusBadge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import FriendlyErrorDetail from "@/components/FriendlyErrorDetail";
import { getFriendlyExtractionError } from "@/lib/extraction-error-messages";
import BatchItemsRealtime from "./BatchItemsRealtime";
import RetryFailedButton from "./RetryFailedButton";

export const dynamic = "force-dynamic";

function tiempoTranscurrido(started: string, finished: string | ""): string {
  const inicio = new Date(started).getTime();
  const fin = finished ? new Date(finished).getTime() : Date.now();
  const minutos = Math.max(0, Math.round((fin - inicio) / 60000));
  if (minutos < 60) return `${minutos}m`;
  const horas = Math.floor(minutos / 60);
  return `${horas}h ${minutos % 60}m`;
}

export default async function LoteDetallePage({
  params,
}: {
  params: Promise<{ batchId: string }>;
}) {
  const { batchId } = await params;
  const pb = await createServerClient();

  let batch: ImportBatchesRecord;
  try {
    batch = await pb
      .collection<ImportBatchesRecord>(Collections.ImportBatches)
      .getOne(batchId);
  } catch {
    notFound();
  }

  // Sin paginar de verdad -- a esta escala (decenas de archivos por
  // corrida) tallar los agregados acá mismo en JS plano es más simple y
  // barato que queries de conteo separadas. Los contadores del resumen NO
  // están persistidos con incrementos en el batch -- se derivan siempre de
  // esta lista, misma filosofía anti-condición-de-carrera que el backend.
  const items = await pb
    .collection<ImportBatchItemWithExpand>(Collections.ImportBatchItems)
    .getFullList({
      filter: `batch = "${batchId}"`,
      sort: "original_path",
      expand: "invoice,duplicate_of_invoice",
    });

  const conteos = {
    pending: items.filter((i) => i.status === "pending").length,
    uploading: items.filter((i) => i.status === "uploading").length,
    processing: items.filter((i) => i.status === "processing").length,
    completed: items.filter((i) => i.status === "completed").length,
    error: items.filter((i) => i.status === "error").length,
    skipped_duplicate: items.filter((i) => i.status === "skipped_duplicate").length,
  };
  const hayFallidos = conteos.error > 0;

  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-[16px] font-semibold text-foreground">
          {batch.label || `Lote ${batch.id}`}
        </h1>
        <span className="ml-auto hidden truncate text-[13px] text-muted-foreground md:block">
          {formatDate(batch.started_at)}
        </span>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="animate-fade-up mx-auto flex max-w-[1100px] flex-col gap-4">
          <BatchItemsRealtime batchId={batchId} />

          {!!batch.monto_override && (
            <div className="rounded-xl border border-status-warning-dot/25 bg-status-warning-bg px-5 py-3 text-[13px] text-status-warning-fg">
              Corrida de prueba: cada factura se registró en BAS con Total=
              {batch.monto_override} en vez del monto real extraído del documento.
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3 rounded-xl bg-card px-5 py-4 shadow-(--shadow-1)">
            <StatusBadge status={batch.status} />
            <span className="text-[13px] text-muted-foreground">
              {batch.total_files} encontradas · {batch.total_unique} únicas ·{" "}
              {batch.total_duplicates} duplicados omitidos · {conteos.completed} completadas ·{" "}
              {conteos.error} con error ·{" "}
              {conteos.processing + conteos.uploading + conteos.pending} en curso · tiempo
              transcurrido {tiempoTranscurrido(batch.started_at, batch.finished_at)}
            </span>
            <div className="ml-auto">
              {hayFallidos && <RetryFailedButton batchId={batchId} />}
            </div>
          </div>

          <div className="hidden overflow-x-auto rounded-xl bg-card shadow-(--shadow-1) md:block">
            <Table className="min-w-[860px]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-5">Archivo</TableHead>
                  <TableHead>Origen</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead>Intentos</TableHead>
                  <TableHead className="pr-5">Detalle</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="max-w-[280px] truncate pl-5 font-mono text-[12.5px] text-foreground">
                      {item.original_path}
                    </TableCell>
                    <TableCell className="text-[12.5px] text-muted-foreground">
                      {item.zip_source || "—"}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={item.status} />
                    </TableCell>
                    <TableCell className="text-[12.5px] text-muted-foreground">
                      {item.attempt_count || 0}
                    </TableCell>
                    <TableCell className="max-w-[320px] pr-5 text-[12.5px]">
                      {item.status === "completed" && item.invoice && (
                        <Link
                          href={`/invoices/${item.invoice}`}
                          className="inline-flex items-center gap-0.5 font-medium text-primary hover:underline"
                        >
                          Ver factura
                          <ArrowUpRight className="size-3" />
                        </Link>
                      )}
                      {item.status === "error" && (
                        <FriendlyErrorDetail
                          error={getFriendlyExtractionError(item.error_message)}
                          className="text-status-destructive-fg [&_p]:line-clamp-2"
                        />
                      )}
                      {item.status === "error" && !item.error_message && (
                        <span className="text-status-destructive-fg">Error sin detalle.</span>
                      )}
                      {item.status === "skipped_duplicate" && (
                        <span className="text-muted-foreground">
                          {item.expand?.duplicate_of_invoice ? (
                            <>
                              Ya existe:{" "}
                              <Link
                                href={`/invoices/${item.duplicate_of_invoice}`}
                                className="font-medium text-primary hover:underline"
                              >
                                {item.expand.duplicate_of_invoice.numero_comprobante ||
                                  "ver factura"}
                              </Link>
                            </>
                          ) : (
                            "Duplicado dentro de este mismo lote"
                          )}
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* Mobile: mismas filas, en cards -- a esta escala (uso admin
              ocasional, no una lista que alguien recorre todo el tiempo
              desde el celular) alcanza con una versión simple. */}
          <ul className="flex flex-col gap-2.5 md:hidden">
            {items.map((item) => (
              <li
                key={item.id}
                className="flex flex-col gap-1.5 rounded-xl bg-card px-4 py-3.5 shadow-(--shadow-1)"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[12.5px] text-foreground">
                    {item.original_path}
                  </span>
                  <StatusBadge status={item.status} />
                </div>
                {item.zip_source && (
                  <span className="text-[11.5px] text-muted-foreground">
                    de {item.zip_source}
                  </span>
                )}
                {item.status === "completed" && item.invoice && (
                  <Link
                    href={`/invoices/${item.invoice}`}
                    className="text-[12.5px] font-medium text-primary hover:underline"
                  >
                    Ver factura →
                  </Link>
                )}
                {item.status === "error" && item.error_message && (
                  <FriendlyErrorDetail
                    error={getFriendlyExtractionError(item.error_message)}
                    className="text-[12.5px] text-status-destructive-fg"
                  />
                )}
                {item.status === "error" && !item.error_message && (
                  <span className="text-[12.5px] text-status-destructive-fg">Error sin detalle.</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
