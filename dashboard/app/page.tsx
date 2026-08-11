import { ops } from "@/lib/ops";
import { Nav } from "@/components/Nav";
import { Card } from "@/components/Card";
import { Badge, statusTone } from "@/components/Badge";
import { Bar, KVPair } from "@/components/Shared";

export const dynamic = "force-dynamic";

function fmt(iso?: string | null) {
  if (!iso) return "—";
  return new Date(iso).toUTCString();
}

export default async function OverviewPage() {
  let ov: Awaited<ReturnType<typeof ops.overview>>;
  let err: string | null = null;
  try {
    ov = await ops.overview();
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
    return (
      <div>
        <Nav active="/" />
        <div className="mx-auto max-w-5xl px-4 py-8">
          <div className="rounded-lg border border-danger/40 bg-danger/10 p-4 text-danger">
            🔴 Cannot reach ops API — {err}
          </div>
        </div>
      </div>
    );
  }
  const hb = ov.overview.heartbeat;
  const dq = ov.overview.data_quality;
  const healthy = ov.status === "healthy";
  const lastCandle = hb.last_processed_candle;

  return (
    <div>
      <Nav active="/" />
      <div className="mx-auto max-w-5xl px-4 py-8">
        {/* incident banner */}
        <div
          className={`mb-6 rounded-lg border p-4 ${
            healthy
              ? "border-accent/40 bg-accent/10 text-accent"
              : "border-danger/40 bg-danger/10 text-danger"
          }`}
        >
          <div className="text-lg font-semibold">
            {healthy ? "🟢 ALL SYSTEMS OPERATIONAL" : "🔴 SYSTEM DEGRADED"}
          </div>
          {!healthy && (
            <div className="mt-1 text-sm opacity-90">
              {ov.issues.join(", ")}
            </div>
          )}
        </div>

        {/* header status row */}
        <div className="mb-6 flex items-center justify-between">
          <div>
            <div className="text-xs uppercase tracking-wider text-slate-500">
              XAUUSDT Platform
            </div>
            <div className="text-2xl font-semibold text-slate-100">
              PAPER MODE
            </div>
          </div>
          <Badge tone={statusTone(ov.status)}>
            {ov.status.toUpperCase()}
          </Badge>
        </div>

        {/* 4 stat cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card title="Heartbeat">
            <div className="text-lg font-semibold">
              <Badge tone={hb.present ? (healthy ? "ok" : "warn") : "bad"}>
                {hb.present ? (healthy ? "HEALTHY" : "STALE") : "MISSING"}
              </Badge>
            </div>
            <div className="mt-2 text-xs text-slate-500">
              Last: {fmt(hb.updated_at)}
            </div>
          </Card>
          <Card title="Coverage">
            <div className="text-lg font-semibold text-slate-100">
              {dq?.coverage_pct != null
                ? `${dq.coverage_pct.toFixed(2)}%`
                : "—"}
            </div>
            <div className="mt-2 text-xs text-slate-500">
              {dq?.rows ?? 0} candles
            </div>
          </Card>
          <Card title="Database">
            <Badge tone="ok">OK</Badge>
            <div className="mt-2 truncate font-mono text-xs text-slate-500">
              {(ov.overview.deployment.commit_sha as string | undefined)?.slice(0, 8) ?? "n/a"}
            </div>
          </Card>
          <Card title="Backup">
            <Badge
              tone={
                ov.overview.backup.age_hours == null ||
                ov.overview.backup.age_hours > 26
                  ? "bad"
                  : "ok"
              }
            >
              {ov.overview.backup.latest ? "CURRENT" : "NONE"}
            </Badge>
            <div className="mt-2 text-xs text-slate-500">
              {ov.overview.backup.age_hours != null
                ? `${ov.overview.backup.age_hours}h old`
                : "no backup"}
            </div>
          </Card>
        </div>

        {/* evaluation + runtime */}
        <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card title="Evaluation">
            <div className="flex items-center gap-2">
              <Badge tone={ov.overview.evaluation.locked ? "info" : "ok"}>
                {ov.overview.evaluation.locked ? "🔒 LOCKED" : "UNLOCKED"}
              </Badge>
              <span className="text-sm text-slate-400">
                60D checkpoint:
              </span>
              <span className="font-mono text-sm text-slate-200">
                {new Date(
                  ov.overview.evaluation.unlock_at_utc
                ).toLocaleDateString("en-GB", {
                  day: "numeric",
                  month: "short",
                  year: "numeric",
                })}
              </span>
            </div>
          </Card>
          <Card title="Runtime">
            <KVPair k="Last candle" v={fmt(lastCandle)} />
            <KVPair k="Observation start" v={fmt(ov.overview.deployment.runtime_observation_start as string | null)} />
            <KVPair k="Deployment commit" v={(ov.overview.deployment.commit_sha as string | null)?.slice(0, 8) ?? "—"} />
            <KVPair k="Config hash" v={(ov.overview.deployment.config_hash as string | null)?.slice(0, 8) ?? "—"} />
          </Card>
        </div>
      </div>
    </div>
  );
}
