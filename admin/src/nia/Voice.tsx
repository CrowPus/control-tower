/* Talking to Nia (stage A2b): press Talk, speak, listen. The words land in the conversation;
   the voice is your setting (no daily limit). The conversation belongs to the whole
   admin (VoiceProvider), so it goes on while Nia opens pages for you and points at things. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Mic, MicOff, PhoneOff } from "lucide-react";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api, errorMessage, unwrap } from "../api";
import { useAction } from "../hooks";
import { Alert, Button, Card, Field, Input, Select } from "../ui";
import { AssistantAudio } from "./audio";
import { NiaAvatar } from "./Avatar";
import { answer as answerChange, subscribe, waitingNow, type Pending } from "./changeGate";
import { point } from "./Meetings";
import { act, snapshot } from "./pageAgent";

export function useNiaVoice() {
  return useQuery({ queryKey: ["nia-voice"], queryFn: async () => unwrap(await api.GET("/admin-api/nia/voice")) });
}

export function VoiceSettingsCard({ owner }: { owner: boolean }) {
  const { data } = useNiaVoice();
  const [voice, setVoice] = useState("");
  useEffect(() => { if (data) setVoice(data.voice ?? ""); }, [data]);
  const save = useAction(async () => unwrap(await api.PUT("/admin-api/nia/voice", { body: { voice: voice || null } })),
    { invalidate: [["nia-voice"]], done: () => "Saved." });
  if (!data) return null;
  return (
    <Card title="Nia's voice">
      {save.notice && <div className="mb-2"><Alert tone={save.notice.tone}>{save.notice.text}</Alert></div>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Voice"><Select disabled={!owner} value={voice} onChange={(e) => setVoice(e.target.value)}><option value="">Not set</option>{data.voices.map((v) => <option key={v} value={v}>{v}</option>)}</Select></Field>
        <Field label="Talked today"><p className="py-1.5 text-[13px] text-fg">{data.minutes_today} minutes · no daily limit</p></Field>
      </div>
      {owner && <div className="mt-2"><Button size="sm" variant="primary" loading={save.busy} onClick={() => save.run(undefined)}>Save</Button></div>}
    </Card>
  );
}

type Status = "idle" | "connecting" | "live" | "ended";

/** Sent when Nia opens a page for you: the chat panel steps aside so you see it. */
export const SHOWING_PAGE = "nia-showing-page";

type Voice = {
  status: Status;
  speaking: boolean;
  conversationId: number | null;
  caption: { role: string; text: string } | null;
  message: string;
  muted: boolean;
  start: (conversationId: number) => Promise<void>;
  stop: () => void;
  toggleMute: () => void;
  /** Typed words into the live conversation (Nia answers by voice and can work the page). */
  say: (text: string) => boolean;
};

const VoiceContext = createContext<Voice | null>(null);

export function useVoice(): Voice {
  const value = useContext(VoiceContext);
  if (!value) throw new Error("useVoice outside VoiceProvider");
  return value;
}

/** One voice conversation for the whole admin: it survives moving between pages, and Nia's
 * open_page / highlight move your screen. */
