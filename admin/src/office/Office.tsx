/* Admin → Office: the HQ — the agent team at work in a 3D office, live. Who works at
   their desk, who walked over to a colleague with a handoff, and where the owner is (in the office of the agent
   they are talking to). For showing off; it only reads. */
import { useQuery } from "@tanstack/react-query";
import { Building2, Eye, EyeOff, Maximize, MessageSquare, Play, RotateCcw, Square, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api";
import type { components } from "../schema";
import { Alert, ago, cn, usd } from "../ui";
import { MODE_COLOR, OWNER_DESK, departmentColor, departmentOrder, desksFor, humanEvent } from "./kit";
import { OfficeScene, type Fly } from "./Scene";

type Agent = components["schemas"]["OfficeAgent"];
const MODE_LABEL: Record<string, string> = { working: "Working", idle: "At the desk", waiting: "Waiting for a setting", off: "Switched off" };

function clock(now: Date): string {
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(now);
}

export function OfficePage() {
  const { data, error } = useQuery({
    queryKey: ["office"],
    queryFn: async () => unwrap(await api.GET("/admin-api/office")),
    refetchInterval: 5_000,
    placeholderData: (p) => p,
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [labels, setLabels] = useState(true);
  const [fly, setFly] = useState<Fly>(null);
  const [tour, setTour] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);

  // Offices handed out by department, in the team's order.
  const agents = useMemo(() => {
    const list = [...(data?.agents ?? [])];
    const order = departmentOrder(list.map((a) => a.department));
    return list.sort((a, b) => order.indexOf(a.department) - order.indexOf(b.department));
  }, [data]);
  // Outside agents sit at the desk the owner gave them in the outside wing.
  const desks = useMemo(() => desksFor(agents.map((a) => a.key), Object.fromEntries(agents.filter((a) => a.desk).map((a) => [a.key, a.desk ?? null]))), [agents]);

  const focus = useCallback((key: string | null) => {
    setSelected(key);
    if (key === "owner") {
      const desk = data?.owner.with_agent ? desks.get(data.owner.with_agent) : undefined;
      setFly({ target: desk ? desk.owner : OWNER_DESK.seat, distance: 8 });
    } else if (key) {
      const desk = desks.get(key);
      if (desk) setFly({ target: desk.seat, distance: 7.5 });
    }
  }, [desks, data]);

  // The tour: from one busy office to the next (working agents first, then the rest who are in).
  useEffect(() => {
    if (!tour) return;
    let i = 0;
    const stops = () => {
      const working = agents.filter((a) => a.mode === "working" || a.visiting).map((a) => a.key);
      const rest = agents.filter((a) => a.mode !== "off" && !working.includes(a.key)).map((a) => a.key);
      return [...working, ...(data?.owner.with_agent ? ["owner"] : []), ...rest];
    };
    const next = () => { const s = stops(); if (s.length) focus(s[i++ % s.length]); };
    next();
    const t = setInterval(next, 6500);
    return () => clearInterval(t);
  }, [tour]); // eslint-disable-line react-hooks/exhaustive-deps

  const counts = useMemo(() => {
    const c: Record<string, number> = { working: 0, idle: 0, waiting: 0, off: 0 };
    for (const a of agents) c[a.mode] = (c[a.mode] ?? 0) + 1;
    return c;
  }, [agents]);
  const byKey = useMemo(() => new Map(agents.map((a) => [a.key, a])), [agents]);
  const agent = selected && selected !== "owner" ? byKey.get(selected) : undefined;
  const ownerName = data?.owner.name || "You";
  const ownerWith = data?.owner.with_agent ?? null;

  return (
    <div ref={box} className="dark relative -mx-4 -my-5 h-[100dvh] overflow-hidden bg-[#0b1020] text-fg md:-mx-8 md:-my-7">
      {data && (
        <OfficeScene agents={agents} desks={desks} ownerName={ownerName} ownerWith={ownerWith} selected={selected}
          onSelect={(k) => { setTour(false); focus(k); }} labels={labels} fly={fly} now={now} />
      )}
      {!data && !error && <div className="grid h-full place-items-center text-[13px] text-muted"><Building2 className="h-6 w-6 animate-pulse text-[#2dd4bf]" /></div>}
      {error && <div className="absolute inset-x-4 top-4"><Alert>{error.message}</Alert></div>}

      {/* top left: the building and your local time */}
      <div className="pointer-events-none absolute left-3 top-3 flex flex-col gap-2 md:left-4 md:top-4">
        <Glass className="pointer-events-auto px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 place-items-center rounded-lg bg-[#2dd4bf]/15 text-[#2dd4bf]"><Building2 className="h-5 w-5" /></div>
            <div>
              <p className="text-[15px] font-semibold tracking-tight">HQ</p>
              <p className="font-mono text-[11px] text-muted">{clock(now)} · live</p>
            </div>
          </div>
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {(["working", "idle", "waiting", "off"] as const).map((m) => (
              <span key={m} className="flex items-center gap-1.5 rounded-full bg-white/5 px-2 py-0.5 text-[11px]">
                <span className={cn("h-1.5 w-1.5 rounded-full", m === "working" && "animate-pulse")} style={{ background: MODE_COLOR[m] }} />
                {counts[m] ?? 0} {m === "idle" ? "at desk" : m}
              </span>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-muted">
            {ownerWith ? <>★ {ownerName} is in <button type="button" className="text-[#fde68a] hover:underline" onClick={() => focus(ownerWith)}>{byKey.get(ownerWith)?.name}</button>'s office</> : <>★ {ownerName} is in the Founder's suite</>}
          </p>
        </Glass>
        <Glass className="pointer-events-auto hidden max-h-[52vh] w-[230px] overflow-y-auto p-1.5 lg:block">
          {departmentOrder(agents.map((a) => a.department)).filter((d) => agents.some((a) => a.department === d)).map((d) => (
            <div key={d} className="mb-1">
              <p className="px-2 pt-1.5 text-[10px] font-semibold uppercase tracking-wider" style={{ color: departmentColor(d) }}>{d}</p>
              {agents.filter((a) => a.department === d).map((a) => (
                <button key={a.key} type="button" onClick={() => { setTour(false); focus(a.key); }}
                  className={cn("flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[12px] hover:bg-white/10", selected === a.key && "bg-white/10")}>
                  <span className={cn("h-2 w-2 shrink-0 rounded-full", a.mode === "working" && "animate-pulse")} style={{ background: MODE_COLOR[a.mode] }} />
                  <span className={cn("min-w-0 flex-1 truncate", a.mode === "off" && "text-subtle")}>{a.name}</span>
                  {a.visiting && <span className="text-[10px] text-[#fde68a]">→ {byKey.get(a.visiting.to)?.name.split(" ")[0]}</span>}
                </button>
              ))}
            </div>
          ))}
        </Glass>
      </div>

      {/* top right: the camera */}
      <div className="absolute right-3 top-3 flex gap-1.5 md:right-4 md:top-4">
        <Glass className="flex gap-0.5 p-1">
          <Btn label={tour ? "Stop the tour" : "Tour the office"} onClick={() => setTour(!tour)} active={tour}>{tour ? <Square /> : <Play />}<span className="hidden sm:inline">{tour ? "Stop" : "Tour"}</span></Btn>
          <Btn label={labels ? "Hide names" : "Show names"} onClick={() => setLabels(!labels)}>{labels ? <Eye /> : <EyeOff />}</Btn>
          <Btn label="See the whole floor" onClick={() => { setTour(false); setSelected(null); setFly({ target: [-1, 1], distance: 62 }); }}><RotateCcw /></Btn>
          <Btn label="Full screen" onClick={() => { if (document.fullscreenElement) void document.exitFullscreen(); else void box.current?.requestFullscreen(); }}><Maximize /></Btn>
        </Glass>
      </div>

      {/* right: who you clicked */}
      {(agent || selected === "owner") && (
        <Glass className="absolute bottom-16 right-3 w-[min(340px,calc(100%-24px))] p-4 md:bottom-20 md:right-4 md:top-20 md:bottom-auto">
          <button type="button" aria-label="Close" onClick={() => setSelected(null)} className="absolute right-3 top-3 text-subtle hover:text-fg"><X className="h-4 w-4" /></button>
          {agent ? <AgentCard agent={agent} byKey={byKey} ownerHere={ownerWith === agent.key} /> : (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-[#fde68a]">Founder</p>
              <h2 className="text-[16px] font-semibold">★ {ownerName}</h2>
              <p className="mt-2 text-[12px] text-muted">{ownerWith ? `Talking with ${byKey.get(ownerWith)?.name} right now.` : "In the founder's suite. Write to any agent and you walk over to their office."}</p>
            </div>
          )}
        </Glass>
      )}

      {/* bottom: the handoffs as they happen */}
      <div className="absolute inset-x-3 bottom-3 md:inset-x-4 md:bottom-4">
        <Glass className="flex items-center gap-3 overflow-hidden px-3 py-2">
          <span className="flex shrink-0 items-center gap-1.5 text-[11px] font-semibold text-[#2dd4bf]"><MessageSquare className="h-3.5 w-3.5" /> Handoffs</span>
          <div className="flex min-w-0 flex-1 gap-4 overflow-x-auto whitespace-nowrap text-[12px] [scrollbar-width:none]">
            {(data?.handoffs ?? []).length === 0 && <span className="text-subtle">Quiet for now. When an agent hands work to a colleague, it walks over to their office.</span>}
            {(data?.handoffs ?? []).map((h, i) => (
              <button key={i} type="button" onClick={() => focus(h.to[0] ?? h.by)} className="shrink-0 text-muted hover:text-fg">
                <span className="text-fg">{byKey.get(h.by)?.name ?? h.by}</span> → {h.to.map((k) => byKey.get(k)?.name ?? k).join(", ") || "the team"} · {humanEvent(h.type)} <span className="text-subtle">{ago(h.at)}</span>
              </button>
            ))}
          </div>
        </Glass>
      </div>
    </div>
  );
}

function AgentCard({ agent, byKey, ownerHere }: { agent: Agent; byKey: Map<string, Agent>; ownerHere: boolean }) {
  const guests = [...byKey.values()].filter((a) => a.visiting?.to === agent.key);
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: departmentColor(agent.department) }}>
        {agent.outside ? `Outside · ${agent.outside}${agent.desk ? ` · desk ${agent.desk}` : ""}` : agent.department}</p>
      <h2 className="pr-6 text-[16px] font-semibold">{agent.name}</h2>
      <p className="mt-2 flex items-center gap-2 text-[12px]">
        <span className={cn("h-2 w-2 rounded-full", agent.mode === "working" && "animate-pulse")} style={{ background: MODE_COLOR[agent.mode] }} />
        {MODE_LABEL[agent.mode] ?? agent.mode}{agent.since && agent.mode === "working" && <span className="text-subtle">· since {ago(agent.since)}</span>}
      </p>
      <p className="mt-1.5 rounded-md bg-white/5 px-2.5 py-2 text-[12px] leading-relaxed">{agent.visiting ? `In ${byKey.get(agent.visiting.to)?.name}'s office: ${humanEvent(agent.visiting.about)}` : agent.doing}</p>
      {(guests.length > 0 || ownerHere) && (
        <p className="mt-1.5 text-[11px] text-[#fde68a]">With: {[...(ownerHere ? ["you"] : []), ...guests.map((g) => g.name)].join(", ")}</p>
      )}
      <p className="mt-3 text-[12px] leading-relaxed text-muted">{agent.purpose}</p>
      <div className="mt-3 grid grid-cols-2 gap-1.5 text-[11px]">
        <Fact label="Runs today" value={agent.runs_today} />
        <Fact label="Proposals today" value={agent.proposals_today} />
        <Fact label="AI cost today" value={usd(agent.cost_today_usd)} />
        <Fact label="Waiting for you" value={agent.waiting_for_owner} />
      </div>
      <p className="mt-2 text-[11px] text-subtle">Last run {agent.last_run_at ? ago(agent.last_run_at) : "never"}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Link to={agent.key === "nia" ? "/nia" : `/team/${agent.key}`} className="rounded-md bg-[#2dd4bf] px-3 py-1.5 text-[12px] font-medium text-[#042f2e] hover:brightness-110">Talk to {agent.name.split(" ")[0]}</Link>
        {agent.waiting_for_owner > 0 && <Link to="/clearance" className="rounded-md border border-white/15 px-3 py-1.5 text-[12px] hover:bg-white/10">Clearance ({agent.waiting_for_owner})</Link>}
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return <div className="rounded-md bg-white/5 px-2 py-1.5"><p className="text-subtle">{label}</p><p className="text-[13px] font-semibold tabular-nums">{value}</p></div>;
}

function Glass({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("rounded-xl border border-white/10 bg-[#0f172a]/75 shadow-2xl backdrop-blur-md", className)}>{children}</div>;
}

function Btn({ label, onClick, active, children }: { label: string; onClick: () => void; active?: boolean; children: ReactNode }) {
  return (
    <button type="button" title={label} aria-label={label} onClick={onClick}
      className={cn("flex h-8 items-center gap-1.5 rounded-lg px-2 text-[12px] text-muted hover:bg-white/10 hover:text-fg [&>svg]:h-4 [&>svg]:w-4", active && "bg-[#2dd4bf]/20 text-[#5eead4]")}>
      {children}
    </button>
  );
}
