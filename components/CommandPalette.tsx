"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  LayoutDashboard,
  Inbox,
  FileText,
  CreditCard,
  Tags,
  Wallet,
} from "lucide-react";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";

const DESTINATIONS = [
  { href: "/", label: "Inicio", icon: LayoutDashboard },
  { href: "/queue", label: "Cola de revisión", icon: Inbox },
  { href: "/invoices", label: "Facturas", icon: FileText },
  { href: "/payment-orders", label: "Órdenes de pago", icon: CreditCard },
  { href: "/category-map", label: "Categorías BAS", icon: Tags },
  { href: "/payment-methods", label: "Métodos de pago", icon: Wallet },
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
