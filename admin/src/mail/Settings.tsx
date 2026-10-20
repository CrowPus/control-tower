/* Mail settings: accounts (add like in Outlook — address and password, the servers filled in for the
   usual providers; test before saving), the team mailbox from Team → Mailboxes, your signature in each
   account, who on the team may use an account, and your own preferences. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plus, Trash2, X, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, errorMessage, unwrap, type Schemas } from "../api";
import { Alert, Badge, Button, Field, Input, Select, Toggle, cn } from "../ui";
import { Editor, type EditorHandle } from "./Editor";
import { type Account, type Overview, type Prefs } from "./lib";

type Tab = "accounts" | "signatures" | "access" | "preferences";
type AccountIn = Schemas["AccountIn"];

const EMPTY: AccountIn = {
  address: "", display_name: "", login: "", password: "", imap_host: "", imap_port: 993, imap_security: "ssl", smtp_host: "", smtp_port: 465, smtp_security: "ssl",
  save_sent_copy: true, language: "de", color: "", sync_days: 90,
};
const COLORS = ["", "#0f766e", "#1d4ed8", "#7c3aed", "#be123c", "#b45309", "#15803d"];

export function MailSettings({ open, onClose, overview, prefs, onPrefs, initialTab = "accounts" }: {
  open: boolean;
  onClose: () => void;
  overview: Overview;
  prefs: Prefs;
  onPrefs: (p: Prefs) => void;
  initialTab?: Tab;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState<Tab>(initialTab);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) { el.showModal(); setTab(initialTab); }
    if (!open && el.open) el.close();
  }, [open, initialTab]);
  return (
    <dialog ref={ref} onClose={onClose} className="m-auto text-left h-[min(46rem,calc(100dvh-2rem))] w-[min(56rem,calc(100vw-2rem))] rounded-[var(--radius-card)] border border-border bg-surface p-0 text-fg shadow-[var(--shadow)] backdrop:bg-black/40">
      <div className="flex h-full flex-col">
        <header className="flex items-center gap-3 border-b border-border px-4 py-3">
          <h2 className="text-[15px] font-semibold">Mail settings</h2>
          <nav className="flex gap-1" role="tablist">
            {([["accounts", "Accounts"], ["signatures", "Signatures"], ["access", "Team access"], ["preferences", "Preferences"]] as const).map(([k, l]) => (
              <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                className={cn("rounded-md px-2.5 py-1 text-[13px]", tab === k ? "bg-raised font-medium text-fg" : "text-muted hover:text-fg")}>{l}</button>
            ))}
          </nav>
          <span className="flex-1" />
          <button type="button" onClick={onClose} className="text-muted hover:text-fg" aria-label="Close"><X className="h-4 w-4" /></button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {open && tab === "accounts" && <Accounts overview={overview} />}
          {open && tab === "signatures" && <Signatures accounts={overview.accounts} />}
          {open && tab === "access" && <Access accounts={overview.accounts.filter((a) => a.can_manage)} />}
          {open && tab === "preferences" && <Preferences prefs={prefs} onPrefs={onPrefs} />}
        </div>
      </div>
    </dialog>
  );
}

// --- accounts ------------------------------------------------------------------------------------------------
function Accounts({ overview }: { overview: Overview }) {
  const client = useQueryClient();
  const [editing, setEditing] = useState<Account | "new" | null>(overview.accounts.length === 0 ? "new" : null);
  const [notice, setNotice] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  async function link(name: string) {
    try {
      unwrap(await api.POST("/admin-api/mail/accounts/team/{name}", { params: { path: { name } } }));
      setNotice({ tone: "success", text: "Added. Its mail appears within a minute." });
      await client.invalidateQueries({ queryKey: ["mail"] });
    } catch (e) { setNotice({ tone: "danger", text: errorMessage(e) }); }
  }
  async function remove(account: Account) {
    if (!window.confirm(`Remove ${account.address} from Mail? The mailbox itself and its mail on the server stay as they are; drafts not sent here are lost.`)) return;
    try {
      await api.DELETE("/admin-api/mail/accounts/{account_id}", { params: { path: { account_id: account.id } } });
      await client.invalidateQueries({ queryKey: ["mail"] });
    } catch (e) { setNotice({ tone: "danger", text: errorMessage(e) }); }
  }
  if (editing) return <AccountForm account={editing === "new" ? null : editing} presets={overview.presets} onDone={() => { setEditing(null); void client.invalidateQueries({ queryKey: ["mail"] }); }} onCancel={() => setEditing(null)} />;
  return (
    <div className="space-y-3">
      {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}
      {overview.team_mailboxes.map((name) => (
        <div key={name} className="flex items-center gap-3 rounded-md border border-dashed border-primary/50 bg-primary-soft/40 px-3 py-2.5">
          <p className="flex-1 text-[13px]">The <b>{name}</b> mailbox from Team → Mailboxes can be added with its settings as they are.</p>
          <Button variant="primary" size="sm" onClick={() => void link(name)}>Add {name} mailbox</Button>
        </div>
      ))}
      <ul className="divide-y divide-border rounded-md border border-border">
        {overview.accounts.map((a) => (
          <li key={a.id} className="flex items-center gap-3 px-3 py-2.5">
            <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: a.color || "var(--color-border)" }} aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-medium">{a.display_name || a.address} <span className="font-normal text-muted">{a.display_name ? a.address : ""}</span></p>
              <p className="text-xs text-muted">
                {a.team_mailbox ? `Team mailbox (${a.team_mailbox}) · ` : ""}{a.can_send ? "Read and send" : "Read only"}
                {a.last_sync_at ? ` · checked ${new Date(a.last_sync_at).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}` : ""}
              </p>
              {a.status === "error" && <p className="text-xs text-warning">{a.last_error}</p>}
            </div>
            <Badge tone={a.status === "ok" ? "success" : a.status === "error" ? "warning" : "neutral"}>{a.status === "ok" ? "Connected" : a.status === "error" ? "Problem" : "Connecting"}</Badge>
            {a.can_manage && <Button size="sm" onClick={() => setEditing(a)}>Edit</Button>}
            {a.can_manage && <button type="button" onClick={() => void remove(a)} className="rounded p-1 text-muted hover:text-danger" aria-label={`Remove ${a.address}`}><Trash2 className="h-4 w-4" /></button>}
          </li>
        ))}
        {overview.accounts.length === 0 && <li className="px-3 py-6 text-center text-[13px] text-muted">No account yet.</li>}
      </ul>
      <Button variant="primary" onClick={() => setEditing("new")}><Plus className="h-4 w-4" />Add account</Button>
      <p className="text-xs text-muted">Any mailbox with IMAP and SMTP works (IONOS, Strato, GMX, WEB.DE, T-Online, Gmail with an app password, …). The password is stored encrypted and never shown again.</p>
    </div>
  );
}

function AccountForm({ account, presets, onDone, onCancel }: { account: Account | null; presets: Overview["presets"]; onDone: () => void; onCancel: () => void }) {
  const linked = !!account?.team_mailbox;
  const [value, setValue] = useState<AccountIn>(account ? {
    address: account.address, display_name: account.display_name, login: account.login, password: null, imap_host: account.imap_host, imap_port: account.imap_port,
    imap_security: account.imap_security as "ssl" | "starttls", smtp_host: account.smtp_host, smtp_port: account.smtp_port, smtp_security: account.smtp_security as "ssl" | "starttls",
    save_sent_copy: account.save_sent_copy, language: account.language as "de" | "en", color: account.color, sync_days: account.sync_days,
  } : EMPTY);
  const [advanced, setAdvanced] = useState(!!account && !linked);
  const [preset, setPreset] = useState("");
  const [test, setTest] = useState<Schemas["TestOut"] | null>(null);
  const [busy, setBusy] = useState<"" | "test" | "save">("");
  const [problem, setProblem] = useState("");
  const set = (patch: Partial<AccountIn>) => { setValue((v) => ({ ...v, ...patch })); setTest(null); };
  const note = presets.find((p) => p.key === preset)?.note;

  function guess(address: string) {
    if (account) return;
    const domain = address.split("@")[1]?.toLowerCase() ?? "";
    const found = presets.find((p) => p.domains.includes(domain));
    if (found) apply(found.key);
    else if (domain && !value.imap_host) set({ imap_host: `imap.${domain}`, smtp_host: `smtp.${domain}` });
  }

  function apply(key: string) {
    setPreset(key);
    const p = presets.find((x) => x.key === key);
    if (p) set({ imap_host: p.imap_host, imap_port: p.imap_port, imap_security: p.imap_security as "ssl" | "starttls", smtp_host: p.smtp_host, smtp_port: p.smtp_port,
      smtp_security: p.smtp_security as "ssl" | "starttls", save_sent_copy: p.save_sent_copy });
  }

  async function runTest() {
    setBusy("test");
    setProblem("");
    try {
      setTest(unwrap(await api.POST("/admin-api/mail/accounts/test", { body: { ...value, account_id: account?.id ?? null } })));
    } catch (e) { setProblem(errorMessage(e)); }
    setBusy("");
  }

  async function save() {
    setBusy("save");
    setProblem("");
    try {
      if (account) unwrap(await api.PUT("/admin-api/mail/accounts/{account_id}", { params: { path: { account_id: account.id } }, body: value }));
      else unwrap(await api.POST("/admin-api/mail/accounts", { body: value }));
      onDone();
    } catch (e) { setProblem(errorMessage(e)); }
    setBusy("");
  }

  return (
    <div className="max-w-2xl space-y-3">
      <h3 className="text-[14px] font-semibold">{account ? `Edit ${account.address}` : "Add an account"}</h3>
      {linked && <Alert tone="info">This is the team mailbox "{account?.team_mailbox}": its servers and password are in Team → Mailboxes. Here you change only how it shows in Mail.</Alert>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="E-mail address"><Input type="email" value={value.address} disabled={linked || !!account} onChange={(e) => set({ address: e.target.value })} onBlur={(e) => guess(e.target.value)} autoFocus={!account} /></Field>
        <Field label="Your name as recipients see it" hint="E.g. your company name, or Support"><Input value={value.display_name} onChange={(e) => set({ display_name: e.target.value })} /></Field>
        {!linked && (
          <Field label="Password" hint={account?.password_set ? "Leave empty to keep the stored one." : "Stored encrypted; never shown again."}>
            <Input type="password" autoComplete="new-password" value={value.password ?? ""} onChange={(e) => set({ password: e.target.value || null })} />
          </Field>
        )}
        {!linked && (
          <Field label="Provider">
            <Select value={preset} onChange={(e) => apply(e.target.value)}>
              <option value="">Other / my own servers</option>
              {presets.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
            </Select>
          </Field>
        )}
      </div>
      {note && <Alert tone="info">{note}</Alert>}
      {!linked && (
        <button type="button" className="text-xs font-medium text-primary hover:underline" onClick={() => setAdvanced(!advanced)}>{advanced ? "Hide server settings" : "Show server settings"}</button>
      )}
      {!linked && advanced && (
        <div className="grid gap-3 rounded-md border border-border p-3 sm:grid-cols-6">
          <div className="sm:col-span-6"><Field label="Login (if not the address)"><Input value={value.login} placeholder={value.address} onChange={(e) => set({ login: e.target.value })} /></Field></div>
          <div className="sm:col-span-3"><Field label="Incoming server (IMAP)"><Input value={value.imap_host} onChange={(e) => set({ imap_host: e.target.value })} /></Field></div>
          <div className="sm:col-span-1"><Field label="Port"><Input type="number" value={value.imap_port} onChange={(e) => set({ imap_port: Number(e.target.value) })} /></Field></div>
          <div className="sm:col-span-2"><Field label="Security"><Select value={value.imap_security} onChange={(e) => set({ imap_security: e.target.value as "ssl" | "starttls" })}><option value="ssl">SSL/TLS</option><option value="starttls">STARTTLS</option></Select></Field></div>
          <div className="sm:col-span-3"><Field label="Outgoing server (SMTP)"><Input value={value.smtp_host} onChange={(e) => set({ smtp_host: e.target.value })} /></Field></div>
          <div className="sm:col-span-1"><Field label="Port"><Input type="number" value={value.smtp_port} onChange={(e) => set({ smtp_port: Number(e.target.value) })} /></Field></div>
          <div className="sm:col-span-2"><Field label="Security"><Select value={value.smtp_security} onChange={(e) => set({ smtp_security: e.target.value as "ssl" | "starttls" })}><option value="ssl">SSL/TLS</option><option value="starttls">STARTTLS</option></Select></Field></div>
          <div className="sm:col-span-6"><Toggle tone="primary" checked={value.save_sent_copy ?? true} onChange={(v) => set({ save_sent_copy: v })} label="Put a copy of sent mail in the Sent folder" description="Off for Gmail and Microsoft: they file sent mail themselves." /></div>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Replies in" hint="Quote line and AW:/WG: or Re:/Fwd:"><Select value={value.language} onChange={(e) => set({ language: e.target.value as "de" | "en" })}><option value="de">German</option><option value="en">English</option></Select></Field>
        <Field label="Mail kept here" hint="How far back the first sync reaches; older mail on request."><Select value={value.sync_days} onChange={(e) => set({ sync_days: Number(e.target.value) })}>
          {[30, 90, 180, 365, 730].map((d) => <option key={d} value={d}>{d < 365 ? `${d} days` : `${d / 365} year${d > 365 ? "s" : ""}`}</option>)}
        </Select></Field>
        <Field label="Colour">
          <div className="flex h-9 items-center gap-1.5">
            {COLORS.map((c) => <button key={c || "none"} type="button" aria-label={c ? `Colour ${c}` : "No colour"} onClick={() => set({ color: c })}
              className={cn("h-6 w-6 rounded-full border", value.color === c ? "ring-2 ring-primary ring-offset-1" : "border-border")} style={{ background: c || "transparent" }} />)}
          </div>
        </Field>
      </div>
      {test && (
        <div className="space-y-1 rounded-md bg-raised p-3 text-[13px]">
          {(["imap", "smtp"] as const).map((k) => (
            <p key={k} className="flex items-start gap-2">
              {test[k] === "ok" ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-success" /> : <XCircle className="mt-0.5 h-4 w-4 text-danger" />}
              <span><b>{k === "imap" ? "Incoming (IMAP)" : "Outgoing (SMTP)"}:</b> {test[k] === "ok" ? "signed in" : test[k]}</span>
            </p>
          ))}
        </div>
      )}
      {problem && <Alert>{problem}</Alert>}
      <div className="flex gap-2">
        <Button variant="primary" loading={busy === "save"} onClick={() => void save()} disabled={!value.address || (!account && !value.password)}>{account ? "Save" : "Add account"}</Button>
        {!linked && <Button loading={busy === "test"} onClick={() => void runTest()} disabled={!value.address || !value.imap_host}>Test connection</Button>}
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

// --- signatures ------------------------------------------------------------------------------------------------
function Signatures({ accounts }: { accounts: Account[] }) {
  const senders = accounts.filter((a) => a.can_send);
  const [id, setId] = useState<number | null>(senders[0]?.id ?? null);
  const account = senders.find((a) => a.id === id);
  if (!senders.length) return <p className="text-[13px] text-muted">Add an account you may send from first.</p>;
  return (
    <div className="space-y-3">
      <Field label="Account">
        <Select value={id ?? ""} onChange={(e) => setId(Number(e.target.value))}>
          {senders.map((a) => <option key={a.id} value={a.id}>{a.display_name ? `${a.display_name} <${a.address}>` : a.address}</option>)}
        </Select>
      </Field>
      {account && <SignatureEditor key={account.id} account={account} />}
    </div>
  );
}

function SignatureEditor({ account }: { account: Account }) {
  const client = useQueryClient();
  const editor = useRef<EditorHandle>(null);
  const [html, setHtml] = useState(account.signature_html);
  const [notice, setNotice] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  async function save() {
    try {
      unwrap(await api.PUT("/admin-api/mail/accounts/{account_id}/signature", { params: { path: { account_id: account.id } }, body: { signature_html: editor.current?.html() ?? html } }));
      setNotice({ tone: "success", text: "Saved. New mails, replies and forwards from this account start with it." });
      await client.invalidateQueries({ queryKey: ["mail"] });
    } catch (e) { setNotice({ tone: "danger", text: errorMessage(e) }); }
  }
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted">Your own signature in this account (each person on the team has theirs). Name, role, phone, the company's details; a logo can be pasted in.</p>
      <div className="overflow-hidden rounded-md border border-border">
        <Editor ref={editor} initial={account.signature_html || "<div>Kind regards</div><div><br></div><div><b>Your name</b></div><div>Your company</div>"} onChange={setHtml} minHeight={160} compact />
      </div>
      {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}
      <Button variant="primary" onClick={() => void save()}>Save signature</Button>
    </div>
  );
}

// --- team access ---------------------------------------------------------------------------------------------
function Access({ accounts }: { accounts: Account[] }) {
  const [id, setId] = useState<number | null>(accounts[0]?.id ?? null);
  if (!accounts.length) return <p className="text-[13px] text-muted">You manage no account (the one who adds an account manages it; owners manage every account).</p>;
  return (
    <div className="space-y-3">
      <Field label="Account">
        <Select value={id ?? ""} onChange={(e) => setId(Number(e.target.value))}>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.display_name ? `${a.display_name} <${a.address}>` : a.address}</option>)}
        </Select>
      </Field>
      {id && <AccessEditor key={id} accountId={id} />}
    </div>
  );
}

function AccessEditor({ accountId }: { accountId: number }) {
  const client = useQueryClient();
  const team = useQuery({ queryKey: ["mail-people"], queryFn: async () => unwrap(await api.GET("/admin-api/mail/people")) });
  const access = useQuery({ queryKey: ["mail-access", accountId], queryFn: async () => unwrap(await api.GET("/admin-api/mail/accounts/{account_id}/access", { params: { path: { account_id: accountId } } })) });
  const [rows, setRows] = useState<Record<number, { on: boolean; send: boolean; manage: boolean }>>({});
  const [notice, setNotice] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  useEffect(() => {
    if (!access.data) return;
    const next: Record<number, { on: boolean; send: boolean; manage: boolean }> = {};
    for (const a of access.data) next[a.admin_id] = { on: true, send: a.can_send, manage: a.can_manage };
    setRows(next);
  }, [access.data]);
  async function save() {
    const entries = Object.entries(rows).filter(([, r]) => r.on).map(([admin_id, r]) => ({ admin_id: Number(admin_id), can_send: r.send, can_manage: r.manage }));
    try {
      unwrap(await api.PUT("/admin-api/mail/accounts/{account_id}/access", { params: { path: { account_id: accountId } }, body: entries }));
      setNotice({ tone: "success", text: "Saved (in the admin log)." });
      await client.invalidateQueries({ queryKey: ["mail-access", accountId] });
      await client.invalidateQueries({ queryKey: ["mail"] });
    } catch (e) { setNotice({ tone: "danger", text: errorMessage(e) }); }
  }
  const set = (id: number, patch: Partial<{ on: boolean; send: boolean; manage: boolean }>) => setRows((r) => ({ ...r, [id]: { ...(r[id] ?? { on: false, send: true, manage: false }), ...patch } }));
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">Who on the team sees this account in Mail. "Send" lets them write from it; "Manage" lets them change its settings and this list.</p>
      <table className="w-full text-[13px]">
        <thead><tr className="border-b border-border text-left text-xs text-muted"><th className="py-2">Person</th><th>Access</th><th>Send</th><th>Manage</th></tr></thead>
        <tbody className="divide-y divide-border">
          {(team.data ?? []).map((p) => {
            const r = rows[p.id] ?? { on: false, send: true, manage: false };
            return (
              <tr key={p.id}>
                <td className="py-2"><span className="font-medium">{p.name || p.email}</span> <span className="text-muted">{p.name ? p.email : ""}</span></td>
                <td><input type="checkbox" aria-label={`Access for ${p.email}`} checked={r.on} onChange={(e) => set(p.id, { on: e.target.checked })} /></td>
                <td><input type="checkbox" aria-label={`Send for ${p.email}`} disabled={!r.on} checked={r.on && r.send} onChange={(e) => set(p.id, { send: e.target.checked })} /></td>
                <td><input type="checkbox" aria-label={`Manage for ${p.email}`} disabled={!r.on} checked={r.on && r.manage} onChange={(e) => set(p.id, { manage: e.target.checked })} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}
      <Button variant="primary" onClick={() => void save()}>Save access</Button>
    </div>
  );
}

// --- preferences -------------------------------------------------------------------------------------------------
function Preferences({ prefs, onPrefs }: { prefs: Prefs; onPrefs: (p: Prefs) => void }) {
  return (
    <div className="max-w-xl space-y-4">
      <Field label="Undo send" hint="How long a sent mail waits so you can still take it back.">
        <Select value={prefs.undoSeconds} onChange={(e) => onPrefs({ ...prefs, undoSeconds: Number(e.target.value) })}>
          {[0, 5, 10, 20, 30].map((s) => <option key={s} value={s}>{s ? `${s} seconds` : "Off"}</option>)}
        </Select>
      </Field>
      <Toggle tone="primary" checked={prefs.markReadOnOpen} onChange={(v) => onPrefs({ ...prefs, markReadOnOpen: v })} label="Mark as read when opened" />
      <div>
        <p className="text-[13px] font-medium">Pictures always shown from</p>
        {prefs.trustedSenders.length === 0 ? <p className="text-xs text-muted">Nobody yet ("Always from …" above a mail).</p> : (
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {prefs.trustedSenders.map((s) => (
              <li key={s} className="inline-flex items-center gap-1 rounded-full bg-raised px-2 py-0.5 text-xs">{s}
                <button type="button" aria-label={`Remove ${s}`} onClick={() => onPrefs({ ...prefs, trustedSenders: prefs.trustedSenders.filter((x) => x !== s) })}><X className="h-3 w-3" /></button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="rounded-md bg-raised p-3 text-xs text-muted">
        <p className="mb-1 font-medium text-fg">Keyboard</p>
        <p>N new mail · R reply · A reply all · F forward · E archive · Del delete · U unread · S flag · J/K or ↓/↑ next/previous · / search · Ctrl+Enter send · Esc close</p>
      </div>
      <p className="text-xs text-subtle">These preferences are kept in this browser.</p>
    </div>
  );
}
