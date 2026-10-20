/* Meetings with Nia (agent team, stage A2c): you schedule them; Nia prepares the deck from live
   data before each one and presents it by voice — showing slides, opening admin pages with
   filters and pointing at what it talks about. You can interrupt at any time. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CalendarPlus, ChevronLeft, ChevronRight, Download, Mic, MicOff, PhoneOff, Presentation, Sparkles, X } from "lucide-react";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorMessage, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { Funnel, Sparkline } from "../insights/charts";
import { fmt } from "../insights/format";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageTitle, StatusBadge, Textarea, Toggle, cn, when } from "../ui";
import { AssistantAudio } from "./audio";
import { act, snapshot } from "./pageAgent";

type Slide = { kind: string; title: string; talk: string; bullets: string[]; data: Record<string, unknown> };
type Meeting = Schemas["NiaMeetingDetail"];

// --- one slide -------------------------------------------------------------------------------------------
function List({ rows }: { rows: string[] }) {
  return <ul className="mt-4 space-y-2 text-lg">{rows.map((r, i) => <li key={i} className="flex gap-3"><span className="text-primary">•</span><span>{r}</span></li>)}</ul>;
}

export function SlideView({ slide, big = false }: { slide: Slide; big?: boolean }) {
  const d = slide.data ?? {};
  return (
    <div className={cn("flex h-full flex-col", big ? "p-10" : "p-5")}>
      <h2 className={cn("font-semibold tracking-tight text-fg", big ? "text-4xl" : "text-lg")}>{slide.title}</h2>
      {slide.kind === "metric" && (
        <div className="mt-6 grid gap-4">
          <p className={cn("text-muted", big ? "text-xl" : "text-sm")}>{String(d.label)} · last {String(d.days)} days</p>
          <p className={cn("font-semibold text-fg", big ? "text-7xl" : "text-3xl")}>{fmt(d.value as number | null, String(d.unit))}</p>
          <p className="text-muted">before: {fmt(d.previous as number | null, String(d.unit))}</p>
          <Sparkline values={(d.series as number[]) ?? []} height={big ? 140 : 50} />
        </div>
      )}
      {slide.kind === "funnel" && <div className="mt-6"><Funnel steps={(d.steps as { step: string; value: number }[]) ?? []} label={(k) => k.replace(/_/g, " ")} /></div>}
      {slide.kind === "opportunities" && <List rows={Object.entries((d.published_by_category as Record<string, number>) ?? {}).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`)} />}
      {slide.kind === "approvals" && <List rows={((d.waiting as { title: string; agent: string }[]) ?? []).slice(0, 8).map((a) => `${a.title} (${a.agent})`)} />}
      {slide.kind === "agents" && <List rows={((d.agents as { name: string; status: string }[]) ?? []).map((a) => `${a.name}: ${a.status}`)} />}
      {slide.kind === "page" && <p className="mt-6 text-muted">Live page: {String(d.path)}{d.query ? `?${String(d.query)}` : ""}</p>}
      {slide.bullets?.length > 0 && <List rows={slide.bullets} />}
    </div>
  );
}

// --- presenting ------------------------------------------------------------------------------------------
type Presenting = { meeting: Meeting; slide: number; mode: "deck" | "page"; caption: string; status: "connecting" | "live"; muted: boolean; message: string };
const PresenterContext = createContext<{ start: (meetingId: number) => Promise<void>; state: Presenting | null } | null>(null);

export function usePresenter() {
  const value = useContext(PresenterContext);
  if (!value) throw new Error("usePresenter outside PresenterProvider");
  return value;
}

/** Point at a part of the open page (its data-nia marker); "" clears it. */
export function point(marker: string) {
  document.querySelectorAll(".nia-highlight").forEach((el) => el.classList.remove("nia-highlight"));
  if (!marker) return;
  let tries = 0;
  const find = () => {
    const el = document.querySelector(`[data-nia="${CSS.escape(marker)}"]`);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.add("nia-highlight"); return; }
    if (++tries < 20) setTimeout(find, 250);
  };
  find();
}

