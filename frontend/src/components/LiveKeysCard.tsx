import { useCallback, useEffect, useState } from "react";
import { api, type Account, type AccountProvider } from "../lib/api";
import { Card } from "./Card";
import { usd } from "../lib/format";

function ProviderRow({
  p,
  defaultCap,
  onChange,
}: {
  p: AccountProvider;
  defaultCap: number;
  onChange: () => void;
}) {
  const [editing, setEditing] = useState(p.source === "none");
  const [val, setVal] = useState("");
  const [cap, setCap] = useState("");
  const [busy, setBusy] = useState<null | "key" | "cap">(null);
  const [err, setErr] = useState<string | null>(null);

  const run = async (kind: "key" | "cap", fn: () => Promise<unknown>) => {
    setBusy(kind);
    setErr(null);
    try {
      await fn();
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const saveKey = () =>
    run("key", async () => {
      await api.setAccountProviderKey(p.provider, val.trim());
      setVal("");
      setEditing(false);
    });
  const clearKey = () => run("key", () => api.clearAccountProviderKey(p.provider));
  const saveCap = () =>
    run("cap", async () => {
      await api.setProviderCap(
        p.provider,
        cap.trim() === "" ? null : Number(cap),
      );
      setCap("");
    });

  const effCap = p.monthly_cap_usd ?? defaultCap;
  const over = p.spend_this_month >= effCap;

  return (
    <li className="space-y-1.5 border-b border-line pb-2 last:border-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-1.5 text-sm">
        <span className="font-medium capitalize">{p.provider}</span>
        {p.source === "env" ? (
          <span className="ml-auto rounded bg-fill px-1.5 py-0.5 text-[10px] text-fg-muted">
            set via server env
          </span>
        ) : (
          <div className="ml-auto flex items-center gap-1.5">
            {p.source === "account" && !editing && (
              <>
                <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400">
                  saved ····{p.last4}
                </span>
                <button
                  className="text-[11px] text-brand-600 hover:underline"
                  onClick={() => setEditing(true)}
                >
                  replace
                </button>
                <button
                  className="text-[11px] text-red-600 hover:underline disabled:opacity-50"
                  disabled={busy !== null}
                  onClick={clearKey}
                >
                  clear
                </button>
              </>
            )}
            {editing && (
              <>
                <input
                  type="password"
                  placeholder={`paste ${p.provider} key`}
                  className="w-40 rounded border border-line px-1.5 py-0.5 text-[11px]"
                  value={val}
                  onChange={(e) => setVal(e.target.value)}
                />
                <button
                  className="rounded bg-brand-600 px-1.5 py-0.5 text-[10px] font-medium text-white hover:bg-brand-700 disabled:opacity-50"
                  disabled={busy !== null || val.trim().length < 8}
                  onClick={saveKey}
                >
                  save
                </button>
                {p.source === "account" && (
                  <button
                    className="text-[11px] text-fg-subtle hover:underline"
                    onClick={() => setEditing(false)}
                  >
                    cancel
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {p.source === "account" && (
        <div className="pl-0.5">
          <div className="flex justify-between text-[11px] text-fg-subtle">
            <span>live spend this month</span>
            <span>
              {usd(p.spend_this_month)} / {usd(effCap)}
            </span>
          </div>
          <div className="mt-1 h-1.5 rounded bg-fill">
            <div
              className={`h-1.5 rounded ${over ? "bg-red-500" : "bg-brand-500"}`}
              style={{
                width: `${Math.min(
                  100,
                  effCap > 0 ? (p.spend_this_month / effCap) * 100 : 100,
                )}%`,
              }}
            />
          </div>
          <div className="mt-1.5 flex items-center gap-1.5">
            <span className="text-[11px] text-fg-muted">Monthly cap $</span>
            <input
              type="number"
              min="0"
              step="1"
              className="w-24 rounded-md border border-line px-2 py-0.5 text-[11px]"
              placeholder={
                p.monthly_cap_usd != null
                  ? String(p.monthly_cap_usd)
                  : `${defaultCap} (default)`
              }
              value={cap}
              onChange={(e) => setCap(e.target.value)}
            />
            <button
              className="rounded-md border border-line px-2 py-0.5 text-[11px] text-fg-muted hover:bg-fill disabled:opacity-50"
              disabled={busy !== null || cap.trim() === ""}
              onClick={saveCap}
            >
              set
            </button>
            {p.monthly_cap_usd != null && (
              <button
                className="text-[11px] text-brand-600 hover:underline disabled:opacity-50"
                disabled={busy !== null}
                onClick={() => run("cap", () => api.setProviderCap(p.provider, null))}
              >
                use default
              </button>
            )}
          </div>
        </div>
      )}
      {err && <div className="text-[10px] text-red-600">{err}</div>}
    </li>
  );
}

export function LiveKeysCard({ onChange }: { onChange: () => void }) {
  const [acct, setAcct] = useState<Account | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .getAccount()
      .then(setAcct)
      .catch((e: Error) => setErr(e.message));
  }, []);
  useEffect(load, [load]);

  const refresh = () => {
    load();
    onChange();
  };

  if (!acct) {
    return (
      <Card title="Live provider keys">
        <div className="text-sm text-fg-subtle">{err ?? "Loading…"}</div>
      </Card>
    );
  }

  return (
    <Card title="Live provider keys">
      <div className="space-y-3 text-sm">
        <p className="text-xs text-fg-muted">
          Paste your own OpenAI / Anthropic / OpenRouter / Gemini key and set a monthly cap for
          it. Virtual keys for a provider you've configured here hit the real provider on your key,
          billed to you, and stop at that provider's cap. Only the last 4 digits are shown back.
        </p>

        <ul className="space-y-2">
          {acct.providers.map((p) => (
            <ProviderRow
              key={p.provider}
              p={p}
              defaultCap={acct.live_cap_default_usd}
              onChange={refresh}
            />
          ))}
        </ul>

        <p className="text-[11px] text-fg-subtle">
          Each provider's cap is separate. A virtual key also stops at its own budget —
          whichever limit is lower wins.
        </p>

        {err && <div className="text-sm text-red-600 dark:text-red-400">{err}</div>}
      </div>
    </Card>
  );
}
