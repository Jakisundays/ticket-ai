"use client";

import { useRef, useState, type FormEvent } from "react";

const INVOICE_API_BAS_URL = process.env.NEXT_PUBLIC_INVOICE_API_BAS_URL;

type Status = "idle" | "uploading" | "success" | "error";

const EXTENSIONES_PERMITIDAS = [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"];

export default function SubirFacturaPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!INVOICE_API_BAS_URL) {
      setStatus("error");
      setMessage("Falta configurar NEXT_PUBLIC_INVOICE_API_BAS_URL.");
      return;
    }

    const file = fileInputRef.current?.files?.[0];
    if (!file) {
      setStatus("error");
      setMessage("Elegí un archivo primero.");
      return;
    }

    setStatus("uploading");
    setMessage(null);

    try {
      const formData = new FormData();
      formData.append("file", file);

      // Sin secret_key: este endpoint es público a propósito, protegido con
      // rate limiting en el backend en vez de un secreto compartido (ver
      // routes/process_invoice_google_2.py, /gemini2/website-upload).
      const response = await fetch(
        `${INVOICE_API_BAS_URL}/gemini2/website-upload`,
        { method: "POST", body: formData }
      );

      if (response.status === 429) {
        throw new Error("Demasiadas subidas seguidas. Esperá un minuto e intentá de nuevo.");
      }

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail || `El servidor respondió ${response.status}.`);
      }

      setStatus("success");
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (error) {
      setStatus("error");
      setMessage(
        error instanceof Error ? error.message : "No se pudo subir la factura."
      );
    }
  }

  return (
    <main className="flex flex-1 items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-lg border border-gray-200 bg-white p-8 shadow-sm">
        <h1 className="mb-1 text-xl font-semibold text-gray-900">
          Subir factura
        </h1>
        <p className="mb-6 text-sm text-gray-500">
          Subí una imagen o PDF de tu factura y la procesamos automáticamente.
        </p>

        {status === "success" ? (
          <div className="space-y-4">
            <p className="rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">
              ¡Listo! Recibimos tu factura.
            </p>
            <button
              type="button"
              onClick={() => setStatus("idle")}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100"
            >
              Subir otra
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="file"
                className="mb-1 block text-sm font-medium text-gray-700"
              >
                Archivo
              </label>
              <input
                ref={fileInputRef}
                id="file"
                name="file"
                type="file"
                required
                accept={EXTENSIONES_PERMITIDAS.join(",")}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-gray-100 file:px-2 file:py-1 file:text-sm focus:border-gray-500 focus:outline-none"
              />
              <p className="mt-1 text-xs text-gray-400">
                PDF, PNG, JPG, WEBP o GIF.
              </p>
            </div>

            {status === "error" && message && (
              <p className="text-sm text-red-600">{message}</p>
            )}

            <button
              type="submit"
              disabled={status === "uploading"}
              className="w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
            >
              {status === "uploading" ? "Subiendo…" : "Subir factura"}
            </button>
          </form>
        )}
      </div>
    </main>
  );
}
