import { Skeleton } from "@/components/ui/skeleton";
import PageHeader from "@/components/PageHeader";

export default function InvoiceDetailLoading() {
  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <Skeleton className="h-4 w-28" />
        <Skeleton className="size-3.5 rounded-full" />
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-5 w-24 rounded-full" />
        <div className="flex-1" />
        <Skeleton className="hidden h-4 w-28 sm:block" />
        <div className="flex items-center gap-1.5">
          <Skeleton className="size-[34px] rounded-lg" />
          <Skeleton className="size-[34px] rounded-lg" />
        </div>
      </PageHeader>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="h-[42vh] shrink-0 border-b p-3 lg:h-auto lg:w-2/5 lg:min-w-[280px] lg:max-w-[560px] lg:border-r lg:border-b-0 lg:p-5">
          <div className="flex h-full flex-col gap-3.5 rounded-xl bg-sidebar p-4 shadow-(--shadow-2)">
            <Skeleton className="h-3 w-40" />
            <Skeleton className="min-h-0 flex-1 rounded-lg" />
          </div>
        </div>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-6 overflow-y-auto px-7 py-5">
          <div className="rounded-xl bg-card p-6 shadow-(--shadow-1)">
            <Skeleton className="mb-4 h-4 w-40" />
            <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
              {Array.from({ length: 12 }).map((_, i) => (
                <div key={i} className="flex flex-col gap-1.5">
                  <Skeleton className="h-2.5 w-24" />
                  <Skeleton className="h-9 w-full" />
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-xl bg-card p-6 shadow-(--shadow-1)">
            <Skeleton className="mb-4 h-4 w-16" />
            <div className="flex flex-col gap-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-full" />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
