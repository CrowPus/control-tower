/* Talking to one agent: conversations, files attached
   to a message, and the proposals the agent makes while talking, decided right under its answer. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FileText, MessageSquare, Paperclip, Plus, Send, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, Card, Select, StatusBadge, Textarea, cn, usd } from "../ui";

type Message = Schemas["TeamMessageOut"];
type Proposal = Schemas["TeamApprovalOut"];
type FileOut = Schemas["TeamAttachmentOut"];

const ACCEPT = ".csv,.tsv,.xlsx,.pdf,.txt,.md,.json,.png,.jpg,.jpeg,.webp";

function size(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function FileChip({ file, href, onRemove }: { file: FileOut; href?: string; onRemove?: () => void }) {
  const body = <><FileText className="h-3.5 w-3.5 shrink-0" aria-hidden /><span className="truncate">{file.name}</span><span className="shrink-0 opacity-70">{size(file.size)}</span></>;
  return (
    <span className="inline-flex max-w-[16rem] items-center gap-1 rounded-full border border-border bg-surface px-2 py-0.5 text-[11px] text-fg">
      {href ? <a href={href} className="inline-flex min-w-0 items-center gap-1 hover:underline" download>{body}</a> : body}
      {onRemove && <button type="button" onClick={onRemove} className="ml-0.5 rounded-full p-0.5 hover:bg-raised" aria-label={`Remove ${file.name}`}><X className="h-3 w-3" aria-hidden /></button>}
    </span>
  );
}

function ProposalCard({ proposal, owner, onDecided }: { proposal: Proposal; owner: boolean; onDecided: () => void }) {
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);
  const decide = useAction(async (body: Schemas["TeamDecisionIn"]) => unwrap(await api.POST("/admin-api/team/approvals/decide", { body })),
    { invalidate: [["team-approvals"], ["team"]], done: () => { setRejecting(false); onDecided(); } });
  const pending = proposal.status === "pending";
  // Nia's admin changes are sent by your own page from Clearance; never from here.
  const here = pending && proposal.action_type !== "admin.change";
  return (
    <div className="rounded-[var(--radius-control)] border border-border bg-surface p-3 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="primary">{proposal.action_label}</Badge>
        <Badge tone={proposal.level === "L3" ? "warning" : "neutral"}>{proposal.level === "L3" ? "Always you" : proposal.level === "L2" ? "Alone within your limit" : "You clear it"}</Badge>
        <StatusBadge status={proposal.status} />
      </div>
      <p className="mt-1.5 font-medium text-fg">{proposal.title}</p>
      {proposal.reason && <p className="mt-0.5 text-xs text-muted">{proposal.reason}</p>}
      <div className="mt-1 flex gap-3">
        <button type="button" className="text-xs text-primary hover:underline" onClick={() => setOpen(!open)}>{open ? "Hide details" : "Details"}</button>
      </div>
      {open && <pre className="mt-1 max-h-60 overflow-auto rounded bg-raised p-2 text-[11px] leading-snug text-fg">{JSON.stringify(proposal.edited_payload ?? proposal.payload, null, 2)}</pre>}
      {decide.notice?.tone === "danger" && <div className="mt-2"><Alert>{decide.notice.text}</Alert></div>}
      {owner && here && !rejecting && (
        <div className="mt-2 flex flex-wrap gap-2">
          <Button size="sm" variant="primary" loading={decide.busy} onClick={() => decide.run({ ids: [proposal.id], decision: "approve", note: "" })}><Check className="h-3.5 w-3.5" aria-hidden />Approve</Button>
          <Button size="sm" variant="ghost" disabled={decide.busy} onClick={() => setRejecting(true)}>Reject…</Button>
        </div>
      )}
      {owner && here && rejecting && (
        <div className="mt-2 space-y-2">
          <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why? The agent learns from your reason." aria-label="Reason for rejecting" />
          <div className="flex gap-2">
            <Button size="sm" variant="danger" disabled={!note.trim()} loading={decide.busy} onClick={() => decide.run({ ids: [proposal.id], decision: "reject", note })}>Reject</Button>
            <Button size="sm" variant="ghost" onClick={() => setRejecting(false)}>Cancel</Button>
          </div>
        </div>
      )}
      {pending && proposal.action_type === "admin.change" && <p className="mt-2 text-xs text-muted">Decide it in <Link to="/clearance" className="underline">Clearance</Link>.</p>}
    </div>
  );
}

function Bubble({ message, name, fileHref, owner, onDecided }: { message: Message; name: string; fileHref: (id: number) => string; owner: boolean; onDecided: () => void }) {
  const mine = message.role === "owner";
  return (
    <div className={cn("flex flex-col gap-1.5", mine ? "items-end" : "items-start")}>
      {message.text && (
        <div className={cn("max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed", mine ? "bg-primary text-primary-fg" : "bg-raised text-fg")}>
          {message.text}
          {!mine && (message.tools.length > 0 || message.cost_usd > 0) && (
            <p className="mt-1 text-[11px] text-muted">
              {message.tools.length > 0 && <>Used: {[...new Set(message.tools.map((t) => t.replace(/_/g, " ")))].join(", ")}</>}
              {message.cost_usd > 0 && <>{message.tools.length > 0 ? " · " : ""}{usd(message.cost_usd)}</>}
            </p>
          )}
        </div>
      )}
      {message.attachments.length > 0 && <div className="flex max-w-[85%] flex-wrap justify-end gap-1">{message.attachments.map((f) => <FileChip key={f.id} file={f} href={fileHref(f.id)} />)}</div>}
      {message.proposals.length > 0 && (
        <div className="w-full max-w-[85%] space-y-2">
          <p className="text-[11px] text-muted">{name} proposed:</p>
          {message.proposals.map((p) => <ProposalCard key={p.id} proposal={p} owner={owner} onDecided={onDecided} />)}
        </div>
      )}
    </div>
  );
}

function Conversation({ keyName, name, id, owner, problem, compact = false }: { keyName: string; name: string; id: number; owner: boolean; problem: string; compact?: boolean }) {
  const client = useQueryClient();
  const queryKey = ["team-conversation", keyName, id];
  const path = { params: { path: { key: keyName, conversation_id: id } } };
  const { data, refetch } = useQuery({ queryKey, queryFn: async () => unwrap(await api.GET("/admin-api/team/agents/{key}/conversations/{conversation_id}", path)) });
  const [text, setText] = useState("");
  const [files, setFiles] = useState<FileOut[]>([]);
  const [uploading, setUploading] = useState(0);
  const [pending, setPending] = useState<{ text: string; files: FileOut[] } | null>(null);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const atEnd = useRef(true);
  useEffect(() => {
    const el = box.current;
    if (el && atEnd.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [data?.messages.length, pending]);
  const fileHref = (fileId: number) => `/admin-api/team/agents/${encodeURIComponent(keyName)}/conversations/${id}/files/${fileId}`;

  async function attach(list: FileList | null) {
    if (!list?.length) return;
    setError("");
    for (const file of Array.from(list)) {
      if (files.length + uploading >= 10) { setError("At most 10 files per message."); break; }
      const form = new FormData();
      form.append("file", file);
      setUploading((n) => n + 1);
      try {
        const r = unwrap(await api.POST("/admin-api/team/agents/{key}/conversations/{conversation_id}/files", { ...path, body: form as unknown as { file: string } }));
        setFiles((f) => [...f, r]);
      } catch (e) {
        setError(`${file.name}: ${errorMessage(e)}`);
      } finally {
        setUploading((n) => n - 1);
      }
    }
    if (input.current) input.current.value = "";
  }

  async function send(event?: FormEvent) {
    event?.preventDefault();
    const said = text.trim();
    if ((!said && !files.length) || pending || uploading) return;
    const sent = files;
    setPending({ text: said, files: sent });
    setText("");
    setFiles([]);
    setError("");
    try {
      const r = unwrap(await api.POST("/admin-api/team/agents/{key}/conversations/{conversation_id}/messages", { ...path, body: { text: said, attachment_ids: sent.map((f) => f.id) } }));
      client.setQueryData(queryKey, r);
      void client.invalidateQueries({ queryKey: ["team-chat", keyName] });
      void client.invalidateQueries({ queryKey: ["team-agent", keyName] });
    } catch (e) {
      setError(errorMessage(e));
      setText(said);
      setFiles(sent);
    } finally {
      setPending(null);
    }
  }

  const messages = data?.messages ?? [];
  const decided = () => { void refetch(); void client.invalidateQueries({ queryKey: ["team-agent", keyName] }); };
  return (
    <div className={cn("flex min-h-0 flex-col", compact ? "flex-1" : "h-[32rem]")}>
      <div ref={box} onScroll={(e) => { const el = e.currentTarget; atEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}
        className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain p-4">
        {messages.length === 0 && !pending && (
          <div className="py-8 text-center text-[13px] text-muted">
            <MessageSquare className="mx-auto mb-2 h-5 w-5 text-primary" aria-hidden />
            Ask {name} anything about its work, attach files (exports, invoices, lists, screenshots), or tell it how to work from now on.
          </div>
        )}
        {messages.map((m) => <Bubble key={m.id} message={m} name={name} fileHref={fileHref} owner={owner} onDecided={decided} />)}
        {pending && (
          <>
            <Bubble message={{ id: -1, role: "owner", text: pending.text, attachments: pending.files, proposals: [], tools: [], cost_usd: 0, created_at: "" }}
              name={name} fileHref={fileHref} owner={owner} onDecided={decided} />
            <p className="text-xs text-muted">{name} is working on it…</p>
          </>
        )}
      </div>
      <div className="shrink-0 border-t border-border">
        {error && <div className="px-3 pt-2"><Alert>{error}</Alert></div>}
        {problem && <div className="px-3 pt-2"><Alert tone="warning">{problem}</Alert></div>}
        {owner && (
          <form onSubmit={send} className="p-3">
            {(files.length > 0 || uploading > 0) && (
              <div className="mb-2 flex flex-wrap gap-1">
                {files.map((f) => <FileChip key={f.id} file={f} onRemove={() => setFiles((all) => all.filter((x) => x.id !== f.id))} />)}
                {uploading > 0 && <span className="text-[11px] text-muted">Checking {uploading} file{uploading > 1 ? "s" : ""}…</span>}
              </div>
            )}
            <div className="flex items-end gap-2">
              <input ref={input} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => void attach(e.target.files)} />
              <Button type="button" variant="ghost" disabled={Boolean(problem) || pending !== null} onClick={() => input.current?.click()} aria-label="Attach files"><Paperclip className="h-4 w-4" aria-hidden /></Button>
              <Textarea rows={2} className="min-h-[2.5rem] flex-1 resize-none" placeholder={`Talk to ${name}…`} value={text} disabled={Boolean(problem)}
                onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} aria-label={`Message to ${name}`} />
              <Button type="submit" variant="primary" disabled={Boolean(problem) || uploading > 0 || (!text.trim() && !files.length)} loading={pending !== null} aria-label="Send"><Send className="h-4 w-4" aria-hidden /></Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

/** An agent's conversations: the list, a new one, deleting one (the agent page's card and the chat panel share it). */
function useAgentChat(keyName: string) {
  const client = useQueryClient();
  const chat = useQuery({ queryKey: ["team-chat", keyName], queryFn: async () => unwrap(await api.GET("/admin-api/team/agents/{key}/chat", { params: { path: { key: keyName } } })) });
  const [current, setCurrent] = useState<number | null>(null);
  const conversations = chat.data?.conversations ?? [];
  useEffect(() => { setCurrent(null); }, [keyName]);
  useEffect(() => {
    if (current === null && conversations.length) setCurrent(conversations[0].id);
  }, [conversations, current]);
  const start = useAction(async () => unwrap(await api.POST("/admin-api/team/agents/{key}/conversations", { params: { path: { key: keyName } } })),
    { done: (c) => { setCurrent(c.id); void client.invalidateQueries({ queryKey: ["team-chat", keyName] }); } });
  const remove = useAction(async (id: number) => unwrap(await api.DELETE("/admin-api/team/agents/{key}/conversations/{conversation_id}", { params: { path: { key: keyName, conversation_id: id } } })),
    { done: () => { setCurrent(null); void client.invalidateQueries({ queryKey: ["team-chat", keyName] }); } });
  return { chat, conversations, current, setCurrent, start, remove, problem: chat.data?.problem ?? "" };
}

