"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { SidebarMenuButton } from "@/components/ui/sidebar";

const emptySubscribe = () => () => {};

// No hay ningún store externo real acá -- esto es el truco estándar para
// saber si ya pasamos la hidratación SIN llamar setState() dentro de un
// efecto. El lint de este proyecto (react-hooks/set-state-in-effect, del
// nuevo eslint-plugin-react-hooks con motor de React Compiler) rechaza el
// patrón clásico `useState(false) + useEffect(() => setMounted(true))` y
// sugiere textualmente useSyncExternalStore para este caso ("force update /
// external sync"), así que usamos eso: snapshot del servidor siempre
// `false`, snapshot del cliente siempre `true` -- mismo resultado (un solo
// re-render extra apenas monta), sin el warning.
function useHasMounted() {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false
  );
}

// Mismo look visual que NavLink (h-9, gap, tamaño de texto) pero es un
// <button> real -- no navega a ningún lado, solo alterna el tema.
export default function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useHasMounted();

  // Placeholder del mismo alto (h-9, igual que el botón real) para no saltar
  // el layout del sidebar mientras no sabemos qué tema resolvió next-themes
  // (podría venir de "system").
  if (!mounted) {
    return <div className="h-9" />;
  }

  const isDark = resolvedTheme === "dark";

  return (
    <SidebarMenuButton
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className="h-9 gap-2.5 px-2.5 text-[13.5px] font-medium"
    >
      {isDark ? <Sun className="size-4" /> : <Moon className="size-4" />}
      {isDark ? "Modo claro" : "Modo oscuro"}
    </SidebarMenuButton>
  );
}
