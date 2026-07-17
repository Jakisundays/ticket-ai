import type { ReactNode } from "react";
import { SidebarTrigger } from "@/components/ui/sidebar";

export default function PageHeader({ children }: { children: ReactNode }) {
  return (
    <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2.5 border-hairline bg-background/92 px-4 backdrop-blur-md md:px-7">
      <SidebarTrigger className="-ml-1 min-[941px]:hidden" />
      {children}
    </header>
  );
}