function ChatTools({ c, owner, small = false }: { c: ReturnType<typeof useAgentChat>; owner: boolean; small?: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      {c.conversations.length > 0 && (
        <Select className={cn("h-8 min-w-0 text-xs", small ? "flex-1" : "max-w-[14rem]")} value={c.current ?? ""} onChange={(e) => c.setCurrent(Number(e.target.value))} aria-label="Conversation">
          {c.conversations.map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}
        </Select>
      )}
      {owner && c.current !== null && <Button size="sm" variant="ghost" loading={c.remove.busy} onClick={() => { if (window.confirm("Delete this conversation and its files? Proposals made in it stay in Clearance.")) c.remove.run(c.current!); }} aria-label="Delete conversation"><Trash2 className="h-3.5 w-3.5" aria-hidden /></Button>}
      {owner && <Button size="sm" loading={c.start.busy} onClick={() => c.start.run(undefined)} aria-label="New conversation"><Plus className="h-3.5 w-3.5" aria-hidden />{small ? "" : "New conversation"}</Button>}
    </div>
  );
}

/** The Chat card at the top of an agent's page. */
export function AgentChatCard({ keyName, name, owner }: { keyName: string; name: string; owner: boolean }) {
  const c = useAgentChat(keyName);
  if (keyName === "nia") {
    return <Card title="Chat"><p className="text-[13px] text-muted">Nia has her own page with chat and voice: <Link to="/nia" className="font-medium text-primary underline">Talk to Nia</Link>.</p></Card>;
  }
  return (
    <Card title={`Chat with ${name}`} padded={false} actions={<ChatTools c={c} owner={owner} />}>
      <Body c={c} keyName={keyName} name={name} owner={owner} />
    </Card>
  );
}

