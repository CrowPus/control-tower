/* Admin → Nia → Browser: watch Nia work in a real browser, answer when Nia asks, take over
   (click on the screen, type, press keys) and hand back, pause or stop; start a session; the owner's rules. Nia works
   only while this view is open: the live screen is what tells Control Tower the owner is watching. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, Globe, Hand, Pause, Play, Square, Undo2 } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type WheelEvent } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { NumberSetting } from "../team/Team";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageTitle, Textarea, Toggle, ago, cn, type Tone } from "../ui";

type Session = Schemas["SessionOut"];
type Rules = Schemas["BrowserRules"];
const WIDTH = 1280;
const HEIGHT = 800;
const STATUS: Record<string, [string, Tone]> = {
  running: ["Nia is working", "info"], idle: ["Ready — window open", "success"], asking: ["Nia asks you", "warning"], owner: ["You have the browser", "primary"], paused: ["Paused", "neutral"],
  done: ["Done", "success"], stopped: ["Stopped", "neutral"], failed: ["Failed", "danger"],
};

export function useBrowser() {
  return useQuery({ queryKey: ["nia-browser"], queryFn: async () => unwrap(await api.GET("/admin-api/nia/browser")), refetchInterval: (q) => (q.state.data?.active ? 2500 : 30_000) });
}

/** The live screen of the active session, with the owner's controls. */
export function LiveBrowser({ session, compact = false }: { session: Session; compact?: boolean }) {
  const client = useQueryClient();
  const [tick, setTick] = useState(0);
  const [frameError, setFrameError] = useState(false);
  const [address, setAddress] = useState("");
  const [reply, setReply] = useState("");
  const live = ["running", "asking", "owner", "paused", "idle"].includes(session.status);
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(() => setTick((n) => n + 1), 900);
    return () => window.clearInterval(t);
  }, [live]);
  // Keeps the session's steps fresh while watching (the frame alone does not carry them).
  useQuery({ queryKey: ["nia-browser", "session", session.id], queryFn: async () => {
    const s = unwrap(await api.GET("/admin-api/nia/browser/sessions/{session_id}", { params: { path: { session_id: session.id } } }));
    void client.invalidateQueries({ queryKey: ["nia-browser"], exact: true });
    return s;
  }, refetchInterval: live ? 2500 : false });
  const refresh = () => client.invalidateQueries({ queryKey: ["nia-browser"] });
  const answer = useAction(async (decision: Schemas["AnswerIn"]["decision"]) => unwrap(await api.POST("/admin-api/nia/browser/sessions/{session_id}/answer", {
    params: { path: { session_id: session.id } }, body: { decision, note: "" } })), { done: () => { void refresh(); } });
  const act = useAction(async (body: Schemas["ActIn"]) => unwrap(await api.POST("/admin-api/nia/browser/sessions/{session_id}/act", { params: { path: { session_id: session.id } }, body })),
    { done: () => { setTick((n) => n + 1); void refresh(); } });
  const say = useAction(async () => unwrap(await api.POST("/admin-api/nia/browser/sessions/{session_id}/reply", { params: { path: { session_id: session.id } }, body: { text: reply } })),
    { done: () => { setReply(""); void refresh(); } });
  const img = useRef<HTMLImageElement>(null);
  // A typing field inside the screen, invisible: whatever the owner types into it (keyboard, phone keyboard, paste, dictation)
  // goes straight into the browser. Clicking or tapping the screen focuses it, so the phone's keyboard opens too.
  const keys = useRef<HTMLInputElement>(null);
  const [focused, setFocused] = useState(false);
  const [sendError, setSendError] = useState("");
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  function send(partial: Partial<Schemas["ActIn"]> & { action: Schemas["ActIn"]["action"] }) {
    const body: Schemas["ActIn"] = { text: "", keys: "", url: "", direction: "down", ...partial };
    queue.current = queue.current.then(async () => {
      try {
        unwrap(await api.POST("/admin-api/nia/browser/sessions/{session_id}/act", { params: { path: { session_id: session.id } }, body }));
        setSendError("");
      } catch (e) {
        setSendError(errorMessage(e));
      }
      setTick((n) => n + 1);
    });
  }
  // Typed characters are sent in small batches, in order.
  const typed = useRef("");
  const flushTimer = useRef<number | null>(null);
  function flush() {
    if (flushTimer.current !== null) { window.clearTimeout(flushTimer.current); flushTimer.current = null; }
    if (typed.current) { send({ action: "type", text: typed.current }); typed.current = ""; }
  }
  function point(e: MouseEvent<HTMLElement>) {
    const box = (img.current ?? e.currentTarget).getBoundingClientRect();
    return { x: Math.round(((e.clientX - box.left) / box.width) * WIDTH), y: Math.round(((e.clientY - box.top) / box.height) * HEIGHT) };
  }
  function click(e: MouseEvent<HTMLElement>) {
    keys.current?.focus({ preventScroll: true });
    flush();
    send({ action: e.detail >= 2 ? "double_click" : "click", ...point(e) });
  }
  function typedInto(value: string) {
    if (!value) return;
    typed.current += value;
    if (keys.current) keys.current.value = "";
    if (flushTimer.current !== null) window.clearTimeout(flushTimer.current);
    flushTimer.current = window.setTimeout(flush, 150);
  }
  function keyDown(e: KeyboardEvent<HTMLInputElement>) {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && ["v", "c", "x"].includes(e.key.toLowerCase())) return; // paste arrives as typed text; copy/cut stay in the admin
    const named: Record<string, string> = { Enter: "Enter", Tab: "Tab", Backspace: "Backspace", Delete: "Delete", Escape: "Escape", ArrowUp: "ArrowUp", ArrowDown: "ArrowDown",
      ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", Home: "Home", End: "End", PageUp: "PageUp", PageDown: "PageDown" };
    const key = mod && e.key.length === 1 ? `Control+${e.key.toLowerCase()}` : named[e.key];
    if (!key) return; // ordinary characters arrive through the input event (also from phone keyboards)
    e.preventDefault();
    flush();
    send({ action: "key", keys: e.shiftKey && e.key === "Tab" ? "Shift+Tab" : key });
  }
  function wheel(e: WheelEvent<HTMLDivElement>) {
    if (!focused) return; // the page scrolls as usual until the owner clicks into the screen
    e.preventDefault();
    send({ action: "scroll", direction: e.deltaY < 0 ? "up" : "down" });
  }
  // When Nia hands the browser over, the keyboard goes to the screen at once.
  useEffect(() => {
    if (session.status === "owner") keys.current?.focus({ preventScroll: true });
  }, [session.status]);
  const [label, tone] = STATUS[session.status] ?? [session.status, "neutral" as Tone];
  const last = session.steps.filter((s) => s.kind !== "answer").slice(-1)[0];
  const notice = answer.notice?.tone === "danger" ? answer.notice : act.notice?.tone === "danger" ? act.notice : say.notice?.tone === "danger" ? say.notice
    : sendError ? { tone: "danger" as const, text: sendError } : null;
  return (
    <Card title={<span className="inline-flex items-center gap-2"><Globe className="h-4 w-4" aria-hidden />Nia's browser <Badge tone={tone}>{label}</Badge></span>}
      actions={live && (
        <div className="flex gap-1">
          {session.status === "paused" ? <Button size="sm" loading={answer.busy} onClick={() => answer.run("resume")}><Play className="h-3.5 w-3.5" aria-hidden />Go on</Button>
            : !["owner", "idle"].includes(session.status) && <Button size="sm" loading={answer.busy} onClick={() => answer.run("pause")}><Pause className="h-3.5 w-3.5" aria-hidden />Pause</Button>}
          {!["owner", "idle"].includes(session.status) && <Button size="sm" loading={answer.busy} onClick={() => answer.run("take_over")}><Hand className="h-3.5 w-3.5" aria-hidden />Take over</Button>}
          <Button size="sm" variant="danger" loading={answer.busy} onClick={() => answer.run("stop")}><Square className="h-3.5 w-3.5" aria-hidden />{session.status === "idle" ? "Close window" : "Stop"}</Button>
        </div>
      )}>
      <p className="mb-2 text-[13px] text-fg"><span className="text-muted">Goal: </span>{session.goal}</p>
      {notice && <div className="mb-2"><Alert>{notice.text}</Alert></div>}
      {session.status === "asking" && (
        <div className="mb-3 rounded-lg border border-warning bg-warning-soft p-3">
          <p className="text-[13px] font-semibold text-fg">{session.question || "Nia asks before going on."}</p>
          {session.asks_in_words ? (
            <div className="mt-2 flex gap-2">
              <Input value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Your answer" onKeyDown={(e) => { if (e.key === "Enter" && reply.trim()) say.run(undefined); }} />
              <Button size="sm" variant="primary" disabled={!reply.trim()} loading={say.busy} onClick={() => say.run(undefined)}>Answer</Button>
            </div>
          ) : (
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="primary" loading={answer.busy} onClick={() => answer.run("allow")}><Check className="h-3.5 w-3.5" aria-hidden />Allow</Button>
              <Button size="sm" loading={answer.busy} onClick={() => answer.run("take_over")}><Hand className="h-3.5 w-3.5" aria-hidden />Take over</Button>
              <Button size="sm" variant="danger" loading={answer.busy} onClick={() => answer.run("stop")}>Stop</Button>
            </div>
          )}
        </div>
      )}
      {session.status === "owner" && (
        <div className="mb-3 flex flex-col gap-2 rounded-lg border border-primary bg-raised p-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-[13px] text-fg"><span className="font-semibold">Your turn.</span> {session.reason && session.reason !== "taken over" ? `${session.reason} ` : ""}Click into the screen below and type, as in any browser — your password goes straight into the page; Nia never sees or keeps it.</p>
          <Button size="sm" variant="primary" loading={answer.busy} onClick={() => answer.run("hand_back")}><Undo2 className="h-3.5 w-3.5" aria-hidden />Hand back to Nia</Button>
        </div>
      )}
      {session.status === "idle" && (
        <div className="mb-3 rounded-lg border border-border bg-raised p-3 text-[13px] text-fg">
          <span className="font-semibold">Nia has finished.</span> The window stays open: look around, click and type in it yourself, or give Nia the next task — she goes on in this window.
          {session.result && <p className="mt-2 whitespace-pre-wrap text-muted">{session.result}</p>}
        </div>
      )}
      {live ? (
        <>
          <div className="mb-2 flex items-center gap-1">
            <Button size="sm" variant="ghost" aria-label="Back" onClick={() => act.run({ action: "back", text: "", keys: "", url: "", direction: "down" })}><ArrowLeft className="h-3.5 w-3.5" /></Button>
            <Button size="sm" variant="ghost" aria-label="Forward" onClick={() => act.run({ action: "forward", text: "", keys: "", url: "", direction: "down" })}><ArrowRight className="h-3.5 w-3.5" /></Button>
            <Input className="text-xs" value={address || session.url} onChange={(e) => setAddress(e.target.value)} onFocus={() => setAddress(session.url)}
              onKeyDown={(e) => { if (e.key === "Enter" && address.startsWith("http")) { act.run({ action: "navigate", url: address, text: "", keys: "", direction: "down" }); setAddress(""); } }} aria-label="Address" />
          </div>
          <div className={cn("relative overflow-hidden rounded-lg border bg-black", focused ? "border-primary ring-2 ring-primary/40" : "border-border")} onWheel={wheel}>
            {frameError && <p className="absolute inset-x-0 top-2 text-center text-xs text-white/80">The screen is not reachable right now…</p>}
            <img ref={img} src={`/admin-api/nia/browser/sessions/${session.id}/frame?t=${tick}`} alt="Nia's browser screen" width={WIDTH} height={HEIGHT} draggable={false}
              className={cn("block h-auto w-full select-none", compact && "max-h-[50vh] object-contain")} style={{ cursor: "text" }} onClick={click}
              onError={() => setFrameError(true)} onLoad={() => setFrameError(false)} />
            <input ref={keys} aria-label="Type into Nia's browser" autoComplete="off" autoCorrect="off" autoCapitalize="off" spellCheck={false}
              className="pointer-events-none absolute bottom-0 left-0 h-px w-px opacity-0" style={{ fontSize: 16 }}
              onFocus={() => setFocused(true)} onBlur={() => { flush(); setFocused(false); }} onKeyDown={keyDown}
              onChange={(e) => typedInto(e.target.value)} />
          </div>
          <p className="mt-1 text-xs text-muted">{focused ? "Typing goes straight into the browser — Enter, Tab, arrows, Ctrl+A and paste too. Click outside the screen to stop." : "Click or tap into the screen to use it yourself: fields, links, typing, scrolling."} What you type is never kept.</p>
          {last && <p className="mt-2 text-xs text-muted">Now: {last.text}</p>}
        </>
      ) : session.result && <p className="whitespace-pre-wrap rounded-lg bg-raised p-3 text-[13px] text-fg">{session.result}</p>}
      {!compact && <Steps session={session} />}
      <p className="mt-2 text-[11px] text-muted">{session.turns} steps · {session.cost_usd.toFixed(2)} USD · started {ago(session.created_at)}</p>
    </Card>
  );
}

