/* Admin → Mail: the team's own e-mail program — folders on the left, the list in
   the middle, the mail or what is being written on the right. Everything the team does here goes to the
   mail server at once, so the phone and webmail show the same. */
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderTree, Loader2, Mail, RotateCcw, Search, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, errorMessage, unwrap } from "../api";
import { Alert, Button, Dialog, Input, cn } from "../ui";
import { Compose } from "./Compose";
import { Folders } from "./Folders";
import { loadPrefs, parsePlace, placeKey, savePrefs, type Account, type Folder, type Outbox, type Place, type Prefs, type Summary } from "./lib";
import { MessageList, type ListAction } from "./MessageList";
import { Reader, type ReaderAction } from "./Reader";
import { MailSettings } from "./Settings";

type Toast = { id: number; text: string; tone: "info" | "danger"; undo?: () => void; until?: number };

export function MailPage() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [prefs, setPrefsState] = useState<Prefs>(loadPrefs);
  const setPrefs = (p: Prefs) => { setPrefsState(p); savePrefs(p); };
  const overviewQuery = useQuery({ queryKey: ["mail", "overview"], queryFn: async () => unwrap(await api.GET("/admin-api/mail")), refetchInterval: 30_000 });
  const overview = overviewQuery.data;
  const place = useMemo(() => parsePlace(params.get("p"), overview), [params, overview]);
  const openId = Number(params.get("m") ?? 0) || null;
  const draftId = Number(params.get("c") ?? 0) || null;
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [scope, setScope] = useState<"here" | "all">("here");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [settings, setSettings] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [foldersOpen, setFoldersOpen] = useState(false);
  const [naming, setNaming] = useState<{ title: string; value: string; run: (name: string) => Promise<void> } | null>(null);
  const searchBox = useRef<HTMLInputElement>(null);

  const go = useCallback((patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) { if (v) next.set(k, v); else next.delete(k); }
    setParams(next, { replace: false });
  }, [params, setParams]);

  const toast = useCallback((t: Omit<Toast, "id">, ms = 6000) => {
    const id = Date.now() + Math.random();
    setToasts((all) => [...all.slice(-2), { ...t, id }]);
    setTimeout(() => setToasts((all) => all.filter((x) => x.id !== id)), ms);
  }, []);

  useEffect(() => { const t = setTimeout(() => setSearch(query.trim()), 300); return () => clearTimeout(t); }, [query]);
  useEffect(() => { setSelected(new Set()); }, [place, search, filter]);

  // Start writing to someone from elsewhere in the admin (?to=address).
  useEffect(() => {
    const to = params.get("to");
    if (!to || !overview?.accounts.some((a) => a.can_send)) return;
    void (async () => {
      try {
        const d = unwrap(await api.POST("/admin-api/mail/drafts", { body: { mode: "new", to: [to] } }));
        go({ to: null, c: String(d.id) });
      } catch (e) { toast({ text: errorMessage(e), tone: "danger" }); go({ to: null }); }
    })();
  }, [params, overview, go, toast]);

  const listQuery = useMemo(() => {
    const q: Record<string, string | number> = {};
    if (search) q.q = search;
    if (filter) q.filter = filter;
    if (place.kind === "folder" && !(search && scope === "all")) q.folder_id = place.folderId;
    else if (place.kind === "view" && !(search && scope === "all")) q.view = place.view;
    return q;
  }, [place, search, scope, filter]);
  const listEnabled = !!overview && overview.accounts.length > 0 && (place.kind === "folder" || place.kind === "view" || !!search);

  const messages = useInfiniteQuery({
    queryKey: ["mail", "messages", listQuery],
    queryFn: async ({ pageParam }) => unwrap(await api.GET("/admin-api/mail/messages", { params: { query: { ...listQuery, ...(pageParam ? { before: pageParam } : {}) } } })),
    initialPageParam: "",
    getNextPageParam: (last) => last.next ?? undefined,
    enabled: listEnabled,
    refetchInterval: 30_000,
  });
  const items: Summary[] = useMemo(() => messages.data?.pages.flatMap((p) => p.items) ?? [], [messages.data]);
  const olderOnServer = messages.data?.pages[messages.data.pages.length - 1]?.has_older_on_server ?? false;

  const drafts = useQuery({ queryKey: ["mail-drafts"], queryFn: async () => unwrap(await api.GET("/admin-api/mail/drafts")), enabled: place.kind === "drafts" });
  const outbox = useQuery({ queryKey: ["mail", "outbox"], queryFn: async () => unwrap(await api.GET("/admin-api/mail/outbox")), enabled: place.kind === "outbox", refetchInterval: 5000 });

  const accounts = overview?.accounts ?? [];
  const folderById = useMemo(() => new Map(accounts.flatMap((a) => a.folders.map((f) => [f.id, f] as const))), [accounts]);
  const currentFolder = place.kind === "folder" ? folderById.get(place.folderId) : undefined;
  const folderRole = currentFolder?.role ?? (place.kind === "view" ? "inbox" : "");

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["mail"] });
  };

  async function act(action: ListAction | "move", ids: number[], folderId?: number) {
    const before = items.filter((m) => ids.includes(m.id)).map((m) => ({ id: m.id, folder: m.folder_id }));
    try {
      const result = unwrap(await api.POST("/admin-api/mail/messages/actions", { body: { ids, action, folder_id: folderId ?? null } })) as { moved?: Record<string, number>; removed?: number };
      refresh();
      ids.forEach((id) => void client.invalidateQueries({ queryKey: ["mail-message", id] }));
      if (["archive", "delete", "junk", "not_junk", "move"].includes(action)) {
        if (openId && ids.includes(openId)) {
          const index = items.findIndex((m) => m.id === openId);
          const next = items.slice(index + 1).find((m) => !ids.includes(m.id)) ?? items.slice(0, index).reverse().find((m) => !ids.includes(m.id));
          go({ m: next ? String(next.id) : null });
        }
        const moved = result.moved ?? {};
        const back = before.filter((b) => moved[String(b.id)] !== undefined);
        const label = action === "delete" ? (folderRole === "trash" ? "Deleted for good" : "Moved to Trash") : action === "archive" ? "Archived" : action === "junk" ? "Moved to Junk" : action === "not_junk" ? "Moved to Inbox" : "Moved";
        toast({
          text: `${label}${ids.length > 1 ? ` (${ids.length})` : ""}.`, tone: "info",
          undo: back.length ? async () => {
            const groups = new Map<number, number[]>();
            back.forEach((b) => groups.set(b.folder, [...(groups.get(b.folder) ?? []), b.id]));
            for (const [folder, group] of groups) await api.POST("/admin-api/mail/messages/actions", { body: { ids: group, action: "move", folder_id: folder } });
            refresh();
          } : undefined,
        });
      }
      setSelected(new Set());
    } catch (e) {
      toast({ text: errorMessage(e), tone: "danger" });
    }
  }

  async function open(m: Summary) {
    go({ m: String(m.id), c: null });
    if (prefs.markReadOnOpen && !m.seen && !m.is_draft) {
      client.setQueriesData({ queryKey: ["mail", "messages"] }, (data: unknown) => {
        const d = data as { pages: { items: Summary[] }[] } | undefined;
        return d ? { ...d, pages: d.pages.map((p) => ({ ...p, items: p.items.map((x) => (x.id === m.id ? { ...x, seen: true } : x)) })) } : d;
      });
      try {
        await api.POST("/admin-api/mail/messages/actions", { body: { ids: [m.id], action: "read" } });
        void client.invalidateQueries({ queryKey: ["mail", "overview"] });
      } catch { /* shown as unread again on the next refresh */ }
    }
  }

  async function startDraft(mode: "new" | "reply" | "reply_all" | "forward", messageId?: number, accountId?: number) {
    try {
      const d = unwrap(await api.POST("/admin-api/mail/drafts", { body: { mode, message_id: messageId ?? null, account_id: accountId ?? (place.kind === "folder" ? place.accountId : null) } }));
      client.setQueryData(["mail-draft", d.id], d);
      go({ c: String(d.id) });
    } catch (e) { toast({ text: errorMessage(e), tone: "danger" }); }
  }

  function sent(o: Outbox) {
    go({ c: null });
    const seconds = Math.max(0, Math.round((new Date(o.send_at).getTime() - Date.now()) / 1000));
    const scheduled = seconds > 120;
    toast({
      text: scheduled ? `Scheduled for ${new Date(o.send_at).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })} (Outbox).` : seconds > 0 ? "Sending…" : "Sent.",
      tone: "info", until: scheduled ? undefined : Date.now() + seconds * 1000,
      undo: scheduled || seconds > 0 ? async () => {
        try {
          const d = unwrap(await api.POST("/admin-api/mail/outbox/{outbox_id}/cancel", { params: { path: { outbox_id: o.id } } }));
          client.setQueryData(["mail-draft", d.id], d);
          go({ c: String(d.id) });
          refresh();
        } catch (e) { toast({ text: errorMessage(e), tone: "danger" }); }
      } : undefined,
    }, scheduled ? 8000 : Math.max(seconds * 1000, 4000));
    refresh();
  }

  async function readerAction(action: ReaderAction, detail: { id: number; account_id: number }) {
    if (action === "reply" || action === "reply_all" || action === "forward") return startDraft(action, detail.id);
    if (action === "edit_draft") {
      try {
        const d = unwrap(await api.POST("/admin-api/mail/messages/{message_id}/edit-draft", { params: { path: { message_id: detail.id } } }));
        client.setQueryData(["mail-draft", d.id], d);
        go({ c: String(d.id) });
      } catch (e) { toast({ text: errorMessage(e), tone: "danger" }); }
      return;
    }
    if (action === "delete" && folderRole === "trash" && !window.confirm("Delete this mail for good?")) return;
    await act(action, [detail.id]);
  }

  async function syncNow() {
    setSyncing(true);
    try {
      for (const a of accounts) await api.POST("/admin-api/mail/accounts/{account_id}/sync", { params: { path: { account_id: a.id } } });
      setTimeout(() => { refresh(); setSyncing(false); }, 4000);
    } catch (e) { setSyncing(false); toast({ text: errorMessage(e), tone: "danger" }); }
  }

  async function older() {
    if (place.kind !== "folder") return;
    try {
      const r = unwrap(await api.POST("/admin-api/mail/folders/{folder_id}/older", { params: { path: { folder_id: place.folderId } } }));
      toast({ text: r.stored ? `${r.stored} older mails fetched.` : "No older mail on the server.", tone: "info" });
      refresh();
    } catch (e) { toast({ text: errorMessage(e), tone: "danger" }); }
  }

  // Keyboard, as in mail programs (not while typing).
  useEffect(() => {
    function key(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      if (target.closest("input, textarea, select, [contenteditable=true], dialog[open]") || e.ctrlKey || e.metaKey || e.altKey || draftId) return;
      const index = items.findIndex((m) => m.id === openId);
      const current = items[index];
      const k = e.key.toLowerCase();
      if (k === "n" || k === "c") { e.preventDefault(); void startDraft("new"); }
      else if (k === "/") { e.preventDefault(); searchBox.current?.focus(); }
      else if ((k === "j" || e.key === "ArrowDown") && items.length) { e.preventDefault(); const next = items[Math.min(index + 1, items.length - 1)]; if (next) void open(next); }
      else if ((k === "k" || e.key === "ArrowUp") && items.length) { e.preventDefault(); const prev = items[Math.max(index - 1, 0)]; if (prev) void open(prev); }
      else if (current && k === "r") void startDraft("reply", current.id);
      else if (current && k === "a") void startDraft("reply_all", current.id);
      else if (current && k === "f") void startDraft("forward", current.id);
      else if (current && k === "e") void act("archive", [current.id]);
      else if (current && (e.key === "Delete" || e.key === "#")) void readerAction("delete", current);
      else if (current && k === "u") void act(current.seen ? "unread" : "read", [current.id]);
      else if (current && k === "s") void act(current.flagged ? "unflag" : "flag", [current.id]);
      else if (e.key === "Escape" && openId) go({ m: null });
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });

  if (overviewQuery.error) return <Alert>{errorMessage(overviewQuery.error)}</Alert>;
  if (!overview) return <div className="flex h-64 items-center justify-center text-muted"><Loader2 className="h-5 w-5 animate-spin" /></div>;

  const settingsDialog = <MailSettings open={settings} onClose={() => { setSettings(false); refresh(); }} overview={overview} prefs={prefs} onPrefs={setPrefs} />;

  if (overview.accounts.length === 0) {
    return (
      <div className="mx-auto mt-10 max-w-lg rounded-[var(--radius-card)] border border-border bg-surface p-8 text-center">
        <Mail className="mx-auto h-10 w-10 text-primary" aria-hidden />
        <h1 className="mt-3 text-lg font-semibold text-fg">Mail</h1>
        <p className="mt-1 text-[13px] text-muted">Your team's e-mail inside the admin: read, answer, file and send from your company mailboxes without leaving Control Tower — and the phone and webmail stay in step.</p>
        <div className="mt-5 flex flex-wrap justify-center gap-2">
          <Button variant="primary" onClick={() => setSettings(true)}>Add an account</Button>
        </div>
        {overview.team_mailboxes.length > 0 && <p className="mt-3 text-xs text-muted">The {overview.team_mailboxes.join(", ")} mailbox from Team → Mailboxes can be added with one click there.</p>}
        {settingsDialog}
      </div>
    );
  }

  const openAccount = (id: number | null): Account | undefined => {
    const m = items.find((x) => x.id === id);
    return accounts.find((a) => a.id === (m?.account_id ?? (place.kind === "folder" ? place.accountId : -1)));
  };
  const title = search && scope === "all" ? `Search: ${search}` : place.kind === "folder" ? currentFolder?.path.join(" / ") ?? "" : place.kind === "view"
    ? { inbox: "All inboxes", flagged: "Flagged", unread: "Unread" }[place.view] : place.kind === "drafts" ? "My drafts" : "Outbox";
  const showRight = !!draftId || !!openId;

  return (
    <div className="-mx-4 -my-5 flex h-[calc(100dvh-7rem)] min-h-[32rem] flex-col md:-mx-8 md:-my-7 md:h-screen">
      <div className="flex items-center gap-2 border-b border-border bg-surface px-3 py-2">
        <button type="button" className="rounded p-1.5 text-muted hover:bg-raised md:hidden" aria-label="Folders" onClick={() => setFoldersOpen(!foldersOpen)}><FolderTree className="h-4 w-4" /></button>
        <h1 className="hidden text-[15px] font-semibold text-fg sm:block">Mail</h1>
        <div className="relative mx-auto flex w-full max-w-xl items-center">
          <Search className="pointer-events-none absolute left-2.5 h-4 w-4 text-subtle" aria-hidden />
          <Input ref={searchBox} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search mail (from: to: subject: has:attachment is:unread)"
            className="pl-8 pr-24" aria-label="Search mail" onKeyDown={(e) => { if (e.key === "Escape") { setQuery(""); (e.target as HTMLInputElement).blur(); } }} />
          {query && (
            <div className="absolute right-1.5 flex items-center gap-1">
              <select aria-label="Search where" value={scope} onChange={(e) => setScope(e.target.value as "here" | "all")} className="h-6 rounded border-0 bg-raised px-1 text-[11px] text-muted">
                <option value="here">This folder</option>
                <option value="all">All mail</option>
              </select>
              <button type="button" onClick={() => setQuery("")} aria-label="Clear search" className="text-muted hover:text-fg"><X className="h-3.5 w-3.5" /></button>
            </div>
          )}
        </div>
      </div>
      <div className="relative grid min-h-0 flex-1 md:grid-cols-[13.5rem_minmax(18rem,24rem)_1fr]">
        <aside className={cn("min-h-0 border-r border-border bg-surface", foldersOpen ? "absolute inset-y-0 left-0 z-30 w-64 shadow-[var(--shadow)]" : "hidden md:block")}>
          <Folders overview={overview} place={place} syncing={syncing}
            onPlace={(p: Place) => { go({ p: placeKey(p), m: null }); setFoldersOpen(false); setQuery(""); }}
            onCompose={() => void startDraft("new")}
            onMove={(ids, folder) => void act("move", ids, folder.id)}
            onSync={() => void syncNow()}
            onSettings={() => setSettings(true)}
            onNewFolder={(account, parent) => setNaming({ title: parent ? `New folder in ${parent.name}` : `New folder in ${account.display_name || account.address}`, value: "", run: async (name) => {
              unwrap(await api.POST("/admin-api/mail/accounts/{account_id}/folders", { params: { path: { account_id: account.id } }, body: { name, parent_id: parent?.id ?? null } }));
            } })}
            onRename={(folder: Folder) => setNaming({ title: `Rename ${folder.name}`, value: folder.name, run: async (name) => {
              unwrap(await api.PATCH("/admin-api/mail/folders/{folder_id}", { params: { path: { folder_id: folder.id } }, body: { name } }));
            } })}
            onDelete={async (folder: Folder) => {
              if (!window.confirm(`Delete the folder "${folder.name}" and the mail in it on the server?`)) return;
              try { await api.DELETE("/admin-api/mail/folders/{folder_id}", { params: { path: { folder_id: folder.id } } }).then((r) => { if (r.error) throw new Error((r.error as { error?: { message?: string } }).error?.message ?? "Not deleted."); }); refresh(); }
              catch (e) { toast({ text: errorMessage(e), tone: "danger" }); }
            }}
            onEmpty={async (folder: Folder) => {
              if (!window.confirm(`Delete everything in ${folder.name} for good?`)) return;
              try { const r = unwrap(await api.POST("/admin-api/mail/folders/{folder_id}/empty", { params: { path: { folder_id: folder.id } } })); toast({ text: `${r.removed} mails deleted.`, tone: "info" }); refresh(); }
              catch (e) { toast({ text: errorMessage(e), tone: "danger" }); }
            }} />
        </aside>
        <section className={cn("min-h-0 border-r border-border bg-surface", showRight && "hidden lg:block", !showRight && "block")} aria-label={title}>
          <div className="flex h-full min-h-0 flex-col">
            <div className="flex items-center gap-2 px-3 pb-1 pt-2.5">
              <h2 className="min-w-0 flex-1 truncate text-[14px] font-semibold text-fg">{title}</h2>
              {messages.isFetching && !messages.isFetchingNextPage && <Loader2 className="h-3.5 w-3.5 animate-spin text-subtle" aria-label="Updating" />}
            </div>
            {place.kind === "drafts" && !search ? (
              <DraftsList drafts={drafts.data ?? []} loading={drafts.isLoading} accounts={accounts} onOpen={(id) => go({ c: String(id) })} />
            ) : place.kind === "outbox" && !search ? (
              <OutboxList items={outbox.data ?? []} loading={outbox.isLoading} onChanged={refresh} onEdit={(id) => go({ c: String(id) })} />
            ) : (
              <div className="min-h-0 flex-1">
                <MessageList items={items} selected={selected} open={openId} folderRole={search && scope === "all" ? "" : folderRole} accounts={accounts}
                  showAccount={accounts.length > 1 && (place.kind !== "folder" || (!!search && scope === "all"))} filter={filter} onFilter={setFilter}
                  onOpen={(m) => (m.is_draft && folderRole === "drafts" ? void readerAction("edit_draft", m) : void open(m))} onSelect={setSelected}
                  onAction={(action, ids) => void (action === "delete" && folderRole === "trash" && !window.confirm(`Delete ${ids.length} mail(s) for good?`) ? null : act(action, ids))}
                  onMove={(ids, folder) => void act("move", ids, folder.id)} loading={messages.isLoading && listEnabled}
                  loadingMore={messages.isFetchingNextPage} hasMore={!!messages.hasNextPage} onMore={() => void messages.fetchNextPage()}
                  olderOnServer={olderOnServer && place.kind === "folder" && !search && !filter} onOlder={() => void older()} loadingOlder={false}
                  empty={search ? "Nothing found." : filter ? "Nothing here with this filter." : currentFolder?.role === "inbox" ? "Your inbox is empty." : "No mail here."} />
              </div>
            )}
          </div>
        </section>
        <section className={cn("min-h-0 bg-surface", showRight ? "block" : "hidden lg:block")}>
          {draftId ? (
            <Compose draftId={draftId} accounts={accounts} prefs={prefs} onClose={() => go({ c: null })} onSent={sent} />
          ) : openId ? (
            <Reader id={openId} account={openAccount(openId)} prefs={prefs} onPrefs={setPrefs} onAction={(a, d) => void readerAction(a, d)} onBack={() => go({ m: null })} />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-[13px] text-muted">
              <Mail className="h-8 w-8 text-subtle" aria-hidden />
              <p>Choose a mail to read it.</p>
              <p className="text-xs text-subtle">N writes a new mail · / searches</p>
            </div>
          )}
        </section>
      </div>
      <div className="pointer-events-none fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 flex-col items-center gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={cn("pointer-events-auto flex items-center gap-3 rounded-md px-4 py-2 text-[13px] shadow-[var(--shadow)]", t.tone === "danger" ? "bg-danger text-white" : "bg-fg text-bg")}>
            <span>{t.text}</span>
            {t.undo && <button type="button" className="inline-flex items-center gap-1 font-semibold underline" onClick={() => { t.undo?.(); setToasts((all) => all.filter((x) => x.id !== t.id)); }}><RotateCcw className="h-3.5 w-3.5" />Undo</button>}
          </div>
        ))}
      </div>
      <Dialog open={!!naming} onClose={() => setNaming(null)} title={naming?.title ?? ""}
        footer={<><Button onClick={() => setNaming(null)}>Cancel</Button><Button variant="primary" disabled={!naming?.value.trim()} onClick={async () => {
          if (!naming) return;
          try { await naming.run(naming.value.trim()); setNaming(null); refresh(); } catch (e) { toast({ text: errorMessage(e), tone: "danger" }); }
        }}>Save</Button></>}>
        <Input autoFocus value={naming?.value ?? ""} onChange={(e) => naming && setNaming({ ...naming, value: e.target.value })} aria-label="Folder name" />
      </Dialog>
      {settingsDialog}
    </div>
  );
}

