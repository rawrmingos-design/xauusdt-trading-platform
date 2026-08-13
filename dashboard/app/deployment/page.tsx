import { ops } from "@/lib/ops";
import { Nav } from "@/components/Nav";
import { Card } from "@/components/Card";
import { Badge } from "@/components/Badge";
import { KVPair } from "@/components/Shared";

export const dynamic = "force-dynamic";

export default async function DeploymentPage() {
  const d = await ops.deployment();
  const m = d.deployment;

  return (
    <div>
      <Nav active="/deployment" />
      <div className="mx-auto max-w-5xl px-4 py-8">
        <h1 className="mb-6 text-xl font-semibold text-slate-100">
          Deployment
        </h1>

        <Card title="Deployment">
          <KVPair k="Environment" v="PAPER" />
          <KVPair k="Commit" v={(m.commit_sha as string) ?? "—"} />
          <KVPair k="Config hash" v={(m.config_hash as string) ?? "—"} />
          <KVPair k="Strategy" v={(m.strategy_version as string) ?? "—"} />
          <KVPair
            k="Forward start"
            v={new Date(d.forward_start_utc).toLocaleDateString("en-GB", {
              day: "numeric",
              month: "short",
              year: "numeric",
            })}
          />
          <KVPair
            k="Observation start"
            v={d.runtime_observation_start ? new Date(d.runtime_observation_start).toUTCString() : "—"}
          />
        </Card>

        <Card title="Evaluation" className="mt-6">
          <div className="flex items-center gap-3">
            <Badge tone={d.evaluation.locked ? "info" : "ok"}>
              {d.evaluation.locked ? "🔒 LOCKED" : "UNLOCKED"}
            </Badge>
            <span className="text-sm text-slate-400">
              Unlocks{" "}
              <span className="font-mono text-slate-200">
                {new Date(d.evaluation.unlock_at_utc).toLocaleDateString(
                  "en-GB",
                  { day: "numeric", month: "short", year: "numeric" }
                )}
              </span>
            </span>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-4 text-sm">
            <div className="rounded border border-edge p-3">
              <div className="text-xs text-slate-500">60D</div>
              <Badge tone="info">🔒 LOCKED</Badge>
            </div>
            <div className="rounded border border-edge p-3">
              <div className="text-xs text-slate-500">90D</div>
              <Badge tone="info">🔒 LOCKED</Badge>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
