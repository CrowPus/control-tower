/* Admin → Team → Outside agents: agents from other providers — Claude, ChatGPT,
   Gemini … — with a desk, the tools the owner ticks, the actions they may propose (each waits in Clearance), a credential
   shown once, and everything they do on record. Connected ones bring their own model through the Agent Gateway; hosted
   ones run on your install's server with the owner's provider key. */
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Copy, KeyRound, Pause, Play, Plus, ShieldOff, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorMessage, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, Card, Dialog, Empty, Field, Input, PageTitle, Select, Table, Td, Textarea, Toggle, ago, when, type Tone } from "../ui";

type Agent = Schemas["OutsideAgentOut"];
type Overview = Schemas["OutsideOverviewOut"];
type Form = Schemas["OutsideIn"];

const STATUS: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Needs its credential", tone: "warning" }, waiting: { label: "Waiting to connect", tone: "info" },
  active: { label: "Working", tone: "success" }, paused: { label: "Paused", tone: "neutral" }, revoked: { label: "Revoked", tone: "danger" },
};
const PROVIDERS = [
  { key: "anthropic", label: "Anthropic (Claude)", example: "claude-sonnet-5-5" },
  { key: "openai", label: "OpenAI (GPT)", example: "gpt-5" },
  { key: "google", label: "Google (Gemini)", example: "gemini-3-flash" },
];
const EMPTY: Form = { name: "", job: "", department: "", kind: "connected", provider: "other", client_label: "Claude Code", model: "", provider_key: "", tools: [], actions: [],
  desk: null, calls_per_day: null, tokens_per_day: null, schedule: {}, ip_allowlist: [], playbook: "" };

function useOverview() {
  return useQuery({ queryKey: ["outside"], queryFn: async () => unwrap(await api.GET("/admin-api/outside")) });
}

