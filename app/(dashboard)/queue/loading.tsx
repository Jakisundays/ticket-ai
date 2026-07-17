import { Skeleton } from "@/components/ui/skeleton";
import PageHeader from "@/components/PageHeader";

export default function QueueLoading() {
  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-base font-semibold text-foreground">
          Cola de revisión
        </h1>
        <Skeleton className="h-[22px] w-24 rounded-full" />
        <span className="ml-auto hidden md:block">
          <Skeleton className="h-4 w-56" />
        </span>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-4 md:p-7">
        <div className="mx-auto flex max-w-[960px] flex-col gap-4">
          <Skeleton className="h-9 w-full sm:w-80" />
          <div className="flex flex-col gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-[76px] rounded-xl" />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
