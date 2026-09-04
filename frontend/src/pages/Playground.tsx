import { useEffect, useMemo, useState } from "react";
import {
  api,
  getAdminToken,
  type ChatResult,
  type KeyInspect,
  type ModelInfo,
} from "../lib/api";
import { Card } from "../components/Card";
import { usd } from "../lib/format";

const DEFAULT_PROMPT =
  "In two sentences, explain what an LLM gateway does and why usage tracking matters.";

export function Playground() {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [key, setKey] = useState("");
  const [inspect, setInspect] = useState<KeyInspect | null>(null);
  const [keyError, setKeyError] = useState<string | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [results, setResults] = useState<ChatResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [liveEnabled, setLiveEnabled] = useState(false);

  useEffect(() => {
    api.models().then(setModels).catch(() => setModels([]));
    api.health().then((h) => setLiveEnabled(h.live_enabled)).catch(() => {});
  }, []);

  // look up the key's allowed providers whenever it changes (debounced)
  useEffect(() => {
    const k = key.trim();
    if (!k) {
      setInspect(null);
      setKeyError(null);
      return;
    }
    const t = setTimeout(() => {
      api
        .inspectKey(k)
        .then((r) => {
          setInspect(r);
          setKeyError(null);
          setProvider((prev) =>
            r.providers.some((p) => p.provider === prev)
              ? prev
              : (r.providers[0]?.provider ?? ""),
          );
        })
        .catch((e: Error) => {
          setInspect(null);
          setKeyError(e.message);
        });
    }, 400);
    return () => clearTimeout(t);
  }, [key]);

  const providerModels = useMemo(
    () => models.filter((m) => m.provider === provider),
    [models, provider],
  );

  useEffect(() => {
    if (providerModels.length && !providerModels.some((m) => m.model === model)) {
      setModel(providerModels[0].model);
    }
  }, [providerModels, model]);

  const currentProvider = inspect?.providers.find((p) => p.provider === provider);
  const currentMode = currentProvider?.mode;

  // explain why a request would run simulated even though it might be expected live
  let simReason = "";
  if (currentProvider && currentMode === "simulated") {
    if (!liveEnabled) simReason = "server has ENABLE_LIVE off";
    else if (!inspect?.allow_live)
      simReason = 'this key is not "allow live" — toggle it in Admin';
    else if (!currentProvider.configured)
      simReason = `${provider} has no server API key configured`;
    else if (!currentProvider.valid)
      simReason = `${provider} key failed its liveness check`;
  }

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.proxyChat({ provider, model, prompt }, key.trim());
      setResults((r) => [res, ...r]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const ready = Boolean(key.trim() && inspect && provider && model && prompt.trim());

  // The Playground sends requests through the gateway (it writes usage_logs and
  // needs a vk_ key), so it is admin-only on the shared public demo.
  if (!getAdminToken()) {
    return (
      <div className="mx-auto max-w-md">
        <Card title="Playground">
          <p className="text-sm text-fg-muted">
            The Playground sends live requests through the gateway, so it is admin-only on
            the public demo. Enter the admin token on the <strong>Admin</strong> page to use
            it. The <strong>Dashboard</strong>, <strong>Requests</strong> and{" "}
            <strong>Insights</strong> tabs are open to everyone.
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
      <Card title="Send a request">
        <div className="space-y-3">
          <label className="block text-xs font-medium text-fg-muted">
            Virtual key
            <input
              className="mt-1 w-full rounded-md border border-line px-2 py-1.5 font-mono text-xs"
              placeholder="vk_…  (create one in Admin)"
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          </label>
          {keyError && <div className="text-xs text-red-600 dark:text-red-400">{keyError}</div>}
          {inspect && (
            <div className="rounded-md bg-surface-2 px-2 py-1.5 text-[11px] text-fg-muted">
              {inspect.label} · {inspect.providers.length} provider
              {inspect.providers.length === 1 ? "" : "s"} allowed
              {inspect.allow_live ? " · live-allowed" : ""}
            </div>
          )}

          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs font-medium text-fg-muted">
              Provider
              <select
                className="mt-1 w-full rounded-md border border-line px-2 py-1.5 text-sm disabled:bg-fill"
                value={provider}
                disabled={!inspect}
                onChange={(e) => setProvider(e.target.value)}
              >
                {!inspect && <option value="">enter a key first</option>}
                {inspect?.providers.map((p) => (
                  <option key={p.provider} value={p.provider}>
                    {p.provider} · {p.mode}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs font-medium text-fg-muted">
              Model
              <select
                className="mt-1 w-full rounded-md border border-line px-2 py-1.5 text-sm disabled:bg-fill"
                value={model}
                disabled={!providerModels.length}
                onChange={(e) => setModel(e.target.value)}
              >
                {providerModels.map((m) => (
                  <option key={m.model}>{m.model}</option>
                ))}
              </select>
            </label>
          </div>

          <label className="block text-xs font-medium text-fg-muted">
            Prompt
            <textarea
              className="mt-1 h-32 w-full rounded-md border border-line p-2 text-sm"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
          </label>

          <button
            onClick={send}
            disabled={busy || !ready}
            className="w-full rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {busy ? "Sending…" : `Send (${currentMode ?? "…"})`}
          </button>

          {error && <div className="text-xs text-red-600 dark:text-red-400">{error}</div>}
          {simReason && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800 dark:text-amber-300">
              Runs <strong>simulated</strong>: {simReason}.
            </div>
          )}
          {currentMode === "live" && (
            <div className="rounded-md border border-emerald-200 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-500/10 px-2 py-1.5 text-[11px] text-emerald-800 dark:text-emerald-300">
              Runs <strong>live</strong> — this calls {provider} for real and costs money.
            </div>
          )}
          <p className="text-[11px] text-fg-subtle">
            Requests are attributed to the key. Simulated cost is estimated from the configured
            price table; live OpenRouter cost is the provider's actual charge.
          </p>
        </div>
      </Card>

      <div className="space-y-3">
        {results.length === 0 && (
          <div className="rounded-xl border border-dashed border-line p-8 text-center text-sm text-fg-subtle">
            Paste a virtual key, pick a provider and model, and send a request.
          </div>
        )}
        {results.map((r) => (
          <Card key={r.request_id}>
            <div className="mb-2 flex items-center justify-between">
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${
                  r.mode === "live"
                    ? "bg-emerald-100 dark:bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                    : "bg-amber-100 dark:bg-amber-500/15 text-amber-700 dark:text-amber-400"
                }`}
              >
                {r.mode === "live" ? "Live Request" : "Simulated Request"}
              </span>
              <span className="font-mono text-xs text-fg-subtle">
                {r.provider}/{r.model} · {r.latency_ms} ms
              </span>
            </div>
            <p className="whitespace-pre-wrap text-sm text-fg">{r.response}</p>
            <div className="mt-3 grid grid-cols-4 gap-2 border-t border-line pt-3 text-center text-xs">
              <div>
                <div className="text-fg-subtle">Prompt</div>
                <div className="font-semibold tabular-nums">{r.usage.prompt_tokens}</div>
              </div>
              <div>
                <div className="text-fg-subtle">Completion</div>
                <div className="font-semibold tabular-nums">
                  {r.usage.completion_tokens}
                </div>
              </div>
              <div>
                <div className="text-fg-subtle">Total</div>
                <div className="font-semibold tabular-nums">{r.usage.total_tokens}</div>
              </div>
              <div>
                <div className="text-fg-subtle">
                  {r.cost_source === "provider" ? "Actual cost" : "Est. cost"}
                </div>
                <div className="font-semibold tabular-nums">{usd(r.cost)}</div>
              </div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
