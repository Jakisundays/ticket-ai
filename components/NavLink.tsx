"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { LucideIcon } from "lucide-react";
import { SidebarMenuButton } from "@/components/ui/sidebar";

export default function NavLink({
  href,
  icon: Icon,
  label,
  badge,
}: {
  href: string;
  icon: LucideIcon;
  label: string;
  /** Contador opcional (ej. facturas pendientes) -- se omite si es 0 o undefined. */
  badge?: number;
}) {
  const pathname = usePathname();
  const isActive = pathname === href || pathname.startsWith(`${href}/`);

  return (
    <SidebarMenuButton
      asChild
      isActive={isActive}
      // El fondo/texto/font-weight del estado activo ya vienen correctamente
      // theme-ados desde sidebarMenuButtonVariants (data-active:bg-sidebar-accent
      // etc, ver components/ui/sidebar.tsx) -- acá solo agregamos el inset de
      // 3px que pide el diseño "Legado", nada más.
      className="h-9 gap-2.5 px-2.5 text-[13.5px] font-medium data-active:shadow-[inset_3px_0_0_var(--sidebar-primary)]"
    >
      <Link href={href} aria-current={isActive ? "page" : undefined}>
        <Icon className="size-4" />
        <span className="truncate">{label}</span>
        {typeof badge === "number" && badge > 0 ? (
          <span className="ml-auto flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-sidebar-primary px-1.5 text-[10.5px] font-semibold tabular-nums text-sidebar-primary-foreground">
            {badge}
          </span>
        ) : null}
      </Link>
    </SidebarMenuButton>
  );
}
