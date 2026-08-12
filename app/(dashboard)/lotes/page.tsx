import Link from "next/link";
import { PackageOpen, ChevronRight } from "lucide-react";
import { createServerClient } from "@/lib/pocketbase-server";
import { Collections, type ImportBatchesRecord } from "@/lib/pocketbase-types";
import { formatDate } from "@/lib/format";
import PageHeader from "@/components/PageHeader";
import EmptyState from "@/components/EmptyState";
import StatusBadge from "@/components/StatusBadge";
import BatchesRealtime from "./BatchesRealtime";

export const dynamic = "force-dynamic";

// "Lotes" (import_batches) -- importación masiva de facturas, ver
// Invoicy/docs/plan-importacion-masiva-facturas.md. El trigger primario es
// scripts/batch_import.py (corre en la máquina del usuario, no acá); esta
// página es solo de lectura + "Reintentar fallidos" desde el detalle.
export default async function LotesPage() {
  const pb = await createServerClient();

  const batches = await pb
    .collection<ImportBatchesRecord>(Collections.ImportBatches)
    .getFullList({ sort: "-started_at" });

  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-[16px] font-semibold text-foreground">Lotes</h1>
        <span className="ml-auto hidden truncate text-[13px] text-muted-foreground md:block">
          Importación masiva de facturas
        </span>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="animate-fade-up mx-auto flex max-w-[900px] flex-col gap-4">
          <BatchesRealtime />

          {batches.length === 0 ? (
            <div className="rounded-xl bg-card shadow-(--shadow-1)">
              <EmptyState
                icon={PackageOpen}
                title="Todavía no hay lotes de importación"
                description="Corré scripts/batch_import.py desde tu máquina para importar una carpeta de facturas -- el progreso va a aparecer acá en vivo."
              />
            </div>
          ) : (
            <ul className="flex flex-col gap-2.5">
              {batches.map((batch) => (
                <li key={batch.id}>
                  <Link
                    href={`/lotes/${batch.id}`}
                    className="flex items-center gap-4 rounded-xl bg-card px-5 py-4 shadow-(--shadow-1) transition-[box-shadow,transform] duration-(--dur-fast) ease-(--ease-out) hover:-translate-y-0.5 hover:shadow-(--shadow-2)"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[14px] font-semibold text-foreground">
                        {batch.label || `Lote ${batch.id}`}
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted-foreground">
                        <span>{formatDate(batch.started_at)}</span>
                        <span>·</span>
                        <span>
                          {batch.total_files} archivo{batch.total_files === 1 ? "" : "s"}
                        </span>
                        {batch.total_duplicates > 0 && (
                          <>
                            <span>·</span>
                            <span>{batch.total_duplicates} duplicados omitidos</span>
                          </>
                        )}
                      </div>
                    </div>
                    <StatusBadge status={batch.status} />
                    <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