function Steps({ session }: { session: Session }) {
  if (session.steps.length === 0) return null;
  return (
    <details className="mt-3" open>
      <summary className="cursor-pointer text-xs font-semibold text-fg">What happened</summary>
      <ol className="mt-1 max-h-72 space-y-0.5 overflow-auto text-xs">
        {session.steps.map((s) => (
          <li key={s.id} className={cn(s.kind === "ask" && "font-medium text-warning", s.kind === "answer" && "text-primary", s.kind === "owner" && "text-fg")}>
            <span className="text-muted">{new Date(s.at).toLocaleTimeString()} </span>{s.text}
            {s.has_screenshot && <> · <a className="text-primary hover:underline" href={`/admin-api/nia/browser/steps/${s.id}/screenshot`} target="_blank" rel="noreferrer">screen</a></>}
          </li>
        ))}
      </ol>
    </details>
  );
}

function StartCard({ disabled }: { disabled: string }) {
  const client = useQueryClient();
  const [goal, setGoal] = useState("");
  const [url, setUrl] = useState("");
  const start = useAction(async () => unwrap(await api.POST("/admin-api/nia/browser/sessions", { body: { goal, url } })),
    { done: () => { setGoal(""); setUrl(""); void client.invalidateQueries({ queryKey: ["nia-browser"] }); } });
  return (
    <Card title="Give Nia a task in the browser">
      {disabled && <div className="mb-2"><Alert tone="warning">{disabled}</Alert></div>}
      {start.notice?.tone === "danger" && <div className="mb-2"><Alert>{start.notice.text}</Alert></div>}
      <Field label="What Nia should do" hint="As you would tell a person, with every detail. You can also just tell Nia by voice or in the chat."><Textarea rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} /></Field>
      <div className="mt-2">
        <Field label="Start at (optional)"><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" /></Field>
      </div>
      <Button className="mt-3" variant="primary" disabled={goal.trim().length < 5 || Boolean(disabled)} loading={start.busy} onClick={() => start.run(undefined)}><Play className="h-3.5 w-3.5" aria-hidden />Start</Button>
    </Card>
  );
}

