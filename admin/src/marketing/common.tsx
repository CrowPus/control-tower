/* Admin → Marketing: shared pieces — network names and colours, a post's
   picture strip, the video script as the owner films it, and a video concept as the owner approves it
  . */
import type { Schemas } from "../api";
import { cn } from "../ui";

export type Overview = Schemas["MarketingOut"];
export type Post = Schemas["SocialPostOut"];
export type Media = Schemas["MediaOut"];
export type MadeVideo = Schemas["VideoOut"];

export const NETWORK: Record<string, { name: string; color: string }> = {
  facebook: { name: "Facebook", color: "#1877F2" },
  instagram: { name: "Instagram", color: "#C13584" },
  linkedin: { name: "LinkedIn", color: "#0A66C2" },
  youtube: { name: "YouTube", color: "#FF0000" },
  tiktok: { name: "TikTok", color: "#010101" },
  x: { name: "X", color: "#111827" },
};

const AGENT_NAMES: Record<string, string> = {
  marketing_lead: "Marketing Lead", content_community: "Social Media Manager", graphic_designer: "Graphic Designer",
};

export function agentName(key: string): string {
  if (!key) return "you";
  if (key.startsWith("admin:")) return "you";
  return AGENT_NAMES[key] ?? key.replace(/_/g, " ");
}

export const STATUS: Record<string, { label: string; tone: "neutral" | "primary" | "success" | "warning" | "danger" | "info" }> = {
  scheduled: { label: "Scheduled", tone: "info" },
  publishing: { label: "Publishing…", tone: "primary" },
  posted: { label: "Posted", tone: "success" },
  needs_video: { label: "Needs your video", tone: "warning" },
  ready: { label: "Not sent yet", tone: "warning" },
  failed: { label: "Failed", tone: "danger" },
  discarded: { label: "Discarded", tone: "neutral" },
};

export function NetworkChip({ network, small }: { network: string; small?: boolean }) {
  const n = NETWORK[network] ?? { name: network, color: "#6b7280" };
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full font-medium text-white", small ? "px-1.5 py-0 text-[10px]" : "px-2 py-0.5 text-[11px]")} style={{ background: n.color }}>
      {n.name}
    </span>
  );
}

export function Pictures({ ids, size = 72 }: { ids: number[]; size?: number }) {
  if (!ids.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {ids.map((id) => (
        <a key={id} href={`/admin-api/marketing/media/${id}/file`} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-md border border-border bg-raised" style={{ width: size, height: size }}>
          <img src={`/admin-api/marketing/media/${id}/file`} alt="" className="h-full w-full object-cover" loading="lazy" />
        </a>
      ))}
    </div>
  );
}

type Scene = { seconds?: string; shot?: string; on_screen_text?: string; voiceover?: string };

export function VideoScript({ script }: { script: Record<string, unknown> }) {
  if (!script || !Object.keys(script).length) return null;
  const scenes = (Array.isArray(script.scenes) ? script.scenes : []) as Scene[];
  return (
    <div className="rounded-md border border-dashed border-warning/60 bg-warning-soft/40 p-3 text-[13px]">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-warning">Video script — for you to film</p>
      {script.hook != null && <p><span className="text-muted">Hook (first 2 seconds): </span>{String(script.hook)}</p>}
      {script.length_seconds != null && <p><span className="text-muted">Length: </span>{String(script.length_seconds)} seconds</p>}
      {scenes.length > 0 && (
        <table className="mt-2 w-full text-xs">
          <thead><tr className="text-left text-muted"><th className="pr-2">Time</th><th className="pr-2">What the camera shows</th><th className="pr-2">Text on screen</th><th>Voice-over</th></tr></thead>
          <tbody className="divide-y divide-border align-top">
            {scenes.map((s, i) => <tr key={i}><td className="py-1 pr-2 tabular-nums">{s.seconds}</td><td className="py-1 pr-2">{s.shot}</td><td className="py-1 pr-2">{s.on_screen_text}</td><td className="py-1">{s.voiceover}</td></tr>)}
          </tbody>
        </table>
      )}
      {script.music != null && <p className="mt-1"><span className="text-muted">Music: </span>{String(script.music)}</p>}
      {script.call_to_action != null && <p><span className="text-muted">Call to action: </span>{String(script.call_to_action)}</p>}
    </div>
  );
}

