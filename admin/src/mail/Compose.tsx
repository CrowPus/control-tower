/* Writing a mail: from which account, to/cc/bcc with suggestions, subject, formatted text with the
   writer's signature, files (button or drop), saved as one writes; Send waits the writer's undo window,
   or a time they choose. Closing keeps the draft (also in the account's Drafts folder). */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, ChevronDown, Loader2, Paperclip, Send, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, unwrap } from "../api";
import { Alert, Button, Dialog, Input, cn } from "../ui";
import { Editor, type EditorHandle } from "./Editor";
import { size, type Account, type Draft, type Outbox, type Prefs } from "./lib";
import { Recipients } from "./Recipients";

type Fields = { account_id: number; to: string[]; cc: string[]; bcc: string[]; subject: string; html: string };

function fieldsOf(d: Draft): Fields {
  return { account_id: d.account_id, to: d.to, cc: d.cc, bcc: d.bcc, subject: d.subject, html: d.html };
}

export function Compose({ draftId, accounts, prefs, onClose, onSent }: {
  draftId: number;
  accounts: Account[];
  prefs: Prefs;
  onClose: () => void;
  onSent: (outbox: Outbox) => void;
}) {
  const client = useQueryClient();
  const { data: draft, error } = useQuery({
    queryKey: ["mail-draft", draftId],
    queryFn: async () => unwrap(await api.GET("/admin-api/mail/drafts/{draft_id}", { params: { path: { draft_id: draftId } } })),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  if (error) return <div className="p-4"><Alert>{errorMessage(error)}</Alert></div>;
  if (!draft) return <div className="flex h-full items-center justify-center text-muted"><Loader2 className="h-5 w-5 animate-spin" /></div>;
  return <Form key={draft.id} draft={draft} accounts={accounts} prefs={prefs} onClose={() => { void client.invalidateQueries({ queryKey: ["mail-drafts"] }); onClose(); }} onSent={onSent} />;
}

function Form({ draft, accounts, prefs, onClose, onSent }: { draft: Draft; accounts: Account[]; prefs: Prefs; onClose: () => void; onSent: (o: Outbox) => void }) {
  const client = useQueryClient();
  const [fields, setFields] = useState<Fields>(fieldsOf(draft));
  const [uploads, setUploads] = useState(draft.uploads);
  const [showCc, setShowCc] = useState(draft.cc.length > 0 || draft.bcc.length > 0);
  const [state, setState] = useState<"saved" | "dirty" | "saving">("saved");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState<"" | "send" | "upload" | "close">("");
  const [scheduling, setScheduling] = useState(false);
  const [when, setWhen] = useState("");
  const [menu, setMenu] = useState(false);
  const editor = useRef<EditorHandle>(null);
  const latest = useRef(fields);
  const fileInput = useRef<HTMLInputElement>(null);
  latest.current = fields;
  const senders = accounts.filter((a) => a.can_send);

  const save = useCallback(async (server = false) => {
    setState("saving");
    try {
      unwrap(await api.PUT("/admin-api/mail/drafts/{draft_id}", { params: { path: { draft_id: draft.id }, query: { server } }, body: latest.current }));
      setState("saved");
    } catch (e) {
      setState("dirty");
      setProblem(errorMessage(e));
    }
  }, [draft.id]);

  // Saved a moment after each change.
  useEffect(() => {
    if (state !== "dirty") return;
    const t = setTimeout(() => void save(false), 2500);
    return () => clearTimeout(t);
  }, [fields, state, save]);

  useEffect(() => {
    if (draft.mode === "new" && draft.to.length === 0) return;
    editor.current?.focus();
  }, [draft.mode, draft.to.length]);

  const change = (patch: Partial<Fields>) => { setFields((f) => ({ ...f, ...patch })); setState("dirty"); setProblem(""); };

  async function attach(files: File[]) {
    setBusy("upload");
    for (const file of files) {
      const form = new FormData();
      form.append("file", file);
      try {
        const row = unwrap(await api.POST("/admin-api/mail/drafts/{draft_id}/files", { params: { path: { draft_id: draft.id } }, body: form as unknown as { file: string } }));
        setUploads((u) => [...u, row]);
      } catch (e) {
        setProblem(`${file.name}: ${errorMessage(e)}`);
      }
    }
    setBusy("");
  }

  async function detach(id: number) {
    await api.DELETE("/admin-api/mail/drafts/{draft_id}/files/{upload_id}", { params: { path: { draft_id: draft.id, upload_id: id } } });
    setUploads((u) => u.filter((x) => x.id !== id));
  }

  async function send(sendAt?: string) {
    setBusy("send");
    setProblem("");
    try {
      latest.current = { ...latest.current, html: editor.current?.html() ?? latest.current.html };
      unwrap(await api.PUT("/admin-api/mail/drafts/{draft_id}", { params: { path: { draft_id: draft.id } }, body: latest.current }));
      const outbox = unwrap(await api.POST("/admin-api/mail/drafts/{draft_id}/send", {
        params: { path: { draft_id: draft.id } },
        body: sendAt ? { send_at: new Date(sendAt).toISOString(), undo_seconds: 0 } : { undo_seconds: prefs.undoSeconds },
      }));
      // Closed first: the draft is gone, so nothing may ask for it again.
      onSent(outbox);
      setTimeout(() => client.removeQueries({ queryKey: ["mail-draft", draft.id] }), 0);
      void client.invalidateQueries({ queryKey: ["mail"] });
      void client.invalidateQueries({ queryKey: ["mail-drafts"] });
    } catch (e) {
      setProblem(errorMessage(e));
      setBusy("");
    }
  }

  async function close() {
    setBusy("close");
    latest.current = { ...latest.current, html: editor.current?.html() ?? latest.current.html };
    await save(true);
    onClose();
    setTimeout(() => client.removeQueries({ queryKey: ["mail-draft", draft.id] }), 0);
    void client.invalidateQueries({ queryKey: ["mail"] });
  }

  async function discard() {
    if (!window.confirm("Discard this draft?")) return;
    await api.DELETE("/admin-api/mail/drafts/{draft_id}", { params: { path: { draft_id: draft.id } } });
    onClose();
    setTimeout(() => client.removeQueries({ queryKey: ["mail-draft", draft.id] }), 0);
    void client.invalidateQueries({ queryKey: ["mail"] });
    void client.invalidateQueries({ queryKey: ["mail-drafts"] });
  }

  const title = fields.subject || (draft.mode === "forward" ? "Forward" : draft.mode.startsWith("reply") ? "Reply" : "New message");

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface"
      onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) e.preventDefault(); }}
      onDrop={(e) => { if (e.dataTransfer.files.length && !(e.target as HTMLElement).closest(".mail-editor")) { e.preventDefault(); void attach(Array.from(e.dataTransfer.files)); } }}
      onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); void close(); } }}>
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <h2 className="min-w-0 flex-1 truncate text-[14px] font-semibold text-fg">{title}</h2>
        <span className="text-xs text-subtle" aria-live="polite">{state === "saving" ? "Saving…" : state === "saved" ? "Draft saved" : ""}</span>
        <button type="button" onClick={() => void close()} className="rounded p-1 text-muted hover:bg-raised hover:text-fg" aria-label="Close (the draft is kept)" title="Close (the draft is kept)">
          {busy === "close" ? <Loader2 className="h-4 w-4 animate-spin" /> : <X className="h-4 w-4" />}
        </button>
      </header>
      {senders.length > 1 && (
        <div className="flex items-center gap-2 border-b border-border px-3 py-1.5">
          <span className="w-10 shrink-0 text-[13px] text-muted">From</span>
          <select aria-label="From" value={fields.account_id} onChange={(e) => change({ account_id: Number(e.target.value) })}
            className="h-7 min-w-0 flex-1 rounded border-0 bg-transparent text-[13px] text-fg hover:bg-raised">
            {senders.map((a) => <option key={a.id} value={a.id}>{a.display_name ? `${a.display_name} <${a.address}>` : a.address}</option>)}
          </select>
        </div>
      )}
      <div className="relative">
        <Recipients label="To" value={fields.to} onChange={(to) => change({ to })} autoFocus={draft.mode === "new" || draft.mode === "forward"} />
        {!showCc && <button type="button" onClick={() => setShowCc(true)} className="absolute right-3 top-2 text-xs text-muted hover:text-fg">Cc Bcc</button>}
      </div>
      {showCc && <Recipients label="Cc" value={fields.cc} onChange={(cc) => change({ cc })} />}
      {showCc && <Recipients label="Bcc" value={fields.bcc} onChange={(bcc) => change({ bcc })} />}
      <div className="border-b border-border px-3">
        <input aria-label="Subject" placeholder="Subject" value={fields.subject} onChange={(e) => change({ subject: e.target.value })}
          className="h-9 w-full bg-transparent text-[14px] font-medium text-fg outline-none placeholder:text-subtle" />
      </div>
      <div className="flex min-h-0 flex-1 flex-col">
        <Editor ref={editor} initial={draft.html} onChange={(html) => change({ html })} onFiles={(files) => void attach(files)} onSubmit={() => void send()} placeholder="Write your message…" />
      </div>
      {uploads.length > 0 && (
        <ul className="flex flex-wrap gap-1.5 border-t border-border px-3 py-2">
          {uploads.map((u) => (
            <li key={u.id} className="inline-flex items-center gap-1.5 rounded-md border border-border bg-raised px-2 py-1 text-xs">
              <Paperclip className="h-3.5 w-3.5 text-muted" aria-hidden />
              <a href={u.url} target="_blank" rel="noreferrer" className="max-w-[14rem] truncate text-fg hover:underline">{u.filename}</a>
              <span className="text-subtle">{size(u.size)}</span>
              <button type="button" onClick={() => void detach(u.id)} className="text-muted hover:text-danger" aria-label={`Remove ${u.filename}`}><X className="h-3 w-3" /></button>
            </li>
          ))}
        </ul>
      )}
      {problem && <div className="px-3 pt-2"><Alert>{problem}</Alert></div>}
      <footer className="flex items-center gap-2 border-t border-border px-3 py-2">
        <div className="relative inline-flex">
          <Button variant="primary" className="rounded-r-none" loading={busy === "send"} onClick={() => void send()} title="Send (Ctrl+Enter)">
            <Send className="h-3.5 w-3.5" aria-hidden />Send
          </Button>
          <Button variant="primary" className="rounded-l-none border-l border-white/20 px-1.5" aria-label="More send options" onClick={() => setMenu(!menu)}>
            <ChevronDown className="h-3.5 w-3.5" />
          </Button>
          {menu && (
            <div className="absolute bottom-10 left-0 z-20 w-48 rounded-md border border-border bg-surface py-1 shadow-[var(--shadow)]">
              <button type="button" className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-raised" onClick={() => { setMenu(false); setScheduling(true); }}>
                <CalendarClock className="h-3.5 w-3.5" aria-hidden />Schedule send…
              </button>
            </div>
          )}
        </div>
        <Button variant="ghost" onClick={() => fileInput.current?.click()} loading={busy === "upload"} title="Attach files (or drop them here)">
          <Paperclip className="h-3.5 w-3.5" aria-hidden />Attach
        </Button>
        <input ref={fileInput} type="file" multiple hidden onChange={(e) => { void attach(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
        <span className="flex-1" />
        <button type="button" onClick={() => void discard()} className="rounded p-1.5 text-muted hover:bg-raised hover:text-danger" aria-label="Discard draft" title="Discard draft">
          <Trash2 className="h-4 w-4" />
        </button>
      </footer>
      <Dialog open={scheduling} onClose={() => setScheduling(false)} title="Schedule send"
        footer={<><Button onClick={() => setScheduling(false)}>Cancel</Button><Button variant="primary" disabled={!when} onClick={() => { setScheduling(false); void send(when); }}>Schedule</Button></>}>
        <p className="mb-2 text-[13px] text-muted">The mail waits in your Outbox and goes out at this time (you can stop it until then).</p>
        <div className="mb-2 flex flex-wrap gap-1.5">
          {[["Tomorrow 08:00", 1, 8], ["Tomorrow 13:00", 1, 13], ["Monday 08:00", ((8 - new Date().getDay()) % 7) || 7, 8]].map(([label, days, hour]) => {
            const d = new Date();
            d.setDate(d.getDate() + Number(days));
            d.setHours(Number(hour), 0, 0, 0);
            const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
            return <button key={String(label)} type="button" onClick={() => setWhen(local)} className={cn("rounded-full border border-border px-2.5 py-1 text-xs", when === local ? "bg-raised text-fg" : "text-muted hover:text-fg")}>{label}</button>;
          })}
        </div>
        <Input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} aria-label="Send at" />
      </Dialog>
    </div>
  );
}