/** The same chat in the panel of the chat button on every page (Nia's panel, another agent chosen). */
export function AgentChatPanel({ keyName, name, owner }: { keyName: string; name: string; owner: boolean }) {
  const c = useAgentChat(keyName);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b border-border px-3 py-1.5"><ChatTools c={c} owner={owner} small /></div>
      <Body c={c} keyName={keyName} name={name} owner={owner} compact />
    </div>
  );
}

function Body({ c, keyName, name, owner, compact = false }: { c: ReturnType<typeof useAgentChat>; keyName: string; name: string; owner: boolean; compact?: boolean }) {
  return (
    <>
      {c.chat.error && <div className="p-3"><Alert>{c.chat.error.message}</Alert></div>}
      {c.start.notice?.tone === "danger" && <div className="p-3"><Alert>{c.start.notice.text}</Alert></div>}
      {c.current !== null
        ? <Conversation key={`${keyName}-${c.current}`} keyName={keyName} name={name} id={c.current} owner={owner} problem={c.problem} compact={compact} />
        : (
          <div className="p-6 text-center text-[13px] text-muted">
            {c.problem ? <Alert tone="warning">{c.problem}</Alert> : owner ? <>Start a <button type="button" className="font-medium text-primary underline" onClick={() => c.start.run(undefined)}>new conversation</button> with {name}.</> : "No conversations yet."}
          </div>
        )}
    </>
  );
}
