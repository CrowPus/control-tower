/* Admin → Plugins: the projects connected to this install — Python plugins from the server's plugins/ folder and
   projects connected over HTTP. The owner switches each on, fills in its settings, renews its shared secret, opens its
   views and collects its numbers. */
import { useQuery } from "@tanstack/react-query";
import { Copy, Plug, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, Card, Dialog, Empty, Field, Input, PageTitle, Toggle, ago } from "../ui";

type PluginOut = Schemas["PluginOut"];

function usePlugins() {
  return useQuery({ queryKey: ["plugins"], queryFn: async () => unwrap(await api.GET("/admin-api/plugins")) });
}

function SecretShown({ secret, onClose }: { secret: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Dialog open={!!secret} onClose={onClose} title="The shared secret" footer={<Button variant="primary" onClick={onClose}>I have put it in the project</Button>}>
      <p className="text-[13px]">Put this into the project's settings now. It is shown only this once; Control Tower keeps it encrypted.</p>
      <div className="mt-3 flex gap-2">
        <Input readOnly value={secret} className="font-mono text-xs" onFocus={(e) => e.target.select()} />
        <Button onClick={() => { void navigator.clipboard?.writeText(secret); setCopied(true); }}><Copy className="h-3.5 w-3.5" aria-hidden />{copied ? "Copied" : "Copy"}</Button>
      </div>
    </Dialog>
  );
}

function Parts({ title, items }: { title: string; items: { key: string; label: string; detail?: string }[] }) {
  if (!items.length) return null;
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      <ul className="mt-1 space-y-1 text-[13px]">
        {items.map((i) => (
          <li key={i.key}><span className="font-medium text-fg">{i.key}</span>{i.detail && <> <Badge tone={i.detail === "always you" ? "warning" : "neutral"}>{i.detail}</Badge></>}<span className="block text-xs text-muted">{i.label}</span></li>
        ))}
      </ul>
    </div>
  );
}

