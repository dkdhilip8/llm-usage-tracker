import { useEffect, useState } from "react";
import { api } from "../lib/api";

export function DemoBanner() {
  const [live, setLive] = useState<boolean | null>(null);

  useEffect(() => {
    api
      .health()
      .then((h) => setLive(h.live_enabled))
      .catch(() => setLive(null));
  }, []);

  const text =
    live === true
      ? "DEMO · LIVE MODE ENABLED — allow-live keys hit real providers. Cost is the provider's actual charge where reported (OpenRouter), otherwise estimated from a configured price table."
      : "DEMO · all requests simulated. Token counts and cost are estimated from a configured price table, not real provider billing.";

  return (
    <div
      className={`border-b px-4 py-2 text-center text-xs font-medium ${
        live
          ? "border-emerald-200 bg-emerald-50 text-emerald-800"
          : "border-amber-200 bg-amber-50 text-amber-800"
      }`}
    >
      {text}
    </div>
  );
}