export function OutsidePage({ owner }: { owner: boolean }) {
  const { data, error } = useOverview();
  const [editing, setEditing] = useState<Agent | "new" | null>(null);
  const live = (data?.agents ?? []).filter((a) => a.status !== "revoked");
  return (
    <>
      <Link to="/team" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg"><ArrowLeft className="h-3.5 w-3.5" />Team</Link>
      <PageTitle title="Outside agents" description="Agents from other providers — Claude, ChatGPT, Gemini … — working in the company like the team: a desk, the tools you tick, a credential you issue, and every change waits for you in Clearance." />
      {error && <Alert>{errorMessage(error)}</Alert>}
      {data && <RulesCard owner={owner} data={data} />}
      <Card className="mt-4" padded={false} title={data?.rules.max_agents != null ? `${live.length} of ${data.rules.max_agents}` : `${live.length} outside agents`}
        actions={owner && <Button size="sm" variant="primary" disabled={!data || (data.rules.max_agents != null && live.length >= data.rules.max_agents)} onClick={() => setEditing("new")}><Plus className="h-3.5 w-3.5" />Hire an outside agent</Button>}>
        {data && data.agents.length === 0 && <div className="p-4"><Empty>No outside agents yet. Hire one: give it a job, its tools and a desk, then issue its credential.</Empty></div>}
        {data && data.agents.length > 0 && (
          <Table head={["Agent", "Kind", "Desk", "Status", "Today", "Last seen"]}>
            {data.agents.map((a) => (
              <tr key={a.id} className="border-b border-border last:border-0 hover:bg-raised/50">
                <Td><Link to={`/team/outside/${a.id}`} className="font-medium text-fg hover:underline">{a.name}</Link><p className="text-xs text-muted">{a.key}</p></Td>
                <Td>{a.kind === "hosted" ? `Hosted · ${a.provider} · ${a.model}` : `Connected · ${a.client_label || "any MCP client"}`}</Td>
                <Td>{a.desk ?? "—"}</Td>
                <Td><Badge tone={STATUS[a.status]?.tone ?? "neutral"}>{STATUS[a.status]?.label ?? a.status}</Badge></Td>
                <Td className="whitespace-nowrap">{a.calls_today} calls{a.tokens_today ? ` · ${a.tokens_today.toLocaleString()} tokens` : ""}</Td>
                <Td className="whitespace-nowrap">{a.last_seen_at ? ago(a.last_seen_at) : "never"}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      {data && editing && <AgentForm data={data} agent={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function RulesCard({ owner, data }: { owner: boolean; data: Overview }) {
  const [form, setForm] = useState(data.rules);
  const save = useAction(async () => unwrap(await api.PUT("/admin-api/outside/rules", { body: form })), { invalidate: [["outside"]], done: () => "Saved." });
  return (
    <Card title="Company rules" actions={owner && <Button size="sm" variant="primary" loading={save.busy} onClick={() => save.run(undefined)}>Save</Button>}>
      {save.notice && <div className="mb-3"><Alert tone={save.notice.tone}>{save.notice.text}</Alert></div>}
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Outside agents at most" hint="Empty: as many as you hire."><Input type="number" min={0} disabled={!owner} value={form.max_agents ?? ""}
          onChange={(e) => setForm({ ...form, max_agents: e.target.value === "" ? null : Number(e.target.value) })} /></Field>
        <Field label="A credential lasts (days)" hint="Empty: until you revoke it"><Input type="number" min={1} disabled={!owner} value={form.credential_days ?? ""}
          onChange={(e) => setForm({ ...form, credential_days: e.target.value ? Number(e.target.value) : null })} /></Field>
        <Field label="Tool calls a day per agent" hint="When an agent has no number of its own"><Input type="number" min={1} disabled={!owner} value={form.calls_per_day ?? ""}
          onChange={(e) => setForm({ ...form, calls_per_day: e.target.value ? Number(e.target.value) : null })} /></Field>
      </div>
      <div className="mt-2"><Toggle label="Pause every outside agent" description="They stop at once; nothing they asked for is lost." checked={!!form.paused_all} disabled={!owner}
        onChange={(v) => setForm({ ...form, paused_all: v })} /></div>
      <p className="mt-2 text-xs text-muted">Connected agents use the gateway at <code>{data.gateway_url}</code>.</p>
    </Card>
  );
}

function AgentForm({ data, agent, onClose }: { data: Overview; agent: Agent | null; onClose: () => void }) {
  const navigate = useNavigate();
  const [form, setForm] = useState<Form>(() => agent ? { name: agent.name, job: agent.job, department: agent.department, kind: agent.kind as Form["kind"],
    provider: agent.provider as Form["provider"], client_label: agent.client_label, model: agent.model, provider_key: "", tools: agent.tools, actions: agent.actions,
    desk: agent.desk ?? null, calls_per_day: agent.calls_per_day ?? null, tokens_per_day: agent.tokens_per_day ?? null, schedule: agent.schedule, ip_allowlist: agent.ip_allowlist, playbook: "" } : EMPTY);
  const taken = new Set(data.agents.filter((a) => a.status !== "revoked" && a.id !== agent?.id && a.desk).map((a) => a.desk));
  const times = ((form.schedule as { times?: string[] }).times ?? []).join(", ");
  const save = useAction(async () => agent
    ? unwrap(await api.PUT("/admin-api/outside/{agent_id}", { params: { path: { agent_id: agent.id } }, body: form }))
    : unwrap(await api.POST("/admin-api/outside", { body: form })), {
    invalidate: [["outside"]], done: (a) => { onClose(); navigate(`/team/outside/${a.id}`); },
  });
  const toggle = (list: string[], key: string) => (list.includes(key) ? list.filter((x) => x !== key) : [...list, key]);
  return (
    <Dialog open onClose={onClose} title={agent ? `Change ${agent.name}` : "Hire an outside agent"}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.busy} onClick={() => save.run(undefined)}>{agent ? "Save" : "Hire"}</Button></>}>
      <div className="max-h-[70vh] space-y-3 overflow-y-auto">
        {save.notice && <Alert tone={save.notice.tone}>{save.notice.text}</Alert>}
        <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Claude Scout" /></Field>
        <Field label="Its job" hint={<span className={form.job.length > 4000 ? "text-danger" : ""}>What it does for the company, in a few sentences — {form.job.length.toLocaleString()} / 4,000. Long instructions go in the playbook below.</span>}>
          <Textarea rows={3} value={form.job} onChange={(e) => setForm({ ...form, job: e.target.value })} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Department"><Input value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })} placeholder="e.g. Opportunity research" /></Field>
          <Field label="Desk (1–10)"><Select value={form.desk ?? ""} onChange={(e) => setForm({ ...form, desk: e.target.value ? Number(e.target.value) : null })}>
            <option value="">No desk</option>{Array.from({ length: data.desks }, (_, i) => i + 1).map((n) => <option key={n} value={n} disabled={taken.has(n)}>Desk {n}{taken.has(n) ? " (taken)" : ""}</option>)}</Select></Field>
        </div>
        <Field label="Kind"><Select value={form.kind} disabled={!!agent} onChange={(e) => setForm({ ...form, kind: e.target.value as Form["kind"], provider: e.target.value === "hosted" ? "anthropic" : "other" })}>
          <option value="connected">Connected — it brings its own model (Claude Code, Claude.ai, ChatGPT, Gemini CLI …)</option>
          <option value="hosted">Hosted — Control Tower runs it with your provider key</option></Select></Field>
        {form.kind === "connected" ? (
          <Field label="Which client" hint="A label for you, e.g. Claude Code or ChatGPT."><Input value={form.client_label} onChange={(e) => setForm({ ...form, client_label: e.target.value })} /></Field>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Provider"><Select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value as Form["provider"] })}>
              {PROVIDERS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}</Select></Field>
            <Field label="Model" hint={`e.g. ${PROVIDERS.find((p) => p.key === form.provider)?.example ?? ""}`}><Input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} /></Field>
            <Field label="Provider API key" hint={agent?.has_provider_key ? "Stored encrypted. Leave empty to keep it." : "Stored encrypted; never shown again."}>
              <Input type="password" autoComplete="off" value={form.provider_key} onChange={(e) => setForm({ ...form, provider_key: e.target.value })} /></Field>
            <Field label="Works at (your time zone)" hint="e.g. 07:00, 15:00 — empty: only when you start it"><Input value={times}
              onChange={(e) => setForm({ ...form, schedule: { ...form.schedule, times: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) } })} /></Field>
          </div>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Tool calls a day" hint="Empty: the company's rule"><Input type="number" min={1} value={form.calls_per_day ?? ""} onChange={(e) => setForm({ ...form, calls_per_day: e.target.value ? Number(e.target.value) : null })} /></Field>
          {form.kind === "hosted" && <Field label="AI tokens a day" hint="Empty: no daily limit"><Input type="number" min={1000} value={form.tokens_per_day ?? ""} onChange={(e) => setForm({ ...form, tokens_per_day: e.target.value ? Number(e.target.value) : null })} /></Field>}
          {form.kind === "connected" && <Field label="Only from these addresses" hint="Comma separated, e.g. 203.0.113.5 or 203.0.113.0/24; empty: anywhere"><Input value={(form.ip_allowlist ?? []).join(", ")}
            onChange={(e) => setForm({ ...form, ip_allowlist: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></Field>}
        </div>
        <Field label="Tools it may use"><div className="grid max-h-56 gap-1 overflow-y-auto rounded border border-border p-2 sm:grid-cols-2">
          {data.tools.map((t) => <label key={t.key} className="flex items-start gap-2 text-xs" title={t.description}>
            <input type="checkbox" checked={(form.tools ?? []).includes(t.key)} onChange={() => setForm({ ...form, tools: toggle(form.tools ?? [], t.key) })} /><span>{t.key.replace(/_/g, " ")}</span></label>)}
        </div></Field>
        <Field label="What it may propose (each waits for you in Clearance)"><div className="grid max-h-44 gap-1 overflow-y-auto rounded border border-border p-2">
          {data.actions.map((a) => <label key={a.key} className="flex items-start gap-2 text-xs">
            <input type="checkbox" checked={(form.actions ?? []).includes(a.key)} onChange={() => setForm({ ...form, actions: toggle(form.actions ?? [], a.key) })} /><span><b>{a.key}</b> — {a.description}</span></label>)}
        </div></Field>
        <Field label="Playbook (optional)" hint={<span className={(form.playbook ?? "").length > 20000 ? "text-danger" : ""}>Your full instructions — the long prompt: how it works, its rules, how it reports. {(form.playbook ?? "").length.toLocaleString()} / 20,000.{agent ? " Empty keeps the current playbook." : ""}</span>}>
          <Textarea rows={8} value={form.playbook ?? ""} onChange={(e) => setForm({ ...form, playbook: e.target.value })} /></Field>
        <p className="text-xs text-muted">Never offered, also not with clearance: admin changes, payments, settings, other agents' playbooks, deciding clearance, anyone's personal data.</p>
      </div>
    </Dialog>
  );
}

export function OutsideAgentPage({ owner }: { owner: boolean }) {
  const id = Number(useParams().id);
  const { data: overview } = useOverview();
  const { data, error } = useQuery({ queryKey: ["outside", id], queryFn: async () => unwrap(await api.GET("/admin-api/outside/{agent_id}", { params: { path: { agent_id: id } } })) });
  const [editing, setEditing] = useState(false);
  const [issued, setIssued] = useState<Schemas["OutsideCredentialOut"] | null>(null);
  const [message, setMessage] = useState("");
  const [playbook, setPlaybook] = useState<string | null>(null);
  const navigate = useNavigate();
  const path = { params: { path: { agent_id: id } } };
  const inv = { invalidate: [["outside"]] };
  const issue = useAction(async () => unwrap(await api.POST("/admin-api/outside/{agent_id}/credential", { ...path, body: {} })), { ...inv, done: (r) => { setIssued(r); } });
  const pause = useAction(async () => unwrap(await api.POST("/admin-api/outside/{agent_id}/pause", path)), { ...inv, done: () => "Paused." });
  const resume = useAction(async () => unwrap(await api.POST("/admin-api/outside/{agent_id}/resume", path)), { ...inv, done: () => "Resumed." });
  const revoke = useAction(async () => unwrap(await api.POST("/admin-api/outside/{agent_id}/revoke", path)), { ...inv, done: () => "Revoked: its credential and sign-ins stopped." });
  const remove = useAction(async () => { await api.DELETE("/admin-api/outside/{agent_id}", path); }, { ...inv, done: () => { navigate("/team/outside"); } });
  const send = useAction(async () => unwrap(await api.POST("/admin-api/outside/{agent_id}/messages", { ...path, body: { text: message } })), { ...inv, done: () => { setMessage(""); return "Sent: it reads it the next time it works."; } });
  const savePlaybook = useAction(async () => unwrap(await api.PUT("/admin-api/outside/{agent_id}/playbook", { ...path, body: { text: playbook ?? "", note: "" } })), { ...inv, done: () => "Playbook saved." });
  const runNow = useAction(async () => unwrap(await api.POST("/admin-api/outside/{agent_id}/run", path)), { ...inv, done: () => "Started: its report appears below when it is done." });
  const notices = [issue, pause, resume, revoke, remove, send, savePlaybook, runNow].map((a) => a.notice).filter(Boolean);
  const a = data?.agent;
  const expiresSoon = useMemo(() => a?.credential_expires_at && new Date(a.credential_expires_at).getTime() - Date.now() < 7 * 86400000, [a]);
  if (error) return <Alert>{errorMessage(error)}</Alert>;
  if (!data || !a) return null;
  return (
    <>
      <Link to="/team/outside" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg"><ArrowLeft className="h-3.5 w-3.5" />Outside agents</Link>
      <PageTitle title={a.name} description={a.job} />
      {notices.map((n, i) => n && <div key={i} className="mb-2"><Alert tone={n.tone}>{n.text}</Alert></div>)}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Status" className="lg:col-span-2" actions={owner && a.status !== "revoked" && <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => setEditing(true)}>Change</Button>
          {a.kind === "connected" && <Button size="sm" variant="primary" loading={issue.busy} onClick={() => { if (!a.credential_prefix || confirm("Issue a new credential? The current one stops at once.")) issue.run(undefined); }}>
            <KeyRound className="h-3.5 w-3.5" />{a.credential_prefix ? "Rotate credential" : "Issue credential"}</Button>}
          {a.kind === "hosted" && <Button size="sm" variant="primary" loading={runNow.busy} onClick={() => runNow.run(undefined)}><Play className="h-3.5 w-3.5" />Run now</Button>}
          {a.status === "paused" ? <Button size="sm" loading={resume.busy} onClick={() => resume.run(undefined)}><Play className="h-3.5 w-3.5" />Resume</Button>
            : <Button size="sm" loading={pause.busy} onClick={() => pause.run(undefined)}><Pause className="h-3.5 w-3.5" />Pause</Button>}
          <Button size="sm" variant="danger" loading={revoke.busy} onClick={() => { if (confirm(`Revoke ${a.name}? Its credential and every connection stop at once.`)) revoke.run(undefined); }}><ShieldOff className="h-3.5 w-3.5" />Revoke</Button>
        </div>}>
          <dl className="grid gap-2 text-[13px] sm:grid-cols-2">
            <div><dt className="text-xs text-muted">Status</dt><dd><Badge tone={STATUS[a.status]?.tone ?? "neutral"}>{STATUS[a.status]?.label ?? a.status}</Badge></dd></div>
            <div><dt className="text-xs text-muted">Kind</dt><dd>{a.kind === "hosted" ? `Hosted · ${a.provider} · ${a.model}${a.has_provider_key ? " · key stored" : ""}` : `Connected · ${a.client_label || "any MCP client"}`}</dd></div>
            <div><dt className="text-xs text-muted">Desk</dt><dd>{a.desk ?? "—"}</dd></div>
            <div><dt className="text-xs text-muted">Key</dt><dd><code>{a.key}</code></dd></div>
            {a.kind === "connected" && <div><dt className="text-xs text-muted">Credential</dt><dd>{a.credential_prefix ? <><code>{a.credential_prefix}…</code> issued {when(a.credential_issued_at ?? "")}
              {a.credential_expires_at && <span className={expiresSoon ? "text-warning" : ""}> · expires {when(a.credential_expires_at)}</span>}</> : "not issued yet"}</dd></div>}
            <div><dt className="text-xs text-muted">Today</dt><dd>{a.calls_today} calls{a.tokens_today ? ` · ${a.tokens_today.toLocaleString()} tokens` : ""}</dd></div>
            <div><dt className="text-xs text-muted">Last seen</dt><dd>{a.last_seen_at ? ago(a.last_seen_at) : "never"}</dd></div>
            <div><dt className="text-xs text-muted">Waiting for you</dt><dd><Link to="/clearance" className="text-primary hover:underline">{data.proposals_waiting} proposal(s)</Link></dd></div>
          </dl>
          <p className="mt-3 text-xs text-muted">Tools: {a.tools.join(", ") || "none"}. May propose: {a.actions.join(", ") || "nothing"}.</p>
          {owner && a.status === "revoked" && <div className="mt-3"><Button size="sm" variant="danger" loading={remove.busy} onClick={() => { if (confirm("Remove this agent and its call log?")) remove.run(undefined); }}><Trash2 className="h-3.5 w-3.5" />Remove</Button></div>}
        </Card>
        <Card title="Message it">
          <Textarea rows={4} value={message} onChange={(e) => setMessage(e.target.value)} placeholder="It reads this the next time it works (my_messages)." />
          <Button className="mt-2" size="sm" variant="primary" disabled={!owner || !message.trim()} loading={send.busy} onClick={() => send.run(undefined)}>Send</Button>
          <ul className="mt-3 space-y-2 text-xs">{data.messages.slice(0, 6).map((m) => <li key={m.id} className="border-t border-border pt-2"><span className="text-muted">{m.sender} · {ago(m.created_at)}{m.read_at ? " · read" : " · not read yet"}</span><p>{m.text}</p></li>)}</ul>
        </Card>
      </div>
      <Card className="mt-4" title="Your playbook for it" actions={owner && <Button size="sm" variant="primary" loading={savePlaybook.busy} disabled={playbook === null} onClick={() => savePlaybook.run(undefined)}>Save</Button>}>
        <Textarea rows={4} value={playbook ?? data.playbook} onChange={(e) => setPlaybook(e.target.value)} placeholder="Standing instructions: what to focus on, what to avoid, how to report." />
      </Card>
      <Card className="mt-4" title="Reports" padded={false}>
        {data.runs.length === 0 ? <div className="p-4"><Empty>No reports yet.</Empty></div> : (
          <ul className="divide-y divide-border">{data.runs.map((r) => <li key={r.id} className="px-4 py-3 text-[13px]"><p className="text-xs text-muted">{when(r.started_at)} · {r.trigger} · {r.status}{r.error ? ` · ${r.error}` : ""}</p>
            <p className="mt-1 whitespace-pre-wrap">{r.summary}</p></li>)}</ul>)}
      </Card>
      <Card className="mt-4" title="Every call" padded={false}>
        {data.calls.length === 0 ? <div className="p-4"><Empty>No calls yet.</Empty></div> : (
          <Table head={["When", "Via", "What", "Result", "From"]}>
            {data.calls.map((c, i) => <tr key={i} className="border-b border-border last:border-0 align-top">
              <Td className="whitespace-nowrap">{ago(c.at)}</Td><Td>{c.via}</Td>
              <Td><span className="font-medium">{c.tool || c.method}</span>{c.args && <p className="max-w-md truncate text-xs text-muted" title={c.args}>{c.args}</p>}</Td>
              <Td>{c.ok ? <Badge tone="success">ok</Badge> : <Badge tone="danger">{c.error || "refused"}</Badge>}{c.tokens ? <span className="ml-1 text-xs text-muted">{c.tokens} tokens</span> : null}</Td>
              <Td className="text-xs text-muted">{c.ip}</Td></tr>)}
          </Table>)}
      </Card>
      {editing && overview && <AgentForm data={overview} agent={a} onClose={() => setEditing(false)} />}
      {issued && <CredentialDialog issued={issued} onClose={() => setIssued(null)} />}
    </>
  );
}

function CredentialDialog({ issued, onClose }: { issued: Schemas["OutsideCredentialOut"]; onClose: () => void }) {
  const copy = (text: string) => { void navigator.clipboard?.writeText(text); };
  const rows: [string, string][] = [["Credential", issued.credential], ["Claude Code", issued.setup.claude_code], ["Gemini CLI (settings.json)", issued.setup.gemini_cli],
    ["Claude.ai / ChatGPT connector", issued.setup.connectors], ["Plain HTTPS", issued.setup.http]];
  return (
    <Dialog open onClose={onClose} title={`Credential for ${issued.agent.name}`} footer={<Button variant="primary" onClick={onClose}>I have saved it</Button>}>
      <Alert tone="warning">Shown this once. Copy it into the agent's set-up now; Control Tower keeps only a fingerprint and cannot show it again.</Alert>
      <div className="mt-3 space-y-3">{rows.map(([label, text]) => (
        <div key={label}><p className="mb-1 text-xs font-medium text-muted">{label}</p>
          <div className="flex items-start gap-2"><pre className="flex-1 overflow-x-auto whitespace-pre-wrap break-all rounded bg-raised p-2 text-[11px]">{text}</pre>
            <Button size="sm" onClick={() => copy(text)} aria-label={`Copy ${label}`}><Copy className="h-3.5 w-3.5" /></Button></div></div>))}
      </div>
    </Dialog>
  );
}
