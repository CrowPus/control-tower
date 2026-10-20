/* Admin → Trust: what Trust & Compliance watches (the legal deadlines, what waits too long) and the owner's rules for it.
   AgentNote shows an agent's note on the page where the owner decides. */
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { api, errorMessage, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { NumberSetting } from "../team/Team";
import { Alert, Badge, Button, Card, Empty, PageTitle, Stat, Table, Td, ago, cn, type Tone } from "../ui";

type Note = Schemas["ReviewNote"];

const RECOMMENDATION: Record<string, [string, Tone]> = {
  approve: ["Recommends: approve", "success"], ask: ["Recommends: ask first", "warning"], reject: ["Recommends: reject", "danger"],
  act: ["Recommends: act", "danger"], dismiss: ["Recommends: dismiss", "neutral"],
};
const AGENT: Record<string, string> = { trust_compliance: "Trust & Compliance" };

function Checks({ items }: { items: Schemas["Finding"][] }) {
  if (items.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1 text-xs">
      {items.map((f, i) => (
        <li key={i} className="flex gap-2">
          {f.ok ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" aria-label="fine" /> : <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" aria-label="problem" />}
          <span><span className="font-medium text-fg">{f.check}</span>{f.detail && <span className="text-muted"> — {f.detail}</span>}
            {f.source && <> · <a href={f.source} target="_blank" rel="noreferrer noopener" className="text-primary hover:underline">source</a></>}</span>
        </li>
      ))}
    </ul>
  );
}

function NoteBody({ note }: { note: Note }) {
  const [label, tone] = RECOMMENDATION[note.recommendation] ?? [note.recommendation, "neutral" as Tone];
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={tone}>{label}</Badge>
        <span className="text-xs text-muted">{AGENT[note.agent] ?? note.agent} · {ago(note.written_at)}</span>
        {note.outdated && <Badge tone="warning">Outdated: changed since — the agent checks again</Badge>}
      </div>
      <p className="mt-2 whitespace-pre-line text-[13px] text-fg">{note.summary}</p>
      <Checks items={note.findings} />
    </div>
  );
}

/** The agent's note on one thing, for the page where the owner decides. Nothing shows when there is none. */
export function AgentNote({ kind, id }: { kind: string; id: number }) {
  const { data } = useQuery({ queryKey: ["note", kind, id], queryFn: async () => unwrap(await api.GET("/admin-api/notes/{kind}/{subject_id}", { params: { path: { kind, subject_id: id } } })) });
  if (!data) return null;
  return <Card title={`${AGENT[data.agent] ?? data.agent}'s note`} className="mb-4"><NoteBody note={data} /><p className="mt-2 text-[11px] text-muted">A recommendation from an agent, not a decision. You decide.</p></Card>;
}

function RulesCard<T extends Record<string, number | null>>({ title, owner, rules, fields, save }: {
  title: string; owner: boolean; rules: T; fields: [keyof T & string, string, string, number][]; save: (body: T) => Promise<unknown>;
}) {
  const [draft, setDraft] = useState<T>(rules);
  useEffect(() => setDraft(rules), [rules]);
  const run = useAction(save, { done: () => "Saved." });
  const dirty = JSON.stringify(draft) !== JSON.stringify(rules);
  return (
    <Card title={title} actions={owner && <Button size="sm" variant="primary" disabled={!dirty} loading={run.busy} onClick={() => run.run(draft)}>Save</Button>}>
      {run.notice && <div className="mb-3"><Alert tone={run.notice.tone}>{run.notice.text}</Alert></div>}
      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map(([key, label, hint, min]) => (
          <NumberSetting key={key} label={label} hint={hint} min={min} value={draft[key]} disabled={!owner} onChange={(v) => setDraft({ ...draft, [key]: v })} />
        ))}
      </div>
    </Card>
  );
}

// --- Trust ---------------------------------------------------------------------------------------------------
export function TrustPage({ owner }: { owner: boolean }) {
  const { data, error, refetch } = useQuery({ queryKey: ["trust"], queryFn: async () => unwrap(await api.GET("/admin-api/trust")) });
  if (error) return <Alert>{errorMessage(error)}</Alert>;
  if (!data) return null;
  const late = data.deadlines.deletion_runs.filter((r) => !r.ok).length;
  return (
    <>
      <PageTitle title="Trust" description="The data-deletion runs and the proposals waiting for your clearance longer than your rule allows. Agents recommend; you decide." />
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <Stat label="Deletion runs late or failing" value={late} tone={late ? "warning" : "success"} />
        <Stat label="Waiting too long" value={data.overdue.length} tone={data.overdue.length ? "warning" : "success"} />
      </div>
      {data.overdue.length > 0 && <div className="mb-4"><Alert tone="warning">Waiting longer than your rules allow: {data.overdue.map((o) => `${String(o.what)} ${String(o.name)} (${String(o.days)} days)`).join(" · ")}</Alert></div>}
      <div className="space-y-4">
        <Card title="Legal deadlines">
          <h3 className="mb-1 text-xs font-semibold text-fg">Data deletion runs</h3>
          {data.deadlines.deletion_runs.length === 0 ? <Empty>No deletion run is scheduled.</Empty> : (
            <ul className="mb-3 space-y-0.5 text-xs">{data.deadlines.deletion_runs.map((r) => (
              <li key={String(r.name)} className="flex items-center gap-2">{r.ok ? <CheckCircle2 className="h-3.5 w-3.5 text-success" aria-label="on time" /> : <XCircle className="h-3.5 w-3.5 text-danger" aria-label="late or failing" />}{String(r.name)} · last run {r.last_run ? ago(String(r.last_run)) : "never"}{r.last_error ? ` · ${String(r.last_error)}` : ""}</li>
            ))}</ul>
          )}
        </Card>
        <RulesCard title="Rules" owner={owner} rules={data.rules} save={async (body) => { const r = unwrap(await api.PUT("/admin-api/trust/rules", { body })); await refetch(); return r; }}
          fields={[["wait_alert_days", "Call out a proposal waiting for your clearance after (days)", "Empty: nothing is called out.", 1]]} />
      </div>
    </>
  );
}
