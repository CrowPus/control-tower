/* Plugins → Connect your project: a guided way for someone who is not a developer. It asks what the project is, makes
   the connection and its shared secret, writes the exact message for the project's coding agent with the brief to
   save next to it, and checks the connection — saying in plain words what to fix when it is not right. */
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Bot, CheckCircle2, Code2, CreditCard, Database, Download, Globe, XCircle } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, Card, Field, Input, PageTitle, Textarea } from "../ui";

type Check = Schemas["CheckOut"];
type Kind = "app" | "stripe" | "database" | "python";
const DOCS = "https://github.com/CrowPus/control-tower/blob/main/docs";
const BRIEF_URL = "https://raw.githubusercontent.com/CrowPus/control-tower/main/docs/CONNECT-A-PROJECT.md";

function slug(name: string): string {
  const s = name.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^[^a-z]+/, "");
  return s.slice(0, 24) || "";
}

function Copy({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return <Button size="sm" onClick={() => { void navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 2000); }}>{done ? "Copied" : label}</Button>;
}

async function download(path: string, name: string) {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) throw new Error(`Could not download ${name}.`);
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function Step({ n, title, done, children }: { n: number; title: string; done?: boolean; children: ReactNode }) {
  return (
    <Card className="mt-4" title={<span className="flex items-center gap-2">{done ? <CheckCircle2 className="h-4 w-4 text-success" aria-hidden /> : <span className="grid h-5 w-5 place-items-center rounded-full bg-raised text-[11px] font-semibold">{n}</span>}{title}</span>}>
      {children}
    </Card>
  );
}

function Choice({ icon: Icon, title, text, onClick, chosen }: { icon: typeof Globe; title: string; text: string; onClick: () => void; chosen: boolean }) {
  return (
    <button type="button" onClick={onClick} className={`rounded-[var(--radius-card)] border p-4 text-left hover:border-primary ${chosen ? "border-primary bg-raised" : "border-border bg-surface"}`}>
      <Icon className="mb-2 h-5 w-5 text-primary" aria-hidden />
      <p className="text-[13px] font-semibold text-fg">{title}</p>
      <p className="mt-1 text-xs text-muted">{text}</p>
    </button>
  );
}

