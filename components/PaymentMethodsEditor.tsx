"use client";

import { useEffect, useRef, useState } from "react";
import { AlertCircle, Check, Loader2 } from "lucide-react";
import { getPocketBase } from "@/lib/pocketbase-browser";
import {
  Collections,
  type BasPaymentMethodsRecord,
  type MetodoPago,
} from "@/lib/pocketbase-types";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

// A diferencia de bas_category_map (categorías libres, se agregan con el
// tiempo), metodo_pago es un select CERRADO -- cada valor está acoplado a
// una rama de código Python (METODO_PAGO_ARRAY_BAS en utils/bas_config.py).
// Por eso este editor no tiene formulario de "agregar nuevo": siempre
// muestra las 4 filas posibles, y crea el record recién al guardar si
// todavía no existe (típicamente cheque/transferencia/tarjeta, sin sembrar
// por migración -- salvo tarjeta, que sí trae una fila semilla sin
// confirmar desde la migración que la agregó).
const METODOS: { value: MetodoPago; label: string }[] = [
  { value: "efectivo", label: "Efectivo" },
  { value: "cheque", label: "Cheque" },
  { value: "transferencia", label: "Transferencia" },
  { value: "tarjeta", label: "Tarjeta" },
];

type Draft = {
  id: string | null;
  bas_medio_pago_codigo: string;
  bas_cuenta_bancaria: string;
  bas_plan_tarjeta: string;
  bas_codigo_tarjeta: string;
  confirmado: boolean;
};

function emptyDrafts(): Record<MetodoPago, Draft> {
  return Object.fromEntries(
    METODOS.map((m) => [
      m.value,
      {
        id: null,
        bas_medio_pago_codigo: "",
        bas_cuenta_bancaria: "",
        bas_plan_tarjeta: "",
        bas_codigo_tarjeta: "",
        confirmado: false,
      },
    ])
  ) as Record<MetodoPago, Draft>;
}

