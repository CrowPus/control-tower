/* Admin → Nia (agent team, stage A2): talk to Nia, the latest brief, directives to confirm, and
   what Nia remembers. The Nia button on every page opens the same conversation in a panel. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Brain, Check, Globe, MessageCircle, Plus, Send, Sparkles, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, errorMessage, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageTitle, Select, Textarea, ago, cn, when } from "../ui";
import { AgentChatPanel } from "../team/AgentChat";
import { LiveBrowser, useBrowser } from "./Browser";
import { MeetingsCard } from "./Meetings";
import { NiaAvatar } from "./Avatar";
import { SHOWING_PAGE, TalkButton, useVoice, VoiceSettingsCard } from "./Voice";

type Directive = Schemas["NiaDirectiveOut"];
type Message = Schemas["NiaMessageOut"];

function useNiaStatus() {
  return useQuery({ queryKey: ["nia-status"], queryFn: async () => unwrap(await api.GET("/admin-api/nia/status")), refetchInterval: 30_000 });
}

function Bubble({ message }: { message: Message }) {
  const mine = message.role === "owner";
  return (
    <div className={cn("flex", mine ? "justify-end" : "justify-start")}>
      <div className={cn("max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed", mine ? "bg-primary text-primary-fg" : "bg-raised text-fg")}>
        {message.text}
        {!mine && message.tool_calls.length > 0 && <p className="mt-1 text-[11px] text-muted">Looked at: {[...new Set(message.tool_calls.map((c) => String(c.tool).replace(/_/g, " ")))].join(", ")}</p>}
      </div>
    </div>
  );
}

export function DirectiveCard({ directive, owner }: { directive: Directive; owner: boolean }) {
  const client = useQueryClient();
  const act = useAction(async (step: "confirm" | "done" | "cancelled") =>
    step === "confirm" ? unwrap(await api.POST("/admin-api/nia/directives/{directive_id}/confirm", { params: { path: { directive_id: directive.id } } }))
      : unwrap(await api.POST("/admin-api/nia/directives/{directive_id}/close", { params: { path: { directive_id: directive.id } }, body: { status: step } })),
  { done: () => { void client.invalidateQueries({ queryKey: ["nia-directives"] }); void client.invalidateQueries({ queryKey: ["nia-status"] }); } });
  return (
    <div className="rounded-[var(--radius-control)] border border-border p-3 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={directive.status === "proposed" ? "warning" : directive.status === "active" ? "primary" : "neutral"}>{directive.status === "proposed" ? "Waiting for your OK" : directive.status}</Badge>
        <span className="text-xs text-muted">for {directive.agent_names.join(", ")}{directive.due_on ? ` · by ${directive.due_on}` : ""}</span>
      </div>
      <p className="mt-1.5 text-fg">{directive.text}</p>
      {directive.goal && <p className="mt-0.5 text-xs text-muted">Goal: {directive.goal}</p>}
      {directive.reports.length > 0 && (
        <ul className="mt-2 space-y-1 border-l-2 border-border pl-2 text-xs text-muted">
          {directive.reports.slice(-3).map((r, i) => <li key={i}><span className="font-medium text-fg">{String(r.agent)}</span>: {String(r.text)}{r.done ? " (done)" : ""}</li>)}
        </ul>
      )}
      {act.notice?.tone === "danger" && <div className="mt-2"><Alert>{act.notice.text}</Alert></div>}
      {owner && (
        <div className="mt-2 flex flex-wrap gap-2">
          {directive.status === "proposed" && <Button size="sm" variant="primary" loading={act.busy} onClick={() => act.run("confirm")}><Check className="h-3.5 w-3.5" aria-hidden />Confirm</Button>}
          {directive.status === "active" && <Button size="sm" loading={act.busy} onClick={() => act.run("done")}>Mark done</Button>}
          {(directive.status === "proposed" || directive.status === "active") && <Button size="sm" variant="ghost" disabled={act.busy} onClick={() => act.run("cancelled")}>{directive.status === "proposed" ? "Discard" : "Cancel"}</Button>}
        </div>
      )}
    </div>
  );
}

/** One conversation: the messages, the box to write in, and directives Nia just proposed. */
function Conversation({ id, owner, compact }: { id: number; owner: boolean; compact?: boolean }) {
  const voice = useVoice();
  const client = useQueryClient();
  const { data } = useQuery({ queryKey: ["nia-conversation", id], queryFn: async () => unwrap(await api.GET("/admin-api/nia/conversations/{conversation_id}", { params: { path: { conversation_id: id } } })) });
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [proposed, setProposed] = useState<Directive[]>([]);
  // Only the messages scroll (never the page), and only when you are already at the newest.
  const box = useRef<HTMLDivElement>(null);
  const atEnd = useRef(true);
  useEffect(() => {
    const el = box.current;
    if (el && atEnd.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [data?.messages.length, pending, proposed.length]);
  async function send(event?: FormEvent) {
    event?.preventDefault();
    const said = text.trim();
    if (!said || pending) return;
    // While you talk with Nia in this conversation, typing goes into the live conversation.
    if (voice.conversationId === id && voice.say(said)) {
      setText("");
      return;
    }
    setPending(said);
    setText("");
    setError("");
    try {
      const r = unwrap(await api.POST("/admin-api/nia/conversations/{conversation_id}/messages", { params: { path: { conversation_id: id } }, body: { text: said } }));
      client.setQueryData(["nia-conversation", id], r.conversation);
      if (r.directives.length) setProposed((p) => [...p, ...r.directives]);
      void client.invalidateQueries({ queryKey: ["nia-conversations"] });
      void client.invalidateQueries({ queryKey: ["nia-directives"] });
    } catch (e) {
      setError(errorMessage(e));
      setText(said);
    } finally {
      setPending(null);
    }
  }
  const messages = data?.messages ?? [];
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={box} onScroll={(e) => { const el = e.currentTarget; atEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}
        className={cn("min-h-0 flex-1 space-y-2 overflow-y-auto overscroll-contain", compact ? "px-3 py-3" : "p-4")}>
        {messages.length === 0 && !pending && (
          <div className="py-6 text-center text-[13px] text-muted">
            <Sparkles className="mx-auto mb-2 h-5 w-5 text-primary" aria-hidden />
            Ask for the rundown, ask about any agent or number, or give the team an instruction.
          </div>
        )}
        {messages.map((m) => <Bubble key={m.id} message={m} />)}
        {pending && (
          <>
            <Bubble message={{ id: -1, role: "owner", text: pending, tool_calls: [], cost_usd: 0, created_at: "" }} />
            <p className="text-xs text-muted">Nia is looking…</p>
          </>
        )}
        {proposed.length > 0 && <div className="space-y-2 pt-1">{proposed.map((d) => <DirectiveCard key={d.id} directive={d} owner={owner} />)}</div>}
      </div>
      <div className="shrink-0">
      {error && <div className="px-3 pb-2"><Alert>{error}</Alert></div>}
      {owner && <div className="flex border-t border-border px-3 pt-2"><TalkButton conversationId={id} /></div>}
      {owner && (
        <form onSubmit={send} className="flex items-end gap-2 p-3">
          <Textarea rows={2} className="min-h-[2.5rem] flex-1 resize-none" placeholder="Talk to Nia…" value={text} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} aria-label="Message to Nia" />
          <Button type="submit" variant="primary" disabled={!text.trim()} loading={pending !== null} aria-label="Send"><Send className="h-4 w-4" aria-hidden /></Button>
        </form>
      )}
      </div>
    </div>
  );
}

function NotReady({ problem }: { problem: string }) {
  return (
    <Alert tone="warning">
      {problem} <Link to="/team/nia" className="font-medium underline">Set up Nia</Link> · <Link to="/team" className="font-medium underline">Team settings</Link>
    </Alert>
  );
}

export function NiaPage({ owner }: { owner: boolean }) {
  const voice = useVoice();
  const client = useQueryClient();
  const status = useNiaStatus();
  const conversations = useQuery({ queryKey: ["nia-conversations"], queryFn: async () => unwrap(await api.GET("/admin-api/nia/conversations")) });
  const directives = useQuery({ queryKey: ["nia-directives"], queryFn: async () => unwrap(await api.GET("/admin-api/nia/directives")) });
  const [current, setCurrent] = useState<number | null>(null);
  useEffect(() => { if (current === null && conversations.data?.length) setCurrent(conversations.data[0].id); }, [conversations.data, current]);
  const start = useAction(async () => unwrap(await api.POST("/admin-api/nia/conversations")), { done: (c) => { setCurrent(c.id); void client.invalidateQueries({ queryKey: ["nia-conversations"] }); } });
  const brief = useAction(async () => unwrap(await api.POST("/admin-api/nia/brief")), { invalidate: [["nia-status"]], done: () => "Nia is preparing the brief; it appears here in a minute." });
  const s = status.data;
  const browsing = useBrowser().data?.active;
  return (
    <>
      <PageTitle title="Nia" description="Your chief of staff. Nia speaks for the whole team: ask for the rundown, ask about any agent or number, and give instructions — they reach the agents after you confirm them."
        actions={<><Link to="/nia/browser"><Button><Globe className="h-3.5 w-3.5" aria-hidden />Browser</Button></Link><Link to="/nia/memory"><Button><Brain className="h-3.5 w-3.5" aria-hidden />Memory</Button></Link>{owner && <Button variant="primary" loading={start.busy} onClick={() => start.run(undefined)}><Plus className="h-3.5 w-3.5" aria-hidden />New conversation</Button>}</>} />
      {s && !s.ready && <div className="mb-4"><NotReady problem={s.problem} /></div>}
      {owner && browsing && <div className="mb-4"><LiveBrowser session={browsing} compact /></div>}
      <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
        <section className="flex h-[calc(100vh-11rem)] min-h-[28rem] flex-col overflow-hidden rounded-[var(--radius-card)] border border-border bg-surface">
          <div className="flex shrink-0 items-center gap-3 border-b border-border px-3 py-2">
            <NiaAvatar state={voice.speaking ? "speaking" : "idle"} size="bar" />
            <Select className="max-w-md" value={current ?? ""} onChange={(e) => setCurrent(Number(e.target.value))} aria-label="Conversation">
              {(conversations.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.title} · {ago(c.updated_at)}</option>)}
              {!conversations.data?.length && <option value="">No conversation yet</option>}
            </Select>
          </div>
          {current !== null ? <Conversation id={current} owner={owner} /> : (
            <div className="grid flex-1 place-items-center p-6 text-center text-[13px] text-muted">
              <div><div className="mx-auto mb-3 w-fit"><NiaAvatar state="idle" size="page" /></div><MessageCircle className="mx-auto mb-2 h-6 w-6" aria-hidden />{owner ? <Button variant="primary" onClick={() => start.run(undefined)}>Start talking to Nia</Button> : "No conversations yet."}</div>
            </div>
          )}
        </section>
        {/* The brief scrolls inside its own box, so however long it is, the cards below keep their place. */}
        <div className="grid content-start gap-4">
          <Card title="Latest brief" bodyClassName="max-h-[40vh] overflow-y-auto overscroll-contain"
            actions={owner && s?.ready && <Button size="sm" loading={brief.busy} onClick={() => brief.run(undefined)}>Brief me now</Button>}>
            <>
              {brief.notice && <div className="mb-2"><Alert tone={brief.notice.tone}>{brief.notice.text}</Alert></div>}
              {s?.brief ? <><p className="mb-1 text-xs text-muted">{when(s.brief.at)}</p><p className="whitespace-pre-wrap text-[13px]">{s.brief.summary}</p></> : <p className="text-[13px] text-muted">No brief yet. Nia briefs you on the schedule you set for it (Team → Nia), or now with the button.</p>}
            </>
          </Card>
          <MeetingsCard owner={owner} />
          <VoiceSettingsCard owner={owner} />
          <Card title="Directives">
            {(directives.data ?? []).length === 0 ? <p className="text-[13px] text-muted">No open directives. Tell Nia what the team should do.</p>
              : <div className="space-y-2">{(directives.data ?? []).map((d) => <DirectiveCard key={d.id} directive={d} owner={owner} />)}</div>}
          </Card>
        </div>
      </div>
    </>
  );
}