export function ConnectProjectPage() {
  const navigate = useNavigate();
  const resume = useParams().key ?? "";
  const { data } = useQuery({ queryKey: ["plugins"], queryFn: async () => unwrap(await api.GET("/admin-api/plugins")) });
  const [kind, setKind] = useState<Kind | null>(resume ? "app" : null);
  const [name, setName] = useState("");
  const [key, setKey] = useState(resume);
  const [keyTouched, setKeyTouched] = useState(false);
  const [address, setAddress] = useState("");
  const [created, setCreated] = useState<{ key: string; base: string; secret: string } | null>(null);
  const [wishes, setWishes] = useState("");
  const [result, setResult] = useState<Check | null>(null);
  const [downloadError, setDownloadError] = useState("");

  useEffect(() => { if (!keyTouched) setKey(slug(name)); }, [name, keyTouched]);
  const tower = data?.control_tower_url || window.location.origin;
  const existingBase = resume ? data?.connections?.[resume] ?? "" : "";
  const connKey = created?.key ?? resume;
  const base = created?.base ?? existingBase;
  const routesPath = base ? new URL(base).pathname : "/control-tower";

  const base_url = useMemo(() => {
    const a = address.trim().replace(/\/+$/, "");
    if (!a) return "";
    return /\/control-tower$/.test(a) ? a : `${a}/control-tower`;
  }, [address]);

  const create = useAction(async () => unwrap(await api.POST("/admin-api/plugins/connections", { body: { key, base_url, secret: "" } })),
    { invalidate: [["plugins"]], done: (r) => { setCreated({ key: r.key, base: base_url, secret: r.secret }); } });
  const renew = useAction(async () => unwrap(await api.POST("/admin-api/plugins/{key}/secret", { params: { path: { key: connKey } } })),
    { done: (r) => { setCreated({ key: connKey, base, secret: r.secret }); } });
  const check = useAction(async () => unwrap(await api.POST("/admin-api/plugins/connections/{key}/check", { params: { path: { key: connKey } } })),
    { invalidate: [["plugins"], ["team-catalogue"]], done: (r) => { setResult(r); } });
  const switchOn = useAction(async () => unwrap(await api.PUT("/admin-api/plugins/{key}", { params: { path: { key: connKey } }, body: { enabled: true } })),
    { invalidate: [["plugins"], ["team-catalogue"]], done: () => "Switched on. Its tools, actions and starter agents are offered now." });

  const settingsText = `CONTROL_TOWER_SECRET=${created?.secret ?? "<the shared secret>"}\nCONTROL_TOWER_URL=${tower}\nCONTROL_TOWER_KEY=${connKey}`;
  const prompt = `Connect this project to Control Tower, the self-hosted office where my AI agents work.

1. Read CONTROL-TOWER.md, which I saved in this project's folder. It is the complete brief: follow its ground rules and its plan. (If the file is missing, read ${BRIEF_URL}.)
2. This connection:
   - key: ${connKey} (CONTROL_TOWER_KEY)
   - Control Tower will call ${base || "<the project's address>/control-tower"}/manifest, /tools/…, /metrics — so serve the routes under ${routesPath}
   - Control Tower's address, for events (CONTROL_TOWER_URL): ${tower}
   - The shared secret goes in CONTROL_TOWER_SECRET. I set its value myself: never ask me to paste it here and never write it into code.
3. What my agents should see: ${wishes.trim() || "look at the project and suggest a short list — numbers such as sign-ups or orders per day, lists such as the newest customers, failures — and ask me before you build it."}
4. Build no actions (nothing that changes data) unless I ask for one by name.
5. When you are done, run the connection check from the brief against the project running here, then tell me in plain words: what my agents can now see, which settings I must set and where, and that I should deploy as usual and press "Check the connection" in Control Tower.`;

  const enabled = data?.plugins.find((p) => p.key === connKey)?.enabled ?? false;
  const offered = data?.plugins.find((p) => p.key === connKey);

  return (
    <>
      <Link to="/plugins" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg"><ArrowLeft className="h-3.5 w-3.5" aria-hidden />Plugins</Link>
      <PageTitle title="Connect your project" description="So your agents can see how your product is doing — and suggest what to do, which you clear. A few minutes for you; the code is written by your coding agent or a developer, guided by what this page gives you." />

      {!resume && (
        <Step n={1} title="What is your project?" done={kind !== null}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Choice icon={Globe} title="My app or website" text="Any language, anywhere it runs. Your coding agent (Claude Code, Cursor, Copilot, Lovable …) or a developer adds a small part to it. Recommended." chosen={kind === "app"} onClick={() => setKind("app")} />
            <Choice icon={CreditCard} title="My payments are on Stripe" text="No code: switch on the Stripe plugin and paste a read-only key." chosen={kind === "stripe"} onClick={() => setKind("stripe")} />
            <Choice icon={Database} title="Only numbers from my database" text="PostgreSQL or MySQL: you write the questions; agents can only ask those." chosen={kind === "database"} onClick={() => setKind("database")} />
            <Choice icon={Code2} title="A Python plugin on this server" text="For developers who can put code on the Control Tower server." chosen={kind === "python"} onClick={() => setKind("python")} />
          </div>
        </Step>
      )}

      {(kind === "stripe" || kind === "database") && (
        <Step n={2} title={kind === "stripe" ? "Switch on Stripe" : "Switch on Database"}>
          <p className="text-[13px] text-muted">{kind === "stripe"
            ? "In Stripe: Developers → API keys → Create restricted key, with Read on Customers, Subscriptions, Charges and Prices. Paste it in the Stripe card on the Plugins page with your main currency, then switch it on."
            : "Ask whoever runs your database for an address of a user that may only read. Paste it in the Database card on the Plugins page, write your questions (the card shows an example), then switch it on."}</p>
          <div className="mt-3"><Link to="/plugins"><Button variant="primary">Open Plugins</Button></Link></div>
        </Step>
      )}

      {kind === "python" && (
        <Step n={2} title="A Python plugin">
          <p className="text-[13px] text-muted">A folder in the server's <code>plugins/</code> folder with a <code>plugin.py</code>; restart Control Tower and it appears on the Plugins page. It runs with all of Control Tower's rights, so only install code you trust.</p>
          <div className="mt-3"><a href={`${DOCS}/PLUGINS.md`} target="_blank" rel="noreferrer"><Button>Read how (PLUGINS.md)</Button></a></div>
        </Step>
      )}

      {kind === "app" && !resume && !created && (
        <Step n={2} title="Name it and say where it runs">
          {create.notice && <div className="mb-3"><Alert tone={create.notice.tone}>{create.notice.text}</Alert></div>}
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Your project's name"><Input value={name} placeholder="My Shop" onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Its short key" hint="Lowercase letters, digits and _. Its tools appear as key_tool."><Input value={key} maxLength={24} onChange={(e) => { setKeyTouched(true); setKey(e.target.value.toLowerCase()); }} /></Field>
            <div className="sm:col-span-2"><Field label="Its web address" hint="Where your project runs on the internet, e.g. https://shop.example.com. Control Tower will call it at the address below.">
              <Input value={address} placeholder="https://shop.example.com" onChange={(e) => setAddress(e.target.value)} />
            </Field>
            {base_url && <p className="mt-1 text-xs text-muted">Control Tower will call: <span className="font-mono text-fg">{base_url}</span></p>}</div>
          </div>
          <div className="mt-4"><Button variant="primary" disabled={!key || !base_url} loading={create.busy} onClick={() => create.run(undefined)}>Create the connection</Button></div>
        </Step>
      )}

      {kind === "app" && (created || resume) && (
        <>
          <Step n={3} title="Your project's settings" done={false}>
            {created?.secret ? (
              <>
                <Alert tone="warning">The shared secret is shown only now. Put these three settings into your project now — in its <code>.env</code> file, or in your hosting dashboard's environment settings.</Alert>
                <div className="mt-3 flex items-start gap-2">
                  <pre className="min-w-0 flex-1 overflow-x-auto rounded bg-raised p-3 text-xs text-fg">{settingsText}</pre>
                  <Copy text={settingsText} />
                </div>
                <p className="mt-2 text-xs text-muted">Keep the secret out of chats, e-mails and code. If it ever leaks, press New secret below and put the new one in.</p>
              </>
            ) : (
              <>
                <p className="text-[13px] text-muted">Your project needs <code>CONTROL_TOWER_SECRET</code> (the secret shown when you connected it), <code>CONTROL_TOWER_URL={tower}</code> and <code>CONTROL_TOWER_KEY={connKey}</code>. Lost the secret? Make a new one and put it in your project.</p>
                <div className="mt-3"><Button size="sm" loading={renew.busy} onClick={() => renew.run(undefined)}>New secret</Button></div>
              </>
            )}
          </Step>

          <Step n={4} title="Teach your project to talk to Control Tower">
            <div className="grid gap-4 lg:grid-cols-2">
              <div>
                <p className="flex items-center gap-2 text-[13px] font-semibold text-fg"><Bot className="h-4 w-4 text-primary" aria-hidden />With your coding agent</p>
                <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-[13px] text-muted">
                  <li>Download the brief and save it in your project's main folder (as <code>CONTROL-TOWER.md</code>).</li>
                  <li>Open your project in your coding agent and paste the message below.</li>
                  <li>Answer its questions, let it finish, then deploy your project as you always do.</li>
                </ol>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button variant="primary" onClick={() => { setDownloadError(""); download(`/admin-api/plugins/connect-kit/brief?key=${encodeURIComponent(connKey)}&base_url=${encodeURIComponent(base)}`, "CONTROL-TOWER.md").catch((e: Error) => setDownloadError(e.message)); }}><Download className="h-3.5 w-3.5" aria-hidden />Download the brief</Button>
                  <Button onClick={() => { setDownloadError(""); download("/admin-api/plugins/connect-kit/check-script", "check_connection.py").catch((e: Error) => setDownloadError(e.message)); }}><Download className="h-3.5 w-3.5" aria-hidden />The connection check</Button>
                </div>
                {downloadError && <p className="mt-2 text-xs text-danger">{downloadError}</p>}
              </div>
              <div>
                <p className="text-[13px] font-semibold text-fg">With a developer</p>
                <p className="mt-2 text-[13px] text-muted">Send them the brief (download it on the left) and the address Control Tower will call: <span className="font-mono text-fg">{base || "—"}</span>. The secret you give them separately, or put it in yourself.</p>
              </div>
            </div>
            <div className="mt-4">
              <Field label="What should your agents see? (optional)" hint="In your own words, e.g. “new sign-ups per day, the newest customers, orders that failed”. Leave empty and your coding agent will suggest a list and ask you.">
                <Textarea rows={2} value={wishes} onChange={(e) => setWishes(e.target.value)} />
              </Field>
            </div>
            <div className="mt-3">
              <div className="mb-1 flex items-center justify-between"><span className="text-xs font-semibold uppercase tracking-wide text-muted">The message for your coding agent</span><Copy text={prompt} label="Copy the message" /></div>
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded bg-raised p-3 text-xs text-fg">{prompt}</pre>
            </div>
          </Step>

          <Step n={5} title="Check the connection" done={result?.ok}>
            <p className="text-[13px] text-muted">When your project runs with the new part and its settings, press the button. Control Tower asks your project the way it will from now on, and tells you what to fix if something is not right.</p>
            <div className="mt-3"><Button variant="primary" loading={check.busy} onClick={() => check.run(undefined)}>Check the connection</Button></div>
            {check.notice?.tone === "danger" && <div className="mt-3"><Alert>{check.notice.text}</Alert></div>}
            {result && (
              <div className={`mt-3 rounded-[var(--radius-card)] border p-3 ${result.ok ? "border-success bg-success-soft" : "border-danger bg-danger-soft"}`}>
                <p className="flex items-center gap-2 text-[13px] font-semibold text-fg">{result.ok ? <CheckCircle2 className="h-4 w-4 text-success" aria-hidden /> : <XCircle className="h-4 w-4 text-danger" aria-hidden />}{result.message}</p>
                {result.hint && <p className="mt-1 text-[13px] text-fg">{result.hint}</p>}
                {result.ok && offered && (
                  <p className="mt-2 text-xs text-muted">{offered.name} offers {offered.tools.length} tools, {offered.actions.length} actions, {offered.metrics.length} numbers and {offered.templates.length} starter agents. Hidden from agents: {offered.personal.join(", ") || "nothing marked personal"}.</p>
                )}
              </div>
            )}
          </Step>

          {result?.ok && (
            <Step n={6} title="Switch it on and make your first agent" done={enabled}>
              {switchOn.notice && <div className="mb-3"><Alert tone={switchOn.notice.tone}>{switchOn.notice.text}</Alert></div>}
              <div className="flex flex-wrap gap-2">
                {!enabled && <Button variant="primary" loading={switchOn.busy} onClick={() => switchOn.run(undefined)}>Switch it on</Button>}
                {enabled && <Badge tone="success">On</Badge>}
                <Button disabled={!enabled} onClick={() => navigate("/team/new")}>Make an agent</Button>
                <Link to="/plugins"><Button>See it on Plugins</Button></Link>
              </div>
              {offered && offered.templates.length > 0 && <p className="mt-2 text-xs text-muted">Its starter agents: {offered.templates.map((t) => t.label).join(", ")}.</p>}
            </Step>
          )}
        </>
      )}

      <p className="mt-6 text-xs text-subtle">Stuck? Read <a className="underline" href={`${DOCS}/GUIDE.md`} target="_blank" rel="noreferrer">the guide</a>, or ask Nia (the round button).</p>
    </>
  );
}
