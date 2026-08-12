import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type StatusBucket = "neutral" | "info" | "warning" | "success" | "destructive";

const STATUS_META: Record<string, { label: string; bucket: StatusBucket }> = {
  pending: { label: "Pendiente", bucket: "neutral" },
  queued: { label: "En cola", bucket: "neutral" },
  needs_review: { label: "Necesita revisión", bucket: "warning" },
  processing: { label: "Procesando", bucket: "info" },
  completed: { label: "Completada", bucket: "success" },
  confirmed: { label: "Confirmada", bucket: "success" },
  done: { label: "Completada", bucket: "success" },
  success: { label: "Exitosa", bucket: "success" },
  error: { label: "Error", bucket: "destructive" },
  failed: { label: "Fallida", bucket: "destructive" },
  running: { label: "Corriendo", bucket: "info" },
  completed_with_errors: { label: "Completado con errores", bucket: "warning" },
  uploading: { label: "Subiendo", bucket: "info" },
  skipped_duplicate: { label: "Duplicado omitido", bucket: "neutral" },
  // bas_registration_status (P0-G) -- estado del registro REAL del
  // comprobante en BAS, ver lib/pocketbase-types.ts:BasRegistrationStatus.
  awaiting_provider_match: { label: "Falta proveedor en BAS", bucket: "warning" },
  awaiting_service_selection: { label: "Falta elegir ítem", bucket: "warning" },
  ready_to_register: { label: "Listo para registrar", bucket: "info" },
  registered: { label: "Registrado en BAS", bucket: "success" },
  register_failed: { label: "Falló el registro", bucket: "destructive" },
};

const BUCKET_CLASSES: Record<StatusBucket, string> = {
  neutral: "bg-status-neutral-bg text-status-neutral-fg",
  info: "bg-status-info-bg text-status-info-fg",
  warning: "bg-status-warning-bg text-status-warning-fg",
  success: "bg-status-success-bg text-status-success-fg",
  destructive: "bg-status-destructive-bg text-status-destructive-fg",
};

const DOT_CLASSES: Record<StatusBucket, string> = {
  neutral: "bg-status-neutral-dot",
  info: "bg-status-info-dot",
  warning: "bg-status-warning-dot",
  success: "bg-status-success-dot",
  destructive: "bg-status-destructive-dot",
};

export default function StatusBadge({
  status,
  dot = false,
}: {
  status: string | null | undefined;
  dot?: boolean;
}) {
  const key = status || "";
  const meta = STATUS_META[key] ?? { label: key || "—", bucket: "neutral" as const };

  return (
    <Badge
      variant="secondary"
      className={cn(
        "h-[22px] gap-1.5 rounded-full border-transparent px-2.5 font-heading text-[11.5px] font-semibold tracking-wide",
        BUCKET_CLASSES[meta.bucket]
      )}
    >
      {dot && (
        <span
          className={cn("size-1.5 shrink-0 rounded-full", DOT_CLASSES[meta.bucket])}
        />
      )}
      {meta.label}
    </Badge>
  );
}