function RulesCard({ rules }: { rules: Rules }) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<Rules>(rules);
  const [blocked, setBlocked] = useState((rules.blocked_sites ?? []).join("\n"));
  const [keep, setKeep] = useState((rules.keep_signed_in ?? []).join("\n"));
  useEffect(() => { setDraft(rules); setBlocked((rules.blocked_sites ?? []).join("\n")); setKeep((rules.keep_signed_in ?? []).join("\n")); }, [rules]);
  const lines = (v: string) => v.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
  const save = useAction(async () => unwrap(await api.PUT("/admin-api/nia/browser/rules", { body: { ...draft, blocked_sites: lines(blocked), keep_signed_in: lines(keep) } })),
    { done: () => { void client.invalidateQueries({ queryKey: ["nia-browser"] }); return "Saved."; } });
  return (
    <Card title="Rules" actions={<Button size="sm" variant="primary" loading={save.busy} onClick={() => save.run(undefined)}>Save</Button>}>
      {save.notice && <div className="mb-3"><Alert tone={save.notice.tone}>{save.notice.text}</Alert></div>}
      <Toggle tone="primary" label="Nia may use the browser" description="Off stops a session at its next step." checked={draft.enabled ?? false} onChange={(enabled) => setDraft({ ...draft, enabled })} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <NumberSetting label="Most minutes per session" hint="Empty: no limit." min={1} value={draft.max_minutes} onChange={(v) => setDraft({ ...draft, max_minutes: v })} />
        <NumberSetting label="Most steps per session" hint="Empty: no limit." min={1} value={draft.max_turns} onChange={(v) => setDraft({ ...draft, max_turns: v })} />
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Field label="Sites Nia may never open" hint="One per line, e.g. your bank. Their subdomains too."><Textarea rows={4} value={blocked} onChange={(e) => setBlocked(e.target.value)} /></Field>
        <Field label="Sites that stay signed in" hint="After a session, you stay signed in only on these. Everything else is forgotten."><Textarea rows={4} value={keep} onChange={(e) => setKeep(e.target.value)} /></Field>
      </div>
      <p className="mt-2 text-xs text-muted">Always, whatever the rules: Nia asks before anything that sends, submits, buys, pays, signs, accepts, deletes, uploads or signs in; passwords are typed by you; Nia works only while you are watching here.</p>
    </Card>
  );
}

