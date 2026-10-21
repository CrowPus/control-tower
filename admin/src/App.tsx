import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, BarChart3, UserRound, Bot, ClipboardList, Cpu, Gauge, Inbox, ListChecks, LogOut, Mail, Server, ShieldCheck, SlidersHorizontal, Sparkles, BookOpen, Mailbox, Megaphone, Network, Building, FlaskConical, ShieldAlert, Plug, Rocket } from "lucide-react";
import { Suspense, lazy, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, NavLink, Navigate, Route, Routes, useNavigate, useSearchParams } from "react-router-dom";
import { NotificationBell, type BellItem } from "./Bell";
import { AccountPage } from "./account/Account";
import { InsightsArea } from "./insights/Area";
import { DocsPage, HelpButton } from "./docs/Docs";
import { MarketingArea } from "./marketing/Marketing";
import { TrustPage } from "./oversight/Oversight";
import { ProductArea } from "./product/Product";
import { MailPage } from "./mail/MailPage";
import { MeetingPage, PresenterProvider } from "./nia/Meetings";
import { VoiceProvider } from "./nia/Voice";
import { BrowserPage } from "./nia/Browser";
import { NiaButton, NiaMemoryPage, NiaPage } from "./nia/Nia";
import { BrainPage } from "./brain/Brain";
// The 3D office loads its 3D engine only when opened.
const OfficePage = lazy(() => import("./office/Office").then((m) => ({ default: m.OfficePage })));
import { AgentDetailPage } from "./team/AgentDetail";
import { NewAgentPage } from "./team/Design";
import { ApprovalsPage } from "./team/Approvals";
import { MailboxesPage } from "./team/Mailboxes";
import { OutsideAgentPage, OutsidePage } from "./team/Outside";
import { TeamPage } from "./team/Team";
import { ConnectProjectPage } from "./pages/ConnectProject";
import { PluginsPage } from "./pages/Plugins";
import { WelcomePage, useSetup } from "./pages/Welcome";
import { api, errorMessage, onSignedOut, setCsrfToken, unwrap } from "./api";
import { AdminLogPage } from "./pages/AdminLog";
import { AdminsPage } from "./pages/Admins";
import { OverviewPage } from "./pages/Overview";
import { PlatformPage } from "./pages/Platform";
import { EmailsPage } from "./pages/Emails";
import { SystemPage } from "./pages/System";
import { TasksPage } from "./pages/Tasks";
import { UsagePage } from "./pages/Usage";
import { Alert, Button, Field, Input, cn } from "./ui";
import type { components } from "./schema";

export type Me = components["schemas"]["MeOut"];

/** The logo's mark: the bright version on light pages, the dark version on dark ones (the theme decides). */
function Mark({ className }: { className: string }) {
  return (
    <>
      <img src="/brand/mark.png" alt="" className={cn("logo-light", className)} />
      <img src="/brand/mark-dark.png" alt="" className={cn("logo-dark", className)} />
    </>
  );
}

function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const result = await api.GET("/admin-api/auth/me");
      if (result.response.status === 401) return null;
      const me = unwrap(result);
      setCsrfToken(me.csrf_token);
      return me;
    },
    retry: false,
  });
}

function Frame({ title, children, footer }: { title: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="grid min-h-screen place-items-center bg-bg px-4 py-8">
      <div className="w-full max-w-sm">
        <div className="mb-5 flex items-center gap-2">
          <Mark className="h-8 w-8" />
          <div>
            <p className="text-[15px] font-semibold text-fg">Control Tower</p>
            <p className="text-xs text-muted">Restricted area. Every action is logged.</p>
          </div>
        </div>
        <div className="rounded-[var(--radius-card)] border border-border bg-surface p-5">
          <h1 className="mb-3 text-[13px] font-semibold text-fg">{title}</h1>
          {children}
        </div>
        {footer && <div className="mt-3 text-center text-xs text-muted">{footer}</div>}
      </div>
    </div>
  );
}

function SignIn({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = unwrap(await api.POST("/admin-api/auth/login", { body: { email, password } }));
      setCsrfToken(result.csrf_token);
      onDone();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Frame title="Sign in">
      <form className="space-y-3" onSubmit={submit}>
        <Field label="E-mail"><Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
        <Field label="Password"><Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        {error && <Alert>{error}</Alert>}
        <Button type="submit" variant="primary" className="w-full" loading={busy}>Continue</Button>
      </form>
    </Frame>
  );
}

