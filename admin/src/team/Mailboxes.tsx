/* The mailboxes the team uses. You enter every value; the password is stored encrypted and
   never shown again. "Test" signs in to both servers without sending or reading anything. */
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, Card, Field, Input, PageTitle, Select } from "../ui";

type View = Schemas["MailboxView"];
type Body = Schemas["TeamMailboxIn"];

const TITLES: Record<string, { title: string; hint: string }> = {
  marketing: { title: "Marketing mailbox", hint: "The address the company shows in public. No agent sends from it; you read and answer it in Mail." },
};

function fromView(v: View): Body {
  return { address: v.address, sender_name: v.sender_name, login: v.login, smtp_host: v.smtp_host, smtp_port: v.smtp_port, smtp_security: v.smtp_security, imap_host: v.imap_host, imap_port: v.imap_port, password: null };
}

function MailboxCard({ box, owner }: { box: View; owner: boolean }) {
  const [draft, setDraft] = useState<Body>(fromView(box));
  useEffect(() => setDraft(fromView(box)), [box]);
  const save = useAction(async (body: Body) => unwrap(await api.PUT("/admin-api/team/mailboxes/{name}", { params: { path: { name: box.name } }, body })), { invalidate: [["team-mailboxes"]], done: () => "Saved." });
  const test = useAction(async () => unwrap(await api.POST("/admin-api/team/mailboxes/{name}/test", { params: { path: { name: box.name } } })));
  const [result, setResult] = useState<Schemas["TeamMailboxTestOut"] | null>(null);
  const text = (key: keyof Body) => ({
    value: (draft[key] as string | null | undefined) ?? "",
    disabled: !owner,
    onChange: (e: { target: { value: string } }) => setDraft({ ...draft, [key]: e.target.value }),
  });
  const port = (key: "smtp_port" | "imap_port") => ({
    type: "number", min: 1, max: 65535, placeholder: "Not set", value: draft[key] ?? "", disabled: !owner,
    onChange: (e: { target: { value: string } }) => setDraft({ ...draft, [key]: e.target.value === "" ? null : Number(e.target.value) }),
  });
  const meta = TITLES[box.name] ?? { title: box.name, hint: "" };
  return (
    <Card title={meta.title} actions={box.missing.length ? <Badge tone="warning">Not complete</Badge> : <Badge tone="success">Complete</Badge>}>
      <p className="mb-3 text-xs text-muted">{meta.hint} The values come from your mail provider.</p>
      {save.notice && <div className="mb-3"><Alert tone={save.notice.tone}>{save.notice.text}</Alert></div>}
      {box.missing.length > 0 && <div className="mb-3"><Alert tone="warning">Missing: {box.missing.join(", ")}.</Alert></div>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Address (sender)"><Input type="email" autoComplete="off" {...text("address")} /></Field>
        <Field label="Sender name"><Input {...text("sender_name")} /></Field>
        <Field label="Login"><Input autoComplete="off" {...text("login")} /></Field>
        <Field label="Password" hint={box.password_set ? "A password is stored. Type a new one to replace it." : "Stored encrypted; never shown again."}>
          <Input type="password" autoComplete="new-password" placeholder={box.password_set ? "••••••••" : "Not set"} value={draft.password ?? ""} disabled={!owner} onChange={(e) => setDraft({ ...draft, password: e.target.value === "" ? null : e.target.value })} />
        </Field>
        <Field label="SMTP server (sending)"><Input {...text("smtp_host")} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="SMTP port"><Input {...port("smtp_port")} /></Field>
          <Field label="SMTP security">
            <Select value={draft.smtp_security ?? ""} disabled={!owner} onChange={(e) => setDraft({ ...draft, smtp_security: (e.target.value || null) as Body["smtp_security"] })}>
              <option value="">Not set</option>
              <option value="ssl">SSL/TLS</option>
              <option value="starttls">STARTTLS</option>
            </Select>
          </Field>
        </div>
        <Field label="IMAP server (reading)"><Input {...text("imap_host")} /></Field>
        <Field label="IMAP port (SSL)"><Input {...port("imap_port")} /></Field>
      </div>
      {result && (
        <div className="mt-3 grid gap-1">
          <Alert tone={result.smtp === "ok" ? "success" : "danger"}>Sending (SMTP): {result.smtp === "ok" ? "signed in" : result.smtp}</Alert>
          <Alert tone={result.imap === "ok" ? "success" : "danger"}>Reading (IMAP): {result.imap === "ok" ? "signed in" : result.imap}</Alert>
        </div>
      )}
      {test.notice?.tone === "danger" && <div className="mt-3"><Alert>{test.notice.text}</Alert></div>}
      {owner && (
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          {box.password_set && <Button variant="ghost" disabled={save.busy} onClick={() => save.run({ ...draft, password: "" })}>Remove password</Button>}
          <Button disabled={box.missing.length > 0} loading={test.busy} onClick={() => { setResult(null); test.run(undefined, { onSuccess: setResult }); }}>Test connection</Button>
          <Button variant="primary" loading={save.busy} onClick={() => save.run(draft)}>Save</Button>
        </div>
      )}
    </Card>
  );
}

export function MailboxesPage({ owner }: { owner: boolean }) {
  const { data, error } = useQuery({ queryKey: ["team-mailboxes"], queryFn: async () => unwrap(await api.GET("/admin-api/team/mailboxes")) });
  if (error) return <Alert>{error.message}</Alert>;
  if (!data) return null;
  return (
    <>
      <Link to="/team" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg"><ArrowLeft className="h-3.5 w-3.5" aria-hidden />Team</Link>
      <PageTitle title="Mailboxes" description="The team's own mailboxes, separate from the service mailbox that sends sign-in e-mails. Every value is yours to enter; nothing is set in the code." />
      <div className="grid gap-4 xl:grid-cols-2">{data.map((box) => <MailboxCard key={box.name} box={box} owner={owner} />)}</div>
    </>
  );
}
