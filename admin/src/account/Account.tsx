/* Admin → My account: the signed-in admin's own details and password. Changing the password ends every other
   sign-in of this admin; this one stays. */
import { useState, type FormEvent } from "react";
import { api, unwrap } from "../api";
import { useAction } from "../hooks";
import { Alert, Button, Card, Field, Input, PageTitle } from "../ui";

export function AccountPage({ email, name, role }: { email: string; name: string; role: string }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const change = useAction(async () => unwrap(await api.POST("/admin-api/auth/password", { body: { current_password: current, new_password: next } })), {
    done: (r) => { setCurrent(""); setNext(""); setAgain(""); return r.message; },
  });
  const mismatch = again.length > 0 && again !== next;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!mismatch) change.run(undefined);
  };
  return (
    <>
      <PageTitle title="My account" description={`${name || email} · ${email} · ${role}`} />
      <Card title="Change password" className="max-w-lg">
        <form onSubmit={submit} className="space-y-3">
          {change.notice && <Alert tone={change.notice.tone}>{change.notice.text}</Alert>}
          <Field label="Current password"><Input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></Field>
          <Field label="New password" hint="At least 12 characters. Use a password manager."><Input type="password" autoComplete="new-password" minLength={12} value={next} onChange={(e) => setNext(e.target.value)} required /></Field>
          <Field label="New password again" hint={mismatch ? "The two new passwords are not the same." : undefined}>
            <Input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
          </Field>
          <Button type="submit" variant="primary" loading={change.busy} disabled={!current || next.length < 12 || again !== next}>Change password</Button>
          <p className="text-xs text-muted">Your other sign-ins (other browsers or devices) end; this one stays. Your authenticator app stays the same.</p>
        </form>
      </Card>
    </>
  );
}
