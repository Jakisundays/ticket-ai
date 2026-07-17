import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

type IconTone = "success" | "neutral" | "destructive";

const ICON_TONE_CLASSES: Record<IconTone, string> = {
  neutral: "bg-status-neutral-bg text-status-neutral-fg",
  success: "bg-status-success-bg text-status-success-fg",
  destructive: "bg-status-destructive-bg text-status-destructive-fg",
};

export default function EmptyState({
  icon: Icon,
  iconTone = "neutral",
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  iconTone?: IconTone;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
      <span
        className={cn(
          "flex size-[52px] shrink-0 items-center justify-center rounded-full",
          ICON_TONE_CLASSES[iconTone]
        )}
      >
        <Icon className="size-6" />
      </span>
      <h3 className="mt-1 font-heading text-base font-semibold">{title}</h3>
      {description ? (
        <p className="max-w-sm text-sm text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
