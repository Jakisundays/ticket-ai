"use client";

import { useEffect, useState } from "react";
import { getPocketBase } from "@/lib/pocketbase-browser";
import {
  Collections,
  type BasPaymentMethodsRecord,
  type MetodoPago,
} from "@/lib/pocketbase-types";

// A diferencia de bas_category_map (categorías libres, se agregan con el
// tiempo), metodo_pago es un select CERRADO -- cada valor está acoplado a
// una rama de código Python (METODO_PAGO_ARRAY_BAS en utils/bas_config.py).
// Por eso este editor no tiene formulario de "agregar nuevo": siempre
// muestra las 3 filas posibles, y crea el record recién al guardar si
// todavía no existe (típicamente cheque/transferencia, sin sembrar por
// migración).
const METODOS: { value: MetodoPago; label: string }[] = [
  { value: "efectivo", label: "Efectivo" },
  { value: "cheque", label: "Cheque" },
  { value: "transferencia", label: "Transferencia" },
];

type Draft = {
  id: string | null;
  bas_medio_pago_codigo: string;
  bas_cuenta_bancaria: string;
  confirmado: boolean;
};

export default function PaymentMethodsEditor() {
  const [drafts, setDrafts] = useState<Record<MetodoPago, Draft>>(() =>
    Object.fromEntries(
      METODOS.map((m) => [
        m.value,
        { id: null, bas_medio_pago_codigo: "", bas_cuenta_bancaria: "", confirmado: false },
      ])
    ) as Record<MetodoPago, Draft>
  );
  const [saved, setSaved] = useState<Record<MetodoPago, Draft>>(drafts);
  const [savingMetodo, setSavingMetodo] = useState<MetodoPago | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

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

  async function handleSave(metodo: MetodoPago) {
    const draft = drafts[metodo];
    setSavingMetodo(metodo);
    try {
      const pb = getPocketBase();
      const payload = {
        metodo_pago: metodo,
        bas_medio_pago_codigo: draft.bas_medio_pago_codigo,
        bas_cuenta_bancaria: draft.bas_cuenta_bancaria,
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
        confirmado: record.confirmado,
      };
      setDrafts((prev) => ({ ...prev, [metodo]: next }));
      setSaved((prev) => ({ ...prev, [metodo]: next }));
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "No se pudo guardar el cambio.");
    } finally {
      setSavingMetodo(null);
    }
  }

  if (isLoading) {
    return <p className="text-sm text-gray-400">Cargando…</p>;
  }

  if (loadError) {
    return <p className="text-sm text-red-600">{loadError}</p>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
      <table className="min-w-full divide-y divide-gray-200 text-sm">
        <thead className="bg-gray-50 text-left text-xs font-medium uppercase text-gray-500">
          <tr>
            <th className="px-4 py-2">Método</th>
            <th className="px-4 py-2">Código BAS (MedioPago)</th>
            <th className="px-4 py-2">Cuenta bancaria</th>
            <th className="px-4 py-2">Confirmado</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {METODOS.map(({ value, label }) => {
            const draft = drafts[value];
            const original = saved[value];
            const isDirty =
              draft.bas_medio_pago_codigo !== original.bas_medio_pago_codigo ||
              draft.bas_cuenta_bancaria !== original.bas_cuenta_bancaria ||
              draft.confirmado !== original.confirmado;

            return (
              <tr key={value}>
                <td className="px-4 py-2 font-medium text-gray-900">{label}</td>
                <td className="px-4 py-2">
                  <input
                    type="text"
                    value={draft.bas_medio_pago_codigo}
                    onChange={(event) =>
                      setDrafts((prev) => ({
                        ...prev,
                        [value]: { ...draft, bas_medio_pago_codigo: event.target.value },
                      }))
                    }
                    className="w-24 rounded-md border border-gray-300 px-2 py-1 text-sm"
                  />
                </td>
                <td className="px-4 py-2">
                  <input
                    type="text"
                    disabled={value !== "transferencia"}
                    value={draft.bas_cuenta_bancaria}
                    onChange={(event) =>
                      setDrafts((prev) => ({
                        ...prev,
                        [value]: { ...draft, bas_cuenta_bancaria: event.target.value },
                      }))
                    }
                    placeholder={value === "transferencia" ? "" : "no aplica"}
                    className="w-40 rounded-md border border-gray-300 px-2 py-1 text-sm disabled:bg-gray-50 disabled:text-gray-400"
                  />
                </td>
                <td className="px-4 py-2">
                  <input
                    type="checkbox"
                    checked={draft.confirmado}
                    onChange={(event) =>
                      setDrafts((prev) => ({
                        ...prev,
                        [value]: { ...draft, confirmado: event.target.checked },
                      }))
                    }
                  />
                </td>
                <td className="px-4 py-2">
                  <button
                    type="button"
                    disabled={!isDirty || savingMetodo === value}
                    onClick={() => handleSave(value)}
                    className="rounded-md bg-gray-900 px-3 py-1 text-xs font-medium text-white hover:bg-gray-800 disabled:opacity-40"
                  >
                    {savingMetodo === value ? "Guardando…" : "Guardar"}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
