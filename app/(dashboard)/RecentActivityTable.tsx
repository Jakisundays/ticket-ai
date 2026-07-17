"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import StatusBadge from "@/components/StatusBadge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/**
 * Fila ya "aplanada" a valores serializables por el Server Component
 * (app/(dashboard)/page.tsx) -- mismo motivo que QueueRow (queue/QueueList.tsx)
 * e InvoiceRow (invoices/InvoicesTable.tsx): nada de funciones ni componentes
 * de ícono cruzan el límite server/client, solo datos planos.
 *
 * `estado` ya viene derivado del par (status, review_status) de la factura
 * -- ver estadoActividad() en page.tsx -- así que acá solo se le pasa tal
 * cual a StatusBadge.
 */
export type RecentActivityRow = {
  id: string;
  href: string;
  proveedorNombre: string;
  fecha: string;
  numero: string;
  monto: string;
  estado: string;
};

export default function RecentActivityTable({ rows }: { rows: RecentActivityRow[] }) {
  const router = useRouter();

  if (rows.length === 0) {
    return (
      <p className="px-6 py-10 text-center text-[13.5px] text-muted-foreground">
        Todavía no hay actividad para mostrar.
      </p>
    );
  }

  return (
    <>
      {/* Mobile/tablet: cards apiladas (la tabla de 4 columnas obliga a scroll
          horizontal en una card angosta del dashboard). Desktop (md+): la
          tabla, sin cambios. */}
      <ul className="flex flex-col md:hidden">
        {rows.map((row) => (
          <li key={row.id} className="border-t first:border-t-0">
            <Link
              href={row.href}
              className="flex items-center gap-3 px-4 py-3 active:bg-accent/55"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13.5px] font-semibold text-foreground">
                  {row.proveedorNombre}
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span className="font-mono">{row.numero}</span>
                  <span>·</span>
                  <span>{row.fecha}</span>
                </div>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <span className="font-mono text-[13px] font-semibold whitespace-nowrap text-foreground">
                  {row.monto}
                </span>
                <StatusBadge status={row.estado} />
              </div>
            </Link>
          </li>
        ))}
      </ul>

      <div className="hidden overflow-x-auto md:block">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-6">Proveedor</TableHead>
              <TableHead className="px-3">Comprobante</TableHead>
              <TableHead className="px-3 text-right">Total</TableHead>
              <TableHead className="pr-6 pl-3">Estado</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow
                key={row.id}
                onClick={() => router.push(row.href)}
                className="h-[58px] cursor-pointer"
              >
                <TableCell className="max-w-[260px] pl-6">
                  <Link
                    href={row.href}
                    onClick={(e) => e.stopPropagation()}
                    className="block"
                  >
                    <div className="truncate text-[13.5px] font-semibold text-foreground hover:underline">
                      {row.proveedorNombre}
                    </div>
                    <div className="mt-0.5 text-xs text-muted-foreground">{row.fecha}</div>
                  </Link>
                </TableCell>
                <TableCell className="px-3 font-mono text-[12.5px] whitespace-nowrap text-muted-foreground">
                  {row.numero}
                </TableCell>
                <TableCell className="px-3 text-right font-mono text-[13px] font-semibold whitespace-nowrap text-foreground">
                  {row.monto}
                </TableCell>
                <TableCell className="pr-6 pl-3">
                  <StatusBadge status={row.estado} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </>
  );
}
