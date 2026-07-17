import { cn } from "@/lib/utils";

type Tone = "sidebar" | "accent";
type Size = "sm" | "md";

const TONE_CLASSES: Record<Tone, string> = {
  sidebar: "bg-sidebar-primary text-sidebar-primary-foreground",
  accent: "bg-accent text-accent-foreground",
};

const SIZE_CLASSES: Record<Size, string> = {
  sm: "size-8 text-xs",
  md: "size-10 text-sm",
};

function getInitials(name?: string, email?: string): string {
  const trimmedName = name?.trim();
  if (trimmedName) {
    const parts = trimmedName.split(/\s+/).filter(Boolean);
    if (parts.length === 1) {
      return parts[0].slice(0, 2).toUpperCase();
    }
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  const trimmedEmail = email?.trim();
  if (trimmedEmail) {
    return trimmedEmail.slice(0, 2).toUpperCase();
  }

  return "?";
}

export default function InitialsAvatar({
  name,
  email,
  initials: initialsOverride,
  tone = "accent",
  size = "md",
}: {
  name?: string;
  email?: string;
  /** Iniciales ya calculadas por el caller (p. ej. razón social de un
   * proveedor, donde "primer + último nombre" no aplica) -- si se pasa, se
   * usa tal cual en vez de derivarla de `name`/`email`. */
  initials?: string;
  tone?: Tone;
  size?: Size;
}) {
  const initials = initialsOverride || getInitials(name, email);

  return (
    <span
      className={cn(
        "flex shrink-0 items-center justify-center rounded-full font-heading font-semibold",
        TONE_CLASSES[tone],
        SIZE_CLASSES[size]
      )}
    >
      {initials}
    </span>
  );
}
