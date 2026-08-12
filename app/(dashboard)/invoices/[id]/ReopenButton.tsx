"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { RotateCcw } from "lucide-react";
import { getPocketBase } from "@/lib/pocketbase-browser";
import { Collections } from "@/lib/pocketbase-types";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";

/**
 * Manda la factura de vuelta a needs_review (la cola de revisión) --
 * puede desbloquear una factura que ya tiene una orden de pago intentada,
 * así que un click accidental no debería hacerlo en silencio. Es el único
 * modal de toda esta feature, justamente por eso.
 */
export default function ReopenButton({ invoiceId }: { invoiceId: string }) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  async function handleReopen() {
    setLoading(true);
    try {
      const pb = getPocketBase();
      // Reabrir tiene que venir SOLA (review_status únicamente) -- el hook
      // rechaza cualquier otro campo en el mismo request.
      await pb.collection(Collections.Invoices).update(invoiceId, {
        review_status: "needs_review",
      });
      setOpen(false);
      router.refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo reabrir la factura.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger asChild>
        <Button variant="outline" className="h-9 w-full justify-center gap-2">
          <RotateCcw className="size-3.5" />
          Enviar a cola de revisión
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>¿Enviar esta factura a la cola de revisión?</AlertDialogTitle>
          <AlertDialogDescription>
            Vas a poder editarla de nuevo desde la cola de revisión. Si ya se creó una orden de
            pago, revisala antes de continuar.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancelar</AlertDialogCancel>
          <AlertDialogAction onClick={handleReopen} disabled={loading}>
            {loading ? "Enviando…" : "Enviar a cola"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
