import type { ReactNode } from "react";
import Link from "next/link";
import { Inbox, FileCheck, TrendingUp, AlertCircle, ChevronRight, UploadCloud } from "lucide-react";
import { createServerClient } from "@/lib/pocketbase-server";
import {
  Collections,
  type InvoicesRecord,
  type PaymentOrdersRecord,
  type ProcessingJobsRecord,
} from "@/lib/pocketbase-types";
import { formatCurrency, formatRelativeDateTime } from "@/lib/format";
import PageHeader from "@/components/PageHeader";
import MetricCard from "@/components/MetricCard";
import { Button } from "@/components/ui/button";
import RecentActivityTable, { type RecentActivityRow } from "./RecentActivityTable";

export const dynamic = "force-dynamic";

// Mismo filtro que app/(dashboard)/layout.tsx (badge del nav): "acción
// humana pendiente" (facturas ya procesadas que todavía nadie confirmó),
// no "actividad en curso" -- deliberadamente más angosto que
// app/(dashboard)/queue/page.tsx, que desde este cambio también muestra
// pending/processing/error para dar visibilidad temprana en la subida.
// review_status != "confirmed" -- no "= needs_review": filas legacy con
// review_status "" también cuentan como pendientes.
const FILTRO_COLA = 'status = "completed" && review_status != "confirmed"';

const MESES_ES = [
  "enero",
  "febrero",
  "marzo",
  "abril",
  "mayo",
  "junio",
  "julio",
  "agosto",
  "septiembre",
  "octubre",
  "noviembre",
  "diciembre",
];

// Argentina no observa horario de verano desde 2009 -- offset fijo UTC-3,
// válido todo el año (mismo supuesto que formatRelativeDateTime en
// lib/format.ts).
const AR_OFFSET_MS = -3 * 60 * 60 * 1000;

// Funciones de módulo puras (no hooks) -- mismo motivo que calcularAntiguedad
// en app/(dashboard)/queue/page.tsx: esta página es dynamic="force-dynamic"
// a propósito, así que depender de la hora real en cada request es el
// comportamiento buscado, no un bug.

/** Instante actual "corrido" -3h: leer sus getters *UTC* devuelve el
 * año/mes/día/hora de pared en Argentina sin depender de la zona horaria del
 * proceso Node. Nunca se le pasa a Intl ni se muestra directo -- es solo
 * para extraer componentes de calendario/hora AR. */
function ahoraCorridoAr(): Date {
  return new Date(Date.now() + AR_OFFSET_MS);
}

function saludoPorHora(horaAr: number): string {
  if (horaAr >= 5 && horaAr < 12) return "Buen día";
  if (horaAr >= 12 && horaAr < 20) return "Buenas tardes";
  return "Buenas noches";
}

function capitalizar(texto: string): string {
  return texto.charAt(0).toUpperCase() + texto.slice(1);
}

/** Medianoche del día 1 de (year, monthIndex) en hora AR (UTC-3), expresada
 * como instante UTC real -- 00:00 AR = 03:00 UTC ese mismo día calendario.
 * Date.UTC ya resuelve el rollover de mes/año (monthIndex=12 -> enero del
 * año siguiente, monthIndex=-1 -> diciembre del año anterior), así que sirve
 * tanto para el límite superior del mes actual como para el mes anterior sin
 * lógica extra. PocketBase interpreta los filtros en UTC -- por eso este
 * instante (no un string de fecha "AR" directo) es lo que hay que mandarle. */
function inicioDeMesArEnUtc(year: number, monthIndex: number): Date {
  return new Date(Date.UTC(year, monthIndex, 1, 3, 0, 0));
}

function diasDeEspera(createdIso: string): number {
  const created = new Date(createdIso);
  if (Number.isNaN(created.getTime())) return 0;
  return Math.floor((Date.now() - created.getTime()) / 86400000);
}

/** Instante hace N días -- envuelto en una función de módulo por el mismo
 * motivo que ahoraCorridoAr(): el linter de purity de React no permite
 * llamar a Date.now() directo en el cuerpo del componente. */
