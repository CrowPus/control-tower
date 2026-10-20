/* Admin → Brain: the agent team's shared knowledge as a living graph. Every agent, the
   handoffs between them, the opportunities and their categories, proposals, notes, Nia's memories and the owner's directives —
   explored by hovering, clicking and searching, and asked in plain words (the answer lights up the nodes it rests on). */
import { useQuery } from "@tanstack/react-query";
import { Crosshair, Expand, Loader2, Maximize2, Minus, Pause, Play, Plus, RefreshCw, Search, Send, Sparkles, Tag, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-2d";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api";
import { useAction } from "../hooks";
import type { components } from "../schema";
import { Alert, ago, cn, when } from "../ui";

type Schemas = components["schemas"];
type Node = NodeObject<Schemas["BrainNode"]>;
type Edge = LinkObject<Node, { kind: string; weight: number }>;

// Each kind of thing in the brain: its colour on the dark canvas, its name, and whether it shows at first.
const KINDS: Record<string, { color: string; label: string; shown: boolean }> = {
  agent: { color: "#2dd4bf", label: "Agents", shown: true },
  department: { color: "#a78bfa", label: "Departments", shown: true },
  event: { color: "#fbbf24", label: "Handoffs", shown: true },
  tool: { color: "#94a3b8", label: "Tools", shown: false },
  opportunity: { color: "#4ade80", label: "Opportunities", shown: true },
  category: { color: "#a3e635", label: "Categories", shown: true },
  proposal: { color: "#60a5fa", label: "Proposals", shown: true },
  note: { color: "#facc15", label: "Agent notes", shown: true },
  memory: { color: "#c084fc", label: "Nia's memories", shown: true },
  topic: { color: "#e879f9", label: "Memory topics", shown: true },
  directive: { color: "#f87171", label: "Directives", shown: true },
};
const WINDOWS = [
  { value: "", label: "All time" },
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
];
const color = (kind: string) => KINDS[kind]?.color ?? "#cbd5e1";
const idOf = (end: string | number | Node | undefined) => (typeof end === "object" ? String(end.id) : String(end ?? ""));

function radius(n: Node): number {
  if (n.kind === "agent") return 7 + Math.min(Math.sqrt(n.degree), 6);
  if (n.kind === "department") return 6;
  return 2.6 + Math.min(Math.sqrt(n.degree), 5) * 0.7;
}

// Where the owner works on a node in the admin.
function adminLink(n: Schemas["BrainNode"]): string | null {
  const d = n.detail as Record<string, unknown>;
  switch (n.kind) {
    case "agent": return `/team/${d.key}`;
    case "note": return `/team/${n.id.split(":")[1]}`;
    case "opportunity": return `/opportunities/${d.listing_id}`;
    case "category": return "/opportunities/categories";
    case "proposal": return "/clearance";
    case "memory": case "topic": return "/nia/memory";
    case "directive": return "/nia";
    default: return null;
  }
}

function value(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  const s = String(v);
  return /^\d{4}-\d{2}-\d{2}T/.test(s) ? when(s) : s;
}

export function BrainPage({ owner }: { owner: boolean }) {
  const [days, setDays] = useState("");
  const { data, error, isFetching, refetch } = useQuery({
    queryKey: ["brain", days],
    queryFn: async () => unwrap(await api.GET("/admin-api/brain", { params: { query: days ? { days: Number(days) } : {} } })),
    refetchInterval: 60_000,
    placeholderData: (p) => p,
  });
  const [shown, setShown] = useState<Set<string>>(() => new Set(Object.entries(KINDS).filter(([, k]) => k.shown).map(([key]) => key)));
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [cited, setCited] = useState<Set<string>>(new Set());
  const [labels, setLabels] = useState(true);
  const [frozen, setFrozen] = useState(false);
  const [query, setQuery] = useState("");
  const graphRef = useRef<ForceGraphMethods<Node, Edge> | undefined>(undefined);
  const boxRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 800, height: 600 });
  // Node objects are kept between refreshes, so the graph keeps its shape when new knowledge arrives.
  const cache = useRef(new Map<string, Node>());
  const fitted = useRef(false);

  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const observer = new ResizeObserver(([entry]) => setSize({ width: Math.floor(entry.contentRect.width), height: Math.floor(entry.contentRect.height) }));
    observer.observe(box);
    return () => observer.disconnect();
  }, []);

  const byId = useMemo(() => {
    const map = new Map<string, Node>();
    for (const n of data?.nodes ?? []) {
      const kept = cache.current.get(n.id);
      map.set(n.id, kept ? Object.assign(kept, n) : { ...n });
    }
    cache.current = map;
    return map;
  }, [data]);

  const neighbours = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const l of data?.links ?? []) {
      if (!map.has(l.source)) map.set(l.source, new Set());
      if (!map.has(l.target)) map.set(l.target, new Set());
      map.get(l.source)!.add(l.target);
      map.get(l.target)!.add(l.source);
    }
    return map;
  }, [data]);

  const graphData = useMemo(() => {
    let keep: Set<string> | null = null;
    if (focus && byId.has(focus)) {
      // The focused node and everything two steps from it.
      keep = new Set([focus]);
      for (let step = 0; step < 2; step++) for (const id of [...keep]) for (const n of neighbours.get(id) ?? []) keep.add(n);
    }
    const nodes = [...byId.values()].filter((n) => (shown.has(n.kind) || n.id === focus) && (!keep || keep.has(n.id)));
    const ids = new Set(nodes.map((n) => n.id));
    const links: Edge[] = (data?.links ?? []).filter((l) => ids.has(l.source) && ids.has(l.target)).map((l) => ({ ...l }));
    return { nodes, links };
  }, [byId, data, shown, focus, neighbours]);

  // What lights up: the node under the mouse (or the chosen one) with its neighbours, or the nodes an answer named.
  const active = useMemo(() => {
    const centre = hovered ?? selected;
    if (centre) return new Set([centre, ...(neighbours.get(centre) ?? [])]);
    return cited.size ? cited : null;
  }, [hovered, selected, neighbours, cited]);

  useEffect(() => {
    const fg = graphRef.current;
    if (!fg) return;
    const charge = fg.d3Force("charge") as { strength?: (s: number) => void; distanceMax?: (d: number) => void } | undefined;
    charge?.strength?.(-55);
    charge?.distanceMax?.(420);
    const link = fg.d3Force("link") as { distance?: (fn: (l: Edge) => number) => void } | undefined;
    link?.distance?.((l) => (l.kind === "works in" ? 70 : l.kind === "uses" ? 45 : l.kind === "sends" || l.kind === "wakes" ? 55 : 30));
  }, [graphData]);

  const select = useCallback((id: string | null, move = true) => {
    setSelected(id);
    const n = id ? byId.get(id) : undefined;
    if (move && n && n.x !== undefined && n.y !== undefined) {
      graphRef.current?.centerAt(n.x, n.y, 600);
      graphRef.current?.zoom(Math.max(graphRef.current.zoom(), 2.2), 600);
    }
  }, [byId]);

  const drawNode = useCallback((n: Node, ctx: CanvasRenderingContext2D, scale: number) => {
    const x = n.x ?? 0, y = n.y ?? 0, r = radius(n);
    const dim = active !== null && !active.has(n.id);
    const recent = n.kind === "agent" && typeof n.detail.last_run === "string" && Date.now() - Date.parse(n.detail.last_run) < 6 * 3600_000;
    ctx.globalAlpha = dim ? 0.12 : 1;
    if ((recent || cited.has(n.id)) && !dim) {
      ctx.shadowColor = cited.has(n.id) ? "#ffffff" : color(n.kind);
      ctx.shadowBlur = 18;
    }
    ctx.beginPath();
    ctx.arc(x, y, r, 0, 2 * Math.PI);
    ctx.fillStyle = color(n.kind);
    ctx.fill();
    ctx.shadowBlur = 0;
    if (n.kind === "agent" || n.kind === "department") {
      ctx.lineWidth = 1.2 / scale + 0.6;
      ctx.strokeStyle = "rgba(15,23,42,0.9)";
      ctx.stroke();
      if (n.kind === "agent" && n.detail.status !== "ready") {
        // An agent that is off or waits for a setting is drawn hollow.
        ctx.beginPath();
        ctx.arc(x, y, r * 0.55, 0, 2 * Math.PI);
        ctx.fillStyle = "#0b1120";
        ctx.fill();
      }
    }
    if (n.id === selected || cited.has(n.id)) {
      ctx.beginPath();
      ctx.arc(x, y, r + 3 / scale + 1.5, 0, 2 * Math.PI);
      ctx.lineWidth = 2 / scale;
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();
    }
    const big = n.kind === "agent" || n.kind === "department";
    const named = (active?.has(n.id) && (active.size < 60 || big || scale > 1.6)) || n.id === selected;
    if (labels && (big || named || scale > 2.6)) {
      const size = (big ? 12 : 10.5) / scale;
      ctx.font = `${big ? 600 : 500} ${size}px Inter, system-ui, sans-serif`;
      const text = n.label.length > 42 ? `${n.label.slice(0, 40)}…` : n.label;
      const w = ctx.measureText(text).width, pad = 3 / scale, h = size + pad * 2;
      const ty = y + r + 3 / scale;
      ctx.fillStyle = "rgba(15,23,42,0.82)";
      ctx.strokeStyle = "rgba(148,163,184,0.25)";
      ctx.lineWidth = 0.6 / scale;
      ctx.beginPath();
      ctx.roundRect(x - w / 2 - pad * 1.5, ty, w + pad * 3, h, 3 / scale);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = big ? "#e2e8f0" : "#cbd5e1";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillText(text, x, ty + pad);
    }
    ctx.globalAlpha = 1;
  }, [active, cited, labels, selected]);

  const centre = hovered ?? selected;
  const lit = (l: Edge) => (centre ? idOf(l.source) === centre || idOf(l.target) === centre : cited.has(idOf(l.source)) && cited.has(idOf(l.target)));

  const search = query.trim().toLowerCase();
  const list = useMemo(() => [...byId.values()]
    .filter((n) => shown.has(n.kind) && (!search || n.label.toLowerCase().includes(search) || n.text.toLowerCase().includes(search)))
    .sort((a, b) => b.degree - a.degree)
    .slice(0, 300), [byId, shown, search]);

  const node = selected ? byId.get(selected) : undefined;
  const counts = data?.counts ?? {};

  return (
    <div className="dark -mx-4 -my-5 flex min-h-[100dvh] flex-col bg-bg text-fg md:-mx-8 md:-my-7 md:h-[100dvh]">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2.5">
        <Sparkles className="h-4 w-4 text-[#2dd4bf]" aria-hidden />
        <div className="min-w-0">
          <h1 className="text-[14px] font-semibold">Brain</h1>
          <p className="text-[11px] text-muted">What the whole team knows and did, linked. Hover to trace, click to read, ask below.</p>
        </div>
        <div className="ml-auto flex items-center gap-2 text-[11px] text-muted">
          <span className="flex items-center gap-1.5"><span className={cn("h-1.5 w-1.5 rounded-full bg-[#2dd4bf]", isFetching && "animate-pulse")} />Live · {data?.nodes.length ?? 0} nodes · {data?.links.length ?? 0} links</span>
          <select aria-label="Time window" value={days} onChange={(e) => setDays(e.target.value)} className="h-7 rounded-[var(--radius-control)] border border-border bg-surface px-2 text-[12px] text-fg">
            {WINDOWS.map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
          </select>
        </div>
      </header>
      {error && <div className="p-3"><Alert>{error.message}</Alert></div>}
      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[250px_1fr] xl:grid-cols-[260px_1fr_330px]">
        {/* Entities */}
        <aside className="order-2 flex min-h-0 flex-col border-border md:order-1 md:border-r">
          <div className="border-b border-border p-3">
            <label className="flex h-8 items-center gap-2 rounded-[var(--radius-control)] border border-border bg-surface px-2.5">
              <Search className="h-3.5 w-3.5 text-subtle" aria-hidden />
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find in the brain" className="min-w-0 flex-1 bg-transparent text-[12px] outline-none placeholder:text-subtle" />
              {query && <button type="button" aria-label="Clear" onClick={() => setQuery("")}><X className="h-3.5 w-3.5 text-subtle" /></button>}
            </label>
            <div className="mt-2.5 flex flex-wrap gap-1">
              {Object.entries(KINDS).map(([key, k]) => (
                <button key={key} type="button" aria-pressed={shown.has(key)} onClick={() => setShown((s) => { const next = new Set(s); if (next.has(key)) next.delete(key); else next.add(key); return next; })}
                  className={cn("flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]", shown.has(key) ? "border-border bg-raised text-fg" : "border-transparent text-subtle line-through")}>
                  <span className="h-2 w-2 rounded-full" style={{ background: k.color }} />{k.label}<span className="tabular-nums text-subtle">{counts[key] ?? 0}</span>
                </button>
              ))}
            </div>
          </div>
          <ul className="max-h-[40vh] min-h-0 flex-1 overflow-y-auto p-1.5 md:max-h-none">
            {list.map((n) => (
              <li key={n.id}>
                <button type="button" onClick={() => select(n.id)} onMouseEnter={() => setHovered(n.id)} onMouseLeave={() => setHovered(null)}
                  className={cn("flex w-full items-center gap-2 rounded-[var(--radius-control)] px-2 py-1.5 text-left text-[12px] hover:bg-raised", selected === n.id && "bg-raised")}>
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: color(n.kind) }} />
                  <span className="min-w-0 flex-1 truncate">{n.label}</span>
                  <span className="rounded px-1.5 text-[10px] text-subtle" style={{ background: `${color(n.kind)}22`, color: color(n.kind) }}>{n.kind}</span>
                </button>
              </li>
            ))}
            {list.length === 0 && <li className="px-3 py-6 text-center text-[12px] text-muted">{data ? "Nothing matches." : "Loading the brain…"}</li>}
          </ul>
        </aside>

        {/* The graph */}
        <section className="relative order-1 h-[62vh] min-h-0 border-b border-border md:order-2 md:h-auto md:border-b-0" style={{ background: "radial-gradient(ellipse at center, #111827 0%, #070b14 75%)" }}>
          <div ref={boxRef} className="absolute inset-0">
            {data && (
              <ForceGraph2D<Node, Edge>
                ref={graphRef}
                width={size.width}
                height={size.height}
                graphData={graphData}
                backgroundColor="rgba(0,0,0,0)"
                nodeId="id"
                nodeLabel={(n) => `${KINDS[n.kind]?.label.replace(/s$/, "") ?? n.kind}: ${n.label}`}
                nodeCanvasObject={drawNode}
                nodePointerAreaPaint={(n, paint, ctx) => { ctx.fillStyle = paint; ctx.beginPath(); ctx.arc(n.x ?? 0, n.y ?? 0, radius(n) + 2, 0, 2 * Math.PI); ctx.fill(); }}
                linkColor={(l) => (lit(l) ? `${color(byId.get(idOf(l.source))?.kind ?? "")}cc` : active ? "rgba(148,163,184,0.05)" : "rgba(148,163,184,0.16)")}
                linkWidth={(l) => (lit(l) ? 1.6 : 0.5)}
                linkDirectionalArrowLength={(l) => (lit(l) ? 4 : 2)}
                linkDirectionalArrowRelPos={0.92}
                linkDirectionalParticles={(l) => (lit(l) ? 2 : 0)}
                linkDirectionalParticleWidth={2.2}
                linkDirectionalParticleSpeed={0.006}
                linkLabel={(l) => l.kind}
                onNodeHover={(n) => setHovered(n ? String(n.id) : null)}
                onNodeClick={(n) => select(String(n.id), false)}
                onBackgroundClick={() => { setSelected(null); setCited(new Set()); }}
                onNodeDragEnd={(n) => { n.fx = n.x; n.fy = n.y; }}
                cooldownTicks={frozen ? 0 : 300}
                onEngineStop={() => { if (!fitted.current) { fitted.current = true; graphRef.current?.zoomToFit(500, 40); } }}
                d3VelocityDecay={0.3}
                warmupTicks={40}
                minZoom={0.2}
                maxZoom={12}
              />
            )}
            {!data && !error && <div className="grid h-full place-items-center"><Loader2 className="h-5 w-5 animate-spin text-muted" /></div>}
          </div>
          <div className="absolute left-3 top-3 flex flex-wrap gap-1 rounded-[var(--radius-control)] border border-border bg-surface/80 p-1 backdrop-blur">
            <Tool label="Zoom in" onClick={() => graphRef.current?.zoom(graphRef.current.zoom() * 1.4, 300)}><Plus /></Tool>
            <Tool label="Zoom out" onClick={() => graphRef.current?.zoom(graphRef.current.zoom() / 1.4, 300)}><Minus /></Tool>
            <Tool label="Fit everything" onClick={() => graphRef.current?.zoomToFit(600, 40)}><Maximize2 /></Tool>
            <Tool label={labels ? "Hide names" : "Show names"} pressed={labels} onClick={() => setLabels(!labels)}><Tag /></Tool>
            <Tool label={frozen ? "Let the graph move" : "Hold the graph still"} pressed={frozen} onClick={() => { setFrozen(!frozen); if (frozen) graphRef.current?.d3ReheatSimulation(); }}>{frozen ? <Play /> : <Pause />}</Tool>
            <Tool label="Shake up the layout" onClick={() => { for (const n of byId.values()) { n.fx = undefined; n.fy = undefined; } graphRef.current?.d3ReheatSimulation(); }}><Expand /></Tool>
            <Tool label="Refresh now" onClick={() => void refetch()}><RefreshCw /></Tool>
          </div>
          {focus && (
            <button type="button" onClick={() => setFocus(null)} className="absolute right-3 top-3 flex items-center gap-1.5 rounded-full border border-border bg-surface/90 px-3 py-1 text-[12px] backdrop-blur hover:bg-raised">
              <Crosshair className="h-3.5 w-3.5 text-[#2dd4bf]" /> Around {byId.get(focus)?.label.slice(0, 30)} <X className="h-3.5 w-3.5 text-subtle" />
            </button>
          )}
          <Legend />
        </section>

        {/* Details */}
        <aside className="order-3 min-h-0 overflow-y-auto border-t border-border md:col-span-2 xl:col-span-1 xl:border-l xl:border-t-0">
          {node ? (
            <Details node={node} neighbours={neighbours.get(node.id) ?? new Set()} byId={byId} links={data?.links ?? []} onPick={(id) => select(id)}
              onFocus={() => setFocus(node.id)} focused={focus === node.id} onClose={() => setSelected(null)} />
          ) : (
            <Overview counts={counts} />
          )}
        </aside>
      </div>
      <Ask owner={owner} days={days} byId={byId} onPick={(id) => select(id)} onCited={(ids) => {
        setCited(new Set(ids));
        setSelected(null);
        if (ids.length) {
          // Make sure every node the answer named is drawn, then bring them into view.
          setShown((s) => new Set([...s, ...ids.map((id) => byId.get(id)?.kind ?? "")].filter(Boolean)));
          setTimeout(() => graphRef.current?.zoomToFit(700, 80, (n) => ids.includes(String(n.id))), 350);
        }
      }} />
    </div>
  );
}

