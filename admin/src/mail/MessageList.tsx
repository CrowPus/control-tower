/* The list of mails in a folder (or across accounts): unread in bold, flags, files, answered; select
   several (click, Ctrl/Shift-click, the box) to read, flag, move, archive or delete them together, or
   drag them onto a folder. More loads as the list ends. */
import { Archive, CornerUpLeft, CornerUpRight, Flag, FolderInput, Loader2, MailOpen, Mail as MailIcon, Paperclip, ShieldAlert, Trash2 } from "lucide-react";
import { type DragEvent } from "react";
import { cn } from "../ui";
import { avatarColor, initials, listDate, people, type Account, type Folder, type Summary } from "./lib";

export type ListAction = "read" | "unread" | "flag" | "unflag" | "archive" | "delete" | "junk" | "not_junk";

export const FILTERS = [["", "All"], ["unread", "Unread"], ["flagged", "Flagged"], ["attachments", "Files"]] as const;

export function MessageList({
  items, selected, open, folderRole, accounts, showAccount, filter, onFilter, onOpen, onSelect, onAction, onMove, loadingMore, hasMore, onMore, olderOnServer,
  onOlder, loadingOlder, loading, empty,
}: {
  items: Summary[];
  selected: Set<number>;
  open: number | null;
  folderRole: string;
  accounts: Account[];
  showAccount: boolean;
  filter: string;
  onFilter: (f: string) => void;
  onOpen: (m: Summary) => void;
  onSelect: (next: Set<number>) => void;
  onAction: (action: ListAction, ids: number[]) => void;
  onMove: (ids: number[], folder: Folder) => void;
  loadingMore: boolean;
  hasMore: boolean;
  onMore: () => void;
  olderOnServer: boolean;
  onOlder: () => void;
  loadingOlder: boolean;
  loading: boolean;
  empty: string;
}) {
  const outgoing = folderRole === "sent" || folderRole === "drafts";
  const ids = [...selected];
  const chosen = items.filter((m) => selected.has(m.id));
  const sameAccount = chosen.length > 0 && chosen.every((m) => m.account_id === chosen[0].account_id);
  const moveTargets = sameAccount ? accounts.find((a) => a.id === chosen[0].account_id)?.folders ?? [] : [];
  const allUnread = chosen.length > 0 && chosen.every((m) => !m.seen);
  const allFlagged = chosen.length > 0 && chosen.every((m) => m.flagged);

  function click(m: Summary, e: React.MouseEvent) {
    if (e.ctrlKey || e.metaKey) {
      const next = new Set(selected);
      if (next.has(m.id)) next.delete(m.id); else next.add(m.id);
      onSelect(next);
      return;
    }
    if (e.shiftKey && open != null) {
      const a = items.findIndex((x) => x.id === open);
      const b = items.findIndex((x) => x.id === m.id);
      const [lo, hi] = a < b ? [a, b] : [b, a];
      onSelect(new Set(items.slice(lo, hi + 1).map((x) => x.id)));
      return;
    }
    onSelect(new Set());
    onOpen(m);
  }

  function drag(m: Summary, e: DragEvent) {
    const moving = selected.has(m.id) ? ids : [m.id];
    e.dataTransfer.setData("application/x-r365-mail", JSON.stringify({ ids: moving, account: m.account_id }));
    e.dataTransfer.effectAllowed = "move";
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-10 items-center gap-1 border-b border-border px-2 py-1">
        <input type="checkbox" aria-label="Select all" className="mx-1.5 h-3.5 w-3.5 accent-[var(--color-primary)]"
          checked={items.length > 0 && selected.size === items.length} onChange={(e) => onSelect(e.target.checked ? new Set(items.map((m) => m.id)) : new Set())} />
        {selected.size > 0 ? (
          <div className="flex flex-wrap items-center gap-0.5">
            <span className="mr-1 text-xs text-muted">{selected.size} selected</span>
            <IconButton label={allUnread ? "Mark read" : "Mark unread"} onClick={() => onAction(allUnread ? "read" : "unread", ids)}>{allUnread ? <MailOpen className="h-4 w-4" /> : <MailIcon className="h-4 w-4" />}</IconButton>
            <IconButton label={allFlagged ? "Unflag" : "Flag"} onClick={() => onAction(allFlagged ? "unflag" : "flag", ids)}><Flag className="h-4 w-4" /></IconButton>
            {folderRole !== "archive" && <IconButton label="Archive" onClick={() => onAction("archive", ids)}><Archive className="h-4 w-4" /></IconButton>}
            <IconButton label={folderRole === "junk" ? "Not junk" : "Junk"} onClick={() => onAction(folderRole === "junk" ? "not_junk" : "junk", ids)}><ShieldAlert className="h-4 w-4" /></IconButton>
            <IconButton label="Delete" onClick={() => onAction("delete", ids)}><Trash2 className="h-4 w-4" /></IconButton>
            {sameAccount && (
              <label className="relative inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded text-muted hover:bg-raised hover:text-fg" title="Move to folder">
                <FolderInput className="h-4 w-4" />
                <select aria-label="Move to folder" className="absolute inset-0 cursor-pointer opacity-0" value="" onChange={(e) => {
                  const target = moveTargets.find((f) => f.id === Number(e.target.value));
                  if (target) onMove(ids, target);
                }}>
                  <option value="">Move to…</option>
                  {moveTargets.map((f) => <option key={f.id} value={f.id}>{"  ".repeat(f.path.length - 1)}{f.name}</option>)}
                </select>
              </label>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-0.5" role="tablist" aria-label="Show">
            {FILTERS.map(([key, label]) => (
              <button key={key} type="button" role="tab" aria-selected={filter === key} onClick={() => onFilter(key)}
                className={cn("rounded px-2 py-1 text-xs", filter === key ? "bg-raised font-medium text-fg" : "text-muted hover:text-fg")}>{label}</button>
            ))}
          </div>
        )}
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto" aria-label="Messages">
        {loading && <li className="flex justify-center py-10 text-muted"><Loader2 className="h-5 w-5 animate-spin" /></li>}
        {!loading && items.length === 0 && <li className="px-4 py-12 text-center text-[13px] text-muted">{empty}</li>}
        {items.map((m) => {
          const who = outgoing ? (m.to.length ? `To: ${people(m.to)}` : "(no recipient)") : m.from_name || m.from_address || "(unknown sender)";
          const account = showAccount ? accounts.find((a) => a.id === m.account_id) : undefined;
          return (
            <li key={m.id} draggable onDragStart={(e) => drag(m, e)}
              className={cn("group relative border-b border-border", open === m.id ? "bg-primary-soft/60" : selected.has(m.id) ? "bg-raised" : "hover:bg-raised/60")}>
              {!m.seen && <span className="absolute inset-y-0 left-0 w-[3px] bg-primary" aria-hidden />}
              <div className="flex cursor-pointer gap-2.5 px-3 py-2.5" onClick={(e) => click(m, e)} role="button" tabIndex={-1} aria-label={`${who}: ${m.subject}`}>
                <div className="relative mt-0.5 shrink-0">
                  <span className={cn("flex h-8 w-8 items-center justify-center rounded-full text-[11px] font-semibold text-white", selected.has(m.id) && "invisible", "group-hover:invisible")}
                    style={{ background: avatarColor(outgoing ? m.to[0]?.[1] ?? "" : m.from_address) }} aria-hidden>
                    {outgoing ? initials(m.to[0]?.[0] ?? "", m.to[0]?.[1] ?? "?") : initials(m.from_name, m.from_address)}
                  </span>
                  <input type="checkbox" aria-label="Select" checked={selected.has(m.id)} onClick={(e) => e.stopPropagation()}
                    onChange={() => { const next = new Set(selected); if (next.has(m.id)) next.delete(m.id); else next.add(m.id); onSelect(next); }}
                    className={cn("absolute left-2 top-2 h-4 w-4 accent-[var(--color-primary)]", selected.has(m.id) ? "visible" : "invisible group-hover:visible")} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2">
                    <span className={cn("min-w-0 flex-1 truncate text-[13px]", m.seen ? "text-fg" : "font-semibold text-fg")}>{who}</span>
                    <span className={cn("shrink-0 text-[11px]", m.seen ? "text-subtle" : "font-semibold text-primary")}>{listDate(m.received_at)}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {m.answered && <CornerUpLeft className="h-3 w-3 shrink-0 text-muted" aria-label="Answered" />}
                    {m.forwarded && <CornerUpRight className="h-3 w-3 shrink-0 text-muted" aria-label="Forwarded" />}
                    <span className={cn("min-w-0 flex-1 truncate text-[13px]", m.seen ? "text-fg/90" : "font-semibold text-fg")}>{m.subject || "(no subject)"}</span>
                    {m.has_attachments && <Paperclip className="h-3.5 w-3.5 shrink-0 text-muted" aria-label="Has files" />}
                    <button type="button" aria-label={m.flagged ? "Unflag" : "Flag"} onClick={(e) => { e.stopPropagation(); onAction(m.flagged ? "unflag" : "flag", [m.id]); }}
                      className={cn("shrink-0", m.flagged ? "text-danger" : "invisible text-muted hover:text-fg group-hover:visible")}>
                      <Flag className={cn("h-3.5 w-3.5", m.flagged && "fill-danger")} />
                    </button>
                  </div>
                  <p className="truncate text-xs text-muted">{m.is_draft && <span className="mr-1 text-danger">[Draft]</span>}{m.preview}</p>
                  {account && <p className="mt-0.5 truncate text-[11px] text-subtle">{account.display_name || account.address}</p>}
                </div>
              </div>
            </li>
          );
        })}
        {hasMore && (
          <li className="flex justify-center py-3">
            <button type="button" onClick={onMore} className="text-xs text-primary hover:underline" disabled={loadingMore}>{loadingMore ? "Loading…" : "Show more"}</button>
          </li>
        )}
        {!hasMore && olderOnServer && (
          <li className="flex justify-center py-3">
            <button type="button" onClick={onOlder} className="text-xs text-primary hover:underline" disabled={loadingOlder}>{loadingOlder ? "Fetching from the server…" : "Get older mail from the server"}</button>
          </li>
        )}
      </ul>
    </div>
  );
}

function IconButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" title={label} aria-label={label} onClick={onClick} className="inline-flex h-7 w-7 items-center justify-center rounded text-muted hover:bg-raised hover:text-fg">{children}</button>
  );
}