function haceNDias(n: number): Date {
  return new Date(Date.now() - n * 86400000);
}

function fraseAntiguedadMasVieja(dias: number): string {
  if (dias <= 0) return "La más antigua espera desde hoy";
  if (dias === 1) return "La más antigua espera hace 1 día";
  return `La más antigua espera hace ${dias} días`;
}

/** Mismo criterio que calcularAntiguedad en queue/page.tsx (hoy/ayer/hace N
 * días), con el prefijo "Espera " que pide el mockup para cada fila de la
 * cola de revisión. */
function fraseEspera(dias: number): string {
  if (dias <= 0) return "Espera hoy";
  if (dias === 1) return "Espera desde ayer";
  return `Espera hace ${dias} días`;
}

/** Deriva el único StatusBadge de una fila de "Actividad reciente" a partir
 * de dos campos (status + review_status): una factura "completed" sin
 * confirmar todavía se muestra como "needs_review" (no "completed" -- ese
 * bucket de StatusBadge no describe si ya se revisó o no), y "completed" +
 * confirmada se muestra como "confirmed". pending/processing/error se
 * muestran tal cual. */
function estadoActividad(invoice: InvoicesRecord): string {
  if (invoice.status === "error") return "error";
  if (invoice.status === "processing" || invoice.status === "pending") {
    return invoice.status;
  }
  return invoice.review_status === "confirmed" ? "confirmed" : "needs_review";
}

function monedaMasFrecuente(items: { moneda: string }[]): string {
  if (items.length === 0) return "ARS";
  const conteos = new Map<string, number>();
  for (const item of items) {
    const key = item.moneda || "ARS";
    conteos.set(key, (conteos.get(key) ?? 0) + 1);
  }
  let mejor = "ARS";
  let mejorConteo = -1;
  for (const [key, count] of conteos) {
    if (count > mejorConteo) {
      mejor = key;
      mejorConteo = count;
    }
  }
  return mejor;
}

type InvoiceMonthRow = Pick<InvoicesRecord, "process_id" | "total" | "moneda">;
type ProcessingJobEmailRow = Pick<ProcessingJobsRecord, "process_id">;

