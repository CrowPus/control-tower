/* Reading a mail: who wrote to whom and when, the text in a safe frame (pictures from the internet only when asked
   for), the files, the earlier mails of the conversation, and what to do with it. */
import { useQuery } from "@tanstack/react-query";
import {
  Archive, ArrowLeft, Building2, ChevronDown, ChevronRight, Download, FileText, Flag, Forward, ImageOff, Loader2, MailOpen, MoreHorizontal, Paperclip, Pencil,
  Printer, Reply, ReplyAll, ShieldAlert, Trash2,
} from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, errorMessage, unwrap } from "../api";
import { Alert, Badge, cn } from "../ui";
import { avatarColor, frameDocument, fullDate, initials, person, size, textAsHtml, unsubscribeLink, type Account, type Detail, type Prefs } from "./lib";

const DESK: Record<string, string> = {
  unsorted: "Not sorted yet", interested: "Interested", question: "Question", objection: "Objection", not_now: "Not now", out_of_office: "Out of office", bounce: "Returned", other: "Other",
};

export function MailFrame({ id, remote }: { id: number; remote: boolean }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(120);
  const dark = document.documentElement.classList.contains("dark");
  const src = `/admin-api/mail/messages/${id}/frame?images=${remote ? "true" : "false"}&dark=${dark ? "true" : "false"}`;
  useLayoutEffect(() => {
    const el = frame.current;
    if (!el) return;
    let observer: ResizeObserver | null = null;
    const fit = () => {
      const body = el.contentDocument?.body;
      if (body) setHeight(Math.max(60, Math.min(body.scrollHeight + 8, 20000)));
    };
    const loaded = () => {
      fit();
      const body = el.contentDocument?.body;
      if (body && "ResizeObserver" in window) {
        observer = new ResizeObserver(fit);
        observer.observe(body);
      }
      el.contentDocument?.querySelectorAll("img").forEach((img) => img.addEventListener("load", fit));
    };
    el.addEventListener("load", loaded);
    return () => { el.removeEventListener("load", loaded); observer?.disconnect(); };
  }, [src]);
  // The page has its own strict policy (no scripts, no forms; internet pictures only when asked for), so
  // sharing the admin's origin to fit the frame's height carries no risk.
  return <iframe ref={frame} title="Message" sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" src={src} className="block w-full border-0" style={{ height }} />;
}

function Avatar({ name, address, small }: { name: string; address: string; small?: boolean }) {
  return (
    <span className={cn("inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white", small ? "h-7 w-7 text-[11px]" : "h-9 w-9 text-xs")} style={{ background: avatarColor(address) }} aria-hidden>
      {initials(name, address)}
    </span>
  );
}

function Recipients({ label, list }: { label: string; list: string[][] }) {
  if (!list.length) return null;
  return <p className="truncate text-xs text-muted"><span className="text-subtle">{label}: </span>{list.map((p) => person(p)).join(", ")}</p>;
}

export type ReaderAction = "reply" | "reply_all" | "forward" | "archive" | "delete" | "junk" | "not_junk" | "unread" | "flag" | "unflag" | "edit_draft";

function ToolButton({ label, onClick, children, disabled }: { label: string; onClick: () => void; children: React.ReactNode; disabled?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={label} aria-label={label}
      className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-[13px] text-muted hover:bg-raised hover:text-fg disabled:opacity-40">
      {children}
    </button>
  );
}

function ThreadEntry({ item, remote }: { item: Detail["thread"][number]; remote: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-border">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-5 py-2 text-left hover:bg-raised">
        {open ? <ChevronDown className="h-3.5 w-3.5 text-muted" /> : <ChevronRight className="h-3.5 w-3.5 text-muted" />}
        <Avatar name={item.from_name} address={item.from_address} small />
        <span className="w-40 shrink-0 truncate text-[13px] font-medium text-fg">{item.from_name || item.from_address}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-muted">{item.preview}</span>
        <span className="shrink-0 text-xs text-subtle">{fullDate(item.received_at)}</span>
        {item.folder && <Badge>{item.folder}</Badge>}
      </button>
      {open && <div className="px-5 pb-3 pl-14"><MailFrame id={item.id} remote={remote} /></div>}
    </div>
  );
}