function DraftsList({ drafts, loading, accounts, onOpen }: { drafts: { id: number; account_id: number; to: string[]; subject: string; preview: string; updated_at: string }[]; loading: boolean; accounts: Account[]; onOpen: (id: number) => void }) {
  if (loading) return <div className="flex justify-center py-10 text-muted"><Loader2 className="h-5 w-5 animate-spin" /></div>;
  if (!drafts.length) return <p className="px-4 py-12 text-center text-[13px] text-muted">No drafts. What you start writing is kept here until you send it.</p>;
  return (
    <ul className="min-h-0 flex-1 overflow-y-auto border-t border-border">
      {drafts.map((d) => (
        <li key={d.id} className="border-b border-border">
          <button type="button" onClick={() => onOpen(d.id)} className="block w-full px-3 py-2.5 text-left hover:bg-raised">
            <p className="truncate text-[13px] text-fg"><span className="mr-1 text-danger">[Draft]</span>{d.to.length ? d.to.join(", ") : "(no recipient)"}</p>
            <p className="truncate text-[13px] font-medium text-fg">{d.subject || "(no subject)"}</p>
            <p className="truncate text-xs text-muted">{d.preview}</p>
            {accounts.length > 1 && <p className="text-[11px] text-subtle">{accounts.find((a) => a.id === d.account_id)?.address}</p>}
          </button>
        </li>
      ))}
    </ul>
  );
}

