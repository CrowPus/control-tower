/* The left side of Mail: the views across accounts (all inboxes, flagged, drafts, outbox), then each
   account with its folders as a tree, the unread counts, and what can be done with a folder (new
   folder inside, rename, delete, empty Trash/Junk). Drop mails on a folder to move them there. */
import { AlertTriangle, Archive, ChevronDown, ChevronRight, FileText, Flag, Folder as FolderIcon, Inbox, MoreHorizontal, PenSquare, RefreshCw, Send, Settings, ShieldAlert, Trash2, Upload } from "lucide-react";
import { useState, type DragEvent, type ReactNode } from "react";
import { cn } from "../ui";
import { placeKey, type Account, type Folder, type Overview, type Place } from "./lib";

const ICON: Record<string, typeof Inbox> = { inbox: Inbox, sent: Send, drafts: FileText, archive: Archive, junk: ShieldAlert, trash: Trash2 };

function Row({ active, onClick, icon, label, count, strong, indent = 0, onDrop, menu, title }: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
  count?: number;
  strong?: boolean;
  indent?: number;
  onDrop?: (e: DragEvent) => void;
  menu?: ReactNode;
  title?: string;
}) {
  const [over, setOver] = useState(false);
  return (
    <div className={cn("group relative flex items-center rounded-md", active ? "bg-raised" : "hover:bg-raised/70", over && "ring-2 ring-primary")}
      onDragOver={onDrop ? (e) => { if (e.dataTransfer.types.includes("application/x-r365-mail")) { e.preventDefault(); setOver(true); } } : undefined}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop ? (e) => { setOver(false); onDrop(e); } : undefined}>
      <button type="button" onClick={onClick} title={title ?? label} style={{ paddingLeft: 8 + indent * 14 }}
        className={cn("flex min-w-0 flex-1 items-center gap-2 py-1.5 pr-2 text-left text-[13px]", active ? "font-medium text-fg" : "text-muted hover:text-fg")}>
        <span className="shrink-0 text-muted">{icon}</span>
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {count ? <span className={cn("shrink-0 text-xs tabular-nums", strong ? "font-semibold text-primary" : "text-subtle")}>{count}</span> : null}
      </button>
      {menu}
    </div>
  );
}

function FolderMenu({ folder, onNew, onRename, onDelete, onEmpty }: { folder: Folder; onNew: () => void; onRename: () => void; onDelete: () => void; onEmpty: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button type="button" aria-label={`${folder.name} options`} onClick={() => setOpen(!open)} className="mr-1 rounded p-0.5 text-muted opacity-0 hover:text-fg group-hover:opacity-100 focus:opacity-100">
        <MoreHorizontal className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div className="absolute right-0 top-6 z-30 w-44 rounded-md border border-border bg-surface py-1 shadow-[var(--shadow)]" onMouseLeave={() => setOpen(false)}>
          <MenuItem onClick={() => { setOpen(false); onNew(); }}>New folder inside</MenuItem>
          {!folder.role && <MenuItem onClick={() => { setOpen(false); onRename(); }}>Rename</MenuItem>}
          {!folder.role && <MenuItem danger onClick={() => { setOpen(false); onDelete(); }}>Delete folder</MenuItem>}
          {(folder.role === "trash" || folder.role === "junk") && <MenuItem danger onClick={() => { setOpen(false); onEmpty(); }}>Empty {folder.name}</MenuItem>}
        </div>
      )}
    </div>
  );
}

function MenuItem({ onClick, children, danger }: { onClick: () => void; children: ReactNode; danger?: boolean }) {
  return <button type="button" onClick={onClick} className={cn("block w-full px-3 py-1.5 text-left text-[13px] hover:bg-raised", danger ? "text-danger" : "text-fg")}>{children}</button>;
}

