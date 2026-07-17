import { Skeleton } from "@/components/ui/skeleton";
import PageHeader from "@/components/PageHeader";

export default function InvoicesLoading() {
  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-[16px] font-semibold text-foreground">
          Facturas
        </h1>
        <span className="ml-auto hidden truncate text-[13px] text-muted-foreground md:block">
          Últimos 50 comprobantes recibidos
        </span>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="mx-auto flex max-w-[1180px] flex-col gap-4">
          <div className="flex items-center gap-3">
            <Skeleton className="h-11 w-full sm:w-[280px]" />
            <Skeleton className="h-11 w-[180px]" />
          </div>
          <div className="flex flex-col gap-2.5 rounded-xl bg-card p-2 shadow-(--shadow-1)">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="mx-3 my-1.5 h-5" />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
