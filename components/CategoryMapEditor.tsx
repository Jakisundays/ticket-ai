"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { AlertCircle, Check, Loader2, Plus } from "lucide-react";
import { getPocketBase } from "@/lib/pocketbase-browser";
import { Collections, type BasCategoryMapRecord } from "@/lib/pocketbase-types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

type Draft = { codigo_item: string; confirmado: boolean };

export default function CategoryMapEditor() {
  const [rows, setRows] = useState<BasCategoryMapRecord[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  // Feedback inline por fila (reemplaza window.alert): id de la fila que
  // acaba de guardar bien (para el check verde temporal) y el mapa de
  // errores por fila (para el mensaje inline en vez de un alert bloqueante).
  const [justSavedId, setJustSavedId] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const [newCategoria, setNewCategoria] = useState("");
  const [newCodigo, setNewCodigo] = useState("");
  const [newConfirmado, setNewConfirmado] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);

  const savedTimers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    let isMounted = true;
    const pb = getPocketBase();

    pb.collection<BasCategoryMapRecord>(Collections.BasCategoryMap)
      .getFullList({ sort: "categoria" })
      .then((items) => {
        if (!isMounted) return;
        setRows(items);
        setDrafts(
          Object.fromEntries(
            items.map((item) => [
              item.id,
              { codigo_item: item.codigo_item, confirmado: item.confirmado },
            ])
          )
        );
      })
      .catch((error: unknown) => {
        if (!isMounted) return;
        setLoadError(
          error instanceof Error
            ? error.message
            : "No se pudo cargar el mapa de categorías."
        );
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    const timers = savedTimers.current;
    return () => {
      timers.forEach(clearTimeout);
    };
  }, []);

  function updateDraft(id: string, patch: Partial<Draft>) {
    setDrafts((prev) => ({
      ...prev,
      [id]: { ...prev[id], ...patch } as Draft,
    }));
    // Editar de nuevo cancela el "Guardado" y limpia un error previo de esa
    // fila, igual que en el mockup (dirty:true, justSaved:false).
    setJustSavedId((current) => (current === id ? null : current));
    setRowErrors((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  async function handleSave(row: BasCategoryMapRecord) {
    const draft = drafts[row.id];
    if (!draft) return;

    setSavingId(row.id);
    setRowErrors((prev) => {
      if (!(row.id in prev)) return prev;
      const next = { ...prev };
      delete next[row.id];
      return next;
    });
    try {
      const pb = getPocketBase();
      const updated = await pb
        .collection<BasCategoryMapRecord>(Collections.BasCategoryMap)
        .update(row.id, draft);

      setRows((prev) =>
        prev.map((item) => (item.id === row.id ? updated : item))
      );
      setJustSavedId(row.id);
      const timer = setTimeout(() => {
        setJustSavedId((current) => (current === row.id ? null : current));
      }, 1600);
      savedTimers.current.push(timer);
    } catch (error) {
      // Antes: window.alert(...). Ahora el error queda inline, solo en la
      // fila afectada, sin bloquear el resto de la pantalla.
      setRowErrors((prev) => ({
        ...prev,
        [row.id]:
          error instanceof Error
            ? error.message
            : "No se pudo guardar el cambio.",
      }));
    } finally {
      setSavingId(null);
    }
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setCreateError(null);

    if (!newCategoria.trim() || !newCodigo.trim()) {
      setCreateError("Categoría y código son obligatorios.");
      return;
    }

    setIsCreating(true);
    try {
      const pb = getPocketBase();
      const created = await pb
        .collection<BasCategoryMapRecord>(Collections.BasCategoryMap)
        .create({
          categoria: newCategoria.trim(),
          codigo_item: newCodigo.trim(),
          confirmado: newConfirmado,
        });

      setRows((prev) =>
        [...prev, created].sort((a, b) => a.categoria.localeCompare(b.categoria))
      );
      setDrafts((prev) => ({
        ...prev,
        [created.id]: {
          codigo_item: created.codigo_item,
          confirmado: created.confirmado,
        },
      }));
      setNewCategoria("");
      setNewCodigo("");
      setNewConfirmado(false);
    } catch (error) {
      setCreateError(
        error instanceof Error ? error.message : "No se pudo crear la categoría."
      );
    } finally {
      setIsCreating(false);
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 rounded-xl bg-card px-4 py-6 text-sm text-muted-foreground shadow-(--shadow-1)">
        <Loader2 className="size-4 animate-spin" />
        Cargando…
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex items-center gap-2 rounded-xl bg-card px-4 py-6 text-sm text-status-destructive-fg shadow-(--shadow-1)">
        <AlertCircle className="size-4 shrink-0" />
        {loadError}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Mobile/tablet: card por fila (la tabla de 4 columnas + input queda
          apretada en pantallas chicas). Desktop (md+): la tabla, sin cambios. */}
      <ul className="flex flex-col gap-2.5 md:hidden">
        {rows.map((row) => {
          const draft = drafts[row.id] ?? {
            codigo_item: row.codigo_item,
            confirmado: row.confirmado,
          };
          const isDirty =
            draft.codigo_item !== row.codigo_item ||
            draft.confirmado !== row.confirmado;
          const isSaving = savingId === row.id;
          const isJustSaved = justSavedId === row.id && !isDirty && !isSaving;
          const rowError = rowErrors[row.id];

          return (
            <li
              key={row.id}
              className="flex flex-col gap-3 rounded-xl bg-card p-4 shadow-(--shadow-1)"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="text-[13.5px] font-medium text-foreground">
                  {row.categoria}
                </span>
                <ConfirmadoToggle
                  checked={draft.confirmado}
                  onCheckedChange={() =>
                    updateDraft(row.id, { confirmado: !draft.confirmado })
                  }
                  ariaLabel={`Confirmado — ${row.categoria}`}
                />
              </div>
              <Input
                value={draft.codigo_item}
                onChange={(event) =>
                  updateDraft(row.id, { codigo_item: event.target.value })
                }
                className="font-mono text-[12.5px]"
                aria-label={`Código item para ${row.categoria}`}
              />
              <div className="flex items-center justify-end gap-2">
                {rowError && (
                  <span className="flex flex-1 items-center gap-1 text-xs font-medium text-status-destructive-fg">
                    <AlertCircle className="size-3.5 shrink-0" />
                    {rowError}
                  </span>
                )}
                {isSaving ? (
                  <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                    <Loader2 className="size-3.5 animate-spin" />
                    Guardando…
                  </span>
                ) : isJustSaved ? (
                  <span className="flex animate-in items-center gap-1.5 text-xs font-medium text-status-success-fg fade-in zoom-in-95 duration-200">
                    <Check className="size-3.5" />
                    Guardado
                  </span>
                ) : isDirty ? (
                  <Button type="button" size="sm" onClick={() => handleSave(row)}>
                    Guardar
                  </Button>
                ) : null}
              </div>
            </li>
          );
        })}
        {rows.length === 0 && (
          <li className="rounded-xl bg-card py-8 text-center text-muted-foreground shadow-(--shadow-1)">
            Todavía no hay categorías cargadas.
          </li>
        )}
      </ul>

      <div className="hidden overflow-hidden rounded-xl bg-card shadow-(--shadow-1) md:block">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="overline text-xs text-muted-foreground">
                Categoría
              </TableHead>
              <TableHead className="overline text-xs text-muted-foreground">
                Código item
              </TableHead>
              <TableHead className="overline text-center text-xs text-muted-foreground">
                Confirmado
              </TableHead>
              <TableHead className="w-[1%]" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const draft = drafts[row.id] ?? {
                codigo_item: row.codigo_item,
                confirmado: row.confirmado,
              };
              const isDirty =
                draft.codigo_item !== row.codigo_item ||
                draft.confirmado !== row.confirmado;
              const isSaving = savingId === row.id;
              const isJustSaved =
                justSavedId === row.id && !isDirty && !isSaving;
              const rowError = rowErrors[row.id];

              return (
                <TableRow key={row.id}>
                  <TableCell className="font-medium text-foreground">
                    {row.categoria}
                  </TableCell>
                  <TableCell>
                    <Input
                      value={draft.codigo_item}
                      onChange={(event) =>
                        updateDraft(row.id, { codigo_item: event.target.value })
                      }
                      className="w-32 font-mono text-[12.5px]"
                    />
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-center">
                      <ConfirmadoToggle
                        checked={draft.confirmado}
                        onCheckedChange={() =>
                          updateDraft(row.id, { confirmado: !draft.confirmado })
                        }
                        ariaLabel={`Confirmado — ${row.categoria}`}
                      />
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-2 whitespace-nowrap">
                      {rowError && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="flex items-center gap-1 text-xs font-medium text-status-destructive-fg">
                              <AlertCircle className="size-3.5" />
                              Error
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>{rowError}</TooltipContent>
                        </Tooltip>
                      )}
                      {isSaving ? (
                        <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                          <Loader2 className="size-3.5 animate-spin" />
                          Guardando…
                        </span>
                      ) : isJustSaved ? (
                        <span className="flex animate-in items-center gap-1.5 text-xs font-medium text-status-success-fg fade-in zoom-in-95 duration-200">
                          <Check className="size-3.5" />
                          Guardado
                        </span>
                      ) : isDirty ? (
                        <Button
                          type="button"
                          size="sm"
                          onClick={() => handleSave(row)}
                        >
                          Guardar
                        </Button>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
            {rows.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={4}
                  className="py-8 text-center text-muted-foreground"
                >
                  Todavía no hay categorías cargadas.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <form
        onSubmit={handleCreate}
        className="flex flex-col gap-3 rounded-xl bg-card p-5 shadow-(--shadow-1)"
      >
        <span className="text-[12.5px] font-semibold text-foreground">
          Agregar categoría
        </span>
        <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-[1fr_160px_auto_auto]">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="new-categoria" className="text-xs text-muted-foreground">
              Categoría nueva
            </Label>
            <Input
              id="new-categoria"
              value={newCategoria}
              onChange={(event) => setNewCategoria(event.target.value)}
              placeholder="Categoría interna"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="new-codigo" className="text-xs text-muted-foreground">
              Código item
            </Label>
            <Input
              id="new-codigo"
              value={newCodigo}
              onChange={(event) => setNewCodigo(event.target.value)}
              placeholder="5.x.xx"
              className="font-mono text-[12.5px]"
            />
          </div>
          <div className="flex flex-col items-center gap-1.5">
            <Label className="text-xs text-muted-foreground">Confirmado</Label>
            <ConfirmadoToggle
              checked={newConfirmado}
              onCheckedChange={() => setNewConfirmado((prev) => !prev)}
              ariaLabel="Confirmado — categoría nueva"
            />
          </div>
          <Button
            type="submit"
            disabled={isCreating || !newCategoria.trim() || !newCodigo.trim()}
            className="gap-1.5"
          >
            <Plus className="size-3.5" />
            {isCreating ? "Creando…" : "Agregar"}
          </Button>
        </div>
        {createError && (
          <p className="flex items-center gap-1.5 text-sm text-status-destructive-fg">
            <AlertCircle className="size-3.5 shrink-0" />
            {createError}
          </p>
        )}
      </form>
    </div>
  );
}

function ConfirmadoToggle({
  checked,
  onCheckedChange,
  disabled,
  ariaLabel,
}: {
  checked: boolean;
  onCheckedChange: () => void;
  disabled?: boolean;
  ariaLabel: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      onClick={onCheckedChange}
      disabled={disabled}
      className={cn(
        "relative h-[19px] w-[34px] shrink-0 rounded-full transition-colors after:absolute after:-inset-[13px] after:content-[''] disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-[oklch(0.85_0_0)]"
      )}
    >
      <span
        className={cn(
          "absolute top-0.5 size-[15px] rounded-full bg-white shadow-sm transition-transform duration-150",
          checked ? "translate-x-[17px]" : "translate-x-0.5"
        )}
      />
    </button>
  );
}
