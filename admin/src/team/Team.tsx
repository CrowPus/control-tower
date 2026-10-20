/* The agent team: the team's switch and budgets, and every
   agent by department with its state, cost and what it waits for. Nothing here has a value
   until the owner sets it. */
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Globe, Inbox, Mail } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, Card, Field, Input, PageTitle, Select, Stat, Toggle, ago, usd } from "../ui";

export type TeamAgentRow = Schemas["TeamAgentRow"];
type TeamSettings = Schemas["TeamSettings"];

export function useTeam() {
  return useQuery({ queryKey: ["team"], queryFn: async () => unwrap(await api.GET("/admin-api/team")), refetchInterval: 30_000 });
}

export function AgentState({ agent }: { agent: TeamAgentRow }) {
  if (agent.status === "unavailable") return <Badge>Not in the team</Badge>;
  if (agent.status === "off") return <Badge>Off</Badge>;
  if (agent.status === "waiting") return <Badge tone="warning">Waiting for settings</Badge>;
  return <Badge tone="success">Ready</Badge>;
}

/** A number box where empty means "not set" (never a default). */
export function NumberSetting({ label, hint, value, onChange, disabled, step = "1", min = 0 }: { label: string; hint?: string; value: number | null | undefined; onChange: (v: number | null) => void; disabled?: boolean; step?: string; min?: number }) {
  return (
    <Field label={label} hint={hint}>
      <Input type="number" min={min} step={step} value={value ?? ""} placeholder="Not set" disabled={disabled} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
    </Field>
  );
}

function TeamSettingsCard({ owner, settings }: { owner: boolean; settings: TeamSettings }) {
  const [draft, setDraft] = useState<TeamSettings>(settings);
  useEffect(() => setDraft(settings), [settings]);
  const save = useAction(async (body: TeamSettings) => unwrap(await api.PUT("/admin-api/team/settings", { body })), { invalidate: [["team"]], done: () => "Saved." });
  const dirty = JSON.stringify(draft) !== JSON.stringify(settings);
  return (
    <Card title="The team" actions={owner && <Button variant="primary" size="sm" disabled={!dirty} loading={save.busy} onClick={() => save.run(draft)}>Save</Button>}>
      {save.notice && <div className="mb-3"><Alert tone={save.notice.tone}>{save.notice.text}</Alert></div>}
      <Toggle tone="primary" label="Team switched on" description="Off stops every agent at once, and cleared actions wait." checked={draft.enabled ?? false} disabled={!owner} onChange={(enabled) => setDraft({ ...draft, enabled })} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Field label="Language the agents write to you in">
          <Select value={draft.language ?? ""} disabled={!owner} onChange={(e) => setDraft({ ...draft, language: (e.target.value || null) as TeamSettings["language"] })}>
            <option value="">Not set</option>
            <option value="en">English</option>
            <option value="de">German</option>
          </Select>
        </Field>
        <NumberSetting label="Clearances in a row before offering to let an action run alone" hint="Empty: never offered." value={draft.autonomy_offer_after} min={1} disabled={!owner} onChange={(v) => setDraft({ ...draft, autonomy_offer_after: v })} />
        <NumberSetting label="Team AI budget per day (USD)" value={draft.ai_daily_usd} step="0.01" disabled={!owner} onChange={(v) => setDraft({ ...draft, ai_daily_usd: v })} />
        <NumberSetting label="Team AI budget per month (USD)" hint="Empty: only the daily budget applies." value={draft.ai_monthly_usd} step="0.01" disabled={!owner} onChange={(v) => setDraft({ ...draft, ai_monthly_usd: v })} />
      </div>
    </Card>
  );
}

export function TeamPage({ owner }: { owner: boolean }) {
  const { data, error } = useTeam();
  if (error) return <Alert>{error.message}</Alert>;
  if (!data) return null;
  return (
    <>
      <PageTitle
        title="Team"
        description="The agents that run the company with you. They propose; you clear. Every value — budgets, schedules, tools, limits — is yours to set; an agent waits until it has what it needs."
        actions={
          <>
            <Link to="/clearance"><Button variant={data.pending_approvals ? "primary" : "secondary"}><Inbox className="h-3.5 w-3.5" aria-hidden />Clearance{data.pending_approvals ? ` (${data.pending_approvals})` : ""}</Button></Link>
            <Link to="/team/mailboxes"><Button><Mail className="h-3.5 w-3.5" aria-hidden />Mailboxes</Button></Link>
            <Link to="/team/outside"><Button><Globe className="h-3.5 w-3.5" aria-hidden />Outside agents</Button></Link>
          </>
        }
      />
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <TeamSettingsCard owner={owner} settings={data.team} />
        <div className="grid content-start gap-3">
          {/* Everything the team's AI work cost; the team budget limits the agents' own work, pictures and videos have their own (Marketing). */}
          <Stat label="AI cost today" value={usd(data.cost_today_usd)}
            hint={`Agents ${usd(data.cost_today?.agents ?? 0)}${data.team.ai_daily_usd != null ? ` of ${usd(data.team.ai_daily_usd)}` : ""} · Pictures ${usd(data.cost_today?.pictures ?? 0)} · Videos ${usd(data.cost_today?.videos ?? 0)}`} />
          <Stat label="AI cost this month" value={usd(data.cost_month_usd)}
            hint={`Agents ${usd(data.cost_month?.agents ?? 0)}${data.team.ai_monthly_usd != null ? ` of ${usd(data.team.ai_monthly_usd)}` : ""} · Pictures ${usd(data.cost_month?.pictures ?? 0)} · Videos ${usd(data.cost_month?.videos ?? 0)}`} />
          <Stat label="Waiting for your clearance" value={data.pending_approvals} />
        </div>
      </div>
      {data.departments.map((department) => {
        const agents = data.agents.filter((a) => a.department === department);
        return (
          <Card key={department} title={department} className="mt-4" padded={false}>
            <ul className="divide-y divide-border">
              {agents.map((agent) => (
                <li key={agent.key} data-nia={`agent:${agent.key}`}>
                  <Link to={`/team/${agent.key}`} className="flex items-center gap-3 px-4 py-3 hover:bg-raised">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13px] font-semibold text-fg">{agent.name}</span>
                        <AgentState agent={agent} />
                        {agent.pending_approvals > 0 && <Badge tone="primary">{agent.pending_approvals} to approve</Badge>}
                        {agent.autonomy_offers.length > 0 && <Badge tone="info">Autonomy offer</Badge>}
                      </div>
                      <p className="mt-0.5 text-xs text-muted">{agent.purpose}</p>
                      {agent.status === "waiting" && <p className="mt-1 text-xs text-warning">Waiting for: {agent.waiting_for.join(", ")}</p>}
                      {agent.status !== "off" && (agent.schedule_problem
                        ? <p className="mt-1 text-xs text-warning">{agent.schedule_problem}</p>
                        : <p className="mt-1 text-xs text-subtle">{agent.schedule}</p>)}
                    </div>
                    <div className="hidden shrink-0 text-right text-xs text-muted sm:block">
                      <p>{agent.last_run_at ? `Ran ${ago(agent.last_run_at)}` : "Never ran"}</p>
                      <p className="tabular-nums" title="Its own work, the pictures it made and its videos">{usd(agent.cost_today_usd)} today · {usd(agent.cost_7d_usd)} 7 days</p>
                    </div>
                    <ChevronRight className="h-4 w-4 shrink-0 text-subtle" aria-hidden />
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        );
      })}
    </>
  );
}
