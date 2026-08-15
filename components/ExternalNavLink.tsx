"use client";

import { ExternalLink } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { SidebarMenuButton } from "@/components/ui/sidebar";

export default function ExternalNavLink({
  href,
  icon: Icon,
  label,
}: {
  href: string;
  icon: LucideIcon;
  label: string;
}) {
  return (
    <SidebarMenuButton
      asChild
      className="h-9 gap-2.5 px-2.5 text-[13.5px] font-medium"
    >
      <a href={href} target="_blank" rel="noreferrer">
        <Icon className="size-4" />
        <span className="truncate">{label}</span>
        <ExternalLink className="ml-auto size-3.5 shrink-0 opacity-60" />
      </a>
    </SidebarMenuButton>
  );
}
