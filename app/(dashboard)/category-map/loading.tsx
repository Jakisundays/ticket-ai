import { Skeleton } from "@/components/ui/skeleton";
import PageHeader from "@/components/PageHeader";

export default function CategoryMapLoading() {
  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-base font-semibold text-foreground">
          Categorías BAS
        </h1>
      </PageHeader>
      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="mx-auto flex max-w-[820px] flex-col gap-4">
          <Skeleton className="h-4 w-full max-w-md" />
          <div className="flex flex-col gap-2.5 rounded-xl border bg-card p-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="mx-3 my-1.5 h-5" />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