export function Folders({ overview, place, onPlace, onCompose, onMove, onSync, onSettings, onNewFolder, onRename, onDelete, onEmpty, syncing }: {
  overview: Overview;
  place: Place;
  onPlace: (p: Place) => void;
  onCompose: () => void;
  onMove: (ids: number[], folder: Folder) => void;
  onSync: () => void;
  onSettings: () => void;
  onNewFolder: (account: Account, parent?: Folder) => void;
  onRename: (folder: Folder) => void;
  onDelete: (folder: Folder) => void;
  onEmpty: (folder: Folder) => void;
  syncing: boolean;
}) {
  const [closed, setClosed] = useState<Set<number>>(new Set());
  const key = placeKey(place);
  const canSend = overview.accounts.some((a) => a.can_send);
  const drop = (folder: Folder) => (e: DragEvent) => {
    try {
      const data = JSON.parse(e.dataTransfer.getData("application/x-r365-mail")) as { ids: number[]; account: number };
      if (data.account === folder.account_id) onMove(data.ids, folder);
    } catch {
      /* not mail */
    }
  };
  return (
    <nav className="flex h-full min-h-0 flex-col" aria-label="Mail folders">
      <div className="flex items-center gap-1 p-2">
        <button type="button" onClick={onCompose} disabled={!canSend}
          className="inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-md bg-primary px-3 text-[13px] font-medium text-primary-fg hover:bg-primary-hover disabled:opacity-50" title="New mail (N)">
          <PenSquare className="h-4 w-4" aria-hidden />New mail
        </button>
        <button type="button" onClick={onSync} className="rounded-md p-2 text-muted hover:bg-raised hover:text-fg" aria-label="Check for new mail" title="Check for new mail">
          <RefreshCw className={cn("h-4 w-4", syncing && "animate-spin")} />
        </button>
        <button type="button" onClick={onSettings} className="rounded-md p-2 text-muted hover:bg-raised hover:text-fg" aria-label="Mail settings" title="Accounts, signatures, access">
          <Settings className="h-4 w-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        <p className="px-2 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-subtle">Favourites</p>
        {overview.accounts.length > 1 && <Row active={key === "v-inbox"} onClick={() => onPlace({ kind: "view", view: "inbox" })} icon={<Inbox className="h-4 w-4" />} label="All inboxes" count={overview.unread} strong />}
        <Row active={key === "v-unread"} onClick={() => onPlace({ kind: "view", view: "unread" })} icon={<Inbox className="h-4 w-4" />} label="Unread" count={overview.unread} />
        <Row active={key === "v-flagged"} onClick={() => onPlace({ kind: "view", view: "flagged" })} icon={<Flag className="h-4 w-4" />} label="Flagged" />
        <Row active={key === "drafts"} onClick={() => onPlace({ kind: "drafts" })} icon={<FileText className="h-4 w-4" />} label="My drafts" count={overview.drafts} />
        <Row active={key === "outbox"} onClick={() => onPlace({ kind: "outbox" })} icon={<Upload className="h-4 w-4" />} label="Outbox" count={overview.outbox} strong={overview.outbox > 0} />
        {overview.accounts.map((account) => {
          const isClosed = closed.has(account.id);
          return (
            <div key={account.id} className="mt-3">
              <div className="group flex items-center">
                <button type="button" onClick={() => { const next = new Set(closed); if (isClosed) next.delete(account.id); else next.add(account.id); setClosed(next); }}
                  className="flex min-w-0 flex-1 items-center gap-1.5 px-1 py-1 text-left text-[12px] font-semibold text-fg" title={account.address}>
                  {isClosed ? <ChevronRight className="h-3.5 w-3.5 text-muted" /> : <ChevronDown className="h-3.5 w-3.5 text-muted" />}
                  {account.color && <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: account.color }} aria-hidden />}
                  <span className="truncate">{account.display_name || account.address}</span>
                  {account.status === "error" && <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning" aria-label={account.last_error} />}
                </button>
                <button type="button" onClick={() => onNewFolder(account)} className="rounded p-0.5 text-xs text-muted opacity-0 hover:text-fg group-hover:opacity-100" aria-label={`New folder in ${account.address}`} title="New folder">+</button>
              </div>
              {account.status === "error" && !isClosed && <p className="mx-2 mb-1 rounded bg-warning-soft px-2 py-1 text-[11px] text-warning">{account.last_error || "The account could not be read."}</p>}
              {account.status === "new" && account.folders.length === 0 && !isClosed && <p className="px-3 py-1 text-[11px] text-subtle">Connecting… the first mail appears within a minute.</p>}
              {!isClosed && account.folders.map((folder) => {
                const Icon = ICON[folder.role] ?? FolderIcon;
                return (
                  <Row key={folder.id} active={key === `f${folder.id}`} onClick={() => onPlace({ kind: "folder", folderId: folder.id, accountId: account.id })}
                    icon={<Icon className="h-4 w-4" />} label={folder.name} indent={folder.path.length - 1}
                    count={folder.role === "drafts" || folder.role === "sent" ? 0 : folder.role === "junk" || folder.role === "trash" ? 0 : folder.unread} strong={folder.role === "inbox"}
                    onDrop={drop(folder)} title={folder.path.join(" / ")}
                    menu={<FolderMenu folder={folder} onNew={() => onNewFolder(account, folder)} onRename={() => onRename(folder)} onDelete={() => onDelete(folder)} onEmpty={() => onEmpty(folder)} />} />
                );
              })}
            </div>
          );
        })}
      </div>
    </nav>
  );
}
