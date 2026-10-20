/* One inbox for every agent's proposals: what, why, the
   evidence and the expected effect; approve, edit or reject with a reason the agent learns
   from. Several can be decided at once. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ConceptPreview, PostPreview } from "../marketing/common";
import { api, sendAsSignedIn, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, Card, Empty, PageTitle, StatusBadge, Textarea, ago, cn } from "../ui";

type Approval = Schemas["TeamApprovalOut"];

const TABS = [
  { key: "pending", label: "Waiting" },
  { key: "executed", label: "Done" },
  { key: "rejected", label: "Rejected" },
  { key: "failed", label: "Failed" },
  { key: "all", label: "All" },
];

function LevelBadge({ level }: { level: string }) {
  if (level === "L3") return <Badge tone="warning">Always you</Badge>;
  if (level === "P") return <Badge tone="danger">Protective, done at once</Badge>;
  if (level === "L2") return <Badge tone="info">Alone within your limit</Badge>;
  return <Badge>You clear it</Badge>;
}

/** Nia's admin change: checked by the server, then sent by this page with your own sign-in. */
function useAdminChange(item: Approval) {
  const client = useQueryClient();
  return useAction(async ({ payload, note }: { payload: Record<string, unknown> | null; note: string }) => {
    const request = unwrap(await api.POST("/admin-api/team/approvals/{approval_id}/admin-change/check", { params: { path: { approval_id: item.id } }, body: { payload } }));
    const sent = await sendAsSignedIn(request.method, request.path, request.query, request.body);
    const done = unwrap(await api.POST("/admin-api/team/approvals/{approval_id}/admin-change/done", {
      params: { path: { approval_id: item.id } }, body: { payload, status_code: sent.status, answer: sent.answer, note } }));
    await client.invalidateQueries();
    if (done.status !== "executed") throw new Error(`The admin answered ${sent.status}: ${JSON.stringify(sent.answer)}`);
    return done;
  }, { done: () => "Done." });
}

function ChangeSummary({ payload }: { payload: Record<string, unknown> }) {
  return (
    <div className="mt-2 rounded-[var(--radius-control)] bg-raised p-2 text-xs">
      <p className="font-mono text-fg"><span className="font-semibold">{String(payload.method)}</span> {String(payload.path)}</p>
      {payload.body != null && <pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-fg">{JSON.stringify(payload.body, null, 2)}</pre>}
      <p className="mt-1 text-muted">When you approve, this page sends exactly this with your own sign-in. The admin log records it as Nia's change approved by you.</p>
    </div>
  );
}

function ProposalCard({ item, owner, selected, onSelect }: { item: Approval; owner: boolean; selected: boolean; onSelect: (on: boolean) => void }) {
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const [payload, setPayload] = useState(JSON.stringify(item.payload, null, 2));
  const [note, setNote] = useState("");
  const [parseError, setParseError] = useState("");
  const decide = useAction(async (body: Schemas["TeamDecisionIn"]) => unwrap(await api.POST("/admin-api/team/approvals/decide", { body })),
    { invalidate: [["team-approvals"], ["team"]] });
  const change = useAdminChange(item);
  const isChange = item.action_type === "admin.change";
  const pending = item.status === "pending";
  const approve = () => (isChange ? change.run({ payload: null, note: "" }) : decide.run({ ids: [item.id], decision: "approve", note: "" }));
  function approveEdited() {
    try {
      const edited = JSON.parse(payload) as Record<string, unknown>;
      setParseError("");
      if (isChange) change.run({ payload: edited, note });
      else decide.run({ ids: [item.id], decision: "approve", note, edited_payload: edited });
    } catch {
      setParseError("The details are not valid JSON.");
    }
  }
  return (
    <div data-nia={`approval:${item.id}`}><Card className={cn(selected && "ring-2 ring-[var(--focus-ring)]")}>
      <div className="flex items-start gap-3">
        {owner && pending && !isChange && <input type="checkbox" className="mt-1" aria-label={`Select ${item.title}`} checked={selected} onChange={(e) => onSelect(e.target.checked)} />}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[13px] font-semibold text-fg">{item.title}</h3>
            <LevelBadge level={item.level} />
            {!pending && <StatusBadge status={item.status} />}
          </div>
          <p className="mt-0.5 text-xs text-muted">
            <Link to={`/team/${item.agent}`} className="hover:text-fg">{item.agent_name}</Link> · {item.action_label} · {ago(item.created_at)}
          </p>
          {item.reason && <p className="mt-2 text-[13px] text-fg"><span className="text-muted">Why: </span>{item.reason}</p>}
          {item.expected_effect && <p className="mt-1 text-[13px] text-fg"><span className="text-muted">Expected effect: </span>{item.expected_effect}</p>}
          {item.evidence.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-xs text-muted">{item.evidence.map((e, i) => <li key={i}>{String(e.text ?? JSON.stringify(e))}</li>)}</ul>
          )}
          {pending && (item.warnings ?? []).length > 0 && (
            <div className="mt-2"><Alert>{(item.warnings ?? []).join(" ")} Approving is blocked; reject it, or edit it so it no longer repeats what is already there.</Alert></div>
          )}
          {isChange && mode !== "edit" && <ChangeSummary payload={item.edited_payload ?? item.payload} />}
          {item.action_type === "social.post" && mode !== "edit" && <PostPreview payload={(item.edited_payload ?? item.payload) as Record<string, unknown>} />}
          {item.action_type === "video.concept" && mode !== "edit" && <ConceptPreview payload={(item.edited_payload ?? item.payload) as Record<string, unknown>} />}
          {mode === "edit" ? (
            <Textarea rows={8} className="mt-2 font-mono text-xs" value={payload} onChange={(e) => setPayload(e.target.value)} aria-label="Details" />
          ) : (
            <details className="mt-2 text-xs">
              <summary className="cursor-pointer text-muted">Details</summary>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded-[var(--radius-control)] bg-raised p-2 text-fg">{JSON.stringify(item.edited_payload ?? item.payload, null, 2)}</pre>
            </details>
          )}
          {item.decision_note && <p className="mt-2 text-xs text-muted">Your note: {item.decision_note}</p>}
          {item.result && Object.keys(item.result).length > 0 && <p className="mt-1 text-xs text-muted">Result: {JSON.stringify(item.result)}</p>}
          {(mode === "reject" || mode === "edit") && (
            <Textarea rows={2} className="mt-2" placeholder={mode === "reject" ? "Why not? The agent learns from this." : "Note for the agent (optional)"} value={note} onChange={(e) => setNote(e.target.value)} />
          )}
          {parseError && <div className="mt-2"><Alert>{parseError}</Alert></div>}
          {decide.notice?.tone === "danger" && <div className="mt-2"><Alert>{decide.notice.text}</Alert></div>}
          {change.notice && <div className="mt-2"><Alert tone={change.notice.tone}>{change.notice.text}</Alert></div>}
          {owner && pending && (
            <div className="mt-3 flex flex-wrap gap-2">
              {mode === "view" && (
                <>
                  <Button variant="primary" size="sm" loading={decide.busy || change.busy} disabled={(item.warnings ?? []).length > 0} onClick={approve}>Approve</Button>
                  <Button size="sm" onClick={() => setMode("edit")}>Edit, then approve</Button>
                  <Button size="sm" variant="ghost" onClick={() => setMode("reject")}>Reject…</Button>
                </>
              )}
              {mode === "edit" && <><Button variant="primary" size="sm" loading={decide.busy || change.busy} onClick={approveEdited}>Approve with changes</Button><Button size="sm" variant="ghost" onClick={() => setMode("view")}>Cancel</Button></>}
              {mode === "reject" && <><Button variant="danger" size="sm" loading={decide.busy} onClick={() => decide.run({ ids: [item.id], decision: "reject", note })}>Reject</Button><Button size="sm" variant="ghost" onClick={() => setMode("view")}>Cancel</Button></>}
            </div>
          )}
        </div>
      </div>
    </Card></div>
  );
}

