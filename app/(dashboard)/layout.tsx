import type { CSSProperties, ReactNode } from "react";
import {
  requireUserSession,
  ClientResponseError,
} from "@/lib/pocketbase-server";
import { Collections, type InvoicesRecord } from "@/lib/pocketbase-types";
import {
  Sidebar,
  SidebarFooter,
  SidebarHeader,
  SidebarInset,
  SidebarProvider,
} from "@/components/ui/sidebar";
import SidebarNav from "@/components/SidebarNav";
import UserMenu from "@/components/UserMenu";
import CommandPalette from "@/components/CommandPalette";

export default async function DashboardLayout({
  children,
}: {
  children: ReactNode;
}) {
  const pb = await requireUserSession();
  const record = pb.authStore.record;
  const email = typeof record?.email === "string" ? record.email : undefined;
  const name = typeof record?.name === "string" ? record.name : undefined;

  // A propósito MÁS angosto que el filtro de app/(dashboard)/queue/page.tsx
  // (que desde este cambio también incluye pending/processing/error para
  // dar visibilidad temprana): este badge del nav es "acción humana
  // pendiente", no "actividad en curso" -- una factura todavía procesándose
  // no necesita que nadie haga nada todavía. Es solo el número para el
  // badge -- si la consulta falla no vale la pena tirar abajo todo el shell
  // del dashboard por eso, el badge simplemente no aparece.
  let queueCount: number | undefined;
  try {
    const result = await pb
      .collection<InvoicesRecord>(Collections.Invoices)
      .getList(1, 1, {
        filter: 'status = "completed" && review_status != "confirmed"',
      });
    queueCount = result.totalItems;
  } catch (error) {
    if (!(error instanceof ClientResponseError)) throw error;
    queueCount = undefined;
  }

  return (
    <SidebarProvider
      defaultOpen
      style={{ "--sidebar-width": "246px" } as CSSProperties}
    >
      <Sidebar collapsible="offcanvas" className="border-r">
        <SidebarHeader className="h-[60px] flex-row items-baseline gap-2 border-b border-sidebar-border px-4">
          <span className="font-serif text-[20px] leading-none text-sidebar-primary italic">
            Ticket AI
          </span>
          <span className="font-heading text-[9.5px] font-semibold tracking-[0.08em] text-sidebar-foreground uppercase">
            Dinardi
          </span>
        </SidebarHeader>
        <SidebarNav queueCount={queueCount} />
        <SidebarFooter className="border-t border-sidebar-border p-3">
          <UserMenu name={name} email={email} />
        </SidebarFooter>
      </Sidebar>
      <SidebarInset className="h-svh overflow-hidden">
        <CommandPalette />
        {children}
      </SidebarInset>
    </SidebarProvider>
  );
}
