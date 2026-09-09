import { useEffect, useMemo, useState } from "react";
import { api, type ChatResult, type KeyInspect, type ModelInfo } from "../lib/api";
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

  useEffect(() => {
    api.models().then(setModels).catch(() => setModels([]));
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

  let blockReason = "";
  if (inspect && currentProvider) {
    if (!inspect.allow_live)
      blockReason = 'this key is paused — turn on "allow live" for it on the Workspace page';
    else if (!currentProvider.ready)
      blockReason = `no ${provider} API key is configured for this workspace`;
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

  const ready = Boolean(
    key.trim() && inspect && provider && model && prompt.trim() && !blockReason,
  );

  return (
    <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
      <Card title="Send a request">
        <div className="space-y-3">
          <label className="block text-xs font-medium text-fg-muted">
            Virtual key
            <input
              className="mt-1 w-full rounded-md border border-line px-2 py-1.5 font-mono text-xs"
              placeholder="vk_…  (create one on the Workspace page)"
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          </label>
          {keyError && <div className="text-xs text-red-600 dark:text-red-400">{keyError}</div>}
          {inspect && (
            <div className="rounded-md bg-surface-2 px-2 py-1.5 text-[11px] text-fg-muted">
              {inspect.label} · {inspect.providers.length} provider
              {inspect.providers.length === 1 ? "" : "s"} allowed
              {inspect.allow_live ? "" : " · paused"}
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
                    {p.provider}
                    {p.ready ? "" : " · no key"}
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
            {busy ? "Sending…" : "Send"}
          </button>

          {error && <div className="text-xs text-red-600 dark:text-red-400">{error}</div>}
          {blockReason && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800 dark:text-amber-300">
              Can't send: {blockReason}.
            </div>
          )}
          <p className="text-[11px] text-fg-subtle">
            Every request calls the real provider and costs money. OpenRouter cost is the
            provider's actual charge; the others are estimated from the price table.
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
            <div className="mb-2 flex items-center justify-end">
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
                <div className="font-semibold tabular-nums">{r.usage.completion_tokens}</div>
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
