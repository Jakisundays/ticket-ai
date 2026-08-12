"use client";

import { Fragment, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, ChevronRight, CreditCard, Search } from "lucide-react";
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
import StatusBadge from "@/components/StatusBadge";
import DeleteRowMenu from "@/components/DeleteRowMenu";
import FriendlyErrorDetail from "@/components/FriendlyErrorDetail";
import { getFriendlyBasError } from "@/lib/bas-error-messages";
import type { MetodoPago, PaymentOrderStatus } from "@/lib/pocketbase-types";

// Fila ya aplanada a valores serializables por el Server Component
// (app/(dashboard)/payment-orders/page.tsx) -- nunca pasarle referencias de
// componentes/funciones (íconos, etc.) a este componente cliente.
export type PaymentOrderRow = {
  id: string;
  processId: string;
  numero: string;
  proveedor: string;
  metodo: MetodoPago;
  moneda: string;
  monto: string;
  fecha: string;
  status: PaymentOrderStatus;
  hasError: boolean;
  errorMensaje: string;
  invoiceHref: string | null;
  requestedByEmail: string;
  retryCount: number;
};

const METODO_LABEL: Record<MetodoPago, string> = {
  efectivo: "Efectivo",
  cheque: "Cheque",
  tarjeta: "Tarjeta",
  transferencia: "Transferencia",
};

type StatusFilter = "todos" | PaymentOrderStatus;

