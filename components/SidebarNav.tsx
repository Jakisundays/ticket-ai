"use client";

import {
  LayoutDashboard,
  Inbox,
  FileText,
  UploadCloud,
  FileSpreadsheet,
  FolderOpen,
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
import ExternalNavLink from "@/components/ExternalNavLink";
import ThemeToggle from "@/components/ThemeToggle";

// Horneadas en el bundle del cliente en build time (ver Dockerfile +
// docker-compose.yml del dashboard) -- no son secretos, son URLs publicas de
// Google que ya requieren ser colaborador del Sheet/Drive para ver contenido.
const SHEET_URL = process.env.NEXT_PUBLIC_TEAM_SHEET_URL;
const DRIVE_FOLDER_URL = process.env.NEXT_PUBLIC_TEAM_DRIVE_FOLDER_URL;

const NAV_GROUPS = [
  {
    label: "Menú",
    items: [
      { href: "/", label: "Inicio", icon: LayoutDashboard },
      { href: "/subir-factura-equipo", label: "Subir factura", icon: UploadCloud },
    ],
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
      // "Órdenes de pago" y "Lotes" ocultos del nav a pedido explícito
      // (2026-08-13) -- las rutas /payment-orders y /lotes siguen existiendo
      // tal cual, solo se sacó el link. Ver también CommandPalette.tsx.
    ],
  },
  // Grupo "Configuración" (Categorías BAS, Métodos de pago) oculto del nav
  // a pedido explícito (2026-08-13) -- mismo criterio que arriba, las
  // rutas /category-map y /payment-methods siguen existiendo tal cual.
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
          avatar + cerrar sesión) -- fuera de los grupos de arriba.
          "Subir factura" se movió al grupo "Menú" (junto a "Inicio"); acá
          queda "Herramientas externas" (Sheets/Drive, si están
          configuradas) y el toggle de tema. */}
      <div className="mt-auto flex flex-col gap-0.5 px-2 pb-1">
        {(SHEET_URL || DRIVE_FOLDER_URL) && (
          <SidebarGroup className="gap-0.5 px-0">
            <SidebarGroupLabel className="mb-1 px-2.5 font-heading text-[11px] font-semibold tracking-[0.08em] text-sidebar-foreground uppercase">
              Herramientas externas
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {SHEET_URL && (
                  <SidebarMenuItem>
                    <ExternalNavLink
                      href={SHEET_URL}
                      icon={FileSpreadsheet}
                      label="Google Sheets"
                    />
                  </SidebarMenuItem>
                )}
                {DRIVE_FOLDER_URL && (
                  <SidebarMenuItem>
                    <ExternalNavLink
                      href={DRIVE_FOLDER_URL}
                      icon={FolderOpen}
                      label="Google Drive"
                    />
                  </SidebarMenuItem>
                )}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}
        <SidebarMenu>
          <SidebarMenuItem>
            <ThemeToggle />
          </SidebarMenuItem>
        </SidebarMenu>
      </div>
    </SidebarContent>
  );
}
