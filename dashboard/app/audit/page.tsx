import { ops } from "@/lib/ops";
import { Nav } from "@/components/Nav";
import { Card } from "@/components/Card";

export const dynamic = "force-dynamic";

const sevCls: Record<string, string> = {
  INFO: "text-slate-400",
  WARNING: "text-warn",
  CRITICAL: "text-danger",
};

export default async function AuditPage() {
  const a = await ops.audit();

  return (
    <div>
      <Nav active="/audit" />
      <div className="mx-auto max-w-5xl px-4 py-8">
        <h1 className="mb-6 text-xl font-semibold text-slate-100">
          Audit Log
        </h1>

        <Card title={`Latest events (${a.count})`}>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-edge text-xs uppercase tracking-wider text-slate-500">
                <th className="py-2 pr-4">Time (UTC)</th>
                <th className="py-2 pr-4">Severity</th>
                <th className="py-2 pr-4">Event</th>
                <th className="py-2">Message</th>
              </tr>
            </thead>
            <tbody>
              {a.events.map((e) => (
                <tr key={e.id} className="border-b border-edge/50">
                  <td className="py-2 pr-4 font-mono text-xs text-slate-500">
                    {new Date(e.timestamp).toISOString().slice(0, 19)}
                  </td>
                  <td className={`py-2 pr-4 font-mono text-xs ${sevCls[e.severity] ?? "text-slate-400"}`}>
                    {e.severity}
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs text-slate-300">
                    {e.code}
                  </td>
                  <td className="py-2 text-slate-300">{e.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
