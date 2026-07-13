"use client";

import { useEffect, useState, type FormEvent } from "react";
import { getPocketBase } from "@/lib/pocketbase-browser";
import { Collections, type BasCategoryMapRecord } from "@/lib/pocketbase-types";

type Draft = { codigo_item: string; confirmado: boolean };

export default function CategoryMapEditor() {
  const [rows, setRows] = useState<BasCategoryMapRecord[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const [newCategoria, setNewCategoria] = useState("");
  const [newCodigo, setNewCodigo] = useState("");
  const [newConfirmado, setNewConfirmado] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);

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

  async function handleSave(row: BasCategoryMapRecord) {
    const draft = drafts[row.id];
    if (!draft) return;

    setSavingId(row.id);
    try {
      const pb = getPocketBase();
      const updated = await pb
        .collection<BasCategoryMapRecord>(Collections.BasCategoryMap)
        .update(row.id, draft);

      setRows((prev) =>
        prev.map((item) => (item.id === row.id ? updated : item))
      );
    } catch (error) {
      window.alert(
        error instanceof Error ? error.message : "No se pudo guardar el cambio."
      );
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
    return <p className="text-sm text-gray-400">Cargando…</p>;
  }

  if (loadError) {
    return <p className="text-sm text-red-600">{loadError}</p>;
  }

  return (
    <div className="space-y-6">
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50 text-left text-xs font-medium uppercase text-gray-500">
            <tr>
              <th className="px-4 py-2">Categoría</th>
              <th className="px-4 py-2">Código item</th>
              <th className="px-4 py-2">Confirmado</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((row) => {
              const draft = drafts[row.id] ?? {
                codigo_item: row.codigo_item,
                confirmado: row.confirmado,
              };
              const isDirty =
                draft.codigo_item !== row.codigo_item ||
                draft.confirmado !== row.confirmado;

              return (
                <tr key={row.id}>
                  <td className="px-4 py-2 font-medium text-gray-900">
                    {row.categoria}
                  </td>
                  <td className="px-4 py-2">
                    <input
                      type="text"
                      value={draft.codigo_item}
                      onChange={(event) =>
                        setDrafts((prev) => ({
                          ...prev,
                          [row.id]: { ...draft, codigo_item: event.target.value },
                        }))
                      }
                      className="w-32 rounded-md border border-gray-300 px-2 py-1 text-sm"
                    />
                  </td>
                  <td className="px-4 py-2">
                    <input
                      type="checkbox"
                      checked={draft.confirmado}
                      onChange={(event) =>
                        setDrafts((prev) => ({
                          ...prev,
                          [row.id]: { ...draft, confirmado: event.target.checked },
                        }))
                      }
                    />
                  </td>
                  <td className="px-4 py-2">
                    <button
                      type="button"
                      disabled={!isDirty || savingId === row.id}
                      onClick={() => handleSave(row)}
                      className="rounded-md bg-gray-900 px-3 py-1 text-xs font-medium text-white hover:bg-gray-800 disabled:opacity-40"
                    >
                      {savingId === row.id ? "Guardando…" : "Guardar"}
                    </button>
                  </td>
                </tr>
              );
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-gray-400">
                  Todavía no hay categorías cargadas.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <form
        onSubmit={handleCreate}
        className="flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 bg-white p-4"
      >
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-500">
            Categoría nueva
          </label>
          <input
            type="text"
            value={newCategoria}
            onChange={(event) => setNewCategoria(event.target.value)}
            className="w-48 rounded-md border border-gray-300 px-2 py-1 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-500">
            Código item
          </label>
          <input
            type="text"
            value={newCodigo}
            onChange={(event) => setNewCodigo(event.target.value)}
            className="w-32 rounded-md border border-gray-300 px-2 py-1 text-sm"
          />
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-600">
          <input
            type="checkbox"
            checked={newConfirmado}
            onChange={(event) => setNewConfirmado(event.target.checked)}
          />
          Confirmado
        </label>
        <button
          type="submit"
          disabled={isCreating}
          className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-40"
        >
          {isCreating ? "Creando…" : "Agregar categoría"}
        </button>
        {createError && (
          <p className="w-full text-sm text-red-600">{createError}</p>
        )}
      </form>
    </div>
  );
}