const KINDS: Record<string, string> = { person: "About you", decision: "Decision", preference: "Preference", work: "Work we did", lesson: "Lesson", fact: "Fact" };
const KIND_TONE: Record<string, "primary" | "info" | "success" | "warning" | "neutral"> = { person: "warning", decision: "primary", preference: "primary", work: "success", lesson: "info", fact: "neutral" };

/** Who Nia talks with: the owner writes it; Nia never guesses a name. */
function AboutYouCard({ owner }: { owner: boolean }) {
  const { data } = useQuery({ queryKey: ["nia-owner"], queryFn: async () => unwrap(await api.GET("/admin-api/nia/owner")) });
  const [draft, setDraft] = useState<Schemas["NiaOwnerIn"]>({ name: "", address_as: "", role: "", about: "" });
  useEffect(() => { if (data) setDraft(data); }, [data]);
  const save = useAction(async () => unwrap(await api.PUT("/admin-api/nia/owner", { body: draft })), { invalidate: [["nia-owner"]], done: () => "Nia knows you now." });
  return (
    <Card title="About you" className="mb-4" actions={owner && <Button size="sm" variant="primary" loading={save.busy} onClick={() => save.run(undefined)}>Save</Button>}>
      {save.notice && <div className="mb-2"><Alert tone={save.notice.tone}>{save.notice.text}</Alert></div>}
      {data && !data.name && <p className="mb-2 text-[13px] text-warning">Nia does not know your name yet. Tell her here who you are and how she should address you.</p>}
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Your name"><Input disabled={!owner} maxLength={200} value={draft.name ?? ""} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
        <Field label="How Nia addresses you" hint="e.g. your first name, or Mr / Ms and your surname"><Input disabled={!owner} maxLength={200} value={draft.address_as ?? ""} onChange={(e) => setDraft({ ...draft, address_as: e.target.value })} /></Field>
        <Field label="Your role"><Input disabled={!owner} maxLength={300} value={draft.role ?? ""} onChange={(e) => setDraft({ ...draft, role: e.target.value })} placeholder="e.g. Founder and managing director" /></Field>
      </div>
      <div className="mt-3"><Field label="What Nia should know about you" hint={`How you like to work, what matters to you. Nia also remembers what you tell her (shown below as About you). ${(draft.about ?? "").length} of 10000 characters.`}><Textarea disabled={!owner} rows={5} maxLength={10000} value={draft.about ?? ""} onChange={(e) => setDraft({ ...draft, about: e.target.value })} /></Field></div>
    </Card>
  );
}

