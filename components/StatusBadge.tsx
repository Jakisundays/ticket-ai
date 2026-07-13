const STATUS_STYLES: Record<string, string> = {
  pending: "bg-yellow-100 text-yellow-800",
  queued: "bg-yellow-100 text-yellow-800",
  processing: "bg-blue-100 text-blue-800",
  completed: "bg-green-100 text-green-800",
  done: "bg-green-100 text-green-800",
  success: "bg-green-100 text-green-800",
  error: "bg-red-100 text-red-800",
  failed: "bg-red-100 text-red-800",
};

export default function StatusBadge({
  status,
}: {
  status: string | null | undefined;
}) {
  const value = status || "—";
  const style = STATUS_STYLES[value] ?? "bg-gray-100 text-gray-700";

  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${style}`}
    >
      {value}
    </span>
  );
}
