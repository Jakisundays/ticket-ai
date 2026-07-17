"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Search, Filter, FileText, ChevronRight } from "lucide-react";
import StatusBadge from "@/components/StatusBadge";
import EmptyState from "@/components/EmptyState";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

/** Fila ya serializada por el Server Component -- solo valores planos, nada
 * de referencias a componentes/funciones (ver nota de la migración sobre
 * pasar props no serializables de Server a Client Components). */
export type InvoiceRow = {
  id: string;
  numero: string;
  emisorNombre: string;
  fecha: string;
  monto: string;
  status: string;
  reviewStatus: string;
  sheetsSaved: boolean;
  driveUrl: string | null;
  basStatus: string | null;
  /** false solo para facturas con status="error" -- ver REGLA DE ORO: el
   * listado no tiene ninguna accion disponible para errores de extraccion. */
  clickable: boolean;
};

const STATUS_FILTER_OPTIONS = [
  { value: "todos", label: "Todos los estados" },
  { value: "pending", label: "Pendiente" },
  { value: "processing", label: "Procesando" },
  { value: "completed", label: "Completada" },
  { value: "error", label: "Error" },
];

export default function InvoicesTable({ rows }: { rows: InvoiceRow[] }) {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("todos");

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((row) => {
      const matchesSearch =
        !q ||
        row.emisorNombre.toLowerCase().includes(q) ||
        row.numero.toLowerCase().includes(q);
      const matchesStatus =
        statusFilter === "todos" || row.status === statusFilter;
      return matchesSearch && matchesStatus;
    });
  }, [rows, search, statusFilter]);

  const isTrulyEmpty = rows.length === 0;
  const hasResults = filtered.length > 0;

  return (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-full min-w-0 basis-full sm:w-[280px] sm:basis-auto">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar por proveedor o comprobante"
            className="pl-8"
          />
        </div>
        <div className="flex items-center gap-1.5">
          <Filter className="size-3.5 shrink-0 text-muted-foreground" />
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_FILTER_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="hidden flex-1 sm:block" />
        {hasResults && (
          <span className="text-[13px] whitespace-nowrap text-muted-foreground">
            {filtered.length} factura{filtered.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {!hasResults ? (
        <div className="rounded-xl bg-card shadow-(--shadow-1)">
          <EmptyState
            icon={FileText}
            title={isTrulyEmpty ? "No hay facturas todavía." : "Sin resultados"}
            description={
              isTrulyEmpty
                ? "Las facturas que lleguen por WhatsApp, email o el formulario web van a aparecer acá."
                : "Ninguna factura coincide con ese filtro. Probá con otro proveedor, comprobante o estado."
            }
          />
        </div>
      ) : (
        <>
          {/* Mobile/tablet: lista de cards (la tabla de 9 columnas no entra
              en una pantalla chica ni siquiera con scroll horizontal de forma
              usable). Desktop (md+): la tabla completa, sin cambios. */}
          <ul className="flex flex-col gap-2.5 md:hidden">
            {filtered.map((row) => (
              <li key={row.id}>
                <RowCard row={row} />
              </li>
            ))}
          </ul>

          <div className="hidden overflow-hidden rounded-xl bg-card shadow-(--shadow-1) md:block">
            <Table className="min-w-[920px]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-5">Comprobante</TableHead>
                  <TableHead>Emisión</TableHead>
                  <TableHead>Emisor</TableHead>
                  <TableHead className="text-right">Total</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead>Revisión</TableHead>
                  <TableHead>Sheets</TableHead>
                  <TableHead>Drive</TableHead>
                  <TableHead className="pr-5">Estado BAS</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((row) => (
                  <TableRow
                    key={row.id}
                    onClick={
                      row.clickable
                        ? () => router.push(`/invoices/${row.id}`)
                        : undefined
                    }
                    className={cn(
                      row.clickable
                        ? "cursor-pointer"
                        : "cursor-default text-muted-foreground"
                    )}
                  >
                    <TableCell className="pl-5 font-mono text-[12.5px] text-foreground">
                      {row.clickable ? (
                        <Link
                          href={`/invoices/${row.id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="hover:underline"
                        >
                          {row.numero}
                        </Link>
                      ) : (
                        row.numero
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {row.fecha}
                    </TableCell>
                    <TableCell className="max-w-[220px] truncate font-medium text-foreground">
                      {row.emisorNombre || "—"}
                    </TableCell>
                    <TableCell className="text-right font-mono font-semibold text-foreground">
                      {row.monto}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={row.status} />
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={row.reviewStatus} />
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {row.sheetsSaved ? "Sí" : "No"}
                    </TableCell>
                    <TableCell>
                      {row.driveUrl ? (
                        <a
                          href={row.driveUrl}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="text-primary hover:underline"
                        >
                          Ver
                        </a>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="pr-5">
                      {row.basStatus ? (
                        <StatusBadge status={row.basStatus} />
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      )}
    </>
  );
}

function RowCard({ row }: { row: InvoiceRow }) {
  const content = (
    <div
      className={cn(
        "flex items-center gap-3 rounded-xl bg-card px-4 py-3.5 shadow-(--shadow-1) transition-colors",
        row.clickable
          ? "active:bg-accent"
          : "cursor-default text-muted-foreground"
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-mono text-[12.5px] text-foreground">
            {row.numero}
          </span>
          <span className="shrink-0 font-mono text-[13px] font-semibold text-foreground">
            {row.monto}
          </span>
        </div>
        <div className="mt-0.5 truncate text-[13px] font-medium text-foreground">
          {row.emisorNombre || "—"}
        </div>
        <div className="mt-1 text-[11.5px] text-muted-foreground">
          {row.fecha}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <StatusBadge status={row.status} />
          <StatusBadge status={row.reviewStatus} />
          {row.basStatus && <StatusBadge status={row.basStatus} />}
        </div>
      </div>
      {row.clickable && (
        <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
      )}
    </div>
  );

  if (!row.clickable) return content;

  return (
    <Link href={`/invoices/${row.id}`} className="block">
      {content}
    </Link>
  );
}
