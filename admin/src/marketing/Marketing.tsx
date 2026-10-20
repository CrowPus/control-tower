/* Admin → Marketing: the Marketing Lead's plan, the social media calendar,
   the picture studio, the company's character and the videos made from cleared concepts,
   the results, the connected networks and the brand kit. Agents propose; you approve
   in Clearance; the install publishes on the connected networks and reads what each post achieved. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, CheckCircle2, Clapperboard, Copy, ExternalLink, Image as ImageIcon, Link2, Megaphone, Palette, Plug, RotateCcw, Sparkles, Trash2, TrendingUp, Upload, UserRound, Video } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useSearchParams } from "react-router-dom";
import { api, errorMessage, unwrap, type Schemas } from "../api";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageTitle, Select, Stat, Textarea, Toggle, ago, cn, when } from "../ui";
import { ConceptPreview, NETWORK, NetworkChip, Pictures, STATUS, VideoScript, agentName, type MadeVideo, type Media, type Overview, type Post } from "./common";

const TABS = [
  ["", "Plan", Megaphone], ["calendar", "Calendar", CalendarDays], ["studio", "Studio", ImageIcon], ["videos", "Videos", Clapperboard], ["character", "Character", UserRound],
  ["results", "Results", TrendingUp],
  ["connections", "Connections", Plug], ["brand", "Brand", Palette],
] as const;

function useOverview() {
  return useQuery({ queryKey: ["marketing"], queryFn: async () => unwrap(await api.GET("/admin-api/marketing")) });
}

export function MarketingArea({ owner }: { owner: boolean }) {
  const { data, error } = useOverview();
  return (
    <>
      <PageTitle title="Marketing" description="The Marketing Lead plans; the Social Media Manager makes the posts and pictures; the Graphic Designer makes videos with your character; you clear them in Clearance; the install publishes and reads the results." />
      <nav className="mb-4 flex flex-wrap gap-1 border-b border-border">
        {TABS.map(([path, label, Icon]) => (
          <NavLink key={path} to={`/marketing${path ? `/${path}` : ""}`} end={!path}
            className={({ isActive }) => cn("-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-[13px]", isActive ? "border-primary font-medium text-fg" : "border-transparent text-muted hover:text-fg")}>
            <Icon className="h-4 w-4" aria-hidden />{label}
          </NavLink>
        ))}
      </nav>
      {error && <Alert>{errorMessage(error)}</Alert>}
      {data && (
        <Routes>
          <Route index element={<PlanTab data={data} />} />
          <Route path="calendar" element={<CalendarTab owner={owner} />} />
          <Route path="studio" element={<StudioTab data={data} owner={owner} />} />
          <Route path="videos" element={<VideosTab data={data} owner={owner} />} />
          <Route path="character" element={<CharacterTab data={data} owner={owner} />} />
          <Route path="results" element={<ResultsTab />} />
          <Route path="connections" element={<ConnectionsTab data={data} owner={owner} />} />
          <Route path="brand" element={<BrandTab data={data} owner={owner} />} />
          <Route path="*" element={<Navigate to="/marketing" replace />} />
        </Routes>
      )}
    </>
  );
}

// --- plan -------------------------------------------------------------------------------------------------
function PlanTab({ data }: { data: Overview }) {
  const plan = data.plan;
  const connected = data.accounts.length;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Connected accounts" value={connected} hint={connected ? data.accounts.map((a) => NETWORK[a.network]?.name ?? a.network).join(", ") : "Connect them under Connections"} />
        <Stat label="Waiting for your video" value={data.waiting.needs_video ?? 0} tone={(data.waiting.needs_video ?? 0) > 0 ? "warning" : undefined} />
        <Stat label="Scheduled" value={data.waiting.scheduled ?? 0} />
        <Stat label="Picture budget left" value={data.budget.left_usd == null ? "not set" : `$${Number(data.budget.left_usd).toFixed(2)}`}
          hint={data.budget.monthly_budget_usd == null ? "Set it under Brand" : `$${Number(data.budget.spent_this_month_usd).toFixed(2)} of $${Number(data.budget.monthly_budget_usd).toFixed(2)} this month`} />
      </div>
      {(data.waiting.failed ?? 0) > 0 && <Alert>{data.waiting.failed} post(s) failed — see the <Link to="/marketing/calendar" className="underline">calendar</Link>.</Alert>}
      {!plan ? (
        <Card><Empty>No campaign plan yet. The Marketing Lead proposes one (Team → Marketing Lead → Run, or ask it in its chat); you clear it in <Link to="/clearance" className="text-primary">Clearance</Link>.</Empty></Card>
      ) : (
        <Card title={<span>{plan.title} <span className="font-normal text-muted">· {plan.period_start} – {plan.period_end}</span></span>}>
          {plan.summary && <p className="whitespace-pre-wrap text-[13px] text-fg">{plan.summary}</p>}
          {plan.goals.length > 0 && (
            <>
              <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-muted">Goals</h3>
              <ul className="mt-1 space-y-1 text-[13px]">
                {(plan.goals as Record<string, unknown>[]).map((g, i) => <li key={i}>• {String(g.metric ?? JSON.stringify(g))}{g.target != null && <b> → {String(g.target)}</b>}{g.baseline != null && <span className="text-muted"> (now {String(g.baseline)})</span>}</li>)}
              </ul>
            </>
          )}
          {plan.campaigns.length > 0 && (
            <>
              <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-muted">Campaigns</h3>
              <div className="mt-1 grid gap-2 md:grid-cols-2">
                {(plan.campaigns as Record<string, unknown>[]).map((c, i) => (
                  <div key={i} className="rounded-md border border-border p-3 text-[13px]">
                    <p className="font-medium">{String(c.name ?? "Campaign")}</p>
                    {c.audience != null && <p className="text-muted">For: {String(c.audience)}</p>}
                    {Array.isArray(c.channels) && <p className="mt-1 flex flex-wrap gap-1">{(c.channels as string[]).map((ch) => NETWORK[ch] ? <NetworkChip key={ch} network={ch} small /> : <Badge key={ch}>{ch}</Badge>)}</p>}
                    {c.message != null && <p className="mt-1">"{String(c.message)}"</p>}
                    {Array.isArray(c.content) && <ul className="mt-1 text-xs text-muted">{(c.content as string[]).map((x, j) => <li key={j}>• {x}</li>)}</ul>}
                  </div>
                ))}
              </div>
            </>
          )}
          {Object.keys(plan.briefs).length > 0 && (
            <>
              <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-muted">Briefs to the team</h3>
              <div className="mt-1 space-y-2">
                {Object.entries(plan.briefs).map(([agent, brief]) => (
                  <div key={agent} className="text-[13px]"><Link to={`/team/${agent}`} className="font-medium text-primary">{agentName(agent)}</Link><p className="whitespace-pre-wrap text-fg">{String(brief)}</p></div>
                ))}
              </div>
            </>
          )}
        </Card>
      )}
    </div>
  );
}

// --- calendar ----------------------------------------------------------------------------------------------
function PostCard({ post, owner }: { post: Post; owner: boolean }) {
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(post.text);
  const [whenAt, setWhenAt] = useState(post.scheduled_at ? new Date(new Date(post.scheduled_at).getTime() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "");
  const [link, setLink] = useState("");
  const [url, setUrl] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const path = { params: { path: { post_id: post.id } } };
  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setProblem("");
    try { await fn(); await client.invalidateQueries({ queryKey: ["marketing-posts"] }); await client.invalidateQueries({ queryKey: ["marketing"] }); setEditing(false); }
    catch (e) { setProblem(errorMessage(e)); }
    setBusy(false);
  }
  const status = STATUS[post.status] ?? { label: post.status, tone: "neutral" as const };
  const full = post.link && !post.text.includes(post.link) ? `${post.text}\n\n${post.link}` : post.text;
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <NetworkChip network={post.network} />
        <Badge tone={status.tone}>{status.label}</Badge>
        <span className="text-xs text-muted">{post.kind} · by {agentName(post.agent)}{post.account ? ` · ${post.account}` : ""}</span>
        <span className="ml-auto text-xs text-muted">{post.posted_at ? `posted ${when(post.posted_at)}` : post.scheduled_at ? when(post.scheduled_at) : ago(post.created_at)}</span>
      </div>
      <div className="mt-2 flex gap-3">
        <Pictures ids={post.media_ids.filter(() => post.kind !== "video")} size={88} />
        <div className="min-w-0 flex-1">
          {post.title && <p className="text-[13px] font-medium">{post.title}</p>}
          {editing ? <Textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} aria-label="Text" /> : <p className="whitespace-pre-wrap text-[13px] text-fg">{post.text}</p>}
          {post.link && <a href={post.link} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs text-primary">{post.link}<ExternalLink className="h-3 w-3" /></a>}
        </div>
      </div>
      {post.kind === "video" && <div className="mt-2"><VideoScript script={post.video_script} /></div>}
      {post.error && <div className="mt-2"><Alert>{post.error}</Alert></div>}
      {post.status === "posted" && Object.keys(post.metrics).length > 0 && (
        <p className="mt-2 flex flex-wrap gap-3 text-xs text-muted">{Object.entries(post.metrics).map(([k, v]) => <span key={k}><b className="text-fg">{String(v)}</b> {k}</span>)}</p>
      )}
      {post.post_url && <a href={post.post_url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs text-primary">Open the post<ExternalLink className="h-3 w-3" /></a>}
      {problem && <div className="mt-2"><Alert>{problem}</Alert></div>}
      {owner && !["posted", "discarded", "publishing"].includes(post.status) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {post.status === "needs_video" && (
            <>
              <Input className="max-w-md flex-1" placeholder="Link to your video (Google Drive or Dropbox share link, or a direct file)" value={link} onChange={(e) => setLink(e.target.value)} />
              <Button size="sm" variant="primary" disabled={!link.trim()} loading={busy} onClick={() => void run(async () => unwrap(await api.POST("/admin-api/marketing/posts/{post_id}/video", { ...path, body: { link } })))}>
                <Video className="h-3.5 w-3.5" />Add video and schedule
              </Button>
            </>
          )}
          {editing ? (
            <>
              {post.status !== "ready" && <Input type="datetime-local" className="w-56" value={whenAt} onChange={(e) => setWhenAt(e.target.value)} aria-label="Time" />}
              <Button size="sm" variant="primary" loading={busy} onClick={() => void run(async () => unwrap(await api.PATCH("/admin-api/marketing/posts/{post_id}", { ...path, body: { text, scheduled_at: whenAt ? new Date(whenAt).toISOString() : null } })))}>Save</Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
            </>
          ) : <Button size="sm" onClick={() => setEditing(true)}>Edit</Button>}
          {(post.status === "scheduled" || post.status === "failed") && <Button size="sm" loading={busy} onClick={() => void run(async () => unwrap(await api.POST("/admin-api/marketing/posts/{post_id}/publish-now", path)))}>{post.status === "failed" ? "Try again" : "Publish now"}</Button>}
          {post.status === "ready" && post.can_publish && (
            <Button size="sm" variant="primary" loading={busy} onClick={() => void run(async () => unwrap(await api.POST("/admin-api/marketing/posts/{post_id}/publish-now", path)))}>
              Publish on {NETWORK[post.network]?.name ?? post.network} now
            </Button>
          )}
          {post.status === "ready" && (
            <>
              <Button size="sm" onClick={() => void navigator.clipboard.writeText(full)}><Copy className="h-3.5 w-3.5" />Copy text</Button>
              <Input className="max-w-xs" placeholder="Address of the post (optional)" value={url} onChange={(e) => setUrl(e.target.value)} />
              <Button size="sm" variant={post.can_publish ? "ghost" : "primary"} loading={busy}
                onClick={() => { if (window.confirm("Mark as posted? Only when you posted it yourself on the network — Control Tower does not send it.")) void run(async () => unwrap(await api.POST("/admin-api/marketing/posts/{post_id}/posted", { ...path, body: { post_url: url } }))); }}>
                I posted it myself
              </Button>
            </>
          )}
          {post.status === "ready" && !post.can_publish && post.network === "instagram" && post.media_ids.length === 0 && (
            <span className="text-xs text-muted">Instagram needs a picture: this text-only post can only be posted by hand.</span>
          )}
          <Button size="sm" variant="ghost" onClick={() => { if (window.confirm("Discard this post?")) void run(async () => unwrap(await api.POST("/admin-api/marketing/posts/{post_id}/discard", path))); }}>Discard</Button>
        </div>
      )}
    </Card>
  );
}

function CalendarTab({ owner }: { owner: boolean }) {
  const { data, error } = useQuery({ queryKey: ["marketing-posts"], queryFn: async () => unwrap(await api.GET("/admin-api/marketing/posts")), refetchInterval: 30_000 });
  const [show, setShow] = useState("open");
  if (error) return <Alert>{errorMessage(error)}</Alert>;
  const posts = data ?? [];
  const open = posts.filter((p) => ["needs_video", "failed", "ready", "scheduled", "publishing"].includes(p.status))
    .sort((a, b) => (["needs_video", "failed", "ready"].indexOf(a.status) + 1 || 9) - (["needs_video", "failed", "ready"].indexOf(b.status) + 1 || 9) || String(a.scheduled_at).localeCompare(String(b.scheduled_at)));
  const done = posts.filter((p) => p.status === "posted" || p.status === "discarded");
  const list = show === "open" ? open : done;
  return (
    <div className="space-y-3">
      <div className="flex gap-1">
        {[["open", `Coming up (${open.length})`], ["done", `Posted (${done.filter((p) => p.status === "posted").length})`]].map(([k, l]) => (
          <button key={k} type="button" onClick={() => setShow(k)} className={cn("rounded-md px-2.5 py-1 text-[13px]", show === k ? "bg-raised font-medium" : "text-muted hover:text-fg")}>{l}</button>
        ))}
      </div>
      {list.length === 0 ? <Card><Empty>{show === "open" ? "Nothing coming up. Cleared posts appear here with their time." : "Nothing posted yet."}</Empty></Card>
        : list.map((p) => <PostCard key={p.id} post={p} owner={owner} />)}
    </div>
  );
}

// --- studio -----------------------------------------------------------------------------------------------
function StudioTab({ data, owner }: { data: Overview; owner: boolean }) {
  const client = useQueryClient();
  const media = useQuery({ queryKey: ["marketing-media"], queryFn: async () => unwrap(await api.GET("/admin-api/marketing/media")) });
  const [form, setForm] = useState({ prompt: "", aspect: "4:5", headline: "", subline: "", shows_people: true, title: "" });
  const [videoLink, setVideoLink] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const file = useRef<HTMLInputElement>(null);
  async function run(kind: string, fn: () => Promise<unknown>, ok: string) {
    setBusy(kind);
    setNotice(null);
    try { await fn(); setNotice({ tone: "success", text: ok }); await client.invalidateQueries({ queryKey: ["marketing-media"] }); await client.invalidateQueries({ queryKey: ["marketing"] }); }
    catch (e) { setNotice({ tone: "danger", text: errorMessage(e) }); }
    setBusy("");
  }
  const logoId = data.brand.logo_media_id;
  return (
    <div className="space-y-4">
      {owner && (
        <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
          <Card title={<span className="flex items-center gap-1.5"><Sparkles className="h-4 w-4 text-primary" />Make a picture</span>}
            actions={<span className="text-xs text-muted">{data.budget.monthly_budget_usd == null ? "Set the monthly budget under Brand first" : `$${Number(data.budget.left_usd).toFixed(2)} left this month`}</span>}>
            <div className="space-y-3">
              <Field label="The picture" hint="Who, doing what, where, the light and mood. E.g. 'A forklift driver in a bright warehouse in Kulmbach, smiling, morning light.'">
                <Textarea rows={3} value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} />
              </Field>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Format"><Select value={form.aspect} onChange={(e) => setForm({ ...form, aspect: e.target.value })}>
                  <option value="4:5">4:5 Instagram / Facebook feed</option><option value="1:1">1:1 square</option><option value="9:16">9:16 story</option><option value="16:9">16:9 LinkedIn / YouTube</option>
                </Select></Field>
                <Field label="Headline (optional)"><Input value={form.headline} maxLength={90} onChange={(e) => setForm({ ...form, headline: e.target.value })} /></Field>
                <Field label="Line under it (optional)"><Input value={form.subline} maxLength={140} onChange={(e) => setForm({ ...form, subline: e.target.value })} /></Field>
              </div>
              <Toggle tone="primary" checked={form.shows_people} onChange={(v) => setForm({ ...form, shows_people: v })} label="People in the picture" description="AI-made people are labelled on the picture and in the post." />
              <Button variant="primary" loading={busy === "image"} disabled={form.prompt.trim().length < 15}
                onClick={() => void run("image", async () => unwrap(await api.POST("/admin-api/marketing/media/image", { body: form })), "Made. It is in the library below.")}>
                <Sparkles className="h-4 w-4" />Make picture
              </Button>
            </div>
          </Card>
          <Card title="Add your own">
            <div className="space-y-3">
              <div>
                <p className="mb-1 text-xs text-muted">A real photo, or your logo (PNG keeps its transparency).</p>
                <Button loading={busy === "upload"} onClick={() => file.current?.click()}><Upload className="h-4 w-4" />Upload a picture</Button>
                <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (!f) return;
                  const body = new FormData();
                  body.append("file", f);
                  void run("upload", async () => unwrap(await api.POST("/admin-api/marketing/media/upload", { params: { query: { title: f.name } }, body: body as unknown as { file: string } })), "Uploaded.");
                }} />
              </div>
              <Field label="A video you made" hint="Share link from Google Drive ('Anyone with the link') or Dropbox, or a direct file address. It is fetched when posted, not kept.">
                <div className="flex gap-2"><Input value={videoLink} onChange={(e) => setVideoLink(e.target.value)} placeholder="https://drive.google.com/file/d/…" />
                  <Button loading={busy === "video"} disabled={!videoLink.trim()} onClick={() => void run("video", async () => { unwrap(await api.POST("/admin-api/marketing/media/video", { body: { link: videoLink, title: "" } })); setVideoLink(""); }, "Video added.")}><Link2 className="h-4 w-4" />Add</Button></div>
              </Field>
            </div>
          </Card>
        </div>
      )}
      {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}
      <Card title={`Library (${media.data?.length ?? 0})`} padded={false}>
        {(media.data ?? []).length === 0 ? <Empty>No pictures yet.</Empty> : (
          <div className="grid grid-cols-2 gap-3 p-3 sm:grid-cols-3 lg:grid-cols-5">
            {(media.data ?? []).map((m: Media) => (
              <div key={m.id} className="overflow-hidden rounded-md border border-border">
                {m.kind === "image" ? (
                  <a href={m.url} target="_blank" rel="noreferrer" className="block aspect-square bg-raised"><img src={m.url} alt={m.alt_text} className="h-full w-full object-cover" loading="lazy" /></a>
                ) : m.source === "generated" ? (
                  <Link to="/marketing/videos" className="relative block aspect-square bg-raised"><img src={`/admin-api/marketing/media/${m.id}/file`} alt={m.alt_text} className="h-full w-full object-cover" loading="lazy" />
                    <span className="absolute bottom-1 right-1 rounded bg-black/60 px-1 text-[10px] text-white">{Math.round(m.seconds)} s</span></Link>
                ) : (
                  <a href={m.link} target="_blank" rel="noreferrer" className="flex aspect-square flex-col items-center justify-center gap-1 bg-raised text-muted"><Video className="h-8 w-8" /><span className="text-xs">Video link</span></a>
                )}
                <div className="space-y-1 p-2">
                  <p className="truncate text-xs font-medium" title={m.prompt || m.title}>#{m.id} {m.title}</p>
                  <p className="flex flex-wrap gap-1 text-[10px]">
                    {m.ai_generated && <Badge tone="info">AI-made</Badge>}{m.source === "upload" && <Badge>uploaded</Badge>}{logoId === m.id && <Badge tone="success">logo</Badge>}{(data.character.picture_ids ?? []).includes(m.id) && <Badge tone="primary">character</Badge>}
                    <span className="text-subtle">{agentName(m.created_by)}</span>
                  </p>
                  {owner && (
                    <div className="flex gap-1">
                      {m.kind === "image" && logoId !== m.id && (
                        <button type="button" className="text-[11px] text-primary hover:underline" onClick={() => void run("logo", async () => unwrap(await api.PUT("/admin-api/marketing/brand", { body: { ...data.brand, logo_media_id: m.id } })), "This is now the logo on new pictures.")}>Use as logo</button>
                      )}
                      <button type="button" className="ml-auto text-muted hover:text-danger" aria-label={`Delete ${m.title}`}
                        onClick={() => { if (window.confirm("Delete this from the library?")) void run("delete", async () => { const r = await api.DELETE("/admin-api/marketing/media/{media_id}", { params: { path: { media_id: m.id } } }); if (r.error) throw new Error((r.error as { error?: { message?: string } }).error?.message ?? "Not deleted."); }, "Deleted."); }}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

// --- results ----------------------------------------------------------------------------------------------
function ResultsTab() {
  const [days, setDays] = useState(30);
  const { data, error } = useQuery({ queryKey: ["marketing-results", days], queryFn: async () => unwrap(await api.GET("/admin-api/marketing/results", { params: { query: { days } } })) });
  if (error) return <Alert>{errorMessage(error)}</Alert>;
  const r = data as { followers: Record<string, number | null>; by_network: Record<string, Record<string, number>>; best_posts: { id: number; network: string; text: string; metrics: Record<string, number>; url: string; posted_at: string }[] } | undefined;
  if (!r) return null;
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2"><span className="text-[13px] text-muted">Last</span>
        <Select className="w-32" value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 30, 90].map((d) => <option key={d} value={d}>{d} days</option>)}</Select>
        <span className="text-xs text-subtle">Numbers are read from the networks every hour.</span></div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Object.entries(r.followers).map(([n, f]) => <Stat key={n} label={`${NETWORK[n]?.name ?? n} followers`} value={f ?? "—"} />)}
        {Object.keys(r.followers).length === 0 && <Stat label="Followers" value="—" hint="Connect your accounts" />}
      </div>
      <Card title="Per network" padded={false}>
        {Object.keys(r.by_network).length === 0 ? <Empty>No posts published in this period yet.</Empty> : (
          <table className="w-full text-[13px]">
            <thead><tr className="border-b border-border text-left text-xs text-muted">{["Network", "Posts", "Reach", "Views", "Likes", "Comments", "Shares", "Saves", "Clicks"].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-border">
              {Object.entries(r.by_network).map(([n, t]) => (
                <tr key={n}><td className="px-4 py-2"><NetworkChip network={n} small /></td>{["posts", "reach", "views", "likes", "comments", "shares", "saves", "clicks"].map((k) => <td key={k} className="px-4 py-2 tabular-nums">{t[k] ?? "—"}</td>)}</tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Best posts">
        {r.best_posts.length === 0 ? <Empty>No results yet.</Empty> : (
          <ul className="divide-y divide-border">
            {r.best_posts.map((p) => (
              <li key={p.id} className="py-2 text-[13px]">
                <div className="flex items-center gap-2"><NetworkChip network={p.network} small /><span className="text-xs text-muted">{when(p.posted_at)}</span>
                  {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="ml-auto text-xs text-primary">Open</a>}</div>
                <p className="mt-0.5 line-clamp-2">{p.text}</p>
                <p className="text-xs text-muted">{Object.entries(p.metrics).map(([k, v]) => `${v} ${k}`).join(" · ")}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

// --- connections ------------------------------------------------------------------------------------------
const APPS: Record<string, { title: string; networks: string[]; steps: string[]; note: string }> = {
  meta: {
    title: "Facebook and Instagram (one Meta app)", networks: ["facebook", "instagram"],
    steps: [
      "Instagram must be a business or creator account linked to your Facebook Page (Instagram → Settings → Account type; Page → Settings → Linked accounts).",
      "Open developers.facebook.com → My Apps → Create app → use case 'Other' → type 'Business'. Name it e.g. 'Company Posting'.",
      "Add the products 'Facebook Login for Business' and 'Instagram' (API with Facebook Login).",
      "Facebook Login for Business → Settings: Client OAuth login and Web OAuth login on; Valid OAuth Redirect URIs: paste the address below (Enter, then Save changes).",
      "Facebook Login for Business → Configurations → Create configuration: name 'Company posting', login variation General, token type 'User access token', assets Pages and Instagram accounts, permissions: pages_show_list, pages_read_engagement, pages_manage_posts, pages_read_user_content, read_insights, business_management, instagram_basic, instagram_content_publish, instagram_manage_insights, instagram_manage_comments. Copy its Configuration ID into the field here.",
      "App settings → Basic: App domains (your domain); copy the App ID and App secret into the fields here. Keep the app in Development mode — it posts to your own Page without Meta's review.",
      "Press Connect and allow the app for your Page and its Instagram account.",
    ],
    note: "Your Page tokens do not expire while you stay admin of the Page.",
  },
  linkedin: {
    title: "LinkedIn company page", networks: ["linkedin"],
    steps: [
      "Open linkedin.com/developers → Create app; link it to your company page (you must be its admin) and verify the app from the page.",
      "Products: request 'Community Management API' (LinkedIn reviews this; it can take days or weeks).",
      "Auth → Authorized redirect URLs: paste the address below.",
      "Auth: copy the Client ID and Client Secret into the fields here.",
      "When LinkedIn has approved the product, press Connect.",
    ],
    note: "LinkedIn's sign-in lasts 60 days and is renewed automatically while LinkedIn allows it; if it runs out you are told to connect again.",
  },
  google: {
    title: "YouTube channel (one Google Cloud app)", networks: ["youtube"],
    steps: [
      "Open console.cloud.google.com → create a project for your company.",
      "APIs & Services → Library → enable 'YouTube Data API v3'.",
      "OAuth consent screen: type External, add your Google account as test user.",
      "Credentials → Create credentials → OAuth client ID → Web application; Authorized redirect URI: paste the address below.",
      "Copy the Client ID and Client secret into the fields here, then press Connect with the Google account that owns the channel.",
    ],
    note: "Until Google verifies the app, YouTube keeps uploads private — you make them public in YouTube Studio (or apply for verification in the consent screen).",
  },
  tiktok: {
    title: "TikTok", networks: ["tiktok"],
    steps: [
      "Open developers.tiktok.com with the TikTok account the company posts from → Manage apps → Connect an app; app name 'Company Posting', category Business, a short description, the website and the privacy policy address.",
      "Products: add 'Login Kit' and 'Content Posting API'; in Content Posting API switch on 'Direct Post'.",
      "Scopes: user.info.basic, user.info.profile, user.info.stats, video.publish, video.list.",
      "Login Kit → Redirect URI (Web): paste the address below.",
      "Copy the Client key and Client secret into the fields here, then press Connect and allow the app.",
      "Submit the app for TikTok's review (audit). Until TikTok has approved it, TikTok lets the app post only privately: you then make each video public in the TikTok app.",
    ],
    note: "TikTok's sign-in lasts a day and is renewed automatically (for a year); videos made by the studio are marked as AI-generated on TikTok.",
  },
};

function AppCard({ app, data, owner }: { app: Schemas["AppView"]; data: Overview; owner: boolean }) {
  const client = useQueryClient();
  const info = APPS[app.provider];
  const [clientId, setClientId] = useState(app.client_id);
  const [secret, setSecret] = useState("");
  const [configId, setConfigId] = useState(app.config_id ?? "");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const linked = data.accounts.filter((a) => info.networks.includes(a.network));
  async function save() {
    setBusy("save");
    try { unwrap(await api.PUT("/admin-api/marketing/apps/{provider}", { params: { path: { provider: app.provider as "meta" | "linkedin" | "google" | "tiktok" } }, body: { client_id: clientId, client_secret: secret || null, config_id: app.provider === "meta" ? configId.trim() : "" } })); setSecret(""); setNotice({ tone: "success", text: "Saved." }); await client.invalidateQueries({ queryKey: ["marketing"] }); }
    catch (e) { setNotice({ tone: "danger", text: errorMessage(e) }); }
    setBusy("");
  }
  async function connect() {
    setBusy("connect");
    try { const r = unwrap(await api.POST("/admin-api/marketing/connect/{provider}", { params: { path: { provider: app.provider as "meta" | "linkedin" | "google" | "tiktok" } } })); window.location.href = r.url; }
    catch (e) { setNotice({ tone: "danger", text: errorMessage(e) }); setBusy(""); }
  }
  async function disconnect(id: number, name: string) {
    if (!window.confirm(`Disconnect ${name}? Posts to it are then posted by hand. Also remove the app's access at the network.`)) return;
    await api.DELETE("/admin-api/marketing/accounts/{account_id}", { params: { path: { account_id: id } } });
    await client.invalidateQueries({ queryKey: ["marketing"] });
  }
  return (
    <Card title={<span className="flex items-center gap-2">{info.title}{linked.length > 0 && <Badge tone="success">Connected</Badge>}</span>}>
      {linked.length > 0 && (
        <ul className="mb-3 space-y-1.5">
          {linked.map((a) => (
            <li key={a.id} className="flex items-center gap-2 rounded-md bg-raised px-2.5 py-1.5 text-[13px]">
              {a.picture_url ? <img src={a.picture_url} alt="" className="h-6 w-6 rounded-full" referrerPolicy="no-referrer" /> : <CheckCircle2 className="h-5 w-5 text-success" />}
              <NetworkChip network={a.network} small />
              <a href={a.profile_url || "#"} target="_blank" rel="noreferrer" className="font-medium hover:underline">{a.name}</a>
              {a.followers != null && <span className="text-xs text-muted">{a.followers} followers</span>}
              {a.status === "error" && <span className="text-xs text-danger">{a.last_error}</span>}
              {owner && <button type="button" className="ml-auto text-xs text-muted hover:text-danger" onClick={() => void disconnect(a.id, a.name)}>Disconnect</button>}
            </li>
          ))}
        </ul>
      )}
      <details className="mb-3 text-[13px]" open={!app.client_id}>
        <summary className="cursor-pointer text-xs font-medium text-primary">How to set it up (once)</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-fg">{info.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
        <p className="mt-2 text-xs text-muted">{info.note}</p>
      </details>
      <Field label="Redirect address (paste it into the app)">
        <div className="flex gap-2"><Input readOnly value={app.redirect_uri} /><Button onClick={() => void navigator.clipboard.writeText(app.redirect_uri)} aria-label="Copy the address"><Copy className="h-4 w-4" /></Button></div>
      </Field>
      {owner && (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label={app.provider === "meta" ? "App ID" : "Client ID"}><Input value={clientId} onChange={(e) => setClientId(e.target.value)} /></Field>
          <Field label={app.provider === "meta" ? "App secret" : "Client secret"} hint={app.secret_set ? "Stored. Leave empty to keep it." : "Stored encrypted; never shown again."}>
            <Input type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} />
          </Field>
          {app.provider === "meta" && (
            <Field label="Configuration ID (Facebook Login for Business)" hint="From Facebook Login for Business → Configurations. Needed when Meta says 'Invalid Scopes'.">
              <Input inputMode="numeric" value={configId} onChange={(e) => setConfigId(e.target.value.replace(/\D/g, ""))} />
            </Field>
          )}
        </div>
      )}
      {notice && <div className="mt-2"><Alert tone={notice.tone}>{notice.text}</Alert></div>}
      {owner && (
        <div className="mt-3 flex gap-2">
          <Button loading={busy === "save"} disabled={clientId.trim().length < 3} onClick={() => void save()}>Save app</Button>
          <Button variant="primary" loading={busy === "connect"} disabled={!app.client_id || !app.secret_set} onClick={() => void connect()}>{linked.length ? "Connect again" : "Connect"}</Button>
        </div>
      )}
    </Card>
  );
}

function ConnectionsTab({ data, owner }: { data: Overview; owner: boolean }) {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const connected = params.get("connected");
  const failed = params.get("error");
  const [hand, setHand] = useState<string[]>(data.networks_by_hand);
  useEffect(() => { if (connected) void client.invalidateQueries({ queryKey: ["marketing"] }); }, [connected, client]);
  return (
    <div className="space-y-4">
      {connected && <Alert tone="success">Connected: {connected}</Alert>}
      {failed && <Alert>Not connected: {failed}</Alert>}
      {(connected || failed) && <button type="button" className="text-xs text-muted underline" onClick={() => setParams({}, { replace: true })}>Dismiss</button>}
      {!data.public_media_ready && <Alert>The server's public address (PUBLIC_BASE_URL) is not set, so Instagram and Facebook cannot fetch pictures.</Alert>}
      <p className="text-[13px] text-muted">Each network lets a service post only through an app in your name. Set each up once (the steps are in each card), then press Connect. After that, approved posts go out by themselves and the numbers come in every hour.</p>
      <div className="grid gap-4 xl:grid-cols-2">
        {data.apps.map((a) => <AppCard key={a.provider} app={a} data={data} owner={owner} />)}
        <Card title="Posted by hand">
          <p className="mb-2 text-[13px] text-muted">Networks you post on yourself (not connected, or X): cleared posts wait in the calendar with "Copy text".</p>
          <div className="flex flex-wrap gap-3">
            {["facebook", "instagram", "tiktok", "linkedin", "youtube", "x"].map((n) => (
              <label key={n} className="flex items-center gap-1.5 text-[13px]">
                <input type="checkbox" disabled={!owner} checked={hand.includes(n)} onChange={(e) => setHand(e.target.checked ? [...hand, n] : hand.filter((x) => x !== n))} />{NETWORK[n].name}
              </label>
            ))}
          </div>
          {owner && <Button className="mt-3" size="sm" onClick={async () => { unwrap(await api.PUT("/admin-api/marketing/by-hand", { body: { networks: hand as ("facebook" | "instagram" | "linkedin" | "youtube" | "tiktok" | "x")[] } })); await client.invalidateQueries({ queryKey: ["marketing"] }); }}>Save</Button>}
        </Card>
      </div>
    </div>
  );
}

// --- brand ------------------------------------------------------------------------------------------------
function BrandTab({ data, owner }: { data: Overview; owner: boolean }) {
  const client = useQueryClient();
  const [kit, setKit] = useState(data.brand);
  const [factsText, setFactsText] = useState((data.brand.facts_for_customers ?? []).join("\n"));
  const [wanted, setWanted] = useState(data.settings);
  const [notice, setNotice] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const list = (v: string[]) => v.join(", ");
  const parse = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);
  async function save(kind: "brand" | "settings") {
    setNotice(null);
    try {
      if (kind === "brand") unwrap(await api.PUT("/admin-api/marketing/brand", { body: kit }));
      else unwrap(await api.PUT("/admin-api/marketing/settings", { body: wanted }));
      setNotice({ tone: "success", text: "Saved (in the admin log)." });
      await client.invalidateQueries({ queryKey: ["marketing"] });
    } catch (e) { setNotice({ tone: "danger", text: errorMessage(e) }); }
  }
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="Budget and rhythm">
        <div className="space-y-3">
          <Field label="Monthly picture budget (USD)" hint={`A picture costs about $${wanted.picture_quality === "best" ? "0.14" : "0.07"}. Empty: no pictures are made.`}>
            <Input type="number" min={0} step="1" disabled={!owner} value={wanted.monthly_picture_budget_usd ?? ""} onChange={(e) => setWanted({ ...wanted, monthly_picture_budget_usd: e.target.value === "" ? null : Number(e.target.value) })} />
          </Field>
          <Field label="Picture quality"><Select disabled={!owner} value={wanted.picture_quality} onChange={(e) => setWanted({ ...wanted, picture_quality: e.target.value as "standard" | "best" })}>
            <option value="standard">Standard (Gemini Flash Image)</option><option value="best">Best (Gemini Pro Image, about twice the price)</option></Select></Field>
          <Field label="Posts per network and day (at most)" hint="Empty: the agents' judgement, at most 2.">
            <Input type="number" min={0} max={20} disabled={!owner} value={wanted.max_posts_per_network_per_day ?? ""} onChange={(e) => setWanted({ ...wanted, max_posts_per_network_per_day: e.target.value === "" ? null : Number(e.target.value) })} />
          </Field>
          <Field label="Posting hours (your time zone)" hint="Cleared posts without their own time go out at the next of these hours, e.g. 9, 12, 18. Empty: right after clearance.">
            <Input disabled={!owner} value={(wanted.posting_hours ?? []).join(", ")} onChange={(e) => setWanted({ ...wanted, posting_hours: parse(e.target.value).map(Number).filter((h) => Number.isInteger(h) && h >= 0 && h <= 23) })} />
          </Field>
          <h3 className="pt-2 text-xs font-semibold uppercase tracking-wide text-muted">Videos</h3>
          <Field label="Monthly video budget (USD)" hint={`A second of video costs about $${Number(data.video_budget.price_per_second_usd ?? 0).toFixed(2)} at this quality (a 24-second video about $${(24 * Number(data.video_budget.price_per_second_usd ?? 0)).toFixed(2)}). Empty: no videos are made.`}>
            <Input type="number" min={0} step="1" disabled={!owner} value={wanted.monthly_video_budget_usd ?? ""} onChange={(e) => setWanted({ ...wanted, monthly_video_budget_usd: e.target.value === "" ? null : Number(e.target.value) })} />
          </Field>
          <Field label="Video quality"><Select disabled={!owner} value={wanted.video_quality} onChange={(e) => setWanted({ ...wanted, video_quality: e.target.value as "fast" | "best" })}>
            <option value="fast">Fast (Veo 3.1 Fast)</option><option value="best">Best (Veo 3.1, about four times the price)</option></Select></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Videos" hint="Made and posted. Empty: none are proposed.">
              <Input type="number" min={0} max={20} disabled={!owner} value={wanted.videos_count ?? ""} onChange={(e) => setWanted({ ...wanted, videos_count: e.target.value === "" ? null : Number(e.target.value) })} />
            </Field>
            <Field label="per"><Select disabled={!owner} value={wanted.videos_per} onChange={(e) => setWanted({ ...wanted, videos_per: e.target.value as "day" | "week" })}>
              <option value="day">day</option><option value="week">week</option></Select></Field>
          </div>
          {owner && <Button variant="primary" onClick={() => void save("settings")}>Save</Button>}
        </div>
      </Card>
      <Card title="Brand kit">
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name"><Input disabled={!owner} value={kit.name} onChange={(e) => setKit({ ...kit, name: e.target.value })} /></Field>
            <Field label="Tagline"><Input disabled={!owner} value={kit.tagline} onChange={(e) => setKit({ ...kit, tagline: e.target.value })} /></Field>
            <Field label="Name said as" hint="Only if a video might say it wrong, e.g. EP-liv."><Input disabled={!owner} maxLength={60} value={kit.pronunciation ?? ""} onChange={(e) => setKit({ ...kit, pronunciation: e.target.value })} /></Field>
          </div>
          <div className="grid grid-cols-3 gap-3">
            {(["primary_color", "text_color", "accent_color"] as const).map((k) => (
              <Field key={k} label={{ primary_color: "Band colour", text_color: "Text colour", accent_color: "Accent" }[k]}>
                <div className="flex items-center gap-2"><input type="color" disabled={!owner} value={kit[k]} onChange={(e) => setKit({ ...kit, [k]: e.target.value.toUpperCase() })} className="h-9 w-10 rounded border border-border" /><span className="text-xs text-muted">{kit[k]}</span></div>
              </Field>
            ))}
          </div>
          <Field label="Logo on pictures" hint={kit.logo_media_id ? `Picture #${kit.logo_media_id} from the library (Studio → Use as logo).` : "Upload your logo in the Studio and press 'Use as logo'."}>
            <Select disabled={!owner} value={kit.logo_position} onChange={(e) => setKit({ ...kit, logo_position: e.target.value as typeof kit.logo_position })}>
              <option value="top_left">Top left</option><option value="top_right">Top right</option><option value="bottom_left">Bottom left</option><option value="bottom_right">Bottom right</option><option value="none">No logo</option>
            </Select>
          </Field>
          <Field label="About the company" hint="What it does and for whom, in a few sentences. Every agent reads this before it works."><Textarea rows={4} disabled={!owner} maxLength={2000} value={kit.about ?? ""} onChange={(e) => setKit({ ...kit, about: e.target.value })} /></Field>
          <Field label="How the company sounds" hint="Tone, formal or casual, what you stand for — the agents write in this voice."><Textarea rows={4} disabled={!owner} value={kit.voice} onChange={(e) => setKit({ ...kit, voice: e.target.value })} /></Field>
          <Field label="Picture style" hint="E.g. 'real workplaces, natural light, warm, no stock-photo poses'."><Textarea rows={2} disabled={!owner} value={kit.image_style} onChange={(e) => setKit({ ...kit, image_style: e.target.value })} /></Field>
          <Field label="Words to use (comma separated)"><Input disabled={!owner} value={list(kit.words_to_use ?? [])} onChange={(e) => setKit({ ...kit, words_to_use: parse(e.target.value) })} /></Field>
          <Field label="Words to avoid"><Input disabled={!owner} value={list(kit.words_to_avoid ?? [])} onChange={(e) => setKit({ ...kit, words_to_avoid: parse(e.target.value) })} /></Field>
          <Field label="Hashtags"><Input disabled={!owner} value={list(kit.hashtags ?? [])} onChange={(e) => setKit({ ...kit, hashtags: parse(e.target.value) })} /></Field>
          <Field label="Languages besides English (codes)" hint="E.g. fr, pt, es, ar — posts can come in your customers' languages."><Input disabled={!owner} value={list(kit.languages ?? [])} onChange={(e) => setKit({ ...kit, languages: parse(e.target.value) })} /></Field>
          <Field label="AI label on made pictures"><Input disabled={!owner} value={kit.ai_label} onChange={(e) => setKit({ ...kit, ai_label: e.target.value })} /></Field>
          <Field label="Facts for customers (one per line)" hint="What the company offers, in your approved words. Videos and posts may say only these about the company.">
            <Textarea rows={8} disabled={!owner} value={factsText} onChange={(e) => { setFactsText(e.target.value); setKit({ ...kit, facts_for_customers: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean) }); }} />
          </Field>
          {owner && <Button variant="primary" onClick={() => void save("brand")}>Save brand kit</Button>}
        </div>
      </Card>
      {notice && <div className="xl:col-span-2"><Alert tone={notice.tone}>{notice.text}</Alert></div>}
    </div>
  );
}

// --- videos ----------------------------------------------------------------------
const VIDEO_STATUS: Record<string, { label: string; tone: "neutral" | "primary" | "success" | "warning" | "danger" | "info" }> = {
  making: { label: "Being made…", tone: "primary" }, ready: { label: "Ready", tone: "success" }, failed: { label: "Failed", tone: "danger" }, deleted: { label: "Deleted", tone: "neutral" },
};

function VideoCard({ video, owner }: { video: MadeVideo; owner: boolean }) {
  const client = useQueryClient();
  const [busy, setBusy] = useState("");
  const [problem, setProblem] = useState("");
  const status = VIDEO_STATUS[video.status] ?? { label: video.status, tone: "neutral" as const };
  const waiting = video.posts.filter((p) => ["scheduled", "needs_video", "ready", "publishing"].includes(String(p.status)));
  const path = { params: { path: { video_id: video.id } } };
  async function run(kind: string, fn: () => Promise<unknown>) {
    setBusy(kind);
    setProblem("");
    try { await fn(); await client.invalidateQueries({ queryKey: ["marketing-videos"] }); await client.invalidateQueries({ queryKey: ["marketing"] }); await client.invalidateQueries({ queryKey: ["marketing-media"] }); }
    catch (e) { setProblem(errorMessage(e)); }
    setBusy("");
  }
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={status.tone}>{status.label}</Badge>
        <span className="text-[13px] font-medium">{video.title}</span>
        <span className="text-xs text-muted">{Math.round(video.seconds)} s · ${video.cost_usd.toFixed(2)} · by {agentName(video.agent)}</span>
        <span className="ml-auto text-xs text-muted">{video.made_at ? `made ${ago(video.made_at)}` : ago(video.created_at)}</span>
      </div>
      <div className="mt-3 flex flex-col gap-4 md:flex-row">
        {video.file_url ? (
          <video controls preload="metadata" poster={video.cover_url} src={video.file_url} className="aspect-[9/16] w-full max-w-[240px] shrink-0 rounded-md bg-black" />
        ) : (
          <div className={cn("flex w-full max-w-[240px] shrink-0 flex-col items-center justify-center gap-2 rounded-md bg-raised p-3 text-center text-xs text-muted", video.status === "making" ? "aspect-[9/16]" : "h-32")}>
            <Clapperboard className="h-8 w-8" />
            {video.status === "making" ? `Scene ${Math.min(video.scenes_done + 1, video.scenes)} of ${video.scenes} — a scene takes up to a few minutes.` : video.status === "deleted" ? "The file was deleted." : "No video."}
          </div>
        )}
        <div className="min-w-0 flex-1">
          {video.error && <Alert>{video.error}</Alert>}
          <ReviewBox review={video.review as Review} />
          {video.posts.length > 0 && (
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
              {video.posts.map((p) => (
                <span key={String(p.id)} className="inline-flex items-center gap-1"><NetworkChip network={String(p.network)} small /><span className="text-muted">{STATUS[String(p.status)]?.label ?? String(p.status)}</span>
                  {p.post_url ? <a href={String(p.post_url)} target="_blank" rel="noreferrer" className="text-primary"><ExternalLink className="h-3 w-3" /></a> : null}</span>
              ))}
            </div>
          )}
          {video.status === "ready" && video.posts.length === 0 && <p className="mb-2 text-xs text-muted">The Social Media Manager proposes when and where it goes out; you clear the post in Clearance.</p>}
          <details className="text-[13px]">
            <summary className="cursor-pointer text-xs font-medium text-primary">The cleared concept</summary>
            <ConceptPreview payload={video.concept as Record<string, unknown>} />
          </details>
          {problem && <div className="mt-2"><Alert>{problem}</Alert></div>}
          {owner && (
            <div className="mt-3 flex flex-wrap gap-2">
              {video.file_url && <a href={video.file_url} download className="inline-flex"><Button size="sm"><Upload className="h-3.5 w-3.5 rotate-180" />Download</Button></a>}
              {video.status === "ready" && video.remake_from && <Remake video={video} busy={busy === "remake"}
                onRemake={(from) => void run("remake", async () => unwrap(await api.POST("/admin-api/marketing/videos/{video_id}/remake", { ...path, body: { from_scene: from } })))} />}
              {video.status === "failed" && (
                <Button size="sm" variant="primary" loading={busy === "again"} onClick={() => void run("again", async () => unwrap(await api.POST("/admin-api/marketing/videos/{video_id}/again", path)))}>
                  <RotateCcw className="h-3.5 w-3.5" />Try again
                </Button>
              )}
              {(video.status === "ready" || video.status === "failed") && (
                <Button size="sm" variant="ghost" loading={busy === "delete"} disabled={waiting.length > 0} title={waiting.length ? "Posts that are not out yet use it" : undefined}
                  onClick={() => { if (window.confirm("Delete this video's file? Posts already on the networks stay there; the concept stays here for the record.")) void run("delete", async () => unwrap(await api.DELETE("/admin-api/marketing/videos/{video_id}", path))); }}>
                  <Trash2 className="h-3.5 w-3.5" />Delete video
                </Button>
              )}
              {waiting.length > 0 && <span className="self-center text-xs text-muted">Delete it once its posts are out.</span>}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

/** After the check found mistakes: make the video again from the first bad scene on (the scenes before are kept and not paid again). */
function Remake({ video, busy, onRemake }: { video: MadeVideo; busy: boolean; onRemake: (from: number) => void }) {
  const [from, setFrom] = useState(video.remake_from ?? 1);
  const seconds = video.scene_seconds ?? [];
  const cost = seconds.slice(from - 1).reduce((n, s) => n + s, 0) * (video.price_per_second ?? 0);
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <Select className="h-8 w-auto text-xs" value={from} onChange={(e) => setFrom(Number(e.target.value))} aria-label="From scene">
        {seconds.map((_, i) => <option key={i} value={i + 1}>From scene {i + 1}{i + 1 === video.remake_from ? " (first mistake)" : ""}</option>)}
      </Select>
      <Button size="sm" variant="primary" loading={busy}
        onClick={() => { if (window.confirm(`Make scene ${from}${from < seconds.length ? `–${seconds.length}` : ""} again for about $${cost.toFixed(2)}? Scenes before it are kept; what the check found is told to the video model; the new video is checked again.`)) onRemake(from); }}>
        <RotateCcw className="h-3.5 w-3.5" />Make again · ${cost.toFixed(2)}
      </Button>
    </span>
  );
}