function SecondFactor({ me, onDone, onCancel }: { me: Me; onDone: () => void; onCancel: () => void }) {
  const enrolling = !me.admin.mfa_enrolled;
  const [enrolment, setEnrolment] = useState<{ secret: string; qr_svg: string } | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!enrolling || enrolment) return;
    api.POST("/admin-api/auth/enroll/start").then((r) => {
      try {
        setEnrolment(unwrap(r));
      } catch (e) {
        setError(errorMessage(e));
      }
    });
  }, [enrolling, enrolment]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      unwrap(enrolling ? await api.POST("/admin-api/auth/enroll/confirm", { body: { code } }) : await api.POST("/admin-api/auth/verify", { body: { code } }));
      onDone();
    } catch (e) {
      setError(errorMessage(e));
      setCode("");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Frame title={enrolling ? "Set up your authenticator app" : "Enter your code"} footer={<button type="button" className="text-primary hover:underline" onClick={onCancel}>Use another account</button>}>
      <form className="space-y-3" onSubmit={submit}>
        {enrolling && (
          <div className="space-y-2">
            <p className="text-[13px] text-muted">Scan this code with an authenticator app (1Password, Google Authenticator, Microsoft Authenticator). It is required at every sign-in.</p>
            {enrolment ? (
              <>
                <div className="mx-auto w-fit rounded bg-white p-2" dangerouslySetInnerHTML={{ __html: enrolment.qr_svg }} />
                <p className="break-all text-center font-mono text-xs text-muted">{enrolment.secret}</p>
              </>
            ) : (
              <p className="text-xs text-muted">Preparing…</p>
            )}
          </div>
        )}
        <Field label="Six-digit code"><Input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} autoFocus required /></Field>
        {error && <Alert>{error}</Alert>}
        <Button type="submit" variant="primary" className="w-full" loading={busy} disabled={code.length !== 6}>{enrolling ? "Confirm and sign in" : "Sign in"}</Button>
      </form>
    </Frame>
  );
}

function AcceptInvite() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(token ? null : "This link is incomplete.");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      unwrap(await api.POST("/admin-api/auth/accept-invite", { body: { token, password } }));
      setDone(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  if (done) {
    return (
      <Frame title="Password set">
        <p className="text-[13px] text-muted">Sign in now. You will set up your authenticator app next.</p>
        <Button variant="primary" className="mt-3 w-full" onClick={() => navigate("/", { replace: true })}>Go to sign-in</Button>
      </Frame>
    );
  }
  return (
    <Frame title="Choose your password">
      <form className="space-y-3" onSubmit={submit}>
        <Field label="Password" hint="At least 12 characters. Use a password manager."><Input type="password" autoComplete="new-password" minLength={12} value={password} onChange={(e) => setPassword(e.target.value)} required disabled={!token} /></Field>
        {error && <Alert>{error}</Alert>}
        <Button type="submit" variant="primary" className="w-full" loading={busy} disabled={!token || password.length < 12}>Set password</Button>
      </form>
    </Frame>
  );
}

function bellText(item: BellItem): string {
  switch (item.kind) {
    case "insight_critical": return `Critical: ${item.params.subject ?? "a platform alert"}`;
    case "insight_warning": return `Warning: ${item.params.subject ?? "a platform alert"}`;
    case "proposal_failed": return `Not done: ${item.params.title ?? "a proposal"} (#${item.params.id ?? ""}, ${item.params.agent ?? ""}) failed — see why in Clearance`;
    case "incident_open": return `${item.params.severity === "critical" ? "URGENT incident" : "Incident"}: ${item.params.title ?? ""}`;
    default: return item.kind;
  }
}