export function ApprovalsPage({ owner }: { owner: boolean }) {
  const [status, setStatus] = useState("pending");
  const [selected, setSelected] = useState<number[]>([]);
  const { data, error } = useQuery({ queryKey: ["team-approvals", status], queryFn: async () => unwrap(await api.GET("/admin-api/team/approvals", { params: { query: { status } } })), refetchInterval: 30_000 });
  const bulk = useAction(async (body: Schemas["TeamDecisionIn"]) => unwrap(await api.POST("/admin-api/team/approvals/decide", { body })),
    { invalidate: [["team-approvals"], ["team"]], done: (r) => {
      setSelected([]);
      const gone = r.withdrawn ?? [];
      return `${r.items.length} decided.${gone.length ? ` ${gone.length} withdrawn because they no longer apply — ${gone.join(" ")}` : ""}`;
    } });
  if (error) return <Alert>{error.message}</Alert>;
  const items = data?.items ?? [];
  // Nia's admin changes are approved one by one on their card (your page sends them).
  const pendingIds = items.filter((i) => i.status === "pending" && i.action_type !== "admin.change").map((i) => i.id);
  return (
    <>
      <Link to="/team" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg"><ArrowLeft className="h-3.5 w-3.5" aria-hidden />Team</Link>
      <PageTitle title="Clearance" description="What the agents propose. Nothing happens until you clear it; cleared actions are carried out within a minute. When you reject, say why: the agent learns from it." />
      <div className="mb-3 flex flex-wrap items-center gap-1" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={status === t.key} onClick={() => { setStatus(t.key); setSelected([]); }}
            className={cn("rounded-[var(--radius-control)] px-2.5 py-1 text-[13px]", status === t.key ? "bg-raised font-medium text-fg" : "text-muted hover:text-fg")}>
            {t.label}{t.key !== "all" && data?.counts[t.key] ? ` (${data.counts[t.key]})` : ""}
          </button>
        ))}
      </div>
      {bulk.notice && <div className="mb-3"><Alert tone={bulk.notice.tone}>{bulk.notice.text}</Alert></div>}
      {owner && pendingIds.length > 1 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[13px]">
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={selected.length === pendingIds.length} onChange={(e) => setSelected(e.target.checked ? pendingIds : [])} />Select all</label>
          {selected.length > 0 && (
            <>
              <Button variant="primary" size="sm" loading={bulk.busy} onClick={() => bulk.run({ ids: selected, decision: "approve", note: "" })}>Approve {selected.length}</Button>
              <Button size="sm" variant="ghost" loading={bulk.busy} onClick={() => bulk.run({ ids: selected, decision: "reject", note: "" })}>Reject {selected.length}</Button>
            </>
          )}
        </div>
      )}
      {data && items.length === 0 && <Card><Empty>{status === "pending" ? "Nothing waits for you." : "Nothing here."}</Empty></Card>}
      <div className="grid gap-3">
        {items.map((item) => (
          <ProposalCard key={item.id} item={item} owner={owner} selected={selected.includes(item.id)} onSelect={(on) => setSelected(on ? [...selected, item.id] : selected.filter((x) => x !== item.id))} />
        ))}
      </div>
    </>
  );
}

