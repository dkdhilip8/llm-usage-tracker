import { useCallback, useEffect, useState } from "react";
import { api, type Account, type AccountProvider } from "../lib/api";
import { Card } from "./Card";
import { usd } from "../lib/format";

function ProviderRow({
  p,
  onChange,
}: {
  p: AccountProvider;
  onChange: () => void;
}) {
  const [editing, setEditing] = useState(p.source === "none");
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.setAccountProviderKey(p.provider, val.trim());
      setVal("");
      setEditing(false);
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const clear = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.clearAccountProviderKey(p.provider);
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="flex flex-wrap items-center gap-1.5 text-sm">
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
                disabled={busy}
                onClick={clear}
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
                disabled={busy || val.trim().length < 8}
                onClick={save}
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
      {err && <span className="w-full text-right text-[10px] text-red-600">{err}</span>}
    </li>
  );
}

export function LiveKeysCard({ onChange }: { onChange: () => void }) {
  const [acct, setAcct] = useState<Account | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cap, setCap] = useState("");
  const [busy, setBusy] = useState(false);

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

  const saveCap = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.patchAccount({ live_cap_usd: Number(cap) });
      setCap("");
      refresh();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!acct) {
    return (
      <Card title="Live provider keys">
        <div className="text-sm text-fg-subtle">{err ?? "Loading…"}</div>
      </Card>
    );
  }

  const effectiveCap = acct.live_cap_usd ?? acct.live_cap_default_usd;

  return (
    <Card title="Live provider keys">
      <div className="space-y-3 text-sm">
        <p className="text-xs text-fg-muted">
          Paste your own OpenAI / Anthropic / OpenRouter key. Virtual keys you mark{" "}
          <strong>Allow live calls</strong> then hit the real provider on your key and are
          billed to you, stopping at your monthly cap. Only the last 4 digits are ever shown
          back.
        </p>

        <ul className="space-y-1.5">
          {acct.providers.map((p) => (
            <ProviderRow key={p.provider} p={p} onChange={refresh} />
          ))}
        </ul>

        <div className="border-t border-line pt-3">
          <div className="flex justify-between text-[11px] text-fg-subtle">
            <span>Live spend this month</span>
            <span>
              {usd(acct.live_spend_this_month)} / {usd(effectiveCap)}
            </span>
          </div>
          <div className="mt-1 h-1.5 rounded bg-fill">
            <div
              className={`h-1.5 rounded ${
                acct.live_spend_this_month >= effectiveCap ? "bg-red-500" : "bg-brand-500"
              }`}
              style={{
                width: `${Math.min(
                  100,
                  effectiveCap > 0
                    ? (acct.live_spend_this_month / effectiveCap) * 100
                    : 100,
                )}%`,
              }}
            />
          </div>
          <div className="mt-2 flex items-center gap-2">
            <span className="text-xs text-fg-muted">Monthly cap $</span>
            <input
              type="number"
              min="0"
              max={acct.live_cap_max_usd}
              step="1"
              className="w-24 rounded-md border border-line px-2 py-1 text-sm"
              placeholder={String(effectiveCap)}
              value={cap}
              onChange={(e) => setCap(e.target.value)}
            />
            <button
              className="rounded-md border border-line px-2 py-1 text-xs text-fg-muted hover:bg-fill disabled:opacity-50"
              disabled={busy || !cap.trim()}
              onClick={saveCap}
            >
              set
            </button>
            <span className="text-[11px] text-fg-subtle">(max ${acct.live_cap_max_usd})</span>
          </div>
        </div>

        {err && <div className="text-sm text-red-600 dark:text-red-400">{err}</div>}
      </div>
    </Card>
  );
}