const NAV = [
  { to: "/insights", label: "Insights", icon: BarChart3, end: false },
  { to: "/nia", label: "Nia", icon: Sparkles, end: false },
  { to: "/team", label: "Team", icon: Bot, end: false },
  { to: "/clearance", label: "Clearance", icon: Inbox },
  { to: "/brain", label: "Brain", icon: Network },
  { to: "/office", label: "Office", icon: Building },
  { to: "/mail", label: "Mail", icon: Mailbox, end: false },
  { to: "/marketing", label: "Marketing", icon: Megaphone, end: false },
  { to: "/product", label: "Product", icon: FlaskConical, end: false },
  { to: "/trust", label: "Trust", icon: ShieldAlert },
  { to: "/overview", label: "Live overview", icon: Gauge },
  { to: "/tasks", label: "Task queue", icon: ListChecks },
  { to: "/usage", label: "AI usage", icon: Cpu },
  { to: "/plugins", label: "Plugins", icon: Plug },
  { to: "/platform", label: "Platform", icon: SlidersHorizontal },
  { to: "/system", label: "System health", icon: Server },
  { to: "/email-deliveries", label: "E-mails", icon: Mail },
  { to: "/log", label: "Admin log", icon: ClipboardList },
  { to: "/docs", label: "Docs", icon: BookOpen, end: false },
];

function Shell({ me, children }: { me: Me; children: ReactNode }) {
  const client = useQueryClient();
  // New mail in the inboxes this admin may open (Admin → Mail).
  const mailUnread = useQuery({ queryKey: ["mail", "unread"], queryFn: async () => unwrap(await api.GET("/admin-api/mail/unread")), refetchInterval: 60_000 }).data?.unread ?? 0;
  const setup = useSetup().data;
  const start = setup && !setup.finished ? [{ to: "/welcome", label: "Getting started", icon: Rocket, end: true }] : [];
  const nav = [...start, ...(me.admin.role === "owner" ? [...NAV, { to: "/admins", label: "Admins", icon: ShieldCheck }] : NAV)];
  return (
    <div className="min-h-screen bg-bg md:grid md:grid-cols-[220px_1fr]">
      <aside className="border-b border-border bg-surface md:sticky md:top-0 md:flex md:h-screen md:flex-col md:border-b-0 md:border-r">
        <div className="flex items-center gap-2 px-4 py-4">
          <Mark className="h-7 w-7" />
          <div className="min-w-0">
            <p className="text-[13px] font-semibold text-fg">Control Tower</p>
            <p className="truncate text-[11px] text-muted">{me.admin.email} · {me.admin.role}</p>
          </div>
          <div className="ml-auto flex items-center">
            <HelpButton />
            <NotificationBell
              load={async () => unwrap(await api.GET("/admin-api/notifications"))}
              markSeen={async () => unwrap(await api.POST("/admin-api/notifications/seen"))}
              text={bellText}
              title="Notifications"
              empty="Nothing new. Alerts, incidents and proposals that could not be carried out appear here."
              lang="en"
            />
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2 md:min-h-0 md:flex-1 md:flex-col md:overflow-y-auto md:overflow-x-hidden md:overscroll-contain">
          {nav.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => cn("flex shrink-0 items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-[13px]", isActive ? "bg-raised font-medium text-fg" : "text-muted hover:bg-raised hover:text-fg")}>
              <Icon className="h-4 w-4" aria-hidden />
              {label}
              {to === "/mail" && mailUnread > 0 && <span className="ml-auto rounded-full bg-primary px-1.5 text-[10px] font-semibold leading-4 text-primary-fg">{mailUnread > 99 ? "99+" : mailUnread}</span>}
            </NavLink>
          ))}
          <NavLink to="/account" className={({ isActive }) => cn("flex shrink-0 items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-[13px] md:mt-4", isActive ? "bg-raised font-medium text-fg" : "text-muted hover:bg-raised hover:text-fg")}>
            <UserRound className="h-4 w-4" aria-hidden /> My account
          </NavLink>
          <button
            type="button"
            className="flex shrink-0 items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-[13px] text-muted hover:bg-raised hover:text-fg"
            onClick={async () => {
              await api.POST("/admin-api/auth/logout");
              setCsrfToken("");
              client.setQueryData(["me"], null);
            }}
          >
            <LogOut className="h-4 w-4" aria-hidden /> Sign out
          </button>
        </nav>
      </aside>
      <VoiceProvider>
        <main className="min-w-0 px-4 py-5 md:px-8 md:py-7"><PresenterProvider>{children}</PresenterProvider></main>
        <NiaButton owner={me.admin.role === "owner"} />
      </VoiceProvider>
    </div>
  );
}

/** The start page: Getting started until the setup is finished, then Insights. */
function Home() {
  const { data } = useSetup();
  if (!data) return null;
  return <Navigate to={data.finished ? "/insights" : "/welcome"} replace />;
}