export function Reader({ id, account, prefs, onPrefs, onAction, onBack }: {
  id: number;
  account: Account | undefined;
  prefs: Prefs;
  onPrefs: (p: Prefs) => void;
  onAction: (action: ReaderAction, detail: Detail) => void;
  onBack: () => void;
}) {
  const { data, error, isLoading } = useQuery({
    queryKey: ["mail-message", id],
    queryFn: async () => unwrap(await api.GET("/admin-api/mail/messages/{message_id}", { params: { path: { message_id: id } } })),
  });
  const [remote, setRemote] = useState(false);
  const [more, setMore] = useState(false);
  const [details, setDetails] = useState(false);
  useEffect(() => { setRemote(false); setMore(false); setDetails(false); }, [id]);
  if (isLoading) return <div className="flex h-full items-center justify-center text-muted"><Loader2 className="h-5 w-5 animate-spin" /></div>;
  if (error || !data) return <div className="p-4"><Alert>{error ? errorMessage(error) : "This message is gone."}</Alert></div>;
  const trusted = prefs.trustedSenders.includes(data.from_address);
  const showRemote = remote || trusted;
  const unsubscribe = unsubscribeLink(data.list_unsubscribe);
  const canSend = data.can_send;
  const isDraft = data.is_draft || data.folder_role === "drafts";
  return (
    <article className="flex h-full min-h-0 flex-col" aria-label="Message">
      <div className="flex flex-wrap items-center gap-0.5 border-b border-border px-2 py-1">
        <button type="button" onClick={onBack} className="mr-1 rounded p-1.5 text-muted hover:bg-raised hover:text-fg lg:hidden" aria-label="Back to the list"><ArrowLeft className="h-4 w-4" /></button>
        {isDraft && canSend ? (
          <ToolButton label="Edit draft" onClick={() => onAction("edit_draft", data)}><Pencil className="h-4 w-4" />Edit draft</ToolButton>
        ) : (
          <>
            <ToolButton label="Reply (R)" onClick={() => onAction("reply", data)} disabled={!canSend}><Reply className="h-4 w-4" /><span className="hidden sm:inline">Reply</span></ToolButton>
            <ToolButton label="Reply all (A)" onClick={() => onAction("reply_all", data)} disabled={!canSend}><ReplyAll className="h-4 w-4" /><span className="hidden sm:inline">Reply all</span></ToolButton>
            <ToolButton label="Forward (F)" onClick={() => onAction("forward", data)} disabled={!canSend}><Forward className="h-4 w-4" /><span className="hidden sm:inline">Forward</span></ToolButton>
          </>
        )}
        <span className="mx-1 h-5 w-px bg-border" />
        {data.folder_role !== "archive" && <ToolButton label="Archive (E)" onClick={() => onAction("archive", data)}><Archive className="h-4 w-4" /></ToolButton>}
        <ToolButton label={data.folder_role === "trash" ? "Delete for good" : "Delete (Del)"} onClick={() => onAction("delete", data)}><Trash2 className="h-4 w-4" /></ToolButton>
        {data.folder_role === "junk"
          ? <ToolButton label="Not junk" onClick={() => onAction("not_junk", data)}><ShieldAlert className="h-4 w-4" />Not junk</ToolButton>
          : <ToolButton label="Junk" onClick={() => onAction("junk", data)}><ShieldAlert className="h-4 w-4" /></ToolButton>}
        <ToolButton label="Mark unread (U)" onClick={() => onAction("unread", data)}><MailOpen className="h-4 w-4" /></ToolButton>
        <ToolButton label={data.flagged ? "Unflag (S)" : "Flag (S)"} onClick={() => onAction(data.flagged ? "unflag" : "flag", data)}>
          <Flag className={cn("h-4 w-4", data.flagged && "fill-danger text-danger")} />
        </ToolButton>
        <div className="relative">
          <ToolButton label="More" onClick={() => setMore(!more)}><MoreHorizontal className="h-4 w-4" /></ToolButton>
          {more && (
            <div className="absolute right-0 top-9 z-20 w-52 rounded-md border border-border bg-surface py-1 shadow-[var(--shadow)]" onMouseLeave={() => setMore(false)}>
              <a href={`/admin-api/mail/messages/${data.id}/source`} className="flex items-center gap-2 px-3 py-1.5 text-[13px] hover:bg-raised"><Download className="h-3.5 w-3.5" />Download (.eml)</a>
              <button type="button" className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-raised" onClick={() => { setMore(false); printMessage(data); }}>
                <Printer className="h-3.5 w-3.5" />Print
              </button>
            </div>
          )}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <header className="px-5 pb-3 pt-4">
          <h1 className="text-[17px] font-semibold leading-snug text-fg">{data.subject || "(no subject)"}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {account && <Badge>{account.display_name || account.address} · {data.folder}</Badge>}
          </div>
          <div className="mt-3 flex items-start gap-3">
            <Avatar name={data.from_name} address={data.from_address} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] text-fg"><span className="font-semibold">{data.from_name || data.from_address}</span>{data.from_name && <span className="ml-1.5 text-muted">&lt;{data.from_address}&gt;</span>}</p>
              <button type="button" onClick={() => setDetails(!details)} className="block max-w-full truncate text-left text-xs text-muted hover:text-fg">
                To: {data.to.map((p) => p[0] || p[1]).join(", ") || "—"}{data.cc.length ? `, Cc: ${data.cc.map((p) => p[0] || p[1]).join(", ")}` : ""}
              </button>
              {details && (
                <div className="mt-1 space-y-0.5">
                  <Recipients label="To" list={data.to} />
                  <Recipients label="Cc" list={data.cc} />
                  <Recipients label="Bcc" list={data.bcc} />
                  <Recipients label="Reply to" list={data.reply_to} />
                </div>
              )}
            </div>
            <time className="shrink-0 text-xs text-muted" dateTime={data.received_at}>{fullDate(data.sent_at ?? data.received_at)}</time>
          </div>
        </header>
        {data.remote_images && !showRemote && (
          <div className="mx-5 mb-3 flex flex-wrap items-center gap-2 rounded-md bg-raised px-3 py-2 text-xs text-muted">
            <ImageOff className="h-3.5 w-3.5" aria-hidden />
            <span className="flex-1">Pictures from the internet are blocked, so the sender cannot see that you opened this mail.</span>
            <button type="button" className="font-medium text-primary hover:underline" onClick={() => setRemote(true)}>Show pictures</button>
            <button type="button" className="font-medium text-primary hover:underline" onClick={() => { onPrefs({ ...prefs, trustedSenders: [...prefs.trustedSenders, data.from_address] }); setRemote(true); }}>
              Always from {data.from_address}
            </button>
          </div>
        )}
        {data.body_state === "too_large" && <div className="mx-5 mb-3"><Alert tone="warning">This mail is larger than 25 MB, so only its details are shown here. Download it (More → Download) to open it whole.</Alert></div>}
        <div className="px-5 pb-4"><MailFrame id={data.id} remote={showRemote} /></div>
        {data.attachments.filter((a) => !a.inline).length > 0 && (
          <div className="border-t border-border px-5 py-3">
            <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted"><Paperclip className="h-3.5 w-3.5" />{data.attachments.filter((a) => !a.inline).length} attachment(s)</p>
            <ul className="flex flex-wrap gap-2">
              {data.attachments.filter((a) => !a.inline).map((a) => (
                <li key={a.index}>
                  <a href={a.url} className="flex w-56 items-center gap-2 rounded-md border border-border px-2.5 py-2 hover:bg-raised" download={a.filename}>
                    <FileText className="h-5 w-5 shrink-0 text-muted" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] text-fg">{a.filename}</span>
                      <span className="block text-[11px] text-subtle">{size(a.size)}</span>
                    </span>
                    <Download className="h-4 w-4 shrink-0 text-muted" aria-hidden />
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}
        {unsubscribe && (
          <p className="px-5 pb-3 text-xs text-subtle">Newsletter · <a href={unsubscribe} target="_blank" rel="noreferrer noopener" className="text-primary hover:underline">Unsubscribe</a></p>
        )}
        {data.thread.length > 0 && (
          <section className="border-t border-border">
            <p className="px-5 py-2 text-xs font-medium text-muted">Earlier in this conversation ({data.thread.length})</p>
            {data.thread.map((t) => <ThreadEntry key={t.id} item={t} remote={showRemote} />)}
          </section>
        )}
        {canSend && !isDraft && (
          <div className="flex gap-2 px-5 py-4">
            <button type="button" onClick={() => onAction("reply", data)} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-[13px] text-fg hover:bg-raised"><Reply className="h-4 w-4" />Reply</button>
            <button type="button" onClick={() => onAction("reply_all", data)} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-[13px] text-fg hover:bg-raised"><ReplyAll className="h-4 w-4" />Reply all</button>
            <button type="button" onClick={() => onAction("forward", data)} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-[13px] text-fg hover:bg-raised"><Forward className="h-4 w-4" />Forward</button>
          </div>
        )}
      </div>
    </article>
  );
}

function printMessage(data: Detail) {
  const win = window.open("", "_blank", "noopener=no,width=800,height=900");
  if (!win) return;
  const head = `<h2 style="font:600 18px Arial">${escapeHtml(data.subject)}</h2><p style="font:13px Arial;color:#555">From: ${escapeHtml(person([data.from_name, data.from_address]))}<br>To: ${escapeHtml(data.to.map((p) => person(p)).join(", "))}<br>${escapeHtml(fullDate(data.sent_at ?? data.received_at))}</p><hr>`;
  win.document.write(frameDocument(head + (data.html || textAsHtml(data.text)), { remote: false, dark: false }));
  win.document.close();
  win.focus();
  win.print();
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