export default function PaymentOrdersTable({ rows }: { rows: PaymentOrderRow[] }) {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("todos");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggleRow = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (statusFilter !== "todos" && row.status !== statusFilter) return false;
      if (!q) return true;
      return (
        row.numero.toLowerCase().includes(q) || row.proveedor.toLowerCase().includes(q)
      );
    });
  }, [rows, search, statusFilter]);

  const hasAnyOrders = rows.length > 0;
  const isEmpty = filtered.length === 0;

  return (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-full min-w-0 basis-full sm:w-[280px] sm:basis-auto">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar por proveedor o comprobante"
            className="h-9 pl-8"
          />
        </div>
        <Select
          value={statusFilter}
          onValueChange={(value) => setStatusFilter(value as StatusFilter)}
        >
          <SelectTrigger className="h-9 w-[172px]" size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="todos">Todos los estados</SelectItem>
            <SelectItem value="processing">Procesando</SelectItem>
            <SelectItem value="success">Exitosa</SelectItem>
            <SelectItem value="failed">Fallida</SelectItem>
          </SelectContent>
        </Select>
        <div className="hidden flex-1 sm:block" />
        {!isEmpty && (
          <span className="text-[13px] whitespace-nowrap text-muted-foreground">
            {filtered.length} orden{filtered.length === 1 ? "" : "es"}
          </span>
        )}
      </div>

      {isEmpty ? (
        <div className="rounded-xl bg-card shadow-(--shadow-1)">
          <EmptyState
            icon={CreditCard}
            title={hasAnyOrders ? "Sin resultados" : "Todavía no hay órdenes de pago"}
            description={
              hasAnyOrders
                ? "Ninguna orden coincide con ese filtro."
                : "Todavía no se pidió ninguna orden de pago."
            }
          />
        </div>
      ) : (
        <>
          {/* Mobile/tablet: cards expandibles (mismo estado `expanded` que la
              tabla desktop). Desktop (md+): la tabla de 8 columnas, sin cambios. */}
          <ul className="flex flex-col gap-2.5 md:hidden">
            {filtered.map((row) => (
              <li key={row.id}>
                <OrderCard
                  row={row}
                  isOpen={expanded.has(row.id)}
                  onToggle={() => toggleRow(row.id)}
                />
              </li>
            ))}
          </ul>

          <div className="hidden overflow-x-auto rounded-xl bg-card shadow-(--shadow-1) md:block">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-9" />
                <TableHead className="overline text-[11px] text-muted-foreground">
                  Comprobante
                </TableHead>
                <TableHead className="overline text-[11px] text-muted-foreground">
                  Proveedor
                </TableHead>
                <TableHead className="overline text-[11px] text-muted-foreground">
                  Método
                </TableHead>
                <TableHead className="overline text-[11px] text-muted-foreground">
                  Moneda
                </TableHead>
                <TableHead className="overline text-right text-[11px] text-muted-foreground">
                  Monto
                </TableHead>
                <TableHead className="overline text-[11px] text-muted-foreground">
                  Último intento
                </TableHead>
                <TableHead className="overline text-[11px] text-muted-foreground">
                  Estado BAS
                </TableHead>
                <TableHead className="w-9 pr-3" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((row) => {
                const isOpen = expanded.has(row.id);
                return (
                  <Fragment key={row.id}>
                    <TableRow
                      className="cursor-pointer"
                      onClick={() => toggleRow(row.id)}
                      aria-expanded={isOpen}
                    >
                      <TableCell>
                        <ChevronRight
                          className={`size-3.5 text-muted-foreground transition-transform duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] ${
                            isOpen ? "rotate-90" : ""
                          }`}
                        />
                      </TableCell>
                      <TableCell className="font-mono text-[12.5px] text-foreground">
                        {row.numero}
                      </TableCell>
                      <TableCell className="max-w-0 truncate text-[13px] font-medium text-foreground">
                        {row.proveedor}
                      </TableCell>
                      <TableCell className="text-[12.5px] text-muted-foreground">
                        {METODO_LABEL[row.metodo] ?? row.metodo}
                      </TableCell>
                      <TableCell className="text-[12.5px] text-muted-foreground">
                        {row.moneda || "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono text-[13px] font-semibold text-foreground">
                        {row.monto}
                      </TableCell>
                      <TableCell className="text-[12.5px] text-muted-foreground">
                        {row.fecha}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={row.status} />
                      </TableCell>
                      <TableCell className="pr-3">
                        <DeleteRowMenu
                          processId={row.processId}
                          endpoint="payment-order"
                          itemLabel="esta orden de pago"
                          requireReason={row.status === "success"}
                          confirmText={row.numero}
                        />
                      </TableCell>
                    </TableRow>
                    {isOpen && (
                      <TableRow className="hover:bg-transparent">
                        <TableCell colSpan={9} className="bg-muted/40 py-3.5 pr-5 pl-[66px]">
                          <div className="flex flex-col gap-2 whitespace-normal">
                            {row.hasError && (
                              <FriendlyErrorDetail
                                error={getFriendlyBasError(row.errorMensaje)}
                                className="max-w-[640px] rounded-md border border-status-warning-dot/25 bg-status-warning-bg px-2.5 py-2 text-xs leading-relaxed text-status-warning-fg"
                              />
                            )}
                            <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12.5px] text-muted-foreground">
                              <span>Pedido por {row.requestedByEmail}</span>
                              {row.retryCount > 0 && (
                                <span>
                                  · {row.retryCount} reintento{row.retryCount === 1 ? "" : "s"}
                                </span>
                              )}
                              {row.invoiceHref && (
                                <>
                                  <span>·</span>
                                  <Link
                                    href={row.invoiceHref}
                                    className="inline-flex items-center gap-0.5 font-medium text-foreground hover:text-primary"
                                  >
                                    Factura vinculada: {row.numero}
                                    <ArrowUpRight className="size-3" />
                                  </Link>
                                </>
                              )}
                            </div>
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
          </div>
        </>
      )}
    </>
  );
}

function OrderCard({
  row,
  isOpen,
  onToggle,
}: {
  row: PaymentOrderRow;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="overflow-hidden rounded-xl bg-card shadow-(--shadow-1)">
      <div className="relative">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={isOpen}
          className="flex w-full items-center gap-3 py-3.5 pr-11 pl-4 text-left active:bg-accent"
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
              {row.proveedor}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11.5px] text-muted-foreground">
              <span>{METODO_LABEL[row.metodo] ?? row.metodo}</span>
              <span>·</span>
              <span>{row.fecha}</span>
            </div>
            <div className="mt-2">
              <StatusBadge status={row.status} />
            </div>
          </div>
          <ChevronRight
            className={`size-4 shrink-0 text-muted-foreground transition-transform duration-150 ${
              isOpen ? "rotate-90" : ""
            }`}
          />
        </button>
        {/* Hermano del <button> de arriba, no anidado adentro -- un botón
            dentro de otro botón es HTML inválido y rompe el toggle. */}
        <DeleteRowMenu
          processId={row.processId}
          endpoint="payment-order"
          itemLabel="esta orden de pago"
          requireReason={row.status === "success"}
          confirmText={row.numero}
          className="absolute top-4.5 right-2"
        />
      </div>
      {isOpen && (
        <div className="flex flex-col gap-2 border-t bg-muted/40 px-4 py-3.5">
          {row.hasError && (
            <FriendlyErrorDetail
              error={getFriendlyBasError(row.errorMensaje)}
              className="rounded-md border border-status-warning-dot/25 bg-status-warning-bg px-2.5 py-2 text-xs leading-relaxed text-status-warning-fg"
            />
          )}
          <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12.5px] text-muted-foreground">
            <span>Pedido por {row.requestedByEmail}</span>
            {row.retryCount > 0 && (
              <span>
                · {row.retryCount} reintento{row.retryCount === 1 ? "" : "s"}
              </span>
            )}
            {row.invoiceHref && (
              <>
                <span>·</span>
                <Link
                  href={row.invoiceHref}
                  onClick={(e) => e.stopPropagation()}
                  className="inline-flex items-center gap-0.5 font-medium text-foreground hover:text-primary"
                >
                  Factura vinculada: {row.numero}
                  <ArrowUpRight className="size-3" />
                </Link>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
