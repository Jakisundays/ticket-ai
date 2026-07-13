"use client";

import { useRouter } from "next/navigation";
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
      className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100"
    >
      Cerrar sesión
    </button>
  );
}
