import { useCallback, useEffect, useState } from "react";
import { api, type Workspace } from "../lib/api";
import { Card } from "./Card";
import { usd } from "../lib/format";

function KeyRow({ p, onChange }: { p: Workspace["providers"][number]; onChange: () => void }) {
  const [editing, setEditing] = useState(p.source === "none");
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.setWorkspaceKey(p.provider, val.trim());
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
    try {
      await api.clearWorkspaceKey(p.provider);
      onChange();
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="flex items-center gap-2 text-sm">
      <span className="font-medium capitalize">{p.provider}</span>
      {p.source === "env" && (
        <span className="ml-auto rounded bg-fill px-1.5 py-0.5 text-[10px] text-fg-muted">
          server env
        </span>
      )}
      {p.source !== "env" && (
        <div className="ml-auto flex items-center gap-1.5">
          {p.source === "db" && !editing && (
            <>
              <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400">
                saved ····{p.last4}
              </span>
              <button className="text-[11px] text-brand-600 hover:underline" onClick={() => setEditing(true)}>
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
              {p.source === "db" && (
                <button className="text-[11px] text-fg-subtle hover:underline" onClick={() => setEditing(false)}>
                  cancel
                </button>
              )}
            </>
          )}
          {err && <span className="text-[10px] text-red-600">{err}</span>}
        </div>
      )}
    </li>
  );
}

export function WorkspaceCard({ onChange }: { onChange: () => void }) {
  const [ws, setWs] = useState<Workspace | null | undefined>(undefined);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [cap, setCap] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .getWorkspace()
      .then((r) => setWs("workspace" in r && r.workspace === null ? null : (r as Workspace)))
      .catch((e: Error) => setErr(e.message));
  }, []);
  useEffect(load, [load]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      load();
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (ws === undefined) return <Card title="Workspace"><div className="text-sm text-fg-subtle">Loading…</div></Card>;

  if (ws === null) {
    return (
      <Card title="Workspace">
        <p className="mb-3 text-xs text-fg-muted">
          A team gateway: you set one OpenAI/Anthropic/OpenRouter key, teammates join with a code
          and make live calls through it without ever seeing it, under a monthly spend cap.
        </p>
        <div className="space-y-2">
          <div className="flex gap-2">
            <input
              className="flex-1 rounded-md border border-line px-2 py-1.5 text-sm"
              placeholder="Workspace name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button
              className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
              disabled={busy || !name.trim()}
              onClick={() => run(() => api.createWorkspace(name.trim()))}
            >
              Create
            </button>
          </div>
          <div className="flex gap-2">
            <input
              className="flex-1 rounded-md border border-line px-2 py-1.5 text-sm"
              placeholder="Join code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            <button
              className="rounded-md border border-line px-3 py-1.5 text-sm text-fg-muted hover:bg-fill disabled:opacity-50"
              disabled={busy || !code.trim()}
              onClick={() => run(() => api.joinWorkspace(code.trim()))}
            >
              Join
            </button>
          </div>
          {err && <div className="text-sm text-red-600 dark:text-red-400">{err}</div>}
        </div>
      </Card>
    );
  }

  return (
    <Card title={`Workspace · ${ws.name}`}>
      <div className="space-y-3 text-sm">
        <div className="text-xs text-fg-subtle">
          {ws.is_owner ? "You own this workspace" : "You're a member"} · {ws.member_count} member
          {ws.member_count === 1 ? "" : "s"} · live spend {usd(ws.spend_this_month)} / {usd(ws.monthly_cap_usd)} this month
        </div>

        <div>
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Provider keys
          </div>
          <ul className="space-y-1.5">
            {ws.providers.map((p) => (
              <KeyRow
                key={p.provider}
                p={p}
                onChange={() => {
                  load();
                  onChange();
                }}
              />
            ))}
          </ul>
          {!ws.is_owner && (
            <p className="mt-1 text-[11px] text-fg-subtle">Only the owner can change these.</p>
          )}
        </div>

        {ws.is_owner && (
          <>
            <div className="flex items-center gap-2">
              <span className="text-xs text-fg-muted">Join code</span>
              <code className="rounded bg-surface px-2 py-0.5 font-mono text-xs">{ws.join_code}</code>
              <button
                className="text-[11px] text-brand-600 hover:underline"
                onClick={() => navigator.clipboard?.writeText(ws.join_code ?? "")}
              >
                copy
              </button>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-fg-muted">Monthly cap $</span>
              <input
                type="number"
                min="0"
                max={ws.cap_max_usd}
                step="1"
                className="w-24 rounded-md border border-line px-2 py-1 text-sm"
                placeholder={String(ws.monthly_cap_usd)}
                value={cap}
                onChange={(e) => setCap(e.target.value)}
              />
              <button
                className="rounded-md border border-line px-2 py-1 text-xs text-fg-muted hover:bg-fill disabled:opacity-50"
                disabled={busy || !cap.trim()}
                onClick={() => run(() => api.patchWorkspace({ monthly_cap_usd: Number(cap) })).then(() => setCap(""))}
              >
                set
              </button>
              <span className="text-[11px] text-fg-subtle">(max ${ws.cap_max_usd})</span>
            </div>

            {ws.members && ws.members.length > 0 && (
              <div>
                <div className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
                  Members
                </div>
                <table className="w-full text-xs">
                  <tbody>
                    {ws.members.map((m) => (
                      <tr key={m.user_id} className="border-b border-line last:border-0">
                        <td className="py-1">
                          {m.email}
                          {m.is_owner && <span className="ml-1 text-fg-subtle">(owner)</span>}
                        </td>
                        <td className="py-1 text-right tabular-nums">{m.requests} req</td>
                        <td className="py-1 text-right tabular-nums">{usd(m.cost)}</td>
                        <td className="py-1 text-right">
                          {!m.is_owner && (
                            <button
                              className="text-red-600 hover:underline"
                              onClick={() => run(() => api.removeMember(m.user_id))}
                            >
                              remove
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        <div className="pt-1">
          {ws.is_owner ? (
            <button
              className="text-xs text-red-600 hover:underline"
              onClick={() =>
                window.confirm("Delete the workspace? Members are detached and their keys removed.") &&
                run(() => api.deleteWorkspace())
              }
            >
              Delete workspace
            </button>
          ) : (
            <button
              className="text-xs text-red-600 hover:underline"
              onClick={() =>
                window.confirm("Leave the workspace? Your keys and usage in it are removed.") &&
                run(() => api.leaveWorkspace())
              }
            >
              Leave workspace
            </button>
          )}
        </div>
        {err && <div className="text-sm text-red-600 dark:text-red-400">{err}</div>}
      </div>
    </Card>
  );
}
