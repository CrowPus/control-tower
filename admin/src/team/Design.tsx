/* The agent template: making a new agent (blank or from a starter template), changing an agent's design, and taking
   it off the team. The server checks every design against the rules on save; this form only helps fill it in. */
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, FilePlus2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, Card, Dialog, Field, Input, PageTitle, Select, Textarea } from "../ui";

type Design = Schemas["Design"];
type Catalogue = Schemas["TeamCatalogueOut"];
type Detail = Schemas["TeamAgentDetailOut"];

const BLANK: Design = { name: "", department: "", job: "", guide: "", tools: [], actions: [], subscribes: [], emits: [], kpi: "", max_rounds: 0 };

export function useCatalogue() {
  return useQuery({ queryKey: ["team-catalogue"], queryFn: async () => unwrap(await api.GET("/admin-api/team/catalogue")), staleTime: 60_000 });
}

function Words({ label, hint, value, onChange, disabled }: { label: string; hint: string; value: string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  const [text, setText] = useState(value.join(", "));
  useEffect(() => setText(value.join(", ")), [value]);
  return (
    <Field label={label} hint={hint}>
      <Input value={text} disabled={disabled} placeholder="none" onChange={(e) => setText(e.target.value)}
        onBlur={() => onChange(text.split(/[\s,]+/).map((w) => w.trim()).filter(Boolean))} />
    </Field>
  );
}

function Ticks({ items, chosen, onChange, disabled }: { items: { key: string; text: string; badge?: string }[]; chosen: string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  return (
    <fieldset disabled={disabled} className="grid gap-1.5 sm:grid-cols-2">
      {items.map((item) => (
        <label key={item.key} className="flex items-start gap-2 text-[13px]">
          <input type="checkbox" className="mt-0.5" checked={chosen.includes(item.key)}
            onChange={(e) => onChange(e.target.checked ? [...chosen, item.key] : chosen.filter((k) => k !== item.key))} />
          <span><span className="font-medium text-fg">{item.key}</span>{item.badge && <> <Badge tone="warning">{item.badge}</Badge></>}<span className="block text-xs text-muted">{item.text}</span></span>
        </label>
      ))}
    </fieldset>
  );
}

/** Every field of the agent template. */
export function DesignForm({ value, onChange, catalogue, disabled }: { value: Design; onChange: (v: Design) => void; catalogue: Catalogue; disabled?: boolean }) {
  const set = (part: Partial<Design>) => onChange({ ...value, ...part });
  const tools = value.tools ?? [];
  const actions = value.actions ?? [];
  const jobShort = (value.job ?? "").trim().length < catalogue.min_job;
  return (
    <div className="grid gap-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name"><Input value={value.name} maxLength={60} disabled={disabled} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field label="Department" hint="Agents are grouped by it on the Team page and in the office.">
          <Input value={value.department} maxLength={40} list="ct-departments" disabled={disabled} onChange={(e) => set({ department: e.target.value })} />
          <datalist id="ct-departments">{catalogue.departments.map((d) => <option key={d} value={d} />)}</datalist>
        </Field>
      </div>
      <Field label="Job" hint={`What it is for, in a sentence or two (at least ${catalogue.min_job} characters).`}>
        <Textarea rows={2} value={value.job} maxLength={2000} disabled={disabled} onChange={(e) => set({ job: e.target.value })} />
        {jobShort && value.job && <p className="mt-1 text-xs text-warning">A little more, please.</p>}
      </Field>
      <Field label="How it works" hint="Its method, step by step. Your playbook (on its page) adds what you want right now.">
        <Textarea rows={6} value={value.guide ?? ""} maxLength={6000} disabled={disabled} onChange={(e) => set({ guide: e.target.value })} />
      </Field>
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Tools it may be given</h3>
        <p className="mb-2 text-xs text-subtle">Every agent also has: {catalogue.common_tools.map((t) => t.key).join(", ")}. You tick which of these it really gets in its settings.</p>
        <Ticks items={catalogue.tools.map((t) => ({ key: t.key, text: t.description }))} chosen={tools} disabled={disabled} onChange={(v) => set({ tools: v })} />
      </div>
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">What it may propose</h3>
        <p className="mb-2 text-xs text-subtle">Nothing happens until you clear it. "Always you" actions can never run alone.</p>
        <Ticks items={catalogue.actions.map((a) => ({ key: a.key, text: a.label, badge: a.ceiling === "L3" ? "always you" : undefined }))} chosen={actions} disabled={disabled} onChange={(v) => set({ actions: v })} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Words label="Woken by these events" hint="Names like marketing.brief, comma-separated." value={value.subscribes ?? []} disabled={disabled} onChange={(v) => set({ subscribes: v })} />
        <Words label="Events it may send" hint="Handoffs to other agents." value={value.emits ?? []} disabled={disabled} onChange={(v) => set({ emits: v })} />
        <Field label="The number you judge it by">
          <Select value={value.kpi ?? ""} disabled={disabled} onChange={(e) => set({ kpi: e.target.value })}>
            <option value="">None</option>
            {catalogue.kpis.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
          </Select>
        </Field>
        <Field label="Rounds of tool use per run" hint={`0: the team's default. At most ${catalogue.max_rounds}.`}>
          <Input type="number" min={0} max={catalogue.max_rounds} value={value.max_rounds ?? 0} disabled={disabled} onChange={(e) => set({ max_rounds: Number(e.target.value) || 0 })} />
        </Field>
      </div>
    </div>
  );
}

export function NewAgentPage() {
  const navigate = useNavigate();
  const { data: catalogue, error } = useCatalogue();
  const [template, setTemplate] = useState<string | null>(null);
  const [design, setDesign] = useState<Design>(BLANK);
  const [playbook, setPlaybook] = useState("");
  const create = useAction(async () => unwrap(await api.POST("/admin-api/team/agents", { body: { template: template ?? "", design, playbook } })),
    { invalidate: [["team"]], done: (made) => { navigate(`/team/${made.agent.key}`); } });
  if (error) return <Alert>{error.message}</Alert>;
  if (!catalogue) return null;
  const choose = (key: string) => {
    const t = catalogue.templates.find((x) => x.key === key);
    setTemplate(key);
    setDesign(t ? t.design : BLANK);
    setPlaybook(t?.playbook ?? "");
  };
  return (
    <>
      <Link to="/team" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg"><ArrowLeft className="h-3.5 w-3.5" aria-hidden />Team</Link>
      <PageTitle title="New agent" description="Start blank or from a starter template, and make it yours. It starts switched off; you give it its tools, budget and schedule on its page." />
      {template === null ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <button type="button" onClick={() => choose("")} className="rounded-[var(--radius-card)] border border-dashed border-border bg-surface p-4 text-left hover:border-primary">
            <FilePlus2 className="mb-2 h-5 w-5 text-primary" aria-hidden />
            <p className="text-[13px] font-semibold text-fg">Blank</p>
            <p className="mt-1 text-xs text-muted">Your own agent from scratch.</p>
          </button>
          {catalogue.templates.map((t) => (
            <button key={t.key} type="button" onClick={() => choose(t.key)} className="rounded-[var(--radius-card)] border border-border bg-surface p-4 text-left hover:border-primary">
              <div className="flex items-center gap-2"><p className="text-[13px] font-semibold text-fg">{t.name}</p><Badge>{t.department}</Badge></div>
              <p className="mt-1 text-xs text-muted">{t.design.job}</p>
            </button>
          ))}
        </div>
      ) : (
        <Card title={template ? `From “${catalogue.templates.find((t) => t.key === template)?.name}”` : "Blank agent"}
          actions={<Button size="sm" onClick={() => setTemplate(null)}>Choose another</Button>}>
          {create.notice && <div className="mb-3"><Alert tone={create.notice.tone}>{create.notice.text}</Alert></div>}
          <DesignForm value={design} onChange={setDesign} catalogue={catalogue} />
          <div className="mt-3">
            <Field label="Your first playbook (optional)" hint="What you want from it right now. You can change it any time on its page.">
              <Textarea rows={3} value={playbook} maxLength={20000} onChange={(e) => setPlaybook(e.target.value)} />
            </Field>
          </div>
          <div className="mt-4 flex justify-end">
            <Button variant="primary" loading={create.busy} disabled={!design.name.trim() || !design.department.trim()} onClick={() => create.run(undefined)}>Make the agent</Button>
          </div>
        </Card>
      )}
    </>
  );
}

/** On an agent of the owner's: its design, editable, and taking it off the team. */
export function DesignCard({ owner, detail, keyName }: { owner: boolean; detail: Detail; keyName: string }) {
  const navigate = useNavigate();
  const { data: catalogue } = useCatalogue();
  const saved = detail.design;
  const [draft, setDraft] = useState<Design | null | undefined>(saved);
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  useEffect(() => setDraft(saved), [saved]);
  const save = useAction(async (body: Design) => unwrap(await api.PUT("/admin-api/team/agents/{key}/design", { params: { path: { key: keyName } }, body })),
    { invalidate: [["team"], ["team-agent", keyName]], done: () => "Saved. Settings it no longer allows were removed." });
  const archive = useAction(async () => unwrap(await api.DELETE("/admin-api/team/agents/{key}", { params: { path: { key: keyName } } })),
    { invalidate: [["team"]], done: () => { navigate("/team"); } });
  if (!saved || !draft || !catalogue) return null;
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  return (
    <Card title="Design" className="mb-4"
      actions={<>
        <Button size="sm" onClick={() => setOpen(!open)}>{open ? "Close" : "Change the design"}</Button>
        {owner && open && <Button variant="primary" size="sm" disabled={!dirty} loading={save.busy} onClick={() => save.run(draft)}>Save</Button>}
      </>}>
      {save.notice && <div className="mb-3"><Alert tone={save.notice.tone}>{save.notice.text}</Alert></div>}
      {detail.template && <p className="mb-2 text-xs text-subtle">Made from the starter template “{catalogue.templates.find((t) => t.key === detail.template)?.name ?? detail.template}”.</p>}
      {open ? (
        <>
          <DesignForm value={draft} onChange={setDraft} catalogue={catalogue} disabled={!owner} />
          {owner && (
            <div className="mt-4 border-t border-border pt-3">
              <Button variant="danger" size="sm" onClick={() => setConfirm(true)}><Trash2 className="h-3.5 w-3.5" aria-hidden />Take off the team</Button>
            </div>
          )}
        </>
      ) : (
        <p className="whitespace-pre-wrap text-[13px] text-muted">{saved.guide || "No method written yet."}</p>
      )}
      <Dialog open={confirm} onClose={() => setConfirm(false)} title={`Take ${detail.agent.name} off the team?`}
        footer={<><Button onClick={() => setConfirm(false)}>Keep it</Button><Button variant="danger" loading={archive.busy} onClick={() => archive.run(undefined)}>Take it off</Button></>}>
        <p className="text-[13px]">It stops at once and its waiting proposals are withdrawn. Its runs, proposals and notes stay for the record.</p>
        {archive.notice && <div className="mt-3"><Alert tone={archive.notice.tone}>{archive.notice.text}</Alert></div>}
      </Dialog>
    </Card>
  );
}