type Review = { state?: string; verdict?: string; summary?: string; said?: string; brand_in_english?: boolean | null; mistakes?: { at?: string; what?: string; severity?: string }[]; false_claims?: string[] };

/** What the automatic check found when it watched the finished video (AI mistakes, the words, claims). */
function ReviewBox({ review }: { review: Review }) {
  if (!review || !review.state) return null;
  if (review.state === "waiting") return <p className="mb-2 text-xs text-muted">Being checked for AI mistakes…</p>;
  if (review.state !== "done") return <div className="mb-2"><Alert>{review.summary || "Not checked automatically — watch it before posting."}</Alert></div>;
  const fix = review.verdict === "fix";
  return (
    <div className={cn("mb-2 rounded-md border p-2.5 text-[13px]", fix ? "border-danger/40 bg-danger-soft/40" : "border-success/40 bg-success-soft/40")}>
      <p className="font-medium">{fix ? "The check found problems — better not post it as it is" : "The check found nothing serious"}</p>
      {review.summary && <p className="mt-0.5">{review.summary}</p>}
      {review.brand_in_english === false && <p className="mt-1 text-danger">The name was not said the way you set.</p>}
      {(review.false_claims ?? []).length > 0 && <ul className="mt-1 list-disc pl-5 text-danger">{(review.false_claims ?? []).map((c, i) => <li key={i}>Not true: {c}</li>)}</ul>}
      {(review.mistakes ?? []).length > 0 && (
        <ul className="mt-1 space-y-0.5 text-xs">{(review.mistakes ?? []).map((m, i) => <li key={i}><span className="tabular-nums text-muted">{m.at}</span> {m.severity === "high" ? <b>{m.what}</b> : m.what}</li>)}</ul>
      )}
      {review.said && <details className="mt-1 text-xs"><summary className="cursor-pointer text-muted">What is said</summary><p className="mt-1 whitespace-pre-wrap">{review.said}</p></details>}
    </div>
  );
}

