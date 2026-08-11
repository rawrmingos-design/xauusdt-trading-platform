import { ops } from "@/lib/ops";
import { Nav } from "@/components/Nav";
import { Card } from "@/components/Card";
import { Bar, KVPair } from "@/components/Shared";

export const dynamic = "force-dynamic";

export default async function DataQualityPage() {
  const dq = await ops.dataQuality();
  const a = dq.audit;
  const tone = a.coverage_pct >= 99.9 ? "ok" : a.coverage_pct >= 99 ? "warn" : "bad";

  return (
    <div>
      <Nav active="/data-quality" />
      <div className="mx-auto max-w-5xl px-4 py-8">
        <h1 className="mb-6 text-xl font-semibold text-slate-100">
          Data Quality
        </h1>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card title="Coverage">
            <div className="text-2xl font-semibold text-slate-100">
              {a.coverage_pct.toFixed(2)}%
            </div>
          </Card>
          <Card title="Expected / Actual">
            <div className="font-mono text-2xl text-slate-100">
              {a.rows}
            </div>
            <div className="text-xs text-slate-500">stored candles</div>
          </Card>
          <Card title="Gaps">
            <div className={`text-2xl font-semibold ${a.gaps ? "text-danger" : "text-accent"}`}>
              {a.gaps}
            </div>
          </Card>
          <Card title="Duplicates">
            <div className={`text-2xl font-semibold ${a.duplicates ? "text-danger" : "text-accent"}`}>
              {a.duplicates}
            </div>
          </Card>
        </div>

        <Card title="Candle coverage" className="mt-6">
          <Bar pct={a.coverage_pct} tone={tone} />
          <div className="mt-2 flex justify-between text-xs text-slate-500">
            <span>0%</span>
            <span>100%</span>
          </div>
        </Card>

        <Card title="Range" className="mt-6">
          <KVPair
            k="First candle"
            v={a.first_candle ? new Date(a.first_candle).toUTCString() : "—"}
          />
          <KVPair
            k="Latest candle"
            v={a.latest_candle ? new Date(a.latest_candle).toUTCString() : "—"}
          />
          <KVPair k="Run" v={dq.run_id} />
        </Card>
      </div>
    </div>
  );
}
