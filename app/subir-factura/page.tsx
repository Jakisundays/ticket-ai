"use client";

import { useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import {
  AlertCircle,
  CheckCircle,
  Clock,
  FileText,
  Loader2,
  UploadCloud,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const INVOICE_API_BAS_URL = process.env.NEXT_PUBLIC_INVOICE_API_BAS_URL;

type Status = "idle" | "uploading" | "success" | "error" | "error429";

const EXTENSIONES_PERMITIDAS = [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"];

const MENSAJE_ERROR_GENERICO = "Probá de nuevo en un momento.";
const MENSAJE_ERROR_429 = "Esperá unos minutos antes de volver a intentarlo.";

function formatFileSize(bytes: number) {
  if (bytes > 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export default function SubirFacturaPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);

  function handleFileInputChange(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    if (selected) setFile(selected);
  }

  function handleDragOver(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragActive(true);
  }

  function handleDragLeave(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragActive(false);
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragActive(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) setFile(dropped);
  }

  function handleRemoveFile() {
    setFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleSubmit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();

    if (!INVOICE_API_BAS_URL) {
      setStatus("error");
      setMessage("Falta configurar NEXT_PUBLIC_INVOICE_API_BAS_URL.");
      return;
    }

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
        setMessage(MENSAJE_ERROR_429);
        setStatus("error429");
        return;
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

  function handleReset() {
    setStatus("idle");
    setFile(null);
    setMessage(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleRetry() {
    void handleSubmit();
  }

  const canSubmit = !!file && status !== "uploading";

  return (
    <main className="flex flex-1 flex-col items-center px-6 py-12">
      <div className="mb-8 flex w-full max-w-[480px] items-center gap-2">
        <div className="flex size-6 items-center justify-center rounded-sm bg-primary text-[10px] font-bold text-primary-foreground">
          TA
        </div>
        <span className="text-[13.5px] font-semibold text-foreground/70">
          Ticket AI · Dinardi
        </span>
      </div>

      <Card className="animate-fade-up w-full max-w-[480px] p-8">
        {status === "idle" && (
          <form onSubmit={handleSubmit} className="flex flex-col">
            <div className="mb-[22px] flex flex-col gap-1.5">
              <h1 className="text-lg font-semibold text-foreground">Subir factura</h1>
              <p className="text-[13.5px] leading-relaxed text-muted-foreground">
                Adjuntá la imagen o el PDF de tu factura para que el equipo la
                procese.
              </p>
            </div>

            <input
              ref={fileInputRef}
              id="file-input"
              name="file"
              type="file"
              accept={EXTENSIONES_PERMITIDAS.join(",")}
              onChange={handleFileInputChange}
              className="hidden"
            />

            {!file ? (
              <label
                htmlFor="file-input"
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                className={cn(
                  "flex cursor-pointer flex-col items-center gap-2.5 rounded-xl border-2 border-dashed px-5 py-9 text-center transition-colors",
                  dragActive
                    ? "border-primary bg-status-info-bg"
                    : "border-border bg-muted/30 hover:border-muted-foreground/40 hover:bg-muted/50"
                )}
              >
                <UploadCloud
                  className={cn(
                    "size-[26px]",
                    dragActive ? "text-status-info-fg" : "text-muted-foreground"
                  )}
                />
                {dragActive ? (
                  <div className="text-[13.5px] font-medium text-status-info-fg">
                    Soltá el archivo acá
                  </div>
                ) : (
                  <>
                    <div className="text-[13.5px] font-medium text-foreground/80">
                      Arrastrá tu archivo acá o hacé clic para elegirlo
                    </div>
                    <div className="text-xs text-muted-foreground">
                      PDF, JPG o PNG · hasta 15 MB
                    </div>
                  </>
                )}
              </label>
            ) : (
              <div className="flex items-center gap-3 rounded-xl border border-border bg-muted/30 px-4 py-3.5">
                <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-status-info-bg text-status-info-fg">
                  <FileText className="size-[17px]" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13.5px] font-medium text-foreground">
                    {file.name}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {formatFileSize(file.size)}
                  </div>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={handleRemoveFile}
                  aria-label="Quitar archivo"
                  className="shrink-0 text-muted-foreground hover:bg-status-destructive-bg hover:text-status-destructive-fg"
                >
                  <X className="size-3.5" />
                </Button>
              </div>
            )}

            <Button
              type="submit"
              disabled={!canSubmit}
              className="mt-[18px] h-10 w-full"
            >
              Subir factura
            </Button>
          </form>
        )}

        {status === "uploading" && (
          <div className="flex flex-col items-center gap-3.5 px-2.5 py-[30px] text-center">
            <Loader2 className="size-[26px] animate-spin text-primary" />
            <div className="text-[14.5px] font-medium text-foreground">
              Subiendo {file?.name}…
            </div>
          </div>
        )}

        {status === "success" && (
          <div className="animate-scale-in flex flex-col items-center gap-3 px-2.5 py-5 text-center">
            <div className="flex size-11 items-center justify-center rounded-full bg-status-success-bg text-status-success-fg">
              <CheckCircle className="size-5" />
            </div>
            <div className="text-[15.5px] font-semibold text-foreground">
              ¡Listo! Recibimos tu factura.
            </div>
            <p className="max-w-[340px] text-[13.5px] leading-relaxed text-muted-foreground">
              El equipo la va a revisar pronto. Vas a recibir la confirmación
              por el mismo medio que la enviaste.
            </p>
            <Button variant="outline" onClick={handleReset} className="mt-2 h-9">
              Subir otra factura
            </Button>
          </div>
        )}

        {status === "error429" && (
          <div className="flex flex-col items-center gap-3 px-2.5 py-5 text-center">
            <div className="flex size-11 items-center justify-center rounded-full bg-status-warning-bg text-status-warning-fg">
              <Clock className="size-5" />
            </div>
            <div className="text-[15.5px] font-semibold text-foreground">
              Demasiados intentos
            </div>
            <p className="max-w-[340px] text-[13.5px] leading-relaxed text-muted-foreground">
              {message || MENSAJE_ERROR_429}
            </p>
            <Button variant="outline" onClick={handleReset} className="mt-2 h-9">
              Volver
            </Button>
          </div>
        )}

        {status === "error" && (
          <div className="flex flex-col items-center gap-3 px-2.5 py-5 text-center">
            <div className="flex size-11 items-center justify-center rounded-full bg-status-destructive-bg text-status-destructive-fg">
              <AlertCircle className="size-5" />
            </div>
            <div className="text-[15.5px] font-semibold text-foreground">
              No pudimos subir tu factura
            </div>
            <p className="max-w-[340px] text-[13.5px] leading-relaxed text-muted-foreground">
              {message || MENSAJE_ERROR_GENERICO}
            </p>
            <Button
              type="button"
              onClick={handleRetry}
              className="mt-2 h-9 bg-foreground text-background hover:bg-foreground/85"
            >
              Reintentar
            </Button>
          </div>
        )}
      </Card>

      <p className="mt-6 text-center text-[13px] text-muted-foreground">
        ¿Preferís otro medio? También podés enviarla por email a{" "}
        <a
          href="mailto:sistemas@dinardi.com"
          className="text-primary hover:underline"
        >
          sistemas@dinardi.com
        </a>
      </p>
    </main>
  );
}