function VideosTab({ data, owner }: { data: Overview; owner: boolean }) {
  const { data: videos, error } = useQuery({
    queryKey: ["marketing-videos"], queryFn: async () => unwrap(await api.GET("/admin-api/marketing/videos")),
    refetchInterval: (q) => ((q.state.data ?? []).some((v) => v.status === "making") ? 10_000 : 60_000),
  });
  const [show, setShow] = useState("current");
  const rhythm = data.video_rhythm as { videos?: number | null; per?: string; this_period?: number; left?: number | null };
  const money = data.video_budget as { monthly_budget_usd?: number | null; left_usd?: number | null; spent_this_month_usd?: number };
  const list = (videos ?? []).filter((v) => (show === "current" ? v.status !== "deleted" : v.status === "deleted"));
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Character" value={data.character.name || "not set"} tone={data.character.name && (data.character.picture_ids ?? []).length ? undefined : "warning"}
          hint={(data.character.picture_ids ?? []).length ? `${(data.character.picture_ids ?? []).length} picture(s)` : "Set it up under Character"} />
        <Stat label={`Videos this ${rhythm.per ?? "week"}`} value={rhythm.videos == null ? "not set" : `${rhythm.this_period ?? 0} of ${rhythm.videos}`} hint={rhythm.videos == null ? "Set the number under Brand" : "made, being made or waiting for you"} />
        <Stat label="Video budget left" value={money.left_usd == null ? "not set" : `$${Number(money.left_usd).toFixed(2)}`}
          hint={money.monthly_budget_usd == null ? "Set it under Brand" : `$${Number(money.spent_this_month_usd ?? 0).toFixed(2)} of $${Number(money.monthly_budget_usd).toFixed(2)} this month`} />
        <Stat label="Concepts waiting for you" value={data.waiting.video_concepts ?? "—"} hint={<Link to="/clearance" className="text-primary">Clearance</Link>} />
      </div>
      <p className="text-[13px] text-muted">The Graphic Designer proposes video concepts with your character; you clear each one in <Link to="/clearance" className="text-primary">Clearance</Link>. Only then is the video made (a few minutes) and the Social Media Manager proposes when it goes out. Delete a video here once it is posted.</p>
      {error && <Alert>{errorMessage(error)}</Alert>}
      <div className="flex gap-1">
        {[["current", "Videos"], ["deleted", "Deleted"]].map(([k, l]) => (
          <button key={k} type="button" onClick={() => setShow(k)} className={cn("rounded-md px-2.5 py-1 text-[13px]", show === k ? "bg-raised font-medium" : "text-muted hover:text-fg")}>{l}</button>
        ))}
      </div>
      {list.length === 0 ? <Card><Empty>{show === "current" ? "No videos yet. Clear a concept from the Graphic Designer (Team → Graphic Designer → Run, or ask it in its chat)." : "No deleted videos."}</Empty></Card>
        : list.map((v) => <VideoCard key={v.id} video={v} owner={owner} />)}
    </div>
  );
}

