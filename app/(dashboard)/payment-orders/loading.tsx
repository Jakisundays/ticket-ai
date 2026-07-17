import { Skeleton } from "@/components/ui/skeleton";
import PageHeader from "@/components/PageHeader";

export default function PaymentOrdersLoading() {
  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-base font-semibold text-foreground">
          Órdenes de pago
        </h1>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="mx-auto flex max-w-[1100px] flex-col gap-4">
          <div className="flex items-center gap-3">
            <Skeleton className="h-9 w-full sm:w-[280px]" />
            <Skeleton className="h-9 w-[172px]" />
          </div>
          <div className="flex flex-col gap-2.5 rounded-xl border bg-card p-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="mx-3 my-1.5 h-5" />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
