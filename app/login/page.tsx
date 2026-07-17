"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Loader2Icon } from "lucide-react";
import { getPocketBase } from "@/lib/pocketbase-browser";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      const pb = getPocketBase();
      // Login contra la coleccion "users" (dashboard humano).
      // NUNCA contra "service_accounts": esa coleccion es exclusiva del
      // backend Python (invoice-api-bas).
      await pb.collection("users").authWithPassword(email, password);

      const params = new URLSearchParams(window.location.search);
      const destination = params.get("from") || "/";

      router.replace(destination);
      router.refresh();
    } catch {
      setError("Email o contraseña incorrectos.");
      setIsSubmitting(false);
    }
  }

  return (
    <main className="flex flex-1 items-center justify-center px-6 py-10">
      <div className="w-full max-w-[380px]">
        <div className="mb-7 flex flex-col items-center gap-2">
          <span className="font-serif text-[2rem] leading-none text-primary italic">
            Ticket AI
          </span>
          <p className="overline text-center text-muted-foreground">
            Panel interno · Dinardi
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="animate-fade-up flex flex-col gap-4 rounded-2xl bg-card p-7 shadow-(--shadow-2)"
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="email" className="text-[13px] font-medium text-foreground">
              Email
            </Label>
            <Input
              id="email"
              name="email"
              type="email"
              required
              autoComplete="email"
              placeholder="vos@dinardi.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="password" className="text-[13px] font-medium text-foreground">
              Contraseña
            </Label>
            <Input
              id="password"
              name="password"
              type="password"
              required
              autoComplete="current-password"
              placeholder="••••••••"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>

          {error && (
            <div className="flex items-center gap-2 rounded-lg border border-status-destructive-fg/20 bg-status-destructive-bg px-3 py-2.5">
              <p className="text-[13px] text-status-destructive-fg">{error}</p>
            </div>
          )}

          <Button
            type="submit"
            disabled={isSubmitting}
            className="mt-1 w-full"
          >
            {isSubmitting ? (
              <>
                <Loader2Icon className="size-3.5 animate-spin" />
                Ingresando…
              </>
            ) : (
              "Ingresar"
            )}
          </Button>
        </form>

        <p className="mt-5 text-center text-[13px] text-muted-foreground">
          ¿Problemas para entrar?{" "}
          <a
            href="mailto:sistemas@dinardi.com"
            className="text-primary hover:underline"
          >
            sistemas@dinardi.com
          </a>
        </p>
      </div>
    </main>
  );
}
