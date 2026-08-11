export function Bar({
  pct,
  tone = "ok",
}: {
  pct: number;
  tone?: "ok" | "warn" | "bad";
}) {
  const color = {
    ok: "bg-accent",
    warn: "bg-warn",
    bad: "bg-danger",
  }[tone];
  const w = Math.max(0, Math.min(100, pct));
  return (
    <div className="h-3 w-full overflow-hidden rounded-full bg-edge">
      <div
        className={`h-full rounded-full ${color} transition-all`}
        style={{ width: `${w}%` }}
      />
    </div>
  );
}

export function KVPair({
  k,
  v,
}: {
  k: string;
  v: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-edge/60 py-1.5 text-sm last:border-0">
      <span className="text-slate-500">{k}</span>
      <span className="font-mono text-slate-200">{v}</span>
    </div>
  );
}