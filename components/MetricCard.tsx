import type { ReactNode } from "react";
import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

type IconTone = "info" | "warning" | "success" | "destructive" | "neutral";

const ICON_TONE_CLASSES: Record<IconTone, string> = {
  neutral: "bg-status-neutral-bg text-status-neutral-fg",
  info: "bg-status-info-bg text-status-info-fg",
  warning: "bg-status-warning-bg text-status-warning-fg",
  success: "bg-status-success-bg text-status-success-fg",
  destructive: "bg-status-destructive-bg text-status-destructive-fg",
};

export default function MetricCard({
  label,
  value,
  icon: Icon,
  iconTone = "neutral",
  hint,
  href,
}: {
  label: string;
  value: string | number;
  icon?: LucideIcon;
  iconTone?: IconTone;
  hint?: ReactNode;
  href?: string;
}) {
  const content = (
    <>
      <div className="flex items-start justify-between gap-3">
        <span className="text-[.7rem] font-semibold tracking-[.08em] text-muted-foreground uppercase font-heading">
          {label}
        </span>
        {Icon ? (
          <span
            className={cn(
              "flex size-[30px] shrink-0 items-center justify-center rounded-full",
              ICON_TONE_CLASSES[iconTone]
            )}
          >
            <Icon className="size-4" />
          </span>
        ) : null}
      </div>
      <div className="mt-3 font-heading text-[1.9rem] leading-none font-bold tabular-nums">
        {value}
      </div>
      {hint ? <div className="mt-2 text-sm text-muted-foreground">{hint}</div> : null}
    </>
  );

  const className = cn(
    "block rounded-xl bg-card p-5 shadow-(--shadow-1) transition-all duration-(--dur-fast) ease-(--ease-out) md:p-6",
    href && "hover:-translate-y-0.5 hover:shadow-(--shadow-2) active:scale-[0.99]"
  );

  if (href) {
    return (
      <Link href={href} className={className}>
        {content}
      </Link>
    );
  }

  return <div className={className}>{content}</div>;
}
