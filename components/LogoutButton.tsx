"use client";

import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { getPocketBase } from "@/lib/pocketbase-browser";

export default function LogoutButton() {
  const router = useRouter();

  function handleLogout() {
    getPocketBase().authStore.clear();
    router.replace("/login");
    router.refresh();
  }

  return (
    <button
      type="button"
      onClick={handleLogout}
      className="flex w-full items-center gap-2 text-[13px] text-status-destructive-fg"
    >
      <LogOut className="size-3.5" />
      Cerrar sesión
    </button>
  );
}