export default async function InicioPage() {
  const pb = await createServerClient();
  const record = pb.authStore.record;
  const name = typeof record?.name === "string" ? record.name : undefined;
  const email = typeof record?.email === "string" ? record.email : undefined;
  // Mismo patrón de fallback que components/UserMenu.tsx.
  const displayName = name || email || "";

  const arNow = ahoraCorridoAr();
  const year = arNow.getUTCFullYear();
  const monthIndex = arNow.getUTCMonth();
  const horaAr = arNow.getUTCHours();

  const inicioMesActual = inicioDeMesArEnUtc(year, monthIndex);
  const inicioMesSiguiente = inicioDeMesArEnUtc(year, monthIndex + 1);
  const inicioMesAnterior = inicioDeMesArEnUtc(year, monthIndex - 1);
  const haceUnaSemana = haceNDias(7);

  const overlineMes = capitalizar(MESES_ES[monthIndex]);
  const mesAnteriorLabel = MESES_ES[(monthIndex + 11) % 12];

  const filtroMesActual = pb.filter(
    'status = "completed" && created >= {:start} && created < {:end}',
    { start: inicioMesActual, end: inicioMesSiguiente }
  );
  const filtroMesAnterior = pb.filter(
    'status = "completed" && created >= {:start} && created < {:end}',
    { start: inicioMesAnterior, end: inicioMesActual }
  );
  const filtroFallidasSemana = pb.filter(
    'status = "failed" && last_attempt_at >= {:desde}',
    { desde: haceUnaSemana }
  );

  // Las 6 queries son independientes entre sí -- se paralelizan con
  // Promise.all en vez de encadenarlas (ver docs/plan-rediseno-legado.md,
  // Fase 4, Riesgos: "paralelizar con Promise.all"). 4 de las 6 le pegan a
  // Collections.Invoices: sin requestKey explícito, el SDK de PocketBase
  // autocancela requests concurrentes al mismo endpoint (ver
  // https://github.com/pocketbase/js-sdk#auto-cancellation), lo que tira
  // abajo la página entera con un ClientResponseError "aborted". Cada
  // llamada necesita su propio requestKey para desactivar esa
  // autocancelación entre sí.
  const [pendientes, mesAnteriorResult, itemsDelMes, fallidasResult, actividadResult, jobsConEmail] =
    await Promise.all([
      pb.collection<InvoicesRecord>(Collections.Invoices).getList(1, 100, {
        filter: FILTRO_COLA,
        sort: "+created",
        requestKey: "inicio_pendientes",
      }),
      pb.collection<InvoicesRecord>(Collections.Invoices).getList(1, 1, {
        filter: filtroMesAnterior,
        requestKey: "inicio_mes_anterior",
      }),
      pb
        .collection<InvoicesRecord>(Collections.Invoices)
        .getFullList<InvoiceMonthRow>({
          filter: filtroMesActual,
          fields: "process_id,total,moneda",
          requestKey: "inicio_items_del_mes",
        }),
      pb.collection<PaymentOrdersRecord>(Collections.PaymentOrders).getList(1, 1, {
        filter: filtroFallidasSemana,
        requestKey: "inicio_fallidas_semana",
      }),
      pb.collection<InvoicesRecord>(Collections.Invoices).getList(1, 6, {
        sort: "-created",
        requestKey: "inicio_actividad_reciente",
      }),
      // Se usa como set de pertenencia por process_id, no filtrado por fecha:
      // un job pudo crearse antes de este mes y su factura completarse recién
      // ahora -- filtrar jobs por fecha perdería esos casos. Ver la nota de
      // honestidad de datos más abajo (Canales de ingreso).
      pb
        .collection<ProcessingJobsRecord>(Collections.ProcessingJobs)
        .getFullList<ProcessingJobEmailRow>({
          filter: 'from_email != ""',
          fields: "process_id",
          requestKey: "inicio_jobs_con_email",
        }),
    ]);

  // --- Metric (a): Pendientes de revisión ---
  const pendientesCount = pendientes.totalItems;
  const diasMasVieja = pendientes.items[0] ? diasDeEspera(pendientes.items[0].created) : 0;
  const hintPendientes =
    pendientesCount === 0 ? "Todo al día" : fraseAntiguedadMasVieja(diasMasVieja);

  // --- Metric (b): Procesadas este mes (+ delta vs. mes anterior) ---
  const procesadasCount = itemsDelMes.length;
  const procesadasMesAnteriorCount = mesAnteriorResult.totalItems;
  let hintProcesadas: ReactNode;
  if (procesadasMesAnteriorCount === 0) {
    hintProcesadas =
      procesadasCount > 0 ? (
        <span className="text-status-success-fg">nuevo este mes</span>
      ) : (
        `Sin comprobantes procesados en ${mesAnteriorLabel}`
      );
  } else {
    const delta = Math.round(
      ((procesadasCount - procesadasMesAnteriorCount) / procesadasMesAnteriorCount) * 100
    );
    const texto = `${delta > 0 ? "+" : ""}${delta}% respecto de ${mesAnteriorLabel}`;
    hintProcesadas =
      procesadasCount >= procesadasMesAnteriorCount ? (
        <span className="text-status-success-fg">{texto}</span>
      ) : (
        texto
      );
  }

  // --- Metric (c): Volumen del mes ---
  const monedaVolumen = monedaMasFrecuente(itemsDelMes);
  const volumenTotal = itemsDelMes.reduce((sum, item) => sum + (item.total ?? 0), 0);
  const hintVolumen = `${monedaVolumen} · ${itemsDelMes.length} comprobante${
    itemsDelMes.length === 1 ? "" : "s"
  }`;

  // --- Metric (d): Órdenes fallidas ---
  const fallidasCount = fallidasResult.totalItems;
  const hintFallidas =
    fallidasCount === 0
      ? "Sin órdenes fallidas esta semana"
      : `BAS rechazó ${fallidasCount} orden${fallidasCount === 1 ? "" : "es"} esta semana`;

  // --- Actividad reciente ---
  const actividadRows: RecentActivityRow[] = actividadResult.items.map((invoice) => ({
    id: invoice.id,
    href: `/invoices/${invoice.id}`,
    proveedorNombre: invoice.emisor_nombre || "Emisor sin identificar",
    fecha: formatRelativeDateTime(invoice.created),
    numero: invoice.numero_comprobante || invoice.process_id,
    monto: formatCurrency(invoice.total, invoice.moneda),
    estado: estadoActividad(invoice),
  }));

  // --- Cola de revisión (panel, top 3 de la misma query que la metric (a)) ---
  const colaPreview = pendientes.items.slice(0, 3).map((invoice) => ({
    id: invoice.id,
    proveedorNombre: invoice.emisor_nombre || "Emisor sin identificar",
    espera: fraseEspera(diasDeEspera(invoice.created)),
    monto: formatCurrency(invoice.total, invoice.moneda),
  }));

  // --- Canales de ingreso ---
  // RESTRICCIÓN DE HONESTIDAD DE DATOS: el schema real (ProcessingJobsRecord)
  // no distingue WhatsApp/Email/Formulario web de forma confiable -- no hay
  // campo de "canal". Lo único verificable es si una factura tiene, o no, un
  // processing_job asociado con from_email no vacío (unidos por process_id,
  // la misma clave que ya usa el resto de la app -- ver invoice.process_id en
  // queue/page.tsx, InvoiceFileViewer, PaymentOrderPanel). Por eso acá se
  // arman 2 buckets reales ("Por email" / "Subida manual") en vez de los 3
  // canales inventados del mockup (WhatsApp/Email/Formulario).
  const processIdsConEmail = new Set(jobsConEmail.map((job) => job.process_id));
  let porEmailCount = 0;
  let subidaManualCount = 0;
  for (const item of itemsDelMes) {
    if (item.process_id && processIdsConEmail.has(item.process_id)) {
      porEmailCount += 1;
    } else {
      subidaManualCount += 1;
    }
  }
  const totalCanales = itemsDelMes.length;

  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-base font-semibold text-foreground">Inicio</h1>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="animate-fade-up mx-auto flex max-w-[1240px] flex-col gap-7">
          <div className="flex flex-col items-start justify-between gap-4 min-[761px]:flex-row min-[761px]:items-end">
            <div>
              <p className="font-heading text-[0.7rem] font-semibold tracking-[.08em] text-muted-foreground uppercase">
                Resumen operativo · {overlineMes} {year}
              </p>
              <h2 className="mt-1.5 font-heading text-[1.65rem] font-bold text-primary">
                {saludoPorHora(horaAr)}
                {displayName ? `, ${displayName}` : ""}
              </h2>
              <p className="mt-1.5 text-[0.95rem] leading-relaxed text-muted-foreground">
                Hay{" "}
                <Link href="/queue" className="font-semibold text-foreground hover:underline">
                  {pendientesCount} factura{pendientesCount === 1 ? "" : "s"} esperando revisión
                </Link>{" "}
                y {fallidasCount} orden{fallidasCount === 1 ? "" : "es"} de pago que necesitan
                atención.
              </p>
            </div>
            <Button asChild size="default" className="shrink-0">
              <Link href="/subir-factura-equipo">
                <UploadCloud className="size-4" />
                Subir factura
              </Link>
            </Button>
          </div>

          <div className="grid grid-cols-1 gap-4 min-[481px]:grid-cols-2 min-[761px]:grid-cols-4">
            <MetricCard
              label="Pendientes de revisión"
              value={pendientesCount}
              hint={hintPendientes}
              icon={Inbox}
              iconTone="warning"
              href="/queue"
            />
            <MetricCard
              label="Procesadas este mes"
              value={procesadasCount}
              hint={hintProcesadas}
              icon={FileCheck}
              iconTone="info"
            />
            <MetricCard
              label="Volumen del mes"
              value={formatCurrency(volumenTotal, monedaVolumen)}
              hint={hintVolumen}
              icon={TrendingUp}
              iconTone="info"
            />
            <MetricCard
              label="Órdenes fallidas"
              value={fallidasCount}
              hint={hintFallidas}
              icon={AlertCircle}
              iconTone="destructive"
              href="/payment-orders"
            />
          </div>

          <div className="grid grid-cols-1 items-start gap-4 min-[1101px]:grid-cols-[1fr_340px]">
            <section className="overflow-hidden rounded-xl bg-card shadow-(--shadow-1)">
              <div className="flex items-center justify-between px-6 pt-[18px] pb-3.5">
                <h3 className="font-heading text-[15px] font-semibold text-foreground">
                  Actividad reciente
                </h3>
                <Link
                  href="/invoices"
                  className="font-heading text-sm font-semibold whitespace-nowrap hover:underline"
                >
                  Ver todas →
                </Link>
              </div>
              <RecentActivityTable rows={actividadRows} />
            </section>

            <div className="flex flex-col gap-4">
              <section className="flex flex-col rounded-xl bg-card p-5 shadow-(--shadow-1)">
                <div className="mb-3.5 flex items-center justify-between gap-2">
                  <h3 className="font-heading text-[15px] font-semibold text-foreground">
                    Cola de revisión
                  </h3>
                  <span className="rounded-full bg-status-warning-bg px-3 py-1 font-heading text-xs font-semibold whitespace-nowrap text-status-warning-fg">
                    {pendientesCount} pendiente{pendientesCount === 1 ? "" : "s"}
                  </span>
                </div>

                {colaPreview.length === 0 ? (
                  <p className="py-3 text-center text-[13px] text-muted-foreground">
                    Todo al día
                  </p>
                ) : (
                  <div className="flex flex-col">
                    {colaPreview.map((row) => (
                      <Link
                        key={row.id}
                        href={`/invoices/${row.id}`}
                        className="flex items-center gap-3 border-t py-2.5 first:border-t-0 hover:bg-accent/40"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[13.5px] font-semibold text-foreground">
                            {row.proveedorNombre}
                          </div>
                          <div className="text-xs text-muted-foreground">{row.espera}</div>
                        </div>
                        <span className="font-mono text-[13px] font-semibold whitespace-nowrap text-foreground">
                          {row.monto}
                        </span>
                        <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
                      </Link>
                    ))}
                  </div>
                )}

                <Button asChild variant="secondary" className="mt-3.5 w-full">
                  <Link href="/queue">Revisar la cola</Link>
                </Button>
              </section>

              <section className="rounded-xl bg-muted/40 p-5">
                <h3 className="mb-1 font-heading text-[15px] font-semibold text-foreground">
                  Canales de ingreso
                </h3>
                {totalCanales === 0 ? (
                  <p className="text-[13px] leading-relaxed text-muted-foreground">
                    Aún no tenemos seguimiento de canal de ingreso.
                  </p>
                ) : (
                  <>
                    <p className="mb-3 text-[13px] leading-relaxed text-muted-foreground">
                      Facturas recibidas este mes por canal.
                    </p>
                    <div className="flex flex-col gap-2.5">
                      <ChannelBar label="Por email" count={porEmailCount} total={totalCanales} />
                      <ChannelBar
                        label="Subida manual"
                        count={subidaManualCount}
                        total={totalCanales}
                      />
                    </div>
                  </>
                )}
              </section>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ChannelBar({ label, count, total }: { label: string; count: number; total: number }) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  return (
    <div className="flex items-center gap-2.5">
      <span className="w-[104px] shrink-0 font-heading text-xs font-semibold text-foreground">
        {label}
      </span>
      <span className="h-2 flex-1 overflow-hidden rounded-full bg-primary/12">
        <span className="block h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </span>
      <span className="w-7 shrink-0 text-right font-mono text-xs font-semibold text-muted-foreground">
        {count}
      </span>
    </div>
  );
}
