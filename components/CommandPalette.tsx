"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { LayoutDashboard, Inbox, FileText } from "lucide-react";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";

// "Órdenes de pago" y "Categorías BAS"/"Métodos de pago" ocultos acá
// también (2026-08-13) -- mismo criterio que SidebarNav.tsx, ver ese
// comentario. Las rutas siguen existiendo, solo se sacó el acceso rápido.
const DESTINATIONS = [
  { href: "/", label: "Inicio", icon: LayoutDashboard },
  { href: "/queue", label: "Cola de revisión", icon: Inbox },
  { href: "/invoices", label: "Facturas", icon: FileText },
];

// Decisión de producto (auditoría de responsive, jul 2026): la paleta de
// comandos queda desktop-only a propósito. Solo se abre por ⌘K/Ctrl+K, que
// no existe en táctil -- no se agregó un botón de búsqueda equivalente en
// el header mobile porque cada pantalla ya tiene su propio buscador inline
// (QueueList, InvoicesTable, PaymentOrdersTable), así que no falta una
// forma de buscar en mobile, solo el atajo global de saltar de sección.
export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const router = useRouter();

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  function go(href: string) {
    setOpen(false);
    router.push(href);
  }

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Saltar a una sección…" />
      <CommandList>
        <CommandEmpty>Sin resultados.</CommandEmpty>
        <CommandGroup heading="Secciones">
          {DESTINATIONS.map((d) => (
            <CommandItem key={d.href} onSelect={() => go(d.href)}>
              <d.icon />
              {d.label}
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