export default function PaymentMethodsEditor() {
  const [drafts, setDrafts] = useState<Record<MetodoPago, Draft>>(emptyDrafts);
  const [saved, setSaved] = useState<Record<MetodoPago, Draft>>(drafts);
  const [savingMetodo, setSavingMetodo] = useState<MetodoPago | null>(null);
  const [justSaved, setJustSaved] = useState<Partial<Record<MetodoPago, boolean>>>({});
  const [errors, setErrors] = useState<Partial<Record<MetodoPago, string>>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const savedTimers = useRef<Partial<Record<MetodoPago, ReturnType<typeof setTimeout>>>>({});

  useEffect(() => {
    let isMounted = true;
    const pb = getPocketBase();

    pb.collection<BasPaymentMethodsRecord>(Collections.BasPaymentMethods)
      .getFullList()
      .then((items) => {
        if (!isMounted) return;
        const next = { ...drafts };
        for (const item of items) {
          next[item.metodo_pago] = {
            id: item.id,
            bas_medio_pago_codigo: item.bas_medio_pago_codigo,
            bas_cuenta_bancaria: item.bas_cuenta_bancaria,
            bas_plan_tarjeta: item.bas_plan_tarjeta,
            bas_codigo_tarjeta: item.bas_codigo_tarjeta,
            confirmado: item.confirmado,
          };
        }
        setDrafts(next);
        setSaved(next);
      })
      .catch((error: unknown) => {
        if (!isMounted) return;
        setLoadError(
          error instanceof Error ? error.message : "No se pudo cargar los métodos de pago."
        );
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });

    return () => {
      isMounted = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const timers = savedTimers.current;
    return () => {
      Object.values(timers).forEach((t) => {
        if (t) clearTimeout(t);
      });
    };
  }, []);

  function updateDraft(metodo: MetodoPago, patch: Partial<Draft>) {
    setDrafts((prev) => ({ ...prev, [metodo]: { ...prev[metodo], ...patch } }));
    // El usuario está corrigiendo la fila: el error anterior (si lo había)
    // ya no aplica al intento nuevo.
    setErrors((prev) => (prev[metodo] ? { ...prev, [metodo]: undefined } : prev));
  }

  async function handleSave(metodo: MetodoPago) {
    const draft = drafts[metodo];
    setSavingMetodo(metodo);
    setErrors((prev) => ({ ...prev, [metodo]: undefined }));
    const pendingTimer = savedTimers.current[metodo];
    if (pendingTimer) clearTimeout(pendingTimer);

    try {
      const pb = getPocketBase();
      const payload = {
        metodo_pago: metodo,
        bas_medio_pago_codigo: draft.bas_medio_pago_codigo,
        bas_cuenta_bancaria: draft.bas_cuenta_bancaria,
        bas_plan_tarjeta: draft.bas_plan_tarjeta,
        bas_codigo_tarjeta: draft.bas_codigo_tarjeta,
        confirmado: draft.confirmado,
      };
      const record = draft.id
        ? await pb.collection<BasPaymentMethodsRecord>(Collections.BasPaymentMethods).update(draft.id, payload)
        : await pb.collection<BasPaymentMethodsRecord>(Collections.BasPaymentMethods).create(payload);

      const next = {
        ...draft,
        id: record.id,
        bas_medio_pago_codigo: record.bas_medio_pago_codigo,
        bas_cuenta_bancaria: record.bas_cuenta_bancaria,
        bas_plan_tarjeta: record.bas_plan_tarjeta,
        bas_codigo_tarjeta: record.bas_codigo_tarjeta,
        confirmado: record.confirmado,
      };
      setDrafts((prev) => ({ ...prev, [metodo]: next }));
      setSaved((prev) => ({ ...prev, [metodo]: next }));
      setJustSaved((prev) => ({ ...prev, [metodo]: true }));
      savedTimers.current[metodo] = setTimeout(() => {
        setJustSaved((prev) => ({ ...prev, [metodo]: false }));
      }, 1600);
    } catch (error) {
      setErrors((prev) => ({
        ...prev,
        [metodo]: error instanceof Error ? error.message : "No se pudo guardar el cambio.",
      }));
    } finally {
      setSavingMetodo(null);
    }
  }

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Cargando…</p>;
  }

  if (loadError) {
    return <p className="text-sm text-status-destructive-fg">{loadError}</p>;
  }

  return (
    <>
      {/* Mobile/tablet: card por método (2 inputs + toggle apilados no
          entran en una tabla de 5 columnas en pantalla chica). Desktop
          (md+): la tabla, sin cambios. */}
      <ul className="flex flex-col gap-2.5 md:hidden">
        {METODOS.map(({ value, label }) => {
          const draft = drafts[value];
          const original = saved[value];
          const isDirty =
            draft.bas_medio_pago_codigo !== original.bas_medio_pago_codigo ||
            draft.bas_cuenta_bancaria !== original.bas_cuenta_bancaria ||
            draft.bas_plan_tarjeta !== original.bas_plan_tarjeta ||
            draft.bas_codigo_tarjeta !== original.bas_codigo_tarjeta ||
            draft.confirmado !== original.confirmado;
          const isSaving = savingMetodo === value;
          const error = errors[value];
          const showSaved = Boolean(justSaved[value]) && !isDirty && !isSaving;
          const requiereCuenta = value === "transferencia";
          const requiereTarjeta = value === "tarjeta";

          return (
            <li
              key={value}
              className="flex flex-col gap-3 rounded-xl bg-card p-4 shadow-(--shadow-1)"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="text-[13.5px] font-medium text-foreground">
                  {label}
                </span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={draft.confirmado}
                  aria-label={`Confirmado — ${label}`}
                  onClick={() => updateDraft(value, { confirmado: !draft.confirmado })}
                  className={cn(
                    "relative h-[19px] w-[34px] shrink-0 rounded-full transition-colors outline-none after:absolute after:-inset-[13px] after:content-[''] focus-visible:ring-3 focus-visible:ring-ring/50",
                    draft.confirmado ? "bg-primary" : "bg-[oklch(0.85_0_0)] dark:bg-muted-foreground/30"
                  )}
                >
                  <span
                    className={cn(
                      "absolute top-0.5 size-[15px] rounded-full bg-white shadow-sm transition-[left]",
                      draft.confirmado ? "left-[17px]" : "left-0.5"
                    )}
                  />
                </button>
              </div>
              <Input
                value={draft.bas_medio_pago_codigo}
                onChange={(event) =>
                  updateDraft(value, { bas_medio_pago_codigo: event.target.value })
                }
                placeholder="Código MedioPago"
                className="text-[13px]"
              />
              {requiereCuenta && (
                <Input
                  value={draft.bas_cuenta_bancaria}
                  onChange={(event) =>
                    updateDraft(value, { bas_cuenta_bancaria: event.target.value })
                  }
                  placeholder="Banco, tipo y número de cuenta"
                  className="font-mono text-[13px]"
                />
              )}
              {requiereTarjeta && (
                <>
                  <Input
                    value={draft.bas_plan_tarjeta}
                    onChange={(event) =>
                      updateDraft(value, { bas_plan_tarjeta: event.target.value })
                    }
                    placeholder="Código de Plan (BAS)"
                    className="font-mono text-[13px]"
                  />
                  <Input
                    value={draft.bas_codigo_tarjeta}
                    onChange={(event) =>
                      updateDraft(value, { bas_codigo_tarjeta: event.target.value })
                    }
                    placeholder="Código de Tarjeta (BAS)"
                    className="font-mono text-[13px]"
                  />
                </>
              )}
              <div className="flex h-8 items-center justify-end">
                {isSaving ? (
                  <Loader2 className="size-[15px] animate-spin text-muted-foreground" />
                ) : error ? (
                  <button
                    type="button"
                    onClick={() => handleSave(value)}
                    className="flex items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-status-destructive-fg hover:bg-status-destructive-bg"
                  >
                    <AlertCircle className="size-[13px]" />
                    {error}
                  </button>
                ) : showSaved ? (
                  <span className="animate-in zoom-in-75 fade-in flex items-center gap-1.5 text-xs font-medium text-status-success-fg duration-200">
                    <Check className="size-[13px]" />
                    Guardado
                  </span>
                ) : isDirty ? (
                  <button
                    type="button"
                    onClick={() => handleSave(value)}
                    className="h-8 w-full rounded-lg bg-primary px-2.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 active:translate-y-px"
                  >
                    Guardar
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>

      <div className="hidden overflow-hidden rounded-xl bg-card shadow-(--shadow-1) md:block">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="overline h-[38px] w-[140px] text-[11px] text-muted-foreground">
              Método
            </TableHead>
            <TableHead className="overline h-[38px] w-[130px] text-[11px] text-muted-foreground">
              Código BAS
            </TableHead>
            <TableHead className="overline h-[38px] text-[11px] text-muted-foreground">
              Datos extra (transferencia/tarjeta)
            </TableHead>
            <TableHead className="overline h-[38px] w-[92px] text-center text-[11px] text-muted-foreground">
              Confirmado
            </TableHead>
            <TableHead className="h-[38px] w-[120px]" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {METODOS.map(({ value, label }) => {
            const draft = drafts[value];
            const original = saved[value];
            const isDirty =
              draft.bas_medio_pago_codigo !== original.bas_medio_pago_codigo ||
              draft.bas_cuenta_bancaria !== original.bas_cuenta_bancaria ||
              draft.bas_plan_tarjeta !== original.bas_plan_tarjeta ||
              draft.bas_codigo_tarjeta !== original.bas_codigo_tarjeta ||
              draft.confirmado !== original.confirmado;
            const isSaving = savingMetodo === value;
            const error = errors[value];
            const showSaved = Boolean(justSaved[value]) && !isDirty && !isSaving;
            const requiereCuenta = value === "transferencia";
            const requiereTarjeta = value === "tarjeta";

            return (
              <TableRow key={value} className="hover:bg-transparent">
                <TableCell className="align-middle text-[13.5px] font-medium text-foreground">
                  {label}
                </TableCell>
                <TableCell className="align-middle">
                  <Input
                    value={draft.bas_medio_pago_codigo}
                    onChange={(event) =>
                      updateDraft(value, { bas_medio_pago_codigo: event.target.value })
                    }
                    placeholder="Código MedioPago"
                    className="h-8 w-full text-[13px]"
                  />
                </TableCell>
                <TableCell className="align-middle">
                  {requiereCuenta && (
                    <Input
                      value={draft.bas_cuenta_bancaria}
                      onChange={(event) =>
                        updateDraft(value, { bas_cuenta_bancaria: event.target.value })
                      }
                      placeholder="Banco, tipo y número de cuenta"
                      className="h-8 w-full font-mono text-[13px]"
                    />
                  )}
                  {requiereTarjeta && (
                    <div className="flex gap-1.5">
                      <Input
                        value={draft.bas_plan_tarjeta}
                        onChange={(event) =>
                          updateDraft(value, { bas_plan_tarjeta: event.target.value })
                        }
                        placeholder="Código de Plan"
                        className="h-8 w-full font-mono text-[13px]"
                      />
                      <Input
                        value={draft.bas_codigo_tarjeta}
                        onChange={(event) =>
                          updateDraft(value, { bas_codigo_tarjeta: event.target.value })
                        }
                        placeholder="Código de Tarjeta"
                        className="h-8 w-full font-mono text-[13px]"
                      />
                    </div>
                  )}
                </TableCell>
                <TableCell className="align-middle">
                  <div className="flex justify-center">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={draft.confirmado}
                      aria-label={`Confirmado — ${label}`}
                      onClick={() => updateDraft(value, { confirmado: !draft.confirmado })}
                      className={cn(
                        "relative h-[19px] w-[34px] shrink-0 rounded-full transition-colors outline-none after:absolute after:-inset-[13px] after:content-[''] focus-visible:ring-3 focus-visible:ring-ring/50",
                        draft.confirmado ? "bg-primary" : "bg-[oklch(0.85_0_0)] dark:bg-muted-foreground/30"
                      )}
                    >
                      <span
                        className={cn(
                          "absolute top-0.5 size-[15px] rounded-full bg-white shadow-sm transition-[left]",
                          draft.confirmado ? "left-[17px]" : "left-0.5"
                        )}
                      />
                    </button>
                  </div>
                </TableCell>
                <TableCell className="align-middle">
                  <div className="flex h-8 items-center justify-end">
                    {isSaving ? (
                      <Loader2 className="size-[15px] animate-spin text-muted-foreground" />
                    ) : error ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            onClick={() => handleSave(value)}
                            className="flex items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-status-destructive-fg hover:bg-status-destructive-bg"
                          >
                            <AlertCircle className="size-[13px]" />
                            Reintentar
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side="left">{error}</TooltipContent>
                      </Tooltip>
                    ) : showSaved ? (
                      <span className="animate-in zoom-in-75 fade-in flex items-center gap-1.5 text-xs font-medium text-status-success-fg duration-200">
                        <Check className="size-[13px]" />
                        Guardado
                      </span>
                    ) : isDirty ? (
                      <button
                        type="button"
                        onClick={() => handleSave(value)}
                        className="h-7 rounded-lg bg-primary px-2.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 active:translate-y-px"
                      >
                        Guardar
                      </button>
                    ) : null}
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      </div>
    </>
  );
}
