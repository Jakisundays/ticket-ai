import { Skeleton } from "@/components/ui/skeleton";
import PageHeader from "@/components/PageHeader";

export default function InicioLoading() {
  return (
    <div className="flex h-full flex-col">
      <PageHeader>
        <h1 className="truncate text-base font-semibold text-foreground">Inicio</h1>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="mx-auto flex max-w-[1240px] flex-col gap-7">
          <div className="flex flex-col gap-2">
            <Skeleton className="h-3 w-48" />
            <Skeleton className="h-7 w-56" />
            <Skeleton className="h-4 w-full max-w-[420px]" />
          </div>

          <div className="grid grid-cols-1 gap-4 min-[481px]:grid-cols-2 min-[761px]:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="rounded-xl bg-card p-5 shadow-(--shadow-1) md:p-6">
                <div className="flex items-center justify-between gap-3">
                  <Skeleton className="h-3 w-24" />
                  <Skeleton className="size-[30px] rounded-full" />
                </div>
                <Skeleton className="mt-3 h-7 w-16" />
                <Skeleton className="mt-2 h-3.5 w-32" />
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 items-start gap-4 min-[1101px]:grid-cols-[1fr_340px]">
            <div className="overflow-hidden rounded-xl bg-card shadow-(--shadow-1)">
              <div className="flex items-center justify-between px-6 pt-[18px] pb-3.5">
                <Skeleton className="h-4 w-36" />
                <Skeleton className="h-4 w-16" />
              </div>
              <div className="flex flex-col gap-2.5 px-6 pb-5">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-4">
              <div className="rounded-xl bg-card p-5 shadow-(--shadow-1)">
                <div className="mb-3.5 flex items-center justify-between">
                  <Skeleton className="h-4 w-28" />
                  <Skeleton className="h-5 w-20 rounded-full" />
                </div>
                <div className="flex flex-col gap-2.5">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} className="h-11 w-full" />
                  ))}
                </div>
                <Skeleton className="mt-3.5 h-11 w-full rounded-full" />
              </div>

              <div className="rounded-xl bg-muted/40 p-5">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="mt-2 h-3 w-full max-w-[220px]" />
                <div className="mt-3 flex flex-col gap-2.5">
                  {Array.from({ length: 2 }).map((_, i) => (
                    <Skeleton key={i} className="h-2 w-full rounded-full" />
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
