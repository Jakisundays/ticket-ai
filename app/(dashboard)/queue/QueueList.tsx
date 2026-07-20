"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Search, ChevronRight } from "lucide-react";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group";
import InitialsAvatar from "@/components/InitialsAvatar";
import StatusBadge from "@/components/StatusBadge";
import { cn } from "@/lib/utils";

/**
 * Fila ya "aplanada" a valores serializables por el Server Component
 * (app/(dashboard)/queue/page.tsx) -- nada de funciones ni componentes de
 * ícono cruzan el límite server/client, solo datos planos.
 */
export type QueueRow = {
  id: string;
  href: string;
  numero: string;
  tipo: string;
  proveedorNombre: string;
  iniciales: string;
  fecha: string;
  antiguedad: string;
  antiguedadUrgente: boolean;
  monto: string;
  /** InvoiceStatus ("pending"|"processing"|"completed"|"error") -- se
   * muestra como badge cuando todavía no es "completed" (ver StatusBadge). */
  status: string;
};

export default function QueueList({ rows }: { rows: QueueRow[] }) {
  const [search, setSearch] = useState("");

  // Filtro client-side puro sobre las filas que ya trajo el fetch server-side
  // (mismo filtro/orden de PocketBase) -- no dispara ningún fetch nuevo.
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (row) =>
        row.proveedorNombre.toLowerCase().includes(q) ||
        row.numero.toLowerCase().includes(q)
    );
  }, [rows, search]);

  return (
    <>
      <div className="flex items-center justify-between gap-4">
        <InputGroup className="h-9 w-80 max-w-full">
          <InputGroupAddon>
            <Search className="size-3.5" />
          </InputGroupAddon>
          <InputGroupInput
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar por proveedor o comprobante"
            className="text-[13.5px]"
          />
        </InputGroup>
        {filtered.length > 0 && (
          <span className="whitespace-nowrap text-[13px] text-muted-foreground">
            {filtered.length} factura{filtered.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-xl bg-card px-6 py-16 text-center shadow-(--shadow-1)">
          <p className="text-[13.5px] font-medium text-foreground">
            Sin resultados para &ldquo;{search}&rdquo;.
          </p>
          <p className="mt-1 text-[13.5px] text-muted-foreground">
            Probá con otro proveedor o número de comprobante.
          </p>
        </div>
      ) : (
        <ul className="flex flex-col gap-3">
          {filtered.map((row) => (
            <li key={row.id}>
              <Link
                href={row.href}
                className="flex items-center gap-4.5 rounded-xl bg-card px-6 py-4.5 shadow-(--shadow-1) transition-[box-shadow,transform] duration-(--dur-fast) ease-(--ease-out) hover:-translate-y-0.5 hover:shadow-(--shadow-2) focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/35 active:scale-[0.99]"
              >
                <InitialsAvatar initials={row.iniciales} />
                <div className="flex min-w-0 flex-1 flex-col gap-0.75">
                  <span className="truncate text-sm font-semibold text-foreground">
                    {row.proveedorNombre}
                  </span>
                  <div className="flex min-w-0 items-center gap-1.75 overflow-hidden">
                    <span className="whitespace-nowrap font-mono text-xs text-muted-foreground">
                      {row.numero}
                    </span>
                    {row.tipo && (
                      <>
                        <span className="text-xs text-muted-foreground/70">
                          ·
                        </span>
                        <span className="whitespace-nowrap text-xs text-muted-foreground">
                          {row.tipo}
                        </span>
                      </>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-0.75">
                  {row.status !== "completed" ? (
                    <StatusBadge status={row.status} dot />
                  ) : (
                    <span className="whitespace-nowrap font-mono text-sm font-semibold text-foreground">
                      {row.monto}
                    </span>
                  )}
                  <span
                    className={cn(
                      "whitespace-nowrap text-[11.5px]",
                      row.antiguedadUrgente
                        ? "text-status-warning-fg"
                        : "text-muted-foreground"
                    )}
                  >
                    {row.fecha}
                    {row.antiguedad ? ` · ${row.antiguedad}` : ""}
                  </span>
                </div>
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
