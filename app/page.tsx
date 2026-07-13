import { redirect } from "next/navigation";

export default function RootPage() {
  // proxy.ts ya garantiza que si no hay sesion valida esto termina
  // rebotando a /login antes de llegar aca.
  redirect("/invoices");
}