export function NiaMemoryPage({ owner }: { owner: boolean }) {
  const [kind, setKind] = useState("");
  const [topic, setTopic] = useState("");
  const [q, setQ] = useState("");
  const { data, error } = useQuery({ queryKey: ["nia-memories", kind, q, topic], queryFn: async () => unwrap(await api.GET("/admin-api/nia/memories", { params: { query: { kind, q, topic } } })), placeholderData: (p) => p });
  const topics = useQuery({ queryKey: ["nia-memories", "topics"], queryFn: async () => unwrap(await api.GET("/admin-api/nia/memory-topics")) });
  const [draft, setDraft] = useState<Schemas["NiaMemoryIn"]>({ kind: "decision", text: "", evidence: "", topic: "" });
  const [editing, setEditing] = useState<number | null>(null);
  const [edit, setEdit] = useState<Schemas["NiaMemoryIn"]>({ kind: "decision", text: "", evidence: "", topic: "" });
  const add = useAction(async () => unwrap(await api.POST("/admin-api/nia/memories", { body: draft })), { invalidate: [["nia-memories"]], done: () => { setDraft({ ...draft, text: "", evidence: "" }); return "Nia will remember."; } });
  const save = useAction(async () => unwrap(await api.PUT("/admin-api/nia/memories/{memory_id}", { params: { path: { memory_id: editing ?? 0 } }, body: edit })), { invalidate: [["nia-memories"]], done: () => { setEditing(null); return "Corrected."; } });
  const forget = useAction(async (id: number) => { const r = await api.DELETE("/admin-api/nia/memories/{memory_id}", { params: { path: { memory_id: id } } }); if (!r.response.ok) unwrap(r); return null; }, { invalidate: [["nia-memories"]], done: () => "Forgotten." });
  if (error) return <Alert>{error.message}</Alert>;
  const notice = add.notice ?? save.notice ?? forget.notice;
  const total = (topics.data ?? []).reduce((n, t) => n + t.count, 0);
  return (
    <>
      <Link to="/nia" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg">← Nia</Link>
      <PageTitle title="What Nia remembers" description="Who you are, your decisions and preferences, the work you did together (Nia writes it down after every conversation and browser task), lessons and facts — each on its branch (topic). Nia reads these before she answers, by text or voice. Correct or delete anything." />
      {notice && <div className="mb-3"><Alert tone={notice.tone}>{notice.text}</Alert></div>}
      <AboutYouCard owner={owner} />
      <Card title={`Branches (${total} memories)`} className="mb-4">
        {(topics.data ?? []).length === 0 ? <p className="text-[13px] text-muted">Nothing yet.</p> : (
          <div className="flex flex-wrap gap-1.5">
            <button type="button" onClick={() => setTopic("")} className={cn("rounded-full border px-2.5 py-1 text-xs", topic === "" ? "border-primary bg-primary text-primary-fg" : "border-border text-fg hover:bg-raised")}>All</button>
            {(topics.data ?? []).map((t) => (
              <button key={t.topic} type="button" onClick={() => setTopic(t.topic)} title={Object.entries(t.kinds).map(([k, n]) => `${KINDS[k] ?? k}: ${n}`).join(", ")}
                className={cn("rounded-full border px-2.5 py-1 text-xs", topic === t.topic ? "border-primary bg-primary text-primary-fg" : "border-border text-fg hover:bg-raised")}>
                {t.topic || "no topic"} <span className="opacity-70">{t.count}</span>
              </button>
            ))}
          </div>
        )}
      </Card>
      {owner && (
        <Card title="Tell Nia to remember" className="mb-4">
          <div className="grid gap-2 sm:grid-cols-[10rem_10rem_1fr]">
            <Select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as typeof draft.kind })} aria-label="Kind">
              {Object.entries(KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </Select>
            <Input placeholder="Topic, e.g. opportunities" value={draft.topic ?? ""} onChange={(e) => setDraft({ ...draft, topic: e.target.value })} aria-label="Topic" />
            <Input placeholder="e.g. No paid workshops in the catalogue." value={draft.text} onChange={(e) => setDraft({ ...draft, text: e.target.value })} aria-label="Text" />
          </div>
          <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_auto]">
            <Input placeholder="Evidence or reason (optional)" value={draft.evidence} onChange={(e) => setDraft({ ...draft, evidence: e.target.value })} aria-label="Evidence" />
            <Button variant="primary" size="sm" disabled={!draft.text.trim()} loading={add.busy} onClick={() => add.run(undefined)}>Remember</Button>
          </div>
        </Card>
      )}
      <div className="mb-3 flex flex-wrap gap-2">
        <Input className="w-64" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" />
        <Select className="w-44" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind filter">
          <option value="">Every kind</option>
          {Object.entries(KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </Select>
      </div>
      <Card padded={false}>
        {data && data.length === 0 ? <Empty>Nothing here yet. Nia remembers what you tell her about yourself, what you decide, how you like things and the work you do together; you can also add memories here.</Empty> : (
          <ul className="divide-y divide-border">
            {(data ?? []).map((m) => (
              <li key={m.id} className="px-4 py-3 text-[13px]">
                {editing === m.id ? (
                  <div className="grid gap-2">
                    <div className="flex gap-2">
                      <Select className="w-40" value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value as typeof edit.kind })}>{Object.entries(KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select>
                      <Input className="w-48" placeholder="Topic" value={edit.topic ?? ""} onChange={(e) => setEdit({ ...edit, topic: e.target.value })} />
                    </div>
                    <Textarea rows={2} value={edit.text} onChange={(e) => setEdit({ ...edit, text: e.target.value })} />
                    <Field label="Evidence"><Input value={edit.evidence ?? ""} onChange={(e) => setEdit({ ...edit, evidence: e.target.value })} /></Field>
                    <div className="flex gap-2"><Button size="sm" variant="primary" loading={save.busy} onClick={() => save.run(undefined)}>Save</Button><Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button></div>
                  </div>
                ) : (
                  <div className="flex items-start gap-3">
                    <div className="flex shrink-0 flex-col items-start gap-1">
                      <Badge tone={KIND_TONE[m.kind] ?? "neutral"}>{KINDS[m.kind] ?? m.kind}</Badge>
                      {m.topic && <button type="button" className="text-[11px] text-muted hover:text-fg hover:underline" onClick={() => setTopic(m.topic ?? "")}>{m.topic}</button>}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="whitespace-pre-wrap text-fg">{m.text}</p>
                      <p className="mt-0.5 text-xs text-muted">{m.evidence ? `${m.evidence} · ` : ""}{m.source} · {ago(m.updated_at)}</p>
                    </div>
                    {owner && <><Button size="sm" variant="ghost" onClick={() => { setEditing(m.id); setEdit({ kind: m.kind, text: m.text, evidence: m.evidence, topic: m.topic ?? "" }); }}>Correct</Button>
                      <Button size="sm" variant="ghost" aria-label="Forget" disabled={forget.busy} onClick={() => forget.run(m.id)}><Trash2 className="h-3.5 w-3.5" aria-hidden /></Button></>}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

// Whom the chat button talks to, remembered on this browser (Nia unless another agent was chosen).
const CHAT_WITH = "tower.admin.chatWith";
function rememberedChat(): string {
  try { return window.localStorage.getItem(CHAT_WITH) || "nia"; } catch { return "nia"; }
}

/** The chat button on every page: Nia, or any agent chosen from the list — no need to go to Team. */
export function NiaButton({ owner }: { owner: boolean }) {
  const voice = useVoice();
  const [open, setOpen] = useState(false);
  const [withKey, setWithKey] = useState(rememberedChat);
  const location = useLocation();
  const client = useQueryClient();
  const status = useNiaStatus();
  const team = useQuery({ queryKey: ["team"], enabled: open, queryFn: async () => unwrap(await api.GET("/admin-api/team")) });
  const conversations = useQuery({ queryKey: ["nia-conversations"], enabled: open && withKey === "nia", queryFn: async () => unwrap(await api.GET("/admin-api/nia/conversations")) });
  const [current, setCurrent] = useState<number | null>(null);
  useEffect(() => {
    const stepAside = () => setOpen(false);
    window.addEventListener(SHOWING_PAGE, stepAside);
    return () => window.removeEventListener(SHOWING_PAGE, stepAside);
  }, []);
  useEffect(() => { if (open && current === null && conversations.data?.length) setCurrent(conversations.data[0].id); }, [open, conversations.data, current]);
  const start = useAction(async () => unwrap(await api.POST("/admin-api/nia/conversations")), { done: (c) => { setCurrent(c.id); void client.invalidateQueries({ queryKey: ["nia-conversations"] }); } });
  const agents = (team.data?.agents ?? []).filter((a) => a.key !== "nia");
  const agent = agents.find((a) => a.key === withKey);
  const choose = (key: string) => {
    setWithKey(key);
    try { window.localStorage.setItem(CHAT_WITH, key); } catch { /* private mode: not remembered */ }
  };
  // Nia's own page already has her chat; the button stays for the other agents.
  if (location.pathname.startsWith("/nia") && withKey === "nia" && !open) return null;
  const waiting = (status.data?.proposed_directives ?? 0) > 0;
  const departments = [...new Set(agents.map((a) => a.department))];
  const name = withKey === "nia" ? "Nia" : agent?.name ?? "Agent";
  return (
    <>
      {open && (
        <div className="fixed inset-x-2 bottom-20 z-40 flex h-[72vh] flex-col overflow-hidden rounded-[var(--radius-card)] border border-border bg-surface shadow-[var(--shadow)] sm:inset-x-auto sm:right-5 sm:w-[28rem]">
          <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
            {withKey === "nia" ? <NiaAvatar state={voice.speaking ? "speaking" : "idle"} size="bar" /> : <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-primary/10 text-primary"><Bot className="h-4 w-4" aria-hidden /></span>}
            <Select className="h-8 min-w-0 flex-1 text-[13px] font-semibold" value={withKey} onChange={(e) => choose(e.target.value)} aria-label="Talk to">
              <option value="nia">Nia · Chief of staff</option>
              {departments.map((d) => (
                <optgroup key={d} label={d}>
                  {agents.filter((a) => a.department === d).map((a) => (
                    <option key={a.key} value={a.key}>{a.name}{a.status === "off" ? " · off" : a.status === "waiting" ? " · waiting for settings" : ""}</option>
                  ))}
                </optgroup>
              ))}
              {withKey !== "nia" && !agent && <option value={withKey}>{team.isLoading ? "Loading…" : withKey}</option>}
            </Select>
            <Link to={withKey === "nia" ? "/nia" : `/team/${withKey}`} className="shrink-0 text-xs text-primary" onClick={() => setOpen(false)}>Full page</Link>
            {owner && withKey === "nia" && <Button size="sm" variant="ghost" onClick={() => start.run(undefined)} aria-label="New conversation"><Plus className="h-3.5 w-3.5" aria-hidden /></Button>}
            <button type="button" className="shrink-0 text-muted hover:text-fg" onClick={() => setOpen(false)} aria-label="Close"><X className="h-4 w-4" /></button>
          </div>
          {withKey !== "nia" ? (
            agent || team.isLoading ? <AgentChatPanel key={withKey} keyName={withKey} name={name} owner={owner} />
              : <div className="p-4 text-[13px] text-muted">This agent is not available. Choose another one above.</div>
          ) : status.data && !status.data.ready ? <div className="p-3"><NotReady problem={status.data.problem} /></div>
            : current !== null ? <Conversation id={current} owner={owner} compact />
              : <div className="grid flex-1 place-items-center p-4">{owner ? <Button variant="primary" onClick={() => start.run(undefined)}>Start talking to Nia</Button> : <p className="text-[13px] text-muted">No conversation yet.</p>}</div>}
        </div>
      )}
      <button type="button" onClick={() => setOpen(!open)} aria-label={`Talk to ${name}`} title={`Talk to ${name} (choose any agent at the top)`}
        className="fixed bottom-5 right-5 z-40 grid h-12 w-12 place-items-center rounded-full bg-primary text-primary-fg shadow-[var(--shadow)] hover:bg-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]">
        {open ? <X className="h-5 w-5" aria-hidden /> : <Sparkles className="h-5 w-5" aria-hidden />}
        {waiting && !open && <span className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-surface bg-warning" aria-label="A directive waits for your OK" />}
      </button>
    </>
  );
}
