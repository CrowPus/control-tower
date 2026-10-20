/* One agent: its settings (all empty until the owner sets them), playbook with versions,
   notes, autonomy offers and recent runs. */
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageTitle, Select, StatusBadge, Table, Td, Textarea, Toggle, ago, usd, when } from "../ui";
import { AgentChatCard } from "./AgentChat";
import { AgentState, NumberSetting } from "./Team";

type Settings = Schemas["AgentSettings"];
type Detail = Schemas["TeamAgentDetailOut"];

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function emptySchedule(s: Settings["schedule"] | undefined): NonNullable<Settings["schedule"]> {
  return { every_minutes: s?.every_minutes ?? null, times: s?.times ?? [], weekdays: s?.weekdays ?? [] };
}

function SettingsCard({ owner, detail, keyName }: { owner: boolean; detail: Detail; keyName: string }) {
  const [draft, setDraft] = useState<Settings>(detail.settings);
  const [time, setTime] = useState("");
  useEffect(() => setDraft(detail.settings), [detail.settings]);
  const save = useAction(async (body: Settings) => unwrap(await api.PUT("/admin-api/team/agents/{key}", { params: { path: { key: keyName } }, body })), { invalidate: [["team"], ["team-agent", keyName]], done: () => "Saved." });
  const schedule = emptySchedule(draft.schedule);
  const setSchedule = (next: Partial<typeof schedule>) => setDraft({ ...draft, schedule: { ...schedule, ...next } });
  const tools = draft.tools ?? [];
  const levels = draft.levels ?? {};
  const l2 = draft.l2_daily_limit ?? {};
  const perDay = draft.proposals_per_day ?? {};
  const locked = !owner;
  const dirty = JSON.stringify(draft) !== JSON.stringify(detail.settings);
  return (
    <Card title="Settings" actions={owner && <Button variant="primary" size="sm" disabled={!dirty} loading={save.busy} onClick={() => save.run(draft)}>Save</Button>}>
      {save.notice && <div className="mb-3"><Alert tone={save.notice.tone}>{save.notice.text}</Alert></div>}
      <Toggle tone="primary" label="Switched on" checked={draft.enabled ?? false} disabled={locked} onChange={(enabled) => setDraft({ ...draft, enabled })} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Field label="Model" hint="Fast for routine work, reasoning for analyses and writing.">
          <Select value={draft.tier ?? ""} disabled={locked} onChange={(e) => setDraft({ ...draft, tier: (e.target.value || null) as Settings["tier"] })}>
            <option value="">Not set</option>
            <option value="fast">Fast</option>
            <option value="reasoning">Reasoning</option>
          </Select>
        </Field>
        <NumberSetting label="AI budget per day (USD)" step="0.01" value={draft.ai_daily_usd} disabled={locked} onChange={(v) => setDraft({ ...draft, ai_daily_usd: v })} />
        <NumberSetting label="Pause when one result costs more than (USD)" hint="Before its first result of the day it may spend this much; each proposal or event it sends allows this much more. Over that, it pauses until tomorrow. Empty: not checked." step="0.01" value={draft.max_cost_per_result_usd} disabled={locked} onChange={(v) => setDraft({ ...draft, max_cost_per_result_usd: v })} />
      </div>

      <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-muted">Schedule</h3>
      <p className="text-xs text-subtle">Your time zone (TIME_ZONE on the server). Empty: it runs only when events arrive for it and when you start it. Days alone do not start it: add a time or "every … minutes".</p>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <NumberSetting label="Every … minutes" min={1} value={schedule.every_minutes} disabled={locked} onChange={(v) => setSchedule({ every_minutes: v })} />
        <Field label="At these times">
          <div className="flex gap-2">
            <Input type="time" value={time} disabled={locked} onChange={(e) => setTime(e.target.value)} />
            <Button disabled={locked || !time} onClick={() => { setSchedule({ times: [...new Set([...(schedule.times ?? []), time])].sort() }); setTime(""); }}>Add</Button>
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {(schedule.times ?? []).map((t) => (
              <button key={t} type="button" disabled={locked} className="rounded-full bg-raised px-2 py-0.5 text-xs text-fg hover:bg-danger-soft hover:text-danger" onClick={() => setSchedule({ times: (schedule.times ?? []).filter((x) => x !== t) })} aria-label={`Remove ${t}`}>{t} ×</button>
            ))}
          </div>
        </Field>
      </div>
      <fieldset className="mt-2" disabled={locked}>
        <legend className="mb-1 text-xs font-medium text-muted">On these days (for the times and "every … minutes"; none ticked = every day)</legend>
        <div className="flex flex-wrap gap-3">
          {WEEKDAYS.map((label, i) => (
            <label key={label} className="flex items-center gap-1.5 text-[13px]">
              <input type="checkbox" checked={(schedule.weekdays ?? []).includes(i)} onChange={(e) => setSchedule({ weekdays: e.target.checked ? [...(schedule.weekdays ?? []), i] : (schedule.weekdays ?? []).filter((d) => d !== i) })} />
              {label}
            </label>
          ))}
        </div>
      </fieldset>

      <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-muted">Tools it may use</h3>
      <fieldset disabled={locked} className="mt-1 grid gap-1.5">
        {detail.tools.map((tool) => (
          <label key={tool.key} className="flex items-start gap-2 text-[13px]">
            <input type="checkbox" className="mt-0.5 shrink-0" checked={tools.includes(tool.key)} onChange={(e) => setDraft({ ...draft, tools: e.target.checked ? [...tools, tool.key] : tools.filter((t) => t !== tool.key) })} />
            <span><span className="font-medium">{tool.key.replace(/_/g, " ")}</span> <span className="text-muted">— {tool.description}</span></span>
          </label>
        ))}
      </fieldset>

      <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-muted">Actions it may propose</h3>
      <Table head={["Action", "Who decides", "Alone per day", "Proposals per day", "Cleared in a row"]}>
        {detail.actions.map((action) => (
          <tr key={action.key}>
            <Td>{action.label}</Td>
            <Td>
              {action.ceiling === "L3" ? <Badge>Always you</Badge> : (
                <Select className="w-44" disabled={locked} value={levels[action.key] ?? "L1"} onChange={(e) => setDraft({ ...draft, levels: { ...levels, [action.key]: e.target.value as "L1" | "L2" } })}>
                  <option value="L1">You clear each</option>
                  <option value="L2">Alone within a limit</option>
                </Select>
              )}
            </Td>
            <Td>
              {action.ceiling !== "L3" && (
                <Input type="number" min={0} className="w-24" placeholder="Not set" disabled={locked} value={l2[action.key] ?? ""}
                  onChange={(e) => { const next = { ...l2 }; if (e.target.value === "") delete next[action.key]; else next[action.key] = Number(e.target.value); setDraft({ ...draft, l2_daily_limit: next }); }} />
              )}
            </Td>
            <Td>
              <Input type="number" min={0} className="w-24" placeholder="No cap" disabled={locked} value={perDay[action.key] ?? ""}
                onChange={(e) => { const next = { ...perDay }; if (e.target.value === "") delete next[action.key]; else next[action.key] = Number(e.target.value); setDraft({ ...draft, proposals_per_day: next }); }} />
            </Td>
            <Td className="tabular-nums">{action.approved_in_a_row}</Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function OfferCard({ owner, detail, keyName }: { owner: boolean; detail: Detail; keyName: string }) {
  const [limits, setLimits] = useState<Record<string, string>>({});
  const answer = useAction(async ({ action, accept }: { action: string; accept: boolean }) =>
    unwrap(await api.POST("/admin-api/team/agents/{key}/offers/{action_type}", { params: { path: { key: keyName, action_type: action } }, body: { accept, daily_limit: accept ? Number(limits[action]) : null } })),
    { invalidate: [["team"], ["team-agent", keyName]], done: () => "Answered." });
  if (!detail.agent.autonomy_offers.length) return null;
  return (
    <Card title="Let it run alone?" className="mb-4">
      {answer.notice && <div className="mb-3"><Alert tone={answer.notice.tone}>{answer.notice.text}</Alert></div>}
      {detail.agent.autonomy_offers.map((key) => {
        const action = detail.actions.find((a) => a.key === key);
        return (
          <div key={key} className="flex flex-wrap items-end gap-3 py-2">
            <p className="min-w-0 flex-1 text-[13px]">You cleared <b>{action?.approved_in_a_row}</b> “{action?.label ?? key}” in a row without changing them. It can do these alone, up to a number per day that you set.</p>
            {owner && (
              <>
                <Field label="Alone per day"><Input type="number" min={1} className="w-24" placeholder="Not set" value={limits[key] ?? ""} onChange={(e) => setLimits({ ...limits, [key]: e.target.value })} /></Field>
                <Button variant="primary" disabled={!limits[key]} loading={answer.busy} onClick={() => answer.run({ action: key, accept: true })}>Allow</Button>
                <Button variant="ghost" disabled={answer.busy} onClick={() => answer.run({ action: key, accept: false })}>Not now</Button>
              </>
            )}
          </div>
        );
      })}
    </Card>
  );
}

function PlaybookCard({ owner, detail, keyName }: { owner: boolean; detail: Detail; keyName: string }) {
  const [text, setText] = useState(detail.playbook?.text ?? "");
  const [note, setNote] = useState("");
  useEffect(() => setText(detail.playbook?.text ?? ""), [detail.playbook?.text]);
  const save = useAction(async () => unwrap(await api.PUT("/admin-api/team/agents/{key}/playbook", { params: { path: { key: keyName } }, body: { text, note } })),
    { invalidate: [["team-agent", keyName]], done: () => { setNote(""); return "Saved as a new version."; } });
  return (
    <Card title="Playbook" actions={detail.playbook && <span className="text-xs text-muted">Version {detail.playbook.version} · {detail.playbook.created_by} · {ago(detail.playbook.created_at)}</span>}>
      <p className="mb-2 text-xs text-muted">Your instructions on how {detail.agent.name} works: whom to focus on, the tone, what to avoid. The law and the team's rules are built in and always apply.</p>
      {save.notice && <div className="mb-3"><Alert tone={save.notice.tone}>{save.notice.text}</Alert></div>}
      <Textarea rows={10} value={text} disabled={!owner} onChange={(e) => setText(e.target.value)} placeholder="No playbook yet." />
      {owner && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Input className="max-w-sm flex-1" placeholder="What changed (for the history)" value={note} onChange={(e) => setNote(e.target.value)} />
          <Button variant="primary" disabled={text === (detail.playbook?.text ?? "")} loading={save.busy} onClick={() => save.run(undefined)}>Save version</Button>
        </div>
      )}
      {detail.playbook_versions.length > 1 && (
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer text-muted">Earlier versions</summary>
          <ul className="mt-2 space-y-2">
            {detail.playbook_versions.slice(1).map((v) => (
              <li key={v.version} className="rounded-[var(--radius-control)] border border-border p-2">
                <p className="text-muted">Version {v.version} · {v.created_by} · {when(v.created_at)}{v.note ? ` · ${v.note}` : ""}</p>
                <p className="mt-1 whitespace-pre-wrap text-fg">{v.text}</p>
                {owner && <Button size="sm" variant="ghost" className="mt-1" onClick={() => setText(v.text)}>Load into the editor</Button>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}

function NotesCard({ owner, detail, keyName }: { owner: boolean; detail: Detail; keyName: string }) {
  const save = useAction(async (notes: Schemas["TeamNote"][]) => unwrap(await api.PUT("/admin-api/team/agents/{key}/notes", { params: { path: { key: keyName } }, body: { notes } })),
    { invalidate: [["team-agent", keyName]], done: () => "Forgotten." });
  return (
    <Card title="What it has learned" padded={false}>
      {detail.notes.length === 0 ? <Empty>No notes yet. The agent keeps lessons here, each with its evidence.</Empty> : (
        <ul className="divide-y divide-border">
          {detail.notes.map((n, i) => (
            <li key={i} className="flex items-start gap-3 px-4 py-2.5">
              <div className="min-w-0 flex-1 text-[13px]">
                <p className="text-fg">{n.text}</p>
                {n.evidence && <p className="mt-0.5 text-xs text-muted">Evidence: {n.evidence}</p>}
              </div>
              {owner && <Button size="sm" variant="ghost" disabled={save.busy} onClick={() => save.run(detail.notes.filter((_, j) => j !== i))}>Forget</Button>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function RunsCard({ detail }: { detail: Detail }) {
  return (
    <Card title="Recent runs" padded={false}>
      {detail.runs.length === 0 ? <Empty>No runs yet.</Empty> : (
        <ul className="divide-y divide-border">
          {detail.runs.map((run) => (
            <li key={run.id} className="px-4 py-2.5">
              <details>
                <summary className="flex cursor-pointer flex-wrap items-center gap-2 text-[13px]">
                  <StatusBadge status={run.status} />
                  <span className="text-muted">{when(run.started_at)} · {run.trigger}</span>
                  <span className="ml-auto tabular-nums text-xs text-muted">{usd(run.cost_usd)}</span>
                </summary>
                <div className="mt-2 space-y-2 text-[13px]">
                  {run.summary && <p className="whitespace-pre-wrap text-fg">{run.summary}</p>}
                  {run.waiting_for.length > 0 && <p className="text-warning">Waiting for: {run.waiting_for.join(", ")}</p>}
                  {run.error && <p className="text-danger">{run.error}</p>}
                  {run.tool_calls.length > 0 && (
                    <ol className="list-decimal space-y-0.5 pl-5 text-xs text-muted">
                      {run.tool_calls.map((c, i) => <li key={i}><span className="font-medium text-fg">{String(c.tool)}</span>{c.ok ? "" : " — refused or failed"}</li>)}
                    </ol>
                  )}
                </div>
              </details>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function AgentDetailPage({ owner }: { owner: boolean }) {
  const keyName = useParams().key ?? "";
  const { data, error } = useQuery({ queryKey: ["team-agent", keyName], queryFn: async () => unwrap(await api.GET("/admin-api/team/agents/{key}", { params: { path: { key: keyName } } })), refetchInterval: 15_000 });
  const start = useAction(async () => unwrap(await api.POST("/admin-api/team/agents/{key}/run", { params: { path: { key: keyName } } })),
    { invalidate: [["team-agent", keyName]], done: (r) => (r.queued ? "Started. The run appears below when it is done." : "A run is already waiting or running.") });
  if (error) return <Alert>{error.message}</Alert>;
  if (!data) return null;
  const agent = data.agent;
  return (
    <>
      <Link to="/team" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg"><ArrowLeft className="h-3.5 w-3.5" aria-hidden />Team</Link>
      <PageTitle title={agent.name} description={`${agent.department} · ${agent.purpose}`}
        actions={<><AgentState agent={agent} />{owner && agent.status === "ready" && <Button variant="primary" loading={start.busy} onClick={() => start.run(undefined)}><Play className="h-3.5 w-3.5" aria-hidden />Run now</Button>}</>} />
      {start.notice && <div className="mb-3"><Alert tone={start.notice.tone}>{start.notice.text}</Alert></div>}
      {agent.status === "waiting" && <div className="mb-4"><Alert tone="warning">Waiting for: {agent.waiting_for.join(", ")}.</Alert></div>}
      {agent.schedule_problem && <div className="mb-4"><Alert tone="warning">{agent.schedule_problem}</Alert></div>}
      <OfferCard owner={owner} detail={data} keyName={keyName} />
      {<div className="mb-4"><AgentChatCard keyName={keyName} name={agent.name} owner={owner} /></div>}
      <div className="grid gap-4 xl:grid-cols-2">
        <SettingsCard owner={owner} detail={data} keyName={keyName} />
        <div className="grid content-start gap-4">
          <PlaybookCard owner={owner} detail={data} keyName={keyName} />
          <NotesCard owner={owner} detail={data} keyName={keyName} />
          <RunsCard detail={data} />
        </div>
      </div>
    </>
  );
}