function OutboxList({ items, loading, onChanged, onEdit }: { items: Outbox[]; loading: boolean; onChanged: () => void; onEdit: (draftId: number) => void }) {
  const [problem, setProblem] = useState("");
  if (loading) return <div className="flex justify-center py-10 text-muted"><Loader2 className="h-5 w-5 animate-spin" /></div>;
  if (!items.length) return <p className="px-4 py-12 text-center text-[13px] text-muted">Nothing waiting. Scheduled mails and mails that could not be sent appear here.</p>;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto border-t border-border">
      {problem && <div className="p-3"><Alert>{problem}</Alert></div>}
      <ul>
        {items.map((o) => (
          <li key={o.id} className="border-b border-border px-3 py-2.5">
            <p className="truncate text-[13px] text-fg">{o.to.join(", ")}</p>
            <p className="truncate text-[13px] font-medium text-fg">{o.subject || "(no subject)"}</p>
            <p className={cn("text-xs", o.status === "failed" ? "text-danger" : "text-muted")}>
              {o.status === "failed" ? `Not sent: ${o.error}` : o.status === "sending" ? "Sending…" : `Goes out ${new Date(o.send_at).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })}`}
            </p>
            <div className="mt-1.5 flex gap-2">
              {o.status === "failed" && <Button size="sm" variant="primary" onClick={async () => { try { unwrap(await api.POST("/admin-api/mail/outbox/{outbox_id}/retry", { params: { path: { outbox_id: o.id } } })); onChanged(); } catch (e) { setProblem(errorMessage(e)); } }}>Try again</Button>}
              {o.status !== "sending" && <Button size="sm" onClick={async () => { try { const d = unwrap(await api.POST("/admin-api/mail/outbox/{outbox_id}/cancel", { params: { path: { outbox_id: o.id } } })); onChanged(); onEdit(d.id); } catch (e) { setProblem(errorMessage(e)); } }}>Stop and edit</Button>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
