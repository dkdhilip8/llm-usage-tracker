export function usd(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n >= 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(6)}`;
}

// cost_source: "provider" (real charge) | "workspace" (this workspace's own
// price override) | "configured" (built-in/JSON price table) | "unknown"
// (cost is null — an honest gap, never a fabricated number).
export function costSourceLabel(source: string, long = false): string {
  switch (source) {
    case "provider":
      return long ? "provider actual" : "actual";
    case "workspace":
      return long ? "your workspace's price" : "your price";
    case "configured":
      return long ? "estimated" : "est";
    default:
      return "unknown";
  }
}

export function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

export function num(n: number): string {
  return n.toLocaleString();
}

export function relTime(iso: string | null): string {
  if (!iso) return "never";
  const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86_400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86_400)}d ago`;
}