// --- the character ----------------------------------------------------------------------------------------------
function CharacterTab({ data, owner }: { data: Overview; owner: boolean }) {
  const client = useQueryClient();
  type Figure = Schemas["Character"] & { picture_ids: number[] };
  const whole = (c: Partial<Figure>): Figure => ({ name: "", who: "", look: "", personality: "", voice: "", rules: "", speaks: false, ...c, picture_ids: c.picture_ids ?? [] });
  const [figure, setFigure] = useState<Figure>(whole(data.character));
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const file = useRef<HTMLInputElement>(null);
  async function save(next = figure, text = "Saved (in the admin log). The Graphic Designer uses it from now on.") {
    setBusy("save");
    setNotice(null);
    try { const saved = unwrap(await api.PUT("/admin-api/marketing/character", { body: next })); setFigure(whole(saved)); setNotice({ tone: "success", text }); await client.invalidateQueries({ queryKey: ["marketing"] }); }
    catch (e) { setNotice({ tone: "danger", text: errorMessage(e) }); }
    setBusy("");
  }
  async function upload(f: File) {
    setBusy("upload");
    try {
      const body = new FormData();
      body.append("file", f);
      const up = unwrap(await api.POST("/admin-api/marketing/media/upload", { params: { query: { title: `${figure.name || "Character"} – ${f.name}` } }, body: body as unknown as { file: string } }));
      await save({ ...figure, picture_ids: [...figure.picture_ids, up.id] }, "Picture added.");
      await client.invalidateQueries({ queryKey: ["marketing-media"] });
    } catch (e) { setNotice({ tone: "danger", text: errorMessage(e) }); setBusy(""); }
  }
  const text = (k: "who" | "look" | "personality" | "voice" | "rules", label: string, hint: string, rows = 3) => (
    <Field label={label} hint={hint}><Textarea rows={rows} disabled={!owner} value={figure[k]} onChange={(e) => setFigure({ ...figure, [k]: e.target.value })} /></Field>
  );
  return (
    <div className="grid gap-4 xl:grid-cols-[2fr_3fr]">
      <Card title="Pictures of the character" actions={<span className="text-xs text-muted">{figure.picture_ids.length} of 3</span>}>
        <p className="mb-3 text-[13px] text-muted">Up to three clear pictures of the same character — the face well visible, ideally front, side and full body. The video model gets them with every scene, so the person stays the same while clothes and places change.</p>
        <div className="grid grid-cols-3 gap-2">
          {figure.picture_ids.map((id, i) => (
            <div key={id} className="relative overflow-hidden rounded-md border border-border">
              <img src={`/admin-api/marketing/media/${id}/file`} alt={`${figure.name} ${i + 1}`} className="aspect-[3/4] w-full object-cover" />
              {i === 0 && <span className="absolute left-1 top-1 rounded bg-black/60 px-1 text-[10px] text-white">main</span>}
              {owner && (
                <div className="absolute right-1 top-1 flex gap-1">
                  {i > 0 && <button type="button" className="rounded bg-black/60 px-1 text-[10px] text-white" onClick={() => void save({ ...figure, picture_ids: [id, ...figure.picture_ids.filter((x) => x !== id)] }, "Main picture changed.")}>Make main</button>}
                  <button type="button" className="rounded bg-black/60 p-0.5 text-white" aria-label="Take out" onClick={() => void save({ ...figure, picture_ids: figure.picture_ids.filter((x) => x !== id) }, "Picture taken out (it stays in the library).")}><Trash2 className="h-3 w-3" /></button>
                </div>
              )}
            </div>
          ))}
          {figure.picture_ids.length === 0 && <div className="col-span-3"><Empty>No pictures yet.</Empty></div>}
        </div>
        {owner && figure.picture_ids.length < 3 && (
          <>
            <Button className="mt-3" loading={busy === "upload"} disabled={!figure.name.trim()} title={figure.name.trim() ? undefined : "Give the character a name first"} onClick={() => file.current?.click()}><Upload className="h-4 w-4" />Upload a picture</Button>
            <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void upload(f); }} />
          </>
        )}
      </Card>
      <Card title="Who the character is">
        <div className="space-y-3">
          <Field label="Name"><Input disabled={!owner} maxLength={60} value={figure.name} onChange={(e) => setFigure({ ...figure, name: e.target.value })} /></Field>
          {text("who", "Who it is", "Its role for the company, its age range, what it stands for. E.g. 'the company's friendly guide, late twenties, explains things simply.'")}
          {text("look", "What never changes", "Face, hair, skin, build, typical features — what makes it recognisable. Clothes may change from video to video.")}
          {text("personality", "Personality", "How it acts: e.g. 'warm, quick-witted, a little cheeky, always on the traveller's side'.")}
          <Toggle tone="primary" checked={!!figure.speaks} disabled={!owner} onChange={(v) => setFigure({ ...figure, speaks: v })}
            label="The character speaks" description="Off: a silent character — the story is told with action, facial expression, music and sound effects; the message is on the end card and in the caption." />
          {figure.speaks && text("voice", "Voice", "How it sounds when it speaks: e.g. 'young woman, warm and clear, slightly fast, light Franconian accent'.", 2)}
          {text("rules", "Your rules", "What it always or never does, says or wears: e.g. 'always waves at the start, never wears other brands, says Servus'.")}
          {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}
          {owner && <Button variant="primary" loading={busy === "save"} onClick={() => void save()}>Save character</Button>}
        </div>
      </Card>
    </div>
  );
}