export function PresenterProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [state, setState] = useState<Presenting | null>(null);
  const socket = useRef<WebSocket | null>(null);
  const audio = useRef<AssistantAudio | null>(null);
  const end = (message = "") => {
    try { socket.current?.send(JSON.stringify({ type: "end" })); } catch { /* closed */ }
    socket.current?.close(); audio.current?.close();
    socket.current = null; audio.current = null;
    point("");
    setState((s) => (s && message ? { ...s, message } : null));
    if (!message) setState(null);
    void client.invalidateQueries({ queryKey: ["nia-meetings"] });
  };
  async function start(meetingId: number) {
    const meeting = unwrap(await api.GET("/admin-api/nia/meetings/{meeting_id}", { params: { path: { meeting_id: meetingId } } }));
    setState({ meeting, slide: 0, mode: "deck", caption: "", status: "connecting", muted: false, message: "" });
    try {
      const lease = unwrap(await api.POST("/admin-api/nia/meetings/{meeting_id}/present", { params: { path: { meeting_id: meetingId } } }));
      const sound = new AssistantAudio(() => {}, () => {});
      audio.current = sound;
      const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${lease.path}?lease=${encodeURIComponent(lease.token)}`);
      ws.binaryType = "arraybuffer";
      socket.current = ws;
      ws.onmessage = (event) => {
        if (typeof event.data !== "string") { try { sound.play(event.data as ArrayBuffer); } catch { end("Audio stopped."); } return; }
        const data = JSON.parse(event.data) as { type: string; role?: string; text?: string; message?: string; action?: string; args?: Record<string, unknown> };
        if (data.type === "ready") {
          setState((s) => s && { ...s, status: "live" });
          void sound.start((pcm) => { if (ws.readyState === WebSocket.OPEN) ws.send(pcm); }).catch((e: unknown) => end(errorMessage(e)));
        } else if (data.type === "transcript" && data.role === "nia" && data.text) {
          setState((s) => s && { ...s, caption: (s.caption + data.text).slice(-220) });
        } else if (data.type === "turn_complete") {
          sound.finishResponse();
          setState((s) => s && { ...s, caption: "" });
        } else if (data.type === "interrupted") {
          sound.stopOutput();
        } else if (data.type === "control" && data.args) {
          const args = data.args;
          if (data.action === "show_slide") {
            point("");
            setState((s) => s && { ...s, mode: "deck", slide: Math.max(0, Math.min(s.meeting.deck.length - 1, Number(args.number) - 1)) });
          } else if (data.action === "open_page") {
            const query = args.query ? `?${String(args.query)}` : "";
            navigate(`${String(args.path)}${query}`);
            setState((s) => s && { ...s, mode: "page" });
            point(String(args.highlight ?? ""));
          } else if (data.action === "highlight") {
            setState((s) => s && { ...s, mode: "page" });
            point(String(args.marker ?? ""));
          } else if (data.action === "read_screen") {
            ws.send(JSON.stringify({ type: "screen", id: String(args.id ?? ""), ...snapshot() }));
          } else if (data.action === "page_action") {
            setState((s) => s && { ...s, mode: "page" });
            void act(String(args.action ?? ""), Number(args.ref), String(args.value ?? "")).then((result) => {
              if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "screen", id: String(args.id ?? ""), ...result }));
            });
          }
        } else if (data.type === "ended" || data.type === "error") {
          end(data.message ?? "The meeting ended.");
        }
      };
      ws.onclose = () => { if (socket.current === ws) end(); };
    } catch (e) {
      setState((s) => s && { ...s, message: errorMessage(e) });
    }
  }
  useEffect(() => () => { socket.current?.close(); audio.current?.close(); }, []);
  const s = state;
  return (
    <PresenterContext.Provider value={{ start, state }}>
      {children}
      {s && s.mode === "deck" && (
        <div className="fixed inset-0 z-50 flex flex-col bg-bg">
          <div className="flex items-center gap-3 border-b border-border px-5 py-3">
            <Sparkles className="h-4 w-4 text-primary" aria-hidden />
            <span className="text-[13px] font-semibold">{s.meeting.title}</span>
            <span className="text-xs text-muted">Slide {s.slide + 1} of {s.meeting.deck.length}</span>
            <span className="ml-auto text-xs text-muted">{s.status === "connecting" ? "Connecting…" : "Nia is presenting — just talk to interrupt"}</span>
            <Controls s={s} setState={setState} audio={audio} end={end} />
          </div>
          {s.message && <div className="p-3"><Alert>{s.message}</Alert></div>}
          <div className="min-h-0 flex-1 overflow-auto">{s.meeting.deck[s.slide] && <SlideView slide={s.meeting.deck[s.slide] as Slide} big />}</div>
          {s.caption && <p className="border-t border-border px-6 py-3 text-center text-[15px] text-muted">{s.caption}</p>}
        </div>
      )}
      {s && s.mode === "page" && (
        <div className="fixed inset-x-2 bottom-3 z-50 flex items-center gap-3 rounded-[var(--radius-card)] border border-border bg-surface px-4 py-2 shadow-[var(--shadow)] sm:inset-x-auto sm:left-1/2 sm:w-[40rem] sm:-translate-x-1/2">
          <Sparkles className="h-4 w-4 shrink-0 text-primary" aria-hidden />
          <span className="min-w-0 flex-1 truncate text-[13px] text-muted">{s.caption || "Nia is showing this page."}</span>
          <Button size="sm" onClick={() => setState({ ...s, mode: "deck" })}>Back to the deck</Button>
          <Controls s={s} setState={setState} audio={audio} end={end} />
        </div>
      )}
    </PresenterContext.Provider>
  );
}

function Controls({ s, setState, audio, end }: { s: Presenting; setState: (v: Presenting) => void; audio: { current: AssistantAudio | null }; end: (m?: string) => void }) {
  return (
    <div className="flex items-center gap-1">
      {s.mode === "deck" && <>
        <Button size="sm" variant="ghost" aria-label="Previous slide" disabled={s.slide === 0} onClick={() => setState({ ...s, slide: s.slide - 1 })}><ChevronLeft className="h-4 w-4" aria-hidden /></Button>
        <Button size="sm" variant="ghost" aria-label="Next slide" disabled={s.slide >= s.meeting.deck.length - 1} onClick={() => setState({ ...s, slide: s.slide + 1 })}><ChevronRight className="h-4 w-4" aria-hidden /></Button>
      </>}
      <Button size="sm" variant="ghost" aria-label={s.muted ? "Unmute" : "Mute"} onClick={() => { audio.current?.mute(!s.muted); setState({ ...s, muted: !s.muted }); }}>{s.muted ? <MicOff className="h-4 w-4" aria-hidden /> : <Mic className="h-4 w-4" aria-hidden />}</Button>
      <Button size="sm" variant="danger" onClick={() => end()}><PhoneOff className="h-3.5 w-3.5" aria-hidden />End</Button>
      {s.message && <Button size="sm" variant="ghost" aria-label="Close" onClick={() => end()}><X className="h-4 w-4" aria-hidden /></Button>}
    </div>
  );
}

// --- scheduling ------------------------------------------------------------------------------------------
type Draft = { title: string; when: string; minutes: string; prepare: string; weekly: boolean; agenda: string };
const EMPTY: Draft = { title: "", when: "", minutes: "", prepare: "", weekly: false, agenda: "" };

export function MeetingsCard({ owner }: { owner: boolean }) {
  const { data } = useQuery({ queryKey: ["nia-meetings"], queryFn: async () => unwrap(await api.GET("/admin-api/nia/meetings")), refetchInterval: 60_000 });
  const presenter = usePresenter();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [open, setOpen] = useState(false);
  const add = useAction(async () => unwrap(await api.POST("/admin-api/nia/meetings", { body: {
    title: draft.title, starts_at: new Date(draft.when).toISOString(), minutes: Number(draft.minutes), prepare_hours: Number(draft.prepare), repeat_weekly: draft.weekly, agenda: draft.agenda,
  } })), { invalidate: [["nia-meetings"]], done: () => { setDraft(EMPTY); setOpen(false); return "Scheduled."; } });
  const prepare = useAction(async (id: number) => unwrap(await api.POST("/admin-api/nia/meetings/{meeting_id}/prepare", { params: { path: { meeting_id: id } } })),
    { invalidate: [["nia-meetings"]], done: () => "Nia is preparing the deck; it appears here in a few minutes." });
  const complete = draft.title && draft.when && draft.minutes && draft.prepare !== "";
  const upcoming = (data ?? []).filter((m) => m.status !== "held").reverse();
  const past = (data ?? []).filter((m) => m.status === "held").slice(0, 5);
  const notice = add.notice ?? prepare.notice;
  return (
    <Card title="Meetings" actions={owner && <Button size="sm" onClick={() => setOpen(!open)}><CalendarPlus className="h-3.5 w-3.5" aria-hidden />Schedule</Button>}>
      {notice && <div className="mb-2"><Alert tone={notice.tone}>{notice.text}</Alert></div>}
      {open && owner && (
        <div className="mb-3 grid gap-2 rounded-[var(--radius-control)] border border-border p-3">
          <Field label="Title"><Input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></Field>
          <div className="grid gap-2 sm:grid-cols-3">
            <Field label="When"><Input type="datetime-local" value={draft.when} onChange={(e) => setDraft({ ...draft, when: e.target.value })} /></Field>
            <Field label="Minutes"><Input type="number" min={5} placeholder="Not set" value={draft.minutes} onChange={(e) => setDraft({ ...draft, minutes: e.target.value })} /></Field>
            <Field label="Prepare hours before"><Input type="number" min={0} placeholder="Not set" value={draft.prepare} onChange={(e) => setDraft({ ...draft, prepare: e.target.value })} /></Field>
          </div>
          <Field label="Agenda (optional)"><Textarea rows={2} value={draft.agenda} onChange={(e) => setDraft({ ...draft, agenda: e.target.value })} /></Field>
          <Toggle tone="primary" label="Every week at this time" checked={draft.weekly} onChange={(weekly) => setDraft({ ...draft, weekly })} />
          <div><Button size="sm" variant="primary" disabled={!complete} loading={add.busy} onClick={() => add.run(undefined)}>Schedule</Button></div>
        </div>
      )}
      {upcoming.length === 0 && !open ? <p className="text-[13px] text-muted">No meeting scheduled. You choose when; Nia prepares a deck before each one.</p> : (
        <ul className="space-y-2">
          {upcoming.map((m) => (
            <li key={m.id} className="rounded-[var(--radius-control)] border border-border p-2.5 text-[13px]">
              <div className="flex flex-wrap items-center gap-2">
                <Link to={`/nia/meetings/${m.id}`} className="font-medium text-fg hover:text-primary">{m.title}</Link>
                <StatusBadge status={m.status} />{m.repeat_weekly && <Badge>weekly</Badge>}
              </div>
              <p className="text-xs text-muted">{when(m.starts_at)} · {m.minutes} min · deck {m.slides ? `${m.slides} slides` : `prepared ${m.prepare_hours} h before`}</p>
              {owner && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {m.slides > 0 && <Button size="sm" variant="primary" onClick={() => void presenter.start(m.id)}><Presentation className="h-3.5 w-3.5" aria-hidden />Present</Button>}
                  <Button size="sm" variant="ghost" loading={prepare.busy && prepare.variables === m.id} onClick={() => prepare.run(m.id)}>{m.slides ? "Prepare again" : "Prepare now"}</Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {past.length > 0 && (
        <details className="mt-3 text-xs"><summary className="cursor-pointer text-muted">Earlier meetings</summary>
          <ul className="mt-1 space-y-1">{past.map((m) => <li key={m.id}><Link to={`/nia/meetings/${m.id}`} className="text-primary">{m.title}</Link> · {when(m.starts_at)}{m.minutes_text ? " · minutes" : ""}</li>)}</ul>
        </details>
      )}
    </Card>
  );
}

export function MeetingPage({ owner }: { owner: boolean }) {
  const id = Number(useParams().id);
  const presenter = usePresenter();
  const { data, error } = useQuery({ queryKey: ["nia-meeting", id], queryFn: async () => unwrap(await api.GET("/admin-api/nia/meetings/{meeting_id}", { params: { path: { meeting_id: id } } })) });
  if (error) return <Alert>{error.message}</Alert>;
  if (!data) return null;
  return (
    <>
      <Link to="/nia" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg"><ArrowLeft className="h-3.5 w-3.5" aria-hidden />Nia</Link>
      <PageTitle title={data.title} description={`${when(data.starts_at)} · ${data.minutes} minutes${data.agenda ? ` · ${data.agenda}` : ""}`}
        actions={<>
          <StatusBadge status={data.status} />
          {data.deck.length > 0 && <a href={`/admin-api/nia/meetings/${id}/pdf`} download><Button><Download className="h-3.5 w-3.5" aria-hidden />PDF</Button></a>}
          {owner && data.deck.length > 0 && data.status !== "held" && <Button variant="primary" onClick={() => void presenter.start(id)}><Presentation className="h-3.5 w-3.5" aria-hidden />Present</Button>}
        </>} />
      {data.minutes_text && <Card title="Minutes" className="mb-4"><p className="whitespace-pre-wrap text-[13px]">{data.minutes_text}</p></Card>}
      {data.deck.length === 0 ? <Card><Empty>No deck yet. Nia prepares it {data.prepare_hours} hours before the meeting, or now with “Prepare now” on the Nia page.</Empty></Card> : (
        <div className="grid gap-4 lg:grid-cols-2">
          {(data.deck as Slide[]).map((slide, i) => (
            <Card key={i} padded={false} title={<span className="text-xs text-muted">Slide {i + 1} · {slide.kind}</span>}>
              <SlideView slide={slide} />
              {slide.talk && <p className="border-t border-border px-5 py-3 text-xs text-muted">Nia says: {slide.talk}</p>}
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