function Tool({ label, onClick, pressed, children }: { label: string; onClick: () => void; pressed?: boolean; children: ReactNode }) {
  return (
    <button type="button" title={label} aria-label={label} aria-pressed={pressed} onClick={onClick}
      className={cn("grid h-7 w-7 place-items-center rounded text-muted hover:bg-raised hover:text-fg [&>svg]:h-3.5 [&>svg]:w-3.5", pressed && "text-fg")}>
      {children}
    </button>
  );
}

function Legend() {
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 hidden max-w-[60%] flex-wrap gap-x-3 gap-y-1 text-[10px] text-subtle sm:flex">
      <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-[#2dd4bf] shadow-[0_0_8px_#2dd4bf]" />agent ran in the last 6 hours</span>
      <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full border-[3px] border-[#2dd4bf] bg-[#0b1120]" />agent off or waiting</span>
      <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full border-2 border-white" />named in an answer</span>
    </div>
  );
}

function Overview({ counts }: { counts: Record<string, number> }) {
  return (
    <div className="space-y-4 p-4">
      <div>
        <h2 className="text-[13px] font-semibold">The team's brain</h2>
        <p className="mt-1 text-[12px] leading-relaxed text-muted">
          Everything the agents know and did, drawn as one web. Agents sit in their departments; handoffs show who wakes whom; proposals hang between the agent that made them and the opportunity they are about; notes and your directives link to every opportunity they mention. Every agent reads the same brain with its <span className="text-fg">search brain</span> tool (tick it on the agent's page).
        </p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {Object.entries(KINDS).filter(([key]) => (counts[key] ?? 0) > 0).map(([key, k]) => (
          <div key={key} className="rounded-[var(--radius-control)] border border-border bg-surface px-3 py-2">
            <p className="flex items-center gap-1.5 text-[11px] text-muted"><span className="h-2 w-2 rounded-full" style={{ background: k.color }} />{k.label}</p>
            <p className="mt-0.5 text-[16px] font-semibold tabular-nums">{counts[key]}</p>
          </div>
        ))}
      </div>
      <ul className="space-y-1 text-[12px] text-muted">
        <li>Hover a node to trace its threads; click it to read it here.</li>
        <li>Drag a node to pin it; "Shake up" lets every node go again.</li>
        <li>"Show around" keeps one node and everything two steps from it.</li>
      </ul>
    </div>
  );
}

function Details({ node, neighbours, byId, links, onPick, onFocus, focused, onClose }: {
  node: Node; neighbours: Set<string>; byId: Map<string, Node>; links: Schemas["BrainLink"][]; onPick: (id: string) => void; onFocus: () => void; focused: boolean; onClose: () => void;
}) {
  const href = adminLink(node);
  const rows = Object.entries(node.detail as Record<string, unknown>).map(([k, v]) => [k.replace(/_/g, " "), value(v)] as const).filter(([, v]) => v !== "");
  const threads = links.filter((l) => l.source === node.id || l.target === node.id).slice(0, 200);
  return (
    <div>
      <div className="flex items-start gap-2 border-b border-border px-4 py-3">
        <span className="mt-1 h-3 w-3 shrink-0 rounded-full" style={{ background: color(node.kind), boxShadow: `0 0 10px ${color(node.kind)}` }} />
        <div className="min-w-0 flex-1">
          <p className="text-[10px] uppercase tracking-wide text-subtle">{KINDS[node.kind]?.label.replace(/s$/, "") ?? node.kind}{node.at && ` · ${ago(node.at)}`}</p>
          <h2 className="break-words text-[14px] font-semibold leading-snug">{node.label}</h2>
        </div>
        <button type="button" aria-label="Close" onClick={onClose} className="text-subtle hover:text-fg"><X className="h-4 w-4" /></button>
      </div>
      <div className="flex flex-wrap gap-2 px-4 py-2.5">
        <button type="button" onClick={onFocus} disabled={focused} className="flex items-center gap-1 rounded-[var(--radius-control)] border border-border px-2.5 py-1 text-[12px] hover:bg-raised disabled:opacity-50"><Crosshair className="h-3.5 w-3.5" /> Show around</button>
        {href && <Link to={href} className="rounded-[var(--radius-control)] border border-border px-2.5 py-1 text-[12px] hover:bg-raised">Open in admin →</Link>}
      </div>
      {node.text && node.text !== node.label && <p className="whitespace-pre-wrap px-4 pb-3 text-[12px] leading-relaxed text-muted">{node.text}</p>}
      {rows.length > 0 && (
        <dl className="border-y border-border text-[12px]">
          {rows.map(([k, v]) => (
            <div key={k} className="grid grid-cols-[110px_1fr] gap-2 border-b border-border/60 px-4 py-1.5 last:border-b-0">
              <dt className="text-muted">{k}</dt>
              <dd className="break-words">{v.startsWith("http") ? <a href={v} target="_blank" rel="noreferrer" className="text-[#2dd4bf] hover:underline">{v}</a> : v}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className="px-4 py-3">
        <p className="mb-2 text-[12px] font-semibold">{neighbours.size} linked</p>
        <ul className="space-y-1">
          {threads.map((l) => {
            const outgoing = l.source === node.id;
            const other = byId.get(outgoing ? l.target : l.source);
            if (!other) return null;
            return (
              <li key={`${l.source}|${l.target}|${l.kind}`}>
                <button type="button" onClick={() => onPick(other.id)} className="flex w-full items-center gap-1.5 rounded-[var(--radius-control)] border border-border bg-surface px-2 py-1 text-left text-[11px] hover:bg-raised">
                  {!outgoing && <Chip node={other} />}
                  <span className="shrink-0 text-subtle">{outgoing ? "→" : ""} {l.kind}{l.weight > 1 ? ` ×${l.weight}` : ""} {outgoing ? "" : "→"}</span>
                  {outgoing && <Chip node={other} />}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function Chip({ node }: { node: Node }) {
  return (
    <span className="min-w-0 truncate rounded px-1.5 py-0.5" style={{ background: `${color(node.kind)}22`, color: color(node.kind) }}>{node.label}</span>
  );
}

// The answer's text with every [[node id]] it names turned into a button that finds the node in the graph.
function AnswerText({ text, byId, onPick }: { text: string; byId: Map<string, Node>; onPick: (id: string) => void }) {
  const parts = text.split(/(\[\[[a-z_]+:[^\]\s]+\]\])/g);
  return (
    <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed">
      {parts.map((part, i) => {
        const m = /^\[\[(.+)\]\]$/.exec(part);
        const n = m ? byId.get(m[1]) : undefined;
        if (!m) return <span key={i}>{part}</span>;
        if (!n) return null;
        return (
          <button key={i} type="button" onClick={() => onPick(n.id)} className="mx-0.5 inline-flex max-w-[220px] items-center gap-1 truncate rounded px-1.5 align-baseline text-[11px] hover:brightness-125"
            style={{ background: `${color(n.kind)}26`, color: color(n.kind) }}>
            {n.label}
          </button>
        );
      })}
    </p>
  );
}

function Ask({ owner, days, byId, onPick, onCited }: { owner: boolean; days: string; byId: Map<string, Node>; onPick: (id: string) => void; onCited: (ids: string[]) => void }) {
  const [question, setQuestion] = useState("");
  const [open, setOpen] = useState(true);
  const [answers, setAnswers] = useState<{ question: string; answer: Schemas["BrainAnswerOut"] }[]>([]);
  const ask = useAction(async (q: string) => {
    const answer = unwrap(await api.POST("/admin-api/brain/ask", { body: { question: q, days: days ? Number(days) : null } }));
    setAnswers((a) => [{ question: q, answer }, ...a].slice(0, 10));
    setQuestion("");
    onCited(answer.cited);
    return answer;
  }, { done: () => "" });
  return (
    <footer className="border-t border-border bg-surface pr-[68px]">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-4 py-1.5 text-[11px] text-muted hover:text-fg">
        <span className="font-mono">&gt;_</span> Ask the brain {answers.length > 0 && `(${answers.length})`} <span className="ml-auto">{open ? "Hide" : "Show"}</span>
      </button>
      {open && (
        <div className="px-4 pb-3">
          {answers.length > 0 && (
            <div className="mb-2 max-h-[26vh] space-y-3 overflow-y-auto rounded-[var(--radius-control)] border border-border bg-bg p-3">
              {answers.map((a, i) => (
                <div key={i} className={cn(i > 0 && "opacity-60")}>
                  <p className="mb-1 text-[11px] text-subtle">{a.question}</p>
                  <AnswerText text={a.answer.text} byId={byId} onPick={onPick} />
                  {a.answer.cited.length > 0 && i === 0 && <button type="button" onClick={() => onCited(a.answer.cited)} className="mt-1 text-[11px] text-[#2dd4bf] hover:underline">Show these {a.answer.cited.length} in the graph</button>}
                </div>
              ))}
            </div>
          )}
          {ask.notice?.tone === "danger" && <div className="mb-2"><Alert>{ask.notice.text}</Alert></div>}
          <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (question.trim()) ask.run(question.trim()); }}>
            <input value={question} onChange={(e) => setQuestion(e.target.value)} disabled={!owner || ask.busy} maxLength={1000}
              placeholder={owner ? "Ask anything: What does the team know about this festival? Which agent is stuck, and why?" : "Only the owner asks the brain (it uses Nia's AI budget)."}
              className="h-9 min-w-0 flex-1 rounded-[var(--radius-control)] border border-border bg-bg px-3 text-[13px] outline-none placeholder:text-subtle focus:border-[#2dd4bf]" />
            <button type="submit" disabled={!owner || ask.busy || !question.trim()} className="flex h-9 items-center gap-1.5 rounded-[var(--radius-control)] bg-[#2dd4bf] px-3 text-[13px] font-medium text-[#042f2e] disabled:opacity-50">
              {ask.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Ask
            </button>
          </form>
        </div>
      )}
    </footer>
  );
}
