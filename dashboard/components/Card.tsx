export function Card({
  title,
  children,
  className = "",
}: {
  title?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border border-edge bg-panel p-4 ${className}`}
    >
      {title ? (
        <div className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-500">
          {title}
        </div>
      ) : null}
      {children}
    </div>
  );
}