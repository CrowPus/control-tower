/* Admin → Product: the Product Analyst's ideas with what they changed, the QA Runner's
   checks (latest run, what fails, history, rules and test accounts) and the Engineer on Duty's incidents. */
import { useQuery } from "@tanstack/react-query";
import { Activity, CheckCircle2, FlaskConical, Siren, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, errorMessage, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { NumberSetting } from "../team/Team";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageTitle, Select, Stat, Table, Td, Textarea, ago, cn } from "../ui";

type Idea = Schemas["IdeaOut"];
type Incident = Schemas["IncidentOut"];
type QaRules = Schemas["QaRules"];

const TABS = [["", "Ideas", FlaskConical], ["checks", "Checks", Activity], ["incidents", "Incidents", Siren]] as const;
const SEVERITY_TONE = { critical: "danger", major: "warning", minor: "neutral" } as const;
const VERDICT: Record<string, string> = { better: "Better", worse: "Worse", no_change: "No change", too_little_data: "Too little data" };

export function ProductArea({ owner }: { owner: boolean }) {
  return (
    <>
      <PageTitle title="Product" description="The Product Analyst proposes changes with the number they should move and measures them after; the QA Runner walks through the live product; the Engineer on Duty keeps incidents with cause and fix." />
      <nav className="mb-4 flex flex-wrap gap-1 border-b border-border">
        {TABS.map(([path, label, Icon]) => (
          <NavLink key={path} to={`/product${path ? `/${path}` : ""}`} end={!path}
            className={({ isActive }) => cn("-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-[13px]", isActive ? "border-primary font-medium text-fg" : "border-transparent text-muted hover:text-fg")}>
            <Icon className="h-4 w-4" aria-hidden />{label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<IdeasTab owner={owner} />} />
        <Route path="checks" element={<ChecksTab owner={owner} />} />
        <Route path="incidents" element={<IncidentsTab />} />
        <Route path="*" element={<Navigate to="/product" replace />} />
      </Routes>
    </>
  );
}

// --- ideas --------------------------------------------------------------------------------------------------
function IdeasTab({ owner }: { owner: boolean }) {
  const { data, error } = useQuery({ queryKey: ["product", "ideas"], queryFn: async () => unwrap(await api.GET("/admin-api/product/ideas")) });
  const step = useAction(async ({ id, to }: { id: number; to: "shipped" | "dropped" }) => unwrap(await api.POST("/admin-api/product/ideas/{idea_id}/{step}", { params: { path: { idea_id: id, step: to } } })),
    { invalidate: [["product", "ideas"]] });
  if (error) return <Alert>{errorMessage(error)}</Alert>;
  if (!data) return null;
  const count = (s: string) => data.filter((i) => i.status === s).length;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Accepted, to build" value={count("accepted")} />
        <Stat label="Shipped, being measured" value={count("shipped")} />
        <Stat label="Measured" value={count("measured")} hint={`${data.filter((i) => i.verdict === "better").length} made it better`} />
        <Stat label="Dropped" value={count("dropped")} />
      </div>
      {step.notice?.tone === "danger" && <Alert>{step.notice.text}</Alert>}
      {data.length === 0 ? <Card><Empty>No idea yet. The Product Analyst proposes them in Clearance; accepted ones appear here.</Empty></Card> : data.map((idea) => (
        <IdeaCard key={idea.id} idea={idea} owner={owner} busy={step.busy} onStep={(to) => step.run({ id: idea.id, to })} />
      ))}
    </div>
  );
}

function fmt(value: number | null | undefined): string {
  return value == null ? "—" : Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function IdeaCard({ idea, owner, busy, onStep }: { idea: Idea; owner: boolean; busy: boolean; onStep: (to: "shipped" | "dropped") => void }) {
  const tone = idea.status === "measured" ? (idea.verdict === "better" ? "success" : idea.verdict === "worse" ? "danger" : "neutral") : idea.status === "shipped" ? "info" : idea.status === "dropped" ? "neutral" : "warning";
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-[13px] font-semibold text-fg">{idea.title}</h3>
          <p className="text-xs text-muted">Should move <span className="text-fg">{idea.metric_label}</span> {idea.expect === "up" ? "up" : "down"} · measured over {idea.measure_days} days · {idea.created_at}</p>
        </div>
        <Badge tone={tone}>{idea.status === "measured" ? VERDICT[idea.verdict] ?? idea.verdict : idea.status === "accepted" ? "To build" : idea.status}</Badge>
      </div>
      {idea.problem && <p className="mt-2 text-[13px] text-fg"><span className="text-muted">Problem: </span>{idea.problem}</p>}
      <p className="mt-1 text-[13px] text-fg"><span className="text-muted">Change: </span>{idea.idea}</p>
      {idea.evidence.length > 0 && <ul className="mt-1 list-disc pl-5 text-xs text-muted">{idea.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>}
      <div className="mt-2 flex flex-wrap gap-4 text-xs">
        <span><span className="text-muted">Before: </span>{fmt(idea.baseline)}</span>
        {idea.status === "shipped" && <span className="text-muted">{idea.ready_to_measure ? `After: ${fmt(idea.after)} (waiting for the analyst's verdict)` : `Measured from ${idea.measure_from}`}</span>}
        {idea.status === "measured" && <span><span className="text-muted">After: </span>{fmt(idea.result)}</span>}
        {idea.shipped_at && <span className="text-muted">Shipped {idea.shipped_at}</span>}
      </div>
      {idea.result_note && <p className="mt-1 text-xs text-fg">{idea.result_note}</p>}
      {owner && (idea.status === "accepted" || idea.status === "shipped") && (
        <div className="mt-3 flex gap-2">
          {idea.status === "accepted" && <Button size="sm" variant="primary" loading={busy} onClick={() => onStep("shipped")}>It is live now</Button>}
          <Button size="sm" variant="ghost" loading={busy} onClick={() => onStep("dropped")}>Drop</Button>
        </div>
      )}
    </Card>
  );
}

// --- checks -------------------------------------------------------------------------------------------------
const GROUP_LABEL: Record<string, string> = { pages: "Pages", security: "Security" };

function ChecksTab({ owner }: { owner: boolean }) {
  const { data, error } = useQuery({ queryKey: ["product", "checks"], queryFn: async () => unwrap(await api.GET("/admin-api/product/checks")) });
  const run = useAction(async () => unwrap(await api.POST("/admin-api/product/checks/run")), { invalidate: [["product", "checks"]] });
  if (error) return <Alert>{errorMessage(error)}</Alert>;
  if (!data) return null;
  const groups = Array.from(new Set(data.checks.map((c) => c.group ?? "pages")));
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Last run" value={data.latest ? ago(data.latest.started_at) : "never"} hint={data.latest ? `${data.latest.trigger}, ${data.latest.total} checks` : undefined} />
        <Stat label="Failing now" value={data.failing_now.length} tone={data.failing_now.length ? "danger" : "success"} />
        <Stat label="Flaky" value={data.flaky.length} tone={data.flaky.length ? "warning" : undefined} hint="Failed now and then in recent runs" />
        <Stat label="Slowest" value={data.latest ? `${data.latest.slowest_ms} ms` : "—"} />
      </div>
      <Card title="Latest run" actions={<Button size="sm" variant="primary" loading={run.busy} onClick={() => run.run(undefined)}>Run now</Button>}>
        {run.notice?.tone === "danger" && <div className="mb-3"><Alert>{run.notice.text}</Alert></div>}
        {data.failing_now.length > 0 && (
          <div className="mb-3"><Alert>{data.failing_now.map((f) => `${f.name}: failing ${f.failing_runs} run(s) in a row${f.detail ? ` (${f.detail})` : ""}`).join(" · ")}</Alert></div>
        )}
        {data.checks.length === 0 ? <Empty>No run yet. Press "Run now", or set how often the checks run by themselves below.</Empty> : groups.map((g) => (
          <div key={g} className="mb-3">
            <h3 className="mb-1 text-xs font-semibold text-fg">{GROUP_LABEL[g] ?? g}</h3>
            <ul className="space-y-0.5 text-xs">
              {data.checks.filter((c) => (c.group ?? "pages") === g).map((c) => (
                <li key={c.name} className="flex flex-wrap items-center gap-2">
                  {c.ok ? <CheckCircle2 className="h-3.5 w-3.5 text-success" aria-label="passed" /> : <XCircle className="h-3.5 w-3.5 text-danger" aria-label="failed" />}
                  <a href={c.url} target="_blank" rel="noreferrer" className="text-fg hover:underline">{c.name}</a>
                  {c.ms > 0 && <span className={cn("tabular-nums", c.slow ? "text-warning" : "text-muted")}>{c.ms} ms{c.slow ? " (slow)" : ""}</span>}
                  {c.detail && <span className={c.ok ? "text-muted" : "text-danger"}>{c.detail}</span>}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </Card>
      <Card title="Recent runs" padded={false}>
        {data.runs.length === 0 ? <div className="p-4"><Empty>None yet.</Empty></div> : (
          <Table head={["When", "How", "Checks", "Failed", "Slowest"]}>
            {data.runs.slice(0, 15).map((r) => (
              <tr key={r.id} className="border-b border-border last:border-0">
                <Td>{ago(r.started_at)}</Td><Td>{r.trigger}</Td><Td className="tabular-nums">{r.total}</Td>
                <Td className={cn("tabular-nums", r.failed && "text-danger")}>{r.failed}</Td><Td className="tabular-nums">{r.slowest_ms} ms</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <CheckSettings owner={owner} rules={data.rules} pages={data.pages} />
    </div>
  );
}

type QaPage = Schemas["QaPage"];

function CheckSettings({ owner, rules, pages }: { owner: boolean; rules: QaRules; pages: QaPage[] }) {
  const [draft, setDraft] = useState<QaRules>(rules);
  const [list, setList] = useState<QaPage[]>(pages);
  useEffect(() => { setDraft(rules); setList(pages); }, [rules, pages]);
  const save = useAction(async () => unwrap(await api.PUT("/admin-api/product/checks/settings", { body: {
    rules: draft, pages: list.filter((p) => p.name.trim() && p.url.trim()),
  } })), { invalidate: [["product", "checks"]], done: () => "Saved." });
  const setPage = (i: number, change: Partial<QaPage>) => setList(list.map((p, j) => (j === i ? { ...p, ...change } : p)));
  const set = (key: keyof QaRules) => (v: number | null) => setDraft({ ...draft, [key]: v });
  return (
    <Card title="Rules and pages" actions={owner && <Button variant="primary" size="sm" loading={save.busy} onClick={() => save.run(undefined)}>Save</Button>}>
      {save.notice && <div className="mb-3"><Alert tone={save.notice.tone}>{save.notice.text}</Alert></div>}
      <div className="grid gap-3 sm:grid-cols-2">
        <NumberSetting label="Run the checks by themselves every (minutes)" hint="Empty: only when the QA Runner runs or you press Run now. No AI cost." min={5} value={draft.every_minutes} disabled={!owner} onChange={set("every_minutes")} />
        <NumberSetting label="A check counts as broken after (failed runs in a row)" hint="Empty: after the first failed run." min={1} value={draft.fail_after} disabled={!owner} onChange={set("fail_after")} />
        <NumberSetting label="Slow above (ms)" hint="Empty: nothing is marked slow." min={100} value={draft.slow_ms} disabled={!owner} onChange={set("slow_ms")} />
        <NumberSetting label="Warn when a certificate ends within (days)" hint="Empty: only an ended certificate fails." min={1} value={draft.tls_warn_days} disabled={!owner} onChange={set("tls_warn_days")} />
      </div>
      <h3 className="mb-1 mt-4 text-xs font-semibold text-fg">Your pages</h3>
      <p className="mb-2 text-xs text-muted">The addresses the checks open: your website, your app, your shop. A page passes when it answers and, if you give one, contains your text. The admin itself is always checked. Nothing is changed.</p>
      <div className="space-y-2">
        {list.map((p, i) => (
          <div key={i} className="grid gap-2 sm:grid-cols-[1fr_2fr_1fr_auto]">
            <Input aria-label="Name" placeholder="Name" maxLength={80} disabled={!owner} value={p.name} onChange={(e) => setPage(i, { name: e.target.value })} />
            <Input aria-label="Address" placeholder="https://…" type="url" disabled={!owner} value={p.url} onChange={(e) => setPage(i, { url: e.target.value })} />
            <Input aria-label="Must contain" placeholder="Must contain (optional)" maxLength={200} disabled={!owner} value={p.contains ?? ""} onChange={(e) => setPage(i, { contains: e.target.value })} />
            {owner && <Button size="sm" variant="ghost" onClick={() => setList(list.filter((_, j) => j !== i))}>Remove</Button>}
          </div>
        ))}
        {owner && <Button size="sm" onClick={() => setList([...list, { name: "", url: "", contains: "" }])}>Add a page</Button>}
      </div>
    </Card>
  );
}

// --- incidents ----------------------------------------------------------------------------------------------
function IncidentsTab() {
  const { data, error } = useQuery({ queryKey: ["product", "incidents"], queryFn: async () => unwrap(await api.GET("/admin-api/product/incidents")), refetchInterval: 60_000 });
  const [form, setForm] = useState({ title: "", severity: "major" as Incident["severity"], impact: "" });
  const add = useAction(async () => unwrap(await api.POST("/admin-api/product/incidents", { body: { title: form.title, severity: form.severity as "critical" | "major" | "minor", impact: form.impact, note: "" } })),
    { invalidate: [["product", "incidents"]], done: () => { setForm({ title: "", severity: "major", impact: "" }); return "Recorded."; } });
  if (error) return <Alert>{errorMessage(error)}</Alert>;
  if (!data) return null;
  return (
    <div className="space-y-4">
      {data.open.length === 0 ? <Alert tone="success">No open incident.</Alert> : data.open.map((i) => <IncidentCard key={i.id} incident={i} />)}
      <Card title="Write down an incident yourself">
        {add.notice && <div className="mb-3"><Alert tone={add.notice.tone}>{add.notice.text}</Alert></div>}
        <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
          <Field label="What is broken"><Input value={form.title} maxLength={200} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          <Field label="Severity">
            <Select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}>
              <option value="critical">Critical — people cannot use the product</option><option value="major">Major — a feature is broken</option><option value="minor">Minor</option>
            </Select>
          </Field>
        </div>
        <div className="mt-3"><Field label="Who is affected, since when"><Textarea rows={2} value={form.impact} onChange={(e) => setForm({ ...form, impact: e.target.value })} /></Field></div>
        <Button className="mt-3" size="sm" variant="primary" disabled={form.title.trim().length < 3} loading={add.busy} onClick={() => add.run(undefined)}>Record</Button>
      </Card>
      <Card title="Recently resolved" padded={false}>
        {data.recently_resolved.length === 0 ? <div className="p-4"><Empty>None yet.</Empty></div> : (
          <Table head={["Incident", "Severity", "Started", "Resolved"]}>
            {data.recently_resolved.map((i) => (
              <tr key={i.id} className="border-b border-border last:border-0">
                <Td><span className="text-fg">{i.title}</span>{i.cause && <p className="text-xs text-muted">Cause: {i.cause}</p>}</Td>
                <Td><Badge tone={SEVERITY_TONE[i.severity as keyof typeof SEVERITY_TONE] ?? "neutral"}>{i.severity}</Badge></Td>
                <Td>{ago(i.started_at)}</Td><Td>{i.resolved_at ? ago(i.resolved_at) : "—"}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}

function IncidentCard({ incident }: { incident: Incident }) {
  const [note, setNote] = useState("");
  const change = useAction(async (body: Schemas["IncidentChangeIn"]) => unwrap(await api.POST("/admin-api/product/incidents/{incident_id}", { params: { path: { incident_id: incident.id } }, body })),
    { invalidate: [["product", "incidents"]], done: () => { setNote(""); return undefined; } });
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-[13px] font-semibold text-fg">{incident.title}</h3>
          <p className="text-xs text-muted">Since {ago(incident.started_at)} · by {incident.opened_by} · {incident.status}</p>
        </div>
        <Badge tone={SEVERITY_TONE[incident.severity as keyof typeof SEVERITY_TONE] ?? "neutral"}>{incident.severity}</Badge>
      </div>
      {incident.impact && <p className="mt-2 text-[13px] text-fg"><span className="text-muted">Impact: </span>{incident.impact}</p>}
      {incident.cause && <p className="mt-1 text-[13px] text-fg"><span className="text-muted">Likely cause: </span>{incident.cause}</p>}
      {incident.fix && <p className="mt-1 text-[13px] text-fg"><span className="text-muted">Fix: </span>{incident.fix}</p>}
      {incident.notes.length > 0 && (
        <ul className="mt-2 space-y-0.5 border-l border-border pl-3 text-xs">
          {incident.notes.map((n, i) => <li key={i}><span className="text-muted">{ago(String(n.at))} · {String(n.by)}: </span>{String(n.text)}</li>)}
        </ul>
      )}
      {change.notice?.tone === "danger" && <div className="mt-2"><Alert>{change.notice.text}</Alert></div>}
      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        <Input placeholder="Add a note" value={note} onChange={(e) => setNote(e.target.value)} />
        <Button size="sm" disabled={!note.trim()} loading={change.busy} onClick={() => change.run({ note })}>Add</Button>
        {incident.status === "open" && <Button size="sm" loading={change.busy} onClick={() => change.run({ note, status: "monitoring" })}>Monitoring</Button>}
        <Button size="sm" variant="primary" loading={change.busy} onClick={() => change.run({ note, status: "resolved" })}>Resolved</Button>
      </div>
    </Card>
  );
}
