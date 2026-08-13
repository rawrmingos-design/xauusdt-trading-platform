export function Badge({
  tone,
  children,
}: {
  tone: "ok" | "warn" | "bad" | "muted" | "info";
  children: React.ReactNode;
}) {
  const cls = {
    ok: "bg-accent/15 text-accent",
    warn: "bg-warn/15 text-warn",
    bad: "bg-danger/15 text-danger",
    muted: "bg-edge text-slate-400",
    info: "bg-sky-500/15 text-sky-400",
  }[tone];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-current" />
      {children}
    </span>
  );
}

export function statusTone(
  s: string
): "ok" | "warn" | "bad" | "muted" | "info" {
  const v = s.toLowerCase();
  if (["healthy", "ok", "connected", "current", "locked", "active"].includes(v))
    return v === "locked" ? "info" : "ok";
  if (["degraded", "warning", "stale", "retry"].includes(v)) return "warn";
  if (["critical", "down", "error", "missing"].includes(v)) return "bad";
  return "muted";
}