/** A proposed post in Approvals: what goes where, with its pictures and script. */
export function PostPreview({ payload }: { payload: Record<string, unknown> }) {
  const networks = (Array.isArray(payload.networks) ? payload.networks : payload.network ? [payload.network] : []) as string[];
  const texts = (payload.texts && typeof payload.texts === "object" ? payload.texts : {}) as Record<string, string>;
  const media = (Array.isArray(payload.media_ids) ? payload.media_ids : []).map(Number).filter(Boolean);
  const text = String(payload.text ?? "");
  return (
    <div className="mt-2 space-y-2 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {networks.map((n) => <NetworkChip key={n} network={n} />)}
        <span className="text-xs text-muted">{String(payload.kind ?? (media.length ? "image" : "text"))}{payload.publish_at ? ` · ${String(payload.publish_at)}` : " · next posting hour"}</span>
      </div>
      <Pictures ids={media} size={110} />
      {text && <p className="whitespace-pre-wrap text-[13px] text-fg">{text}</p>}
      {Object.entries(texts).map(([n, t]) => (
        <div key={n} className="border-l-2 pl-2" style={{ borderColor: NETWORK[n]?.color }}>
          <p className="text-[11px] font-medium text-muted">{NETWORK[n]?.name ?? n} version</p>
          <p className="whitespace-pre-wrap text-[13px] text-fg">{t}</p>
        </div>
      ))}
      {payload.link ? <p className="text-xs text-primary">{String(payload.link)}</p> : null}
      {payload.video_script && typeof payload.video_script === "object" ? <VideoScript script={payload.video_script as Record<string, unknown>} /> : null}
    </div>
  );
}

type ConceptScene = { seconds?: number | string; start_frame?: number | string; shot?: string; action?: string; expression?: string; dialogue?: string; sound?: string; on_screen_text?: string };

/** A video concept from the Graphic Designer: what you approve before the video is made (and paid for). */
export function ConceptPreview({ payload, perSecond }: { payload: Record<string, unknown>; perSecond?: number }) {
  const scenes = (Array.isArray(payload.scenes) ? payload.scenes : []) as ConceptScene[];
  const seconds = scenes.reduce((sum, s) => sum + (Number(s.seconds) || 8), 0);
  const end = (payload.end_card && typeof payload.end_card === "object" ? payload.end_card : {}) as { headline?: string; subline?: string };
  const boards = (Array.isArray(payload.storyboard_media_ids) ? payload.storyboard_media_ids : []).map(Number).filter(Boolean);
  const facts = (Array.isArray(payload.facts_used) ? payload.facts_used : []) as string[];
  const networks = (Array.isArray(payload.networks) ? payload.networks : []) as string[];
  return (
    <div className="mt-2 space-y-2 rounded-md border border-border p-3 text-[13px]">
      <div className="flex flex-wrap items-center gap-1.5">
        {networks.map((n) => <NetworkChip key={n} network={n} />)}
        <span className="text-xs text-muted">{scenes.length} scene{scenes.length === 1 ? "" : "s"} · {seconds} s + end card{payload.uses_character !== false ? " · with the character" : ""}
          {perSecond ? ` · about $${(seconds * perSecond).toFixed(2)}` : ""}{payload.series ? ` · series “${String(payload.series)}”` : ""}</span>
      </div>
      {payload.idea != null && <p className="text-fg">{String(payload.idea)}</p>}
      {payload.hook != null && <p><span className="text-muted">Hook (first 2 seconds): </span>{String(payload.hook)}</p>}
      {payload.why_it_works != null && <p className="text-xs text-muted">Why it works: {String(payload.why_it_works)}</p>}
      {payload.setting != null && <p className="text-xs text-muted">Where: {String(payload.setting)}</p>}
      {payload.outfit != null && <p className="text-xs text-muted">Wears: {String(payload.outfit)}</p>}
      <Pictures ids={boards} size={96} />
      {scenes.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-xs">
            <thead><tr className="text-left text-muted"><th className="pr-2">#</th><th className="pr-2">First frame</th><th className="pr-2">What we see</th><th className="pr-2">Face / says</th><th>Sound</th></tr></thead>
            <tbody className="divide-y divide-border align-top">
              {scenes.map((s, i) => (
                <tr key={i}>
                  <td className="py-1 pr-2 tabular-nums">{i + 1}<span className="block text-subtle">{Number(s.seconds) || 8}s</span></td>
                  <td className="py-1 pr-2">{Number(s.start_frame) ? <Pictures ids={[Number(s.start_frame)]} size={72} /> : "—"}</td>
                  <td className="py-1 pr-2">{s.shot}{s.action ? <span className="block text-muted">{s.action}</span> : null}</td>
                  <td className="py-1 pr-2">{s.expression}{s.dialogue ? <span className="block">„{s.dialogue}“</span> : null}{!s.expression && !s.dialogue ? "—" : null}</td>
                  <td className="py-1">{s.sound}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(end.headline || end.subline) && <p><span className="text-muted">End card: </span><b>{end.headline}</b>{end.subline ? ` · ${end.subline}` : ""}</p>}
      {payload.caption != null && <p className="whitespace-pre-wrap"><span className="text-muted">Caption: </span>{String(payload.caption)}</p>}
      {facts.length > 0 && (
        <div className="text-xs"><span className="text-muted">What it says about the company (from your facts):</span>
          <ul className="list-disc pl-5">{facts.map((f, i) => <li key={i}>{f}</li>)}</ul></div>
      )}
      {Array.isArray(payload.hashtags) && payload.hashtags.length > 0 && <p className="text-xs text-primary">{(payload.hashtags as string[]).join(" ")}</p>}
    </div>
  );
}
