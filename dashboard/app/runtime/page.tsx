import { ops } from "@/lib/ops";
import { Nav } from "@/components/Nav";
import { Card } from "@/components/Card";
import { Badge, statusTone } from "@/components/Badge";
import { KVPair } from "@/components/Shared";

export const dynamic = "force-dynamic";

function ageLabel(iso?: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  const sec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (sec < 90) return `${Math.round(sec)}s ago`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m ago`;
  return `${Math.floor(h / 24)}d ${h % 24}h ago`;
}

export default async function RuntimePage() {
  const h = await ops.health();
  const hb = h.heartbeat;
  const collectorUp = hb.collector_consecutive_errors === 0;
  const hbAge = ageLabel(hb.updated_at);

  return (
    <div>
      <Nav active="/runtime" />
      <div className="mx-auto max-w-5xl px-4 py-8">
        <h1 className="mb-6 text-xl font-semibold text-slate-100">Runtime</h1>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Card title="Status">
            <div className="grid grid-cols-2 gap-2 text-sm">
              <span className="text-slate-500">Collector</span>
              <Badge tone={collectorUp ? "ok" : "bad"}>
                {collectorUp ? "RUNNING" : "ERRORING"}
              </Badge>
              <span className="text-slate-500">Heartbeat</span>
              <Badge tone={statusTone(h.status)}>
                {hb.present ? (h.status === "healthy" ? "HEALTHY" : "STALE") : "MISSING"}
              </Badge>
              <span className="text-slate-500">Database</span>
              <Badge tone="ok">OK</Badge>
              <span className="text-slate-500">Systemd</span>
              <Badge tone="ok">ACTIVE</Badge>
            </div>
          </Card>

          <Card title="Detail">
            <KVPair k="Heartbeat" v={hbAge} />
            <KVPair
              k="Last successful poll"
              v={ageLabel(hb.collector_last_success)}
            />
            <KVPair
              k="Last candle"
              v={hb.last_processed_candle ? new Date(hb.last_processed_candle).toUTCString() : "—"}
            />
            <KVPair k="Consecutive errors" v={hb.collector_consecutive_errors ?? 0} />
            <KVPair k="Total errors" v={hb.collector_total_errors ?? 0} />
            <KVPair k="Stale candles" v={hb.stale_candle_count ?? 0} />
          </Card>
        </div>

        <Card title="Position" className="mt-6">
          <KVPair k="Open" v={hb.position_open ? "YES" : "NO"} />
          <KVPair k="Side" v={hb.position_side || "—"} />
        </Card>
      </div>
    </div>
  );
}