export function VoiceProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [status, setStatus] = useState<Status>("idle");
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [caption, setCaption] = useState<{ role: string; text: string } | null>(null);
  const [message, setMessage] = useState("");
  const [muted, setMuted] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const audio = useRef<AssistantAudio | null>(null);
  const current = useRef<number | null>(null);
  const stop = () => {
    try { socket.current?.send(JSON.stringify({ type: "end" })); } catch { /* already closed */ }
    socket.current?.close();
    audio.current?.close();
    socket.current = null;
    audio.current = null;
    point("");
    setStatus("ended");
    setSpeaking(false);
    setCaption(null);
    setMuted(false);
    if (current.current !== null) void client.invalidateQueries({ queryKey: ["nia-conversation", current.current] });
    void client.invalidateQueries({ queryKey: ["nia-voice"] });
  };
  useEffect(() => () => { socket.current?.close(); audio.current?.close(); }, []);
  async function start(id: number) {
    if (socket.current) stop();
    current.current = id;
    setConversationId(id);
    setMessage("");
    setStatus("connecting");
    try {
      const lease = unwrap(await api.POST("/admin-api/nia/voice/lease", { body: { conversation_id: id } }));
      const sound = new AssistantAudio(setSpeaking, () => {});
      audio.current = sound;
      const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${lease.path}?lease=${encodeURIComponent(lease.token)}`;
      const ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      socket.current = ws;
      ws.onmessage = (event) => {
        if (typeof event.data !== "string") { try { sound.play(event.data as ArrayBuffer); } catch { stop(); } return; }
        const data = JSON.parse(event.data) as { type: string; role?: string; text?: string; message?: string; action?: string; args?: Record<string, unknown> };
        if (data.type === "ready") {
          setStatus("live");
          void sound.start((pcm) => { if (ws.readyState === WebSocket.OPEN) ws.send(pcm); }).catch((e: unknown) => { setMessage(errorMessage(e)); stop(); });
        } else if (data.type === "transcript" && data.text) {
          setCaption((c) => (c && c.role === data.role ? { role: c.role, text: (c.text + data.text).slice(-220) } : { role: data.role ?? "", text: data.text ?? "" }));
        } else if (data.type === "turn_complete") {
          sound.finishResponse();
          void client.invalidateQueries({ queryKey: ["nia-conversation", id] });
        } else if (data.type === "interrupted") {
          sound.stopOutput();
        } else if (data.type === "control" && data.args) {
          // Nia shows you a page or points at a part of it.
          if (data.action === "open_page" && typeof data.args.path === "string" && data.args.path.startsWith("/")) {
            navigate(`${data.args.path}${data.args.query ? `?${String(data.args.query)}` : ""}`);
            window.dispatchEvent(new Event(SHOWING_PAGE));
            point(String(data.args.highlight ?? ""));
          } else if (data.action === "highlight") {
            point(String(data.args.marker ?? ""));
          } else if (data.action === "read_screen") {
            // Nia reads what you see, with every control (wait a moment after a page change so it has loaded).
            const asked = String(data.args.id ?? "");
            setTimeout(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "screen", id: asked, ...snapshot() })); }, 600);
          } else if (data.action === "page_action") {
            // Nia clicks, types, ticks or chooses on the page; a change waits for your Allow.
            const asked = String(data.args.id ?? "");
            void act(String(data.args.action ?? ""), Number(data.args.ref), String(data.args.value ?? "")).then((result) => {
              const held = waitingNow().length > 0;
              if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "screen", id: asked, ...result, ...(held ? { waiting_for_owner: "A change waits for the owner's Allow on screen." } : {}) }));
            });
          }
        } else if (data.type === "ended" || data.type === "error") {
          setMessage(data.message ?? "");
          stop();
        }
      };
      ws.onclose = () => { if (socket.current === ws) stop(); };
    } catch (e) {
      setMessage(errorMessage(e));
      setStatus("idle");
    }
  }
  const toggleMute = () => { const next = !muted; setMuted(next); audio.current?.mute(next); };
  const say = (text: string) => {
    const ws = socket.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || status !== "live") return false;
    ws.send(JSON.stringify({ type: "text", text }));
    return true;
  };
  const value: Voice = { status, speaking, conversationId, caption, message, muted, start, stop, toggleMute, say };
  return <VoiceContext.Provider value={value}>{children}<AllowBar /><LiveVoiceBar /></VoiceContext.Provider>;
}

function LiveControls() {
  const voice = useVoice();
  return (
    <>
      <Button size="sm" variant="ghost" onClick={voice.toggleMute} aria-label={voice.muted ? "Unmute" : "Mute"}>{voice.muted ? <MicOff className="h-4 w-4" aria-hidden /> : <Mic className="h-4 w-4" aria-hidden />}</Button>
      <Button size="sm" variant="danger" onClick={voice.stop}><PhoneOff className="h-3.5 w-3.5" aria-hidden />End</Button>
    </>
  );
}

/** While you talk: a small bar on every page, so you can mute or end wherever Nia takes you. */
function LiveVoiceBar() {
  const voice = useVoice();
  if (voice.status !== "live" && voice.status !== "connecting") return null;
  return (
    <div className="fixed bottom-5 left-1/2 z-50 flex w-[min(34rem,calc(100vw-6rem))] -translate-x-1/2 items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 shadow-[var(--shadow)]">
      <NiaAvatar state={voice.speaking ? "speaking" : "idle"} size="bar" />
      <span className="flex shrink-0 items-center gap-1.5 text-[13px] text-primary"><span className="h-2 w-2 animate-pulse rounded-full bg-primary" />{voice.status === "connecting" ? "Connecting…" : "Nia is listening"}</span>
      {voice.caption && <span className="min-w-0 flex-1 truncate text-xs text-muted">{voice.caption.role === "owner" ? "You: " : "Nia: "}{voice.caption.text}</span>}
      <span className="ml-auto flex shrink-0 items-center gap-1"><LiveControls /></span>
    </div>
  );
}

/** Nia wants to save a change: nothing happens until you press Allow (nia/changeGate.ts). */
function AllowBar() {
  const [pending, setPending] = useState<Pending[]>(waitingNow());
  useEffect(() => subscribe(setPending), []);
  if (!pending.length) return null;
  const first = pending[0];
  return (
    <div role="alertdialog" aria-label="Nia wants to save a change" data-nia-skip
      className="fixed bottom-20 left-1/2 z-50 flex w-[min(34rem,calc(100vw-2rem))] -translate-x-1/2 flex-wrap items-center gap-2 rounded-[var(--radius-card)] border-2 border-warning bg-surface px-4 py-3 shadow-[var(--shadow)]">
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-semibold text-fg">Nia wants to save a change on this page</p>
        <p className="truncate font-mono text-[11px] text-muted">{first.method} {first.path}{pending.length > 1 ? ` (+${pending.length - 1})` : ""}</p>
      </div>
      <Button size="sm" variant="ghost" onClick={() => answerChange(first.id, false)}>Cancel</Button>
      <Button size="sm" variant="primary" onClick={() => answerChange(first.id, true)}>Allow</Button>
    </div>
  );
}

/** The Talk button for one conversation (the live conversation itself is VoiceProvider's). */
export function TalkButton({ conversationId }: { conversationId: number }) {
  const settings = useNiaVoice();
  const voice = useVoice();
  const problem = settings.data?.problem ?? "";
  const here = voice.conversationId === conversationId;
  if (here && (voice.status === "live" || voice.status === "connecting")) {
    return <div className="flex flex-1 items-center gap-2 text-[13px] text-primary"><span className="h-2 w-2 animate-pulse rounded-full bg-primary" />Talking — the bar at the bottom stays on every page<span className="ml-auto flex gap-1"><LiveControls /></span></div>;
  }
  const message = here ? voice.message : "";
  return (
    <div className="flex items-center gap-2">
      <Button size="sm" variant="primary" disabled={!!problem} title={problem || undefined} onClick={() => void voice.start(conversationId)}><Mic className="h-3.5 w-3.5" aria-hidden />Talk</Button>
      {(message || problem) && <span className="text-xs text-muted">{message || problem}</span>}
    </div>
  );
}
