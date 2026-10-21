/* Getting started: what the team needs before it can work (company, AI, the team's switch and budget), then the first
   agent and the project. And the AI card (also on Platform): provider, key, models, prices, test. */
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Circle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, Card, Field, Input, PageTitle, Select, Textarea, Toggle } from "../ui";

type AIOut = Schemas["AIOut"];
type ProviderName = AIOut["provider"];
type TeamSettings = Schemas["TeamSettings"];

export function useSetup() {
  return useQuery({ queryKey: ["setup"], queryFn: async () => unwrap(await api.GET("/admin-api/setup")) });
}

const SOURCE: Record<string, string> = { admin: "set here", environment: "from the server's settings (.env)", none: "none" };

export function AICard({ owner, onSaved }: { owner: boolean; onSaved?: () => void }) {
  const { data, error } = useQuery({ queryKey: ["ai"], queryFn: async () => unwrap(await api.GET("/admin-api/ai")) });
  const [provider, setProvider] = useState<ProviderName>("gemini");
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [models, setModels] = useState<Record<string, { reasoning: string; fast: string }>>({});
  const [prices, setPrices] = useState<Record<string, number[]>>({});
  const [baseUrl, setBaseUrl] = useState("");
  useEffect(() => {
    if (!data) return;
    setProvider(data.provider);
    setModels(Object.fromEntries(data.providers.map((p) => [p.key, { reasoning: p.reasoning_model, fast: p.fast_model }])));
    setPrices(data.prices);
    setBaseUrl(data.openai_base_url);
    setKeys({});
  }, [data]);
  const save = useAction(async () => unwrap(await api.PUT("/admin-api/ai", { body: { provider, keys, models, prices, openai_base_url: baseUrl } })),
    { invalidate: [["ai"], ["setup"]], done: () => { onSaved?.(); return "Saved."; } });
  const test = useAction(async () => unwrap(await api.POST("/admin-api/ai/test")), { done: (r) => (r.ok ? `It works: ${r.model} answered “${r.message}” in ${r.latency_ms} ms.` : `Not working: ${r.message}`) });
  if (error) return <Alert>{error.message}</Alert>;
  if (!data) return null;
  const chosen = data.providers.find((p) => p.key === provider)!;
  const mine = models[provider] ?? { reasoning: "", fast: "" };
  const priceOf = (model: string) => prices[model] ?? [];
  const setPrice = (model: string, i: number, v: string) => {
    if (!model) return;
    const next = [...(prices[model] ?? [0, 0])];
    next[i] = Number(v) || 0;
    setPrices({ ...prices, [model]: next });
  };
  return (
    <Card title="AI" actions={owner && <><Button size="sm" loading={test.busy} onClick={() => test.run(undefined)}>Test</Button><Button variant="primary" size="sm" loading={save.busy} onClick={() => save.run(undefined)}>Save</Button></>}>
      {(save.notice ?? test.notice) && <div className="mb-3"><Alert tone={(test.notice?.text.startsWith("Not") ? "danger" : (save.notice ?? test.notice)!.tone)}>{(test.notice ?? save.notice)!.text}</Alert></div>}
      <p className="mb-3 text-xs text-muted">What the agents, Nia's chat, meetings and the brain think with — with your own key from the provider.</p>
      <Field label="Provider">
        <Select value={provider} disabled={!owner} onChange={(e) => setProvider(e.target.value as ProviderName)}>
          {data.providers.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        </Select>
      </Field>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Field label={`${chosen.label} key`} hint={`Now: ${SOURCE[chosen.key_source]}. Stored encrypted; type to replace it, leave empty to keep it.`}>
          <Input type="password" autoComplete="off" disabled={!owner} placeholder={chosen.key_source === "none" ? "" : "••••••••"} value={keys[provider] ?? ""} onChange={(e) => setKeys({ ...keys, [provider]: e.target.value })} />
        </Field>
        {provider === "openai" && (
          <Field label="Address (optional)" hint="Empty: OpenAI. Or another service speaking OpenAI's API, e.g. https://openrouter.ai/api/v1.">
            <Input value={baseUrl} disabled={!owner} placeholder="https://api.openai.com/v1" onChange={(e) => setBaseUrl(e.target.value)} />
          </Field>
        )}
        {(["reasoning", "fast"] as const).map((tier) => (
          <Field key={tier} label={tier === "reasoning" ? "Reasoning model (analyses, writing)" : "Fast model (routine work)"}
            hint={chosen[tier === "reasoning" ? "default_reasoning_model" : "default_fast_model"] ? `Default: ${chosen[tier === "reasoning" ? "default_reasoning_model" : "default_fast_model"]}` : "Name the model you use."}>
            <Input value={mine[tier]} disabled={!owner} onChange={(e) => setModels({ ...models, [provider]: { ...mine, [tier]: e.target.value } })} />
            <div className="mt-1.5 flex items-center gap-2 text-xs text-muted">
              <span>USD per million tokens: in</span>
              <Input className="w-20" type="number" min={0} step="0.01" disabled={!owner || !mine[tier]} value={priceOf(mine[tier])[0] ?? ""} onChange={(e) => setPrice(mine[tier], 0, e.target.value)} />
              <span>out</span>
              <Input className="w-20" type="number" min={0} step="0.01" disabled={!owner || !mine[tier]} value={priceOf(mine[tier])[1] ?? ""} onChange={(e) => setPrice(mine[tier], 1, e.target.value)} />
            </div>
          </Field>
        ))}
      </div>
      <p className="mt-3 text-xs text-muted">Prices come from the provider's price list; budgets count with them. <span className="font-medium">Gemini only</span>, whichever provider thinks: {data.gemini_only.join(", ")} — {data.gemini_key ? "a Gemini key is set." : "they need a Gemini key (choose Gemini above, add its key, then switch back)."}</p>
    </Card>
  );
}

function CompanyStep({ owner }: { owner: boolean }) {
  const brand = useQuery({ queryKey: ["marketing"], queryFn: async () => unwrap(await api.GET("/admin-api/marketing")) }).data;
  const [name, setName] = useState("");
  const [about, setAbout] = useState("");
  useEffect(() => { if (brand) { setName(brand.brand.name); setAbout(brand.brand.about); } }, [brand]);
  const save = useAction(async () => unwrap(await api.PUT("/admin-api/setup/company", { body: { name, about } })), { invalidate: [["setup"], ["marketing"]], done: () => "Saved." });
  return (
    <div className="grid gap-3">
      {save.notice && <Alert tone={save.notice.tone}>{save.notice.text}</Alert>}
      <Field label="Company name"><Input value={name} maxLength={80} disabled={!owner} onChange={(e) => setName(e.target.value)} /></Field>
      <Field label="What it does, and for whom" hint="In your own words, a few sentences. Every agent reads this first.">
        <Textarea rows={4} value={about} maxLength={2000} disabled={!owner} onChange={(e) => setAbout(e.target.value)} />
      </Field>
      {owner && <div><Button variant="primary" size="sm" disabled={!name.trim() || about.trim().length < 20} loading={save.busy} onClick={() => save.run(undefined)}>Save</Button></div>}
    </div>
  );
}

function TeamStep({ owner }: { owner: boolean }) {
  const team = useQuery({ queryKey: ["team"], queryFn: async () => unwrap(await api.GET("/admin-api/team")) }).data;
  const [draft, setDraft] = useState<TeamSettings | null>(null);
  useEffect(() => { if (team) setDraft(team.team); }, [team]);
  const save = useAction(async (body: TeamSettings) => unwrap(await api.PUT("/admin-api/team/settings", { body })), { invalidate: [["team"], ["setup"]], done: () => "Saved." });
  if (!draft) return null;
  return (
    <div className="grid gap-3">
      {save.notice && <Alert tone={save.notice.tone}>{save.notice.text}</Alert>}
      <Toggle tone="primary" label="Team switched on" description="Agents still each start off until you set them up." checked={draft.enabled ?? false} disabled={!owner} onChange={(enabled) => setDraft({ ...draft, enabled })} />
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Language the agents write to you in">
          <Select value={draft.language ?? ""} disabled={!owner} onChange={(e) => setDraft({ ...draft, language: (e.target.value || null) as TeamSettings["language"] })}>
            <option value="">Not set</option><option value="en">English</option><option value="de">German</option>
          </Select>
        </Field>
        <Field label="Team AI budget per day (USD)">
          <Input type="number" min={0} step="0.01" value={draft.ai_daily_usd ?? ""} placeholder="Not set" disabled={!owner} onChange={(e) => setDraft({ ...draft, ai_daily_usd: e.target.value === "" ? null : Number(e.target.value) })} />
        </Field>
      </div>
      {owner && <div><Button variant="primary" size="sm" loading={save.busy} onClick={() => save.run(draft)}>Save</Button></div>}
    </div>
  );
}

const LINKS: Record<string, { to: string; label: string }> = {
  agent: { to: "/team/new", label: "Make an agent" },
  project: { to: "/plugins/connect", label: "Connect it" },
  mail: { to: "/docs/getting-started", label: "How" },
};

export function WelcomePage({ owner }: { owner: boolean }) {
  const navigate = useNavigate();
  const { data, error } = useSetup();
  const [open, setOpen] = useState<string>("");
  const finish = useAction(async () => unwrap(await api.POST("/admin-api/setup/finish")), { invalidate: [["setup"]], done: () => { navigate("/insights"); } });
  useEffect(() => { if (data && !open) setOpen(data.steps.find((s) => !s.done)?.key ?? ""); }, [data, open]);
  if (error) return <Alert>{error.message}</Alert>;
  if (!data) return null;
  const ready = data.steps.every((s) => !s.required || s.done);
  return (
    <>
      <PageTitle title="Getting started" description="What your team needs before it can work, and what comes next. Every step can be changed later on its own page." />
      {finish.notice && <div className="mb-3"><Alert tone={finish.notice.tone}>{finish.notice.text}</Alert></div>}
      <div className="grid gap-3">
        {data.steps.map((step, i) => (
          <Card key={step.key} padded={false}>
            <button type="button" className="flex w-full items-center gap-3 px-4 py-3 text-left" onClick={() => setOpen(open === step.key ? "" : step.key)}>
              {step.done ? <CheckCircle2 className="h-5 w-5 shrink-0 text-success" aria-hidden /> : <Circle className="h-5 w-5 shrink-0 text-subtle" aria-hidden />}
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-2 text-[13px] font-semibold text-fg">{i + 1}. {step.title}{!step.required && <Badge>optional</Badge>}</span>
                <span className="block text-xs text-muted">{step.detail}</span>
              </span>
              {LINKS[step.key] && <Link to={LINKS[step.key].to} onClick={(e) => e.stopPropagation()}><Button size="sm">{LINKS[step.key].label}</Button></Link>}
            </button>
            {open === step.key && ["company", "ai", "team"].includes(step.key) && (
              <div className="border-t border-border px-4 py-3">
                {step.key === "company" && <CompanyStep owner={owner} />}
                {step.key === "ai" && <AICard owner={owner} />}
                {step.key === "team" && <TeamStep owner={owner} />}
              </div>
            )}
          </Card>
        ))}
      </div>
      {owner && (
        <div className="mt-4 flex items-center gap-3">
          <Button variant="primary" disabled={!ready} loading={finish.busy} onClick={() => finish.run(undefined)}>Finish</Button>
          {!ready && <span className="text-xs text-muted">Finish when the needed steps are done.</span>}
        </div>
      )}
    </>
  );
}
