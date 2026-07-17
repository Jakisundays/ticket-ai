import Link from "next/link";
import { CheckCircle } from "lucide-react";
import { createServerClient } from "@/lib/pocketbase-server";
import { Collections, type InvoicesRecord } from "@/lib/pocketbase-types";
import { formatCurrency, formatDate } from "@/lib/format";
import PageHeader from "@/components/PageHeader";
import EmptyState from "@/components/EmptyState";
import QueueList, { type QueueRow } from "./QueueList";

export const dynamic = "force-dynamic";

type Antiguedad = { label: string; urgente: boolean };

// Función de módulo (no es un componente/hook) para que el linter de
// purity de React no la trate como una llamada impura dentro del render --
// esta página es dynamic="force-dynamic" a propósito, así que depender de
// la hora real en cada request es el comportamiento buscado, no un bug.
function calcularAntiguedad(createdIso: string): Antiguedad {
  const created = new Date(createdIso);
  const days = Math.floor((Date.now() - created.getTime()) / 86400000);
  if (Number.isNaN(days)) return { label: "", urgente: false };
  // 3+ días esperando revisión se marca en rojo en la fila (mismo umbral que
  // usa el mockup Legado para "urgente").
  const urgente = days >= 3;
  if (days <= 0) return { label: "hoy", urgente };
  if (days === 1) return { label: "ayer", urgente };
  return { label: `hace ${days} días`, urgente };
}

// Sufijos societarios y conectores que no identifican al proveedor -- se
// ignoran al calcular las iniciales del avatar de cada fila.
const PALABRAS_NO_SIGNIFICATIVAS = new Set([
  "sa",
  "srl",
  "sas",
  "sac",
  "scs",
  "ltda",
  "cia",
  "de",
  "del",
  "la",
  "las",
  "los",
  "y",
  "e",
]);

/** Iniciales para el avatar de cada fila: primeras letras de las primeras 2
 * palabras "significativas" del nombre del emisor (mayúsculas), ignorando
 * sufijos societarios (S.A., S.R.L., ...) y conectores (de/del/la/...). Si
 * sólo queda una palabra significativa, se usan sus primeras 2 letras (p. ej.
 * "Metrogas S.A." -> "ME"). */
function calcularIniciales(nombre: string): string {
  const palabras = nombre
    .split(/\s+/)
    .map((palabra) => palabra.replace(/[.,]/g, ""))
    .filter(
      (palabra) => palabra.length > 0 && !PALABRAS_NO_SIGNIFICATIVAS.has(palabra.toLowerCase())
    );

  if (palabras.length === 0) return "?";
  if (palabras.length === 1) return palabras[0].slice(0, 2).toUpperCase();
  return (palabras[0][0] + palabras[1][0]).toUpperCase();
}

export default async function QueuePage() {
  const pb = await createServerClient();

  // status = "completed": una factura todavía en processing/error no es
  // asunto de la cola de revisión humana todavía (eso lo cubre /invoices).
  // review_status != "confirmed" (no "= needs_review"): filas legacy sin
  // review_status seteado (string vacío) también cuentan como pendientes.
  const result = await pb
    .collection<InvoicesRecord>(Collections.Invoices)
    .getList(1, 100, {
      filter: 'status = "completed" && review_status != "confirmed"',
      sort: "+created",
    });

  const rows: QueueRow[] = result.items.map((invoice) => {
    // "created" (campo de sistema de PocketBase, ya viene en cada fetch) es
    // lo que efectivamente ordena la cola (`sort: "+created"`), así que la
    // antigüedad mostrada se calcula sobre esa fecha -- no sobre
    // fecha_emision, que es la fecha de emisión de la factura del proveedor,
    // no cuánto tiempo lleva esperando revisión en nuestra cola.
    const antiguedad = calcularAntiguedad(invoice.created);
    const proveedorNombre = invoice.emisor_nombre || "Emisor sin identificar";

    return {
      id: invoice.id,
      href: `/invoices/${invoice.id}`,
      numero: invoice.numero_comprobante || invoice.process_id,
      tipo: invoice.tipo_comprobante,
      proveedorNombre,
      iniciales: calcularIniciales(proveedorNombre),
      fecha: formatDate(invoice.fecha_emision),
      antiguedad: antiguedad.label,
      antiguedadUrgente: antiguedad.urgente,
      monto: formatCurrency(invoice.total, invoice.moneda),
    };
  });

  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-base font-semibold text-foreground">
          Cola de revisión
        </h1>
        <span className="rounded-full bg-status-warning-bg px-3 py-1 font-heading text-xs font-semibold whitespace-nowrap text-status-warning-fg">
          {rows.length} pendiente{rows.length === 1 ? "" : "s"}
        </span>
        <span className="ml-auto hidden truncate text-[13px] text-muted-foreground md:block">
          Ordenadas por antigüedad · la más vieja primero
        </span>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-4 md:p-7">
        <div className="animate-fade-up mx-auto flex max-w-[960px] flex-col gap-4">
          {rows.length === 0 ? (
            <div className="rounded-xl bg-card shadow-(--shadow-1)">
              <EmptyState
                icon={CheckCircle}
                iconTone="success"
                title="Todo al día"
                description={
                  <>
                    No hay facturas esperando revisión. Cuando lleguen nuevas
                    por WhatsApp, email o el{" "}
                    <Link
                      href="/subir-factura"
                      className="text-primary hover:underline"
                    >
                      formulario web
                    </Link>
                    , van a aparecer acá para que las revises.
                  </>
                }
              />
            </div>
          ) : (
            <QueueList rows={rows} />
          )}
        </div>
      </div>
    </div>
  );
}
