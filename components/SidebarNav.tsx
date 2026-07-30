"use client";

import {
  LayoutDashboard,
  Inbox,
  FileText,
  CreditCard,
  Tags,
  Wallet,
  UploadCloud,
} from "lucide-react";
import {
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import NavLink from "@/components/NavLink";
import ThemeToggle from "@/components/ThemeToggle";

const NAV_GROUPS = [
  {
    label: "Panorama",
    items: [{ href: "/", label: "Inicio", icon: LayoutDashboard }],
  },
  {
    label: "Trabajo diario",
    items: [
      {
        href: "/queue",
        label: "Cola de revisión",
        icon: Inbox,
        // única entrada del nav con contador -- ver queueCount más abajo
        countKey: "queue" as const,
      },
      { href: "/invoices", label: "Facturas", icon: FileText },
      { href: "/payment-orders", label: "Órdenes de pago", icon: CreditCard },
    ],
  },
  {
    label: "Configuración",
    items: [
      { href: "/category-map", label: "Categorías BAS", icon: Tags },
      { href: "/payment-methods", label: "Métodos de pago", icon: Wallet },
    ],
  },
];

export default function SidebarNav({
  queueCount,
}: {
  /** Total de facturas completadas sin confirmar (mismo filtro que /queue). */
  queueCount?: number;
}) {
  return (
    <SidebarContent className="gap-5 px-1 py-4">
      {NAV_GROUPS.map((group) => (
        <SidebarGroup key={group.label} className="gap-0.5">
          {/* text-sidebar-foreground (no text-muted-foreground): estos
              overlines viven sobre la banda navy del sidebar, no sobre el
              fondo/card de la página -- muted-foreground está calibrado para
              ese otro contexto y quedaba casi ilegible acá. */}
          <SidebarGroupLabel className="mb-1 px-2.5 font-heading text-[11px] font-semibold tracking-[0.08em] text-sidebar-foreground uppercase">
            {group.label}
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {group.items.map((item) => (
                <SidebarMenuItem key={item.href}>
                  <NavLink
                    href={item.href}
                    icon={item.icon}
                    label={item.label}
                    badge={item.countKey === "queue" ? queueCount : undefined}
                  />
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      ))}

      {/* mt-auto empuja este bloque al fondo del área de nav, justo antes
          del SidebarFooter (que es un elemento hermano aparte con el
          avatar + cerrar sesión) -- fuera de los 3 grupos de arriba. */}
      <div className="mt-auto flex flex-col gap-0.5 px-2 pb-1">
        <SidebarMenu>
          <SidebarMenuItem>
            <NavLink
              href="/subir-factura-equipo"
              icon={UploadCloud}
              label="Subir factura"
            />
          </SidebarMenuItem>
          <SidebarMenuItem>
            <ThemeToggle />
          </SidebarMenuItem>
        </SidebarMenu>
      </div>
    </SidebarContent>
  );
}
