"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
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
 * Reabrir puede desbloquear una factura que ya tiene una orden de pago
 * intentada -- un click accidental no debería reabrir en silencio. Es el
 * único modal de toda esta feature, justamente por eso.
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
        <Button variant="outline" size="sm">
          Reabrir para editar
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>¿Reabrir esta factura?</AlertDialogTitle>
          <AlertDialogDescription>
            Vas a poder editarla de nuevo. Si ya se creó una orden de pago, revisala antes de
            reabrir.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancelar</AlertDialogCancel>
          <AlertDialogAction onClick={handleReopen} disabled={loading}>
            {loading ? "Reabriendo…" : "Reabrir"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