export function BrowserPage({ owner }: { owner: boolean }) {
  const { data, error } = useBrowser();
  if (!owner) return <Alert>Only the owner uses Nia's browser.</Alert>;
  if (error) return <Alert>{errorMessage(error)}</Alert>;
  if (!data) return null;
  const off = !data.rules.enabled ? "Switch Nia's browser on below first." : "";
  return (
    <>
      <Link to="/nia" className="mb-3 inline-flex items-center gap-1 text-[13px] text-muted hover:text-fg"><ArrowLeft className="h-3.5 w-3.5" /> Nia</Link>
      <PageTitle title="Nia's browser" description={`Nia works in a real browser while you watch, asks before anything that counts, and hands passwords to you. Cost today: ${data.spent_today_usd.toFixed(2)} USD.`} />
      <div className="space-y-4">
        {data.active ? <LiveBrowser session={data.active} /> : <StartCard disabled={off} />}
        <Card title="Earlier sessions">
          {data.recent.length === 0 ? <Empty>None yet.</Empty> : (
            <ul className="space-y-2 text-[13px]">{data.recent.filter((r) => r.id !== data.active?.id).map((r) => (
              <li key={r.id}><Badge tone={(STATUS[r.status] ?? ["", "neutral"])[1]}>{(STATUS[r.status] ?? [r.status])[0]}</Badge> <span className="text-fg">{r.goal}</span> <span className="text-xs text-muted">· {ago(r.created_at)} · {r.cost_usd.toFixed(2)} USD</span>
                {r.result && <p className="mt-0.5 whitespace-pre-wrap text-xs text-muted">{r.result}</p>}</li>
            ))}</ul>
          )}
        </Card>
        <RulesCard rules={data.rules} />
      </div>
    </>
  );
}
