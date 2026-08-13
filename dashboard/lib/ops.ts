// Server-side only: reads the ops API with the bearer token.
// The token lives in env (NEXT_PUBLIC_* is NEVER used) so it never
// reaches the browser bundle.

const OPS_API_URL =
  process.env.OPS_API_URL ?? "http://127.0.0.1:8090";
const OPS_TOKEN = process.env.OPS_TOKEN ?? "";

export type Health = {
  status: "healthy" | "degraded";
  issues: string[];
  heartbeat: {
    present: boolean;
    updated_at?: string;
    collector_last_success?: string;
    collector_consecutive_errors?: number;
    collector_total_errors?: number;
    stale_candle_count?: number;
    last_processed_candle?: string | null;
    position_open?: boolean;
    position_side?: string;
  };
  backup: { latest: string | null; age_hours: number | null };
  db: { path: string };
  generated_utc: string;
};

export type DataQuality = {
  run_id: string;
  audit: {
    rows: number;
    coverage_pct: number;
    gaps: number;
    duplicates: number;
    first_candle?: string;
    latest_candle?: string;
  };
  generated_utc: string;
};

export type Deployment = {
  deployment: Record<string, unknown>;
  forward_start_utc: string;
  checkpoint_60d_utc: string;
  evaluation: { locked: boolean; unlock_at_utc: string };
  runtime_observation_start: string | null;
  generated_utc: string;
};

export type AuditEvent = {
  id: number;
  code: string;
  severity: "INFO" | "WARNING" | "CRITICAL" | string;
  message: string;
  timestamp: string;
  metadata: Record<string, unknown>;
};

export type Audit = {
  events: AuditEvent[];
  count: number;
  generated_utc: string;
};

export type Overview = {
  status: "healthy" | "degraded";
  issues: string[];
  overview: {
    heartbeat: Health["heartbeat"];
    data_quality?: DataQuality["audit"];
    deployment: Deployment["deployment"];
    backup: { latest: string | null; age_hours: number | null };
    evaluation: { locked: boolean; unlock_at_utc: string };
  };
  generated_utc: string;
};

async function opsGet<T>(path: string, revalidate = 30): Promise<T> {
  const res = await fetch(`${OPS_API_URL}${path}`, {
    headers: { Authorization: `Bearer ${OPS_TOKEN}` },
    next: { revalidate },
  });
  if (!res.ok) {
    throw new Error(`ops-api ${path} -> ${res.status}`);
  }
  return (await res.json()) as T;
}

export const ops = {
  health: () => opsGet<Health>("/api/health"),
  dataQuality: () => opsGet<DataQuality>("/api/data-quality"),
  deployment: () => opsGet<Deployment>("/api/deployment"),
  audit: () => opsGet<Audit>("/api/audit?limit=60"),
  overview: () => opsGet<Overview>("/api/overview"),
};