function Table({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead><tr>{columns.map((c) => <th key={c} className="border-b border-border px-2 py-1.5 font-semibold text-muted">{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{columns.map((c) => <td key={c} className="border-b border-border px-2 py-1.5 text-fg">{typeof r[c] === "object" ? JSON.stringify(r[c]) : String(r[c] ?? "")}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function ViewResult({ result }: { result: Record<string, unknown> }) {
  const lists = Object.entries(result).filter(([, v]) => Array.isArray(v) && v.length > 0 && v.every((x) => x && typeof x === "object" && !Array.isArray(x)));
  const rest = Object.fromEntries(Object.entries(result).filter(([k]) => !lists.some(([l]) => l === k)));
  return (
    <div className="space-y-3">
      {lists.map(([k, v]) => <div key={k}><p className="mb-1 text-xs font-semibold text-muted">{k}</p><Table rows={v as Record<string, unknown>[]} /></div>)}
      {Object.keys(rest).length > 0 && <pre className="overflow-x-auto rounded bg-raised p-2 text-xs text-fg">{JSON.stringify(rest, null, 2)}</pre>}
    </div>
  );
}

function PluginCard({ plugin, owner, onSecret }: { plugin: PluginOut; owner: boolean; onSecret: (s: string) => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [view, setView] = useState<{ key: string; result: Record<string, unknown> } | null>(null);
  const [confirm, setConfirm] = useState(false);
  useEffect(() => setValues({}), [plugin]);
  const change = useAction(async (body: { enabled?: boolean; values?: Record<string, string | null> }) => unwrap(await api.PUT("/admin-api/plugins/{key}", { params: { path: { key: plugin.key } }, body })),
    { invalidate: [["plugins"], ["team-catalogue"]], done: () => "Saved." });
  const secret = useAction(async () => unwrap(await api.POST("/admin-api/plugins/{key}/secret", { params: { path: { key: plugin.key } } })), { invalidate: [["plugins"]], done: (r) => { onSecret(r.secret); } });
  const fetch = useAction(async () => unwrap(await api.POST("/admin-api/plugins/connections/{key}/fetch", { params: { path: { key: plugin.key } } })), { invalidate: [["plugins"], ["team-catalogue"]], done: () => "Fetched what the project offers." });
  const remove = useAction(async () => unwrap(await api.DELETE("/admin-api/plugins/connections/{key}", { params: { path: { key: plugin.key } } })), { invalidate: [["plugins"], ["team-catalogue"]] });
  const collect = useAction(async () => unwrap(await api.POST("/admin-api/plugins/{key}/collect", { params: { path: { key: plugin.key } } })), { invalidate: [["plugins"]], done: (r) => ((r.problems[plugin.key] ?? []).length ? `Collected, with problems: ${(r.problems[plugin.key] ?? []).join("; ")}` : "Collected.") });
  const open = useAction(async (tool: string) => ({ tool, ...unwrap(await api.POST("/admin-api/plugins/{key}/views/{tool}", { params: { path: { key: plugin.key, tool } } })) }), { done: (r) => { setView({ key: r.tool, result: r.result }); } });
  const notice = change.notice ?? fetch.notice ?? collect.notice ?? secret.notice ?? remove.notice ?? open.notice;
  const dirty = Object.keys(values).length > 0;
  return (
    <Card className="mt-4" title={<span className="flex flex-wrap items-center gap-2">{plugin.name}<Badge>{plugin.source === "http" ? "HTTP" : "Python"}</Badge><Badge>v{plugin.version}</Badge>{plugin.enabled ? <Badge tone="success">On</Badge> : <Badge>Off</Badge>}</span>}
      actions={owner && plugin.source === "http" && <Button size="sm" loading={fetch.busy} onClick={() => fetch.run(undefined)}><RefreshCw className="h-3.5 w-3.5" aria-hidden />Fetch</Button>}>
      {notice && <div className="mb-3"><Alert tone={notice.tone}>{notice.text}</Alert></div>}
      {plugin.description && <p className="mb-2 text-[13px] text-muted">{plugin.description}</p>}
      <p className="mb-3 break-all text-xs text-subtle">{plugin.key} · {plugin.location}</p>
      <Toggle tone="primary" label="Switched on" description={plugin.missing.length ? `Fill in first: ${plugin.missing.join(", ")}.` : plugin.enabled ? "On: you can give its tools and actions to agents, and make its starter agents." : "Off: its tools, actions and starter agents are not offered, and calls are refused."}
        checked={plugin.enabled} disabled={!owner || change.busy} onChange={(enabled) => change.run({ enabled })} />
      {plugin.warnings.map((w) => <div key={w} className="mt-2"><Alert tone="warning">{w}</Alert></div>)}

      {(plugin.settings.length > 0 || plugin.source === "http") && (
        <div className="mt-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Settings</h3>
          <div className="mt-2 grid gap-3 sm:grid-cols-2">
            {plugin.settings.map((s) => (
              <Field key={s.key} label={`${s.label}${s.required ? "" : " (optional)"}`} hint={s.secret ? (s.set ? "Set. Type to replace it; leave empty to keep it." : s.hint || "Stored encrypted.") : s.hint}>
                <Input type={s.secret ? "password" : "text"} autoComplete="off" disabled={!owner} placeholder={s.secret && s.set ? "••••••••" : ""}
                  value={values[s.key] ?? (s.secret ? "" : s.value ?? "")} onChange={(e) => setValues({ ...values, [s.key]: e.target.value })} />
              </Field>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {owner && plugin.settings.length > 0 && <Button variant="primary" size="sm" disabled={!dirty} loading={change.busy} onClick={() => change.run({ values })}>Save settings</Button>}
            {owner && <Button size="sm" loading={secret.busy} onClick={() => secret.run(undefined)}>New shared secret</Button>}
            <span className="text-xs text-subtle">Shared secret: {plugin.secret_set ? "set" : "not set"}</span>
          </div>
        </div>
      )}

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <Parts title="Tools" items={plugin.tools} />
        <Parts title="Actions" items={plugin.actions} />
        <Parts title="Numbers" items={plugin.metrics} />
        <Parts title="Starter agents" items={plugin.templates} />
      </div>
      {plugin.personal.length > 0 && <p className="mt-3 text-xs text-muted">Hidden from agents: {plugin.personal.join(", ")}.</p>}

      {plugin.metrics.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {owner && <Button size="sm" disabled={!plugin.enabled} loading={collect.busy} onClick={() => collect.run(undefined)}>Collect numbers now</Button>}
          <span className="text-xs text-subtle">{plugin.collected_at ? `Last collected ${ago(plugin.collected_at)}` : "Not collected yet."}</span>
          {plugin.collect_errors.map((e) => <span key={e} className="text-xs text-warning">{e}</span>)}
        </div>
      )}

      {plugin.views.length > 0 && (
        <div className="mt-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Views</h3>
          <div className="mt-1 flex flex-wrap gap-2">
            {plugin.views.map((v) => <Button key={v.key} size="sm" disabled={!owner || !plugin.enabled} loading={open.busy && open.variables === v.key} title={v.label} onClick={() => open.run(v.key)}>{v.key}</Button>)}
          </div>
          {view && <div className="mt-3"><ViewResult result={view.result} /></div>}
        </div>
      )}

      {owner && plugin.source === "http" && (
        <div className="mt-4 border-t border-border pt-3">
          <Button variant="danger" size="sm" onClick={() => setConfirm(true)}><Trash2 className="h-3.5 w-3.5" aria-hidden />Remove the connection</Button>
        </div>
      )}
      <Dialog open={confirm} onClose={() => setConfirm(false)} title={`Disconnect ${plugin.name}?`}
        footer={<><Button onClick={() => setConfirm(false)}>Keep it</Button><Button variant="danger" loading={remove.busy} onClick={() => { remove.run(undefined); setConfirm(false); }}>Disconnect</Button></>}>
        <p className="text-[13px]">Its tools, actions and starter agents leave the team at once; its numbers stay in Insights' history.</p>
      </Dialog>
    </Card>
  );
}

function ConnectDialog({ open, onClose, onSecret }: { open: boolean; onClose: () => void; onSecret: (s: string) => void }) {
  const [key, setKey] = useState("");
  const [url, setUrl] = useState("");
  const add = useAction(async () => unwrap(await api.POST("/admin-api/plugins/connections", { body: { key, base_url: url, secret: "" } })),
    { invalidate: [["plugins"]], done: (r) => { onClose(); setKey(""); setUrl(""); onSecret(r.secret); } });
  return (
    <Dialog open={open} onClose={onClose} title="Connect a project over HTTP"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!key || !url} loading={add.busy} onClick={() => add.run(undefined)}>Connect</Button></>}>
      {add.notice && <div className="mb-3"><Alert tone={add.notice.tone}>{add.notice.text}</Alert></div>}
      <div className="grid gap-3">
        <Field label="Key" hint="Short, lowercase: it names the project's tools (key_tool) and events (key.event)."><Input value={key} maxLength={24} placeholder="shop" onChange={(e) => setKey(e.target.value.toLowerCase())} /></Field>
        <Field label="The project's address for Control Tower" hint="Where it answers /manifest, /tools/…, /actions/… and /metrics."><Input value={url} placeholder="https://shop.example.com/control-tower" onChange={(e) => setUrl(e.target.value)} /></Field>
      </div>
      <p className="mt-3 text-xs text-muted">Next: Control Tower shows a shared secret once. Put it into the project's settings, then press Fetch on the new card.</p>
    </Dialog>
  );
}

export function PluginsPage({ owner }: { owner: boolean }) {
  const { data, error } = usePlugins();
  const [connect, setConnect] = useState(false);
  const [shown, setShown] = useState("");
  if (error) return <Alert>{error.message}</Alert>;
  if (!data) return null;
  return (
    <>
      <PageTitle title="Plugins" description="Your projects, connected: what they give the agents to read, what agents may propose to do in them (only after your clearance), their numbers and their starter agents. Each starts off."
        actions={owner && <Button variant="primary" onClick={() => setConnect(true)}><Plug className="h-3.5 w-3.5" aria-hidden />Connect a project</Button>} />
      {data.problems.map((p) => (
        <div key={p.key} className="mb-3"><Alert tone="warning"><span className="font-semibold">{p.key}</span> ({p.source === "http" ? p.location : `plugins/${p.key}`}): {p.error}
          {p.source === "http" && owner && <FetchButton keyName={p.key} />}</Alert></div>
      ))}
      {data.plugins.length === 0 && data.problems.length === 0 && (
        <Card><Empty>No plugins yet. Put a Python plugin in the server's plugins/ folder and restart, or connect a project over HTTP.</Empty></Card>
      )}
      {data.plugins.map((p) => <PluginCard key={p.key} plugin={p} owner={owner} onSecret={setShown} />)}
      <p className="mt-4 text-xs text-subtle">Projects send events to {data.events_address}, signed with their shared secret.</p>
      <ConnectDialog open={connect} onClose={() => setConnect(false)} onSecret={setShown} />
      <SecretShown secret={shown} onClose={() => setShown("")} />
    </>
  );
}

function FetchButton({ keyName }: { keyName: string }) {
  const fetch = useAction(async () => unwrap(await api.POST("/admin-api/plugins/connections/{key}/fetch", { params: { path: { key: keyName } } })), { invalidate: [["plugins"], ["team-catalogue"]] });
  const remove = useAction(async () => unwrap(await api.DELETE("/admin-api/plugins/connections/{key}", { params: { path: { key: keyName } } })), { invalidate: [["plugins"]] });
  return (
    <span className="ml-2 inline-flex flex-wrap items-center gap-2">
      <Button size="sm" loading={fetch.busy} onClick={() => fetch.run(undefined)}><RefreshCw className="h-3.5 w-3.5" aria-hidden />Fetch</Button>
      <Button size="sm" variant="ghost" loading={remove.busy} onClick={() => remove.run(undefined)}>Remove</Button>
      {fetch.notice?.tone === "danger" && <span className="text-xs">{fetch.notice.text}</span>}
    </span>
  );
}
