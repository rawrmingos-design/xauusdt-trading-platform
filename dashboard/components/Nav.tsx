import Link from "next/link";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/data-quality", label: "Data Quality" },
  { href: "/runtime", label: "Runtime" },
  { href: "/deployment", label: "Deployment" },
  { href: "/audit", label: "Audit" },
];

export function Nav({ active }: { active: string }) {
  return (
    <nav className="border-b border-edge bg-panel">
      <div className="mx-auto flex max-w-5xl items-center gap-6 px-4 py-3">
        <div className="text-sm font-semibold tracking-wide text-slate-100">
          XAUUSDT <span className="text-slate-500">· OPS</span>
        </div>
        <div className="flex gap-1 text-sm">
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className={`rounded px-3 py-1.5 ${
                active === n.href
                  ? "bg-edge text-slate-100"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {n.label}
            </Link>
          ))}
        </div>
      </div>
    </nav>
  );
}