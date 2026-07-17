"use client";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import InitialsAvatar from "@/components/InitialsAvatar";
import LogoutButton from "@/components/LogoutButton";

export default function UserMenu({
  name,
  email,
}: {
  name?: string;
  email?: string;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left outline-none hover:bg-sidebar-accent">
        <InitialsAvatar name={name} email={email} tone="sidebar" size="sm" />
        <div className="flex min-w-0 flex-col">
          {/* text-sidebar-accent-foreground (blanco sólido, no
              text-foreground): esta línea vive sobre la banda navy del
              sidebar, no sobre el fondo/card de la página. */}
          <span className="block max-w-[140px] truncate text-[12.5px] font-medium text-sidebar-accent-foreground">
            {name || email || "—"}
          </span>
          <span className="text-[11px] text-sidebar-foreground">
            Equipo Dinardi
          </span>
        </div>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-[200px]">
        <DropdownMenuItem asChild>
          <LogoutButton />
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
