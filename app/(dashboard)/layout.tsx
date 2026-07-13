import type { ReactNode } from "react";
import Link from "next/link";
import { requireUserSession } from "@/lib/pocketbase-server";
import LogoutButton from "@/components/LogoutButton";

export default async function DashboardLayout({
  children,
}: {
  children: ReactNode;
}) {
  const pb = await requireUserSession();
  const record = pb.authStore.record;
  const email = typeof record?.email === "string" ? record?.email : undefined;

  return (
    <div className="flex min-h-full flex-1 flex-col">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <nav className="flex flex-wrap items-center gap-4 text-sm font-medium text-gray-600">
            <span className="mr-2 text-sm font-semibold text-gray-900">
              Ticket AI
            </span>
            <Link href="/invoices" className="hover:text-gray-900">
              Facturas
            </Link>
            <Link href="/category-map" className="hover:text-gray-900">
              Categorías BAS
            </Link>
          </nav>
          <div className="flex items-center gap-3 text-sm text-gray-500">
            {email && <span className="hidden sm:inline">{email}</span>}
            <LogoutButton />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
        {children}
      </main>
    </div>
  );
}