export function App() {
  const client = useQueryClient();
  const { data: me, isLoading, refetch } = useMe();
  useEffect(() => onSignedOut(() => client.setQueryData(["me"], null)), [client]);
  const reload = () => void refetch();
  return (
    <Routes>
      <Route path="/accept-invite" element={<AcceptInvite />} />
      <Route
        path="*"
        element={
          isLoading ? (
            <div className="grid min-h-screen place-items-center text-[13px] text-muted"><Activity className="h-4 w-4 animate-pulse" /></div>
          ) : !me ? (
            <SignIn onDone={reload} />
          ) : !me.mfa_passed ? (
            <SecondFactor
              me={me}
              onDone={reload}
              onCancel={async () => {
                await api.POST("/admin-api/auth/logout");
                client.setQueryData(["me"], null);
              }}
            />
          ) : (
            <Shell me={me}>
              <Routes>
                <Route index element={<Home />} />
                <Route path="welcome" element={<WelcomePage owner={me.admin.role === "owner"} />} />
                <Route path="insights/*" element={<InsightsArea owner={me.admin.role === "owner"} />} />
                <Route path="nia" element={<NiaPage owner={me.admin.role === "owner"} />} />
                <Route path="nia/meetings/:id" element={<MeetingPage owner={me.admin.role === "owner"} />} />
                <Route path="nia/memory" element={<NiaMemoryPage owner={me.admin.role === "owner"} />} />
                <Route path="nia/browser" element={<BrowserPage owner={me.admin.role === "owner"} />} />
                <Route path="office" element={<Suspense fallback={<p className="text-[13px] text-muted">Opening the office…</p>}><OfficePage /></Suspense>} />
                <Route path="brain" element={<BrainPage owner={me.admin.role === "owner"} />} />
                <Route path="plugins" element={<PluginsPage owner={me.admin.role === "owner"} />} />
                <Route path="plugins/connect" element={me.admin.role === "owner" ? <ConnectProjectPage /> : <Navigate to="/plugins" replace />} />
                <Route path="plugins/connect/:key" element={me.admin.role === "owner" ? <ConnectProjectPage /> : <Navigate to="/plugins" replace />} />
                <Route path="team" element={<TeamPage owner={me.admin.role === "owner"} />} />
                <Route path="team/new" element={me.admin.role === "owner" ? <NewAgentPage /> : <Navigate to="/team" replace />} />
                <Route path="team/mailboxes" element={<MailboxesPage owner={me.admin.role === "owner"} />} />
                <Route path="team/outside" element={<OutsidePage owner={me.admin.role === "owner"} />} />
                <Route path="team/outside/:id" element={<OutsideAgentPage owner={me.admin.role === "owner"} />} />
                <Route path="team/:key" element={<AgentDetailPage owner={me.admin.role === "owner"} />} />
                <Route path="mail" element={<MailPage />} />
                <Route path="marketing/*" element={<MarketingArea owner={me.admin.role === "owner"} />} />
                <Route path="product/*" element={<ProductArea owner={me.admin.role === "owner"} />} />
                <Route path="trust" element={<TrustPage owner={me.admin.role === "owner"} />} />
                <Route path="social" element={<Navigate to="/marketing/calendar" replace />} />
                <Route path="account" element={<AccountPage email={me.admin.email} name={me.admin.full_name} role={me.admin.role} />} />
                <Route path="clearance" element={<ApprovalsPage owner={me.admin.role === "owner"} />} />
                <Route path="approvals" element={<Navigate to="/clearance" replace />} />
                <Route path="overview" element={<OverviewPage />} />
                <Route path="tasks" element={<TasksPage />} />
                <Route path="usage" element={<UsagePage />} />
                <Route path="platform" element={<PlatformPage me={me} />} />
                <Route path="system" element={<SystemPage />} />
                <Route path="email-deliveries" element={<EmailsPage />} />
                <Route path="log" element={<AdminLogPage />} />
                <Route path="docs" element={<DocsPage />} />
                <Route path="docs/:key" element={<DocsPage />} />
                <Route path="admins" element={me.admin.role === "owner" ? <AdminsPage me={me} /> : <Navigate to="/" replace />} />
                <Route path="*" element={<p className="text-[13px] text-muted">Not found. <Link to="/insights" className="text-primary">Insights</Link></p>} />
              </Routes>
            </Shell>
          )
        }
      />
    </Routes>
  );
}
