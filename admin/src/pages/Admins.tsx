import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { Me } from "../App";
import { api, unwrap } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, Card, Field, Input, PageTitle, Select, StatusBadge, Table, Td, ago } from "../ui";

export function AdminsPage({ me }: { me: Me }) {
  const { data } = useQuery({ queryKey: ["admins"], queryFn: async () => unwrap(await api.GET("/admin-api/admins")) });
  const [form, setForm] = useState({ email: "", full_name: "", role: "support" as "owner" | "support" });
  const [link, setLink] = useState<string | null>(null);
  const invite = useAction(async () => unwrap(await api.POST("/admin-api/admins", { body: form })), {
    invalidate: [["admins"]],
    done: (r) => { setLink(r.link); setForm({ email: "", full_name: "", role: "support" }); return r.emailed ? `Invitation e-mailed to ${r.admin.email}.` : `Could not e-mail ${r.admin.email}; send them the link below.`; },
  });
  const patch = useAction(async ({ id, body }: { id: number; body: { role?: "owner" | "support"; status?: "active" | "disabled" } }) => unwrap(await api.PATCH("/admin-api/admins/{admin_id}", { params: { path: { admin_id: id } }, body })), { invalidate: [["admins"]], done: () => "Saved." });
  const resetMfa = useAction(async (id: number) => unwrap(await api.POST("/admin-api/admins/{admin_id}/reset-mfa", { params: { path: { admin_id: id } } })), { invalidate: [["admins"]] });
  return (
    <>
      <PageTitle title="Admins" description="Owners can change everything, including this list. Support admins can read and work in the admin, but not change the platform, the admins or other owner settings." />
      {[invite.notice, patch.notice, resetMfa.notice].map((n, i) => n && <div key={i} className="mb-2"><Alert tone={n.tone}>{n.text}</Alert></div>)}
      <Card title="Invite an admin">
        <form className="grid gap-3 sm:grid-cols-[1fr_1fr_140px_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); invite.run(undefined); }}>
          <Field label="Name"><Input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required /></Field>
          <Field label="E-mail"><Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required /></Field>
          <Field label="Role"><Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as "owner" | "support" })}><option value="support">Support</option><option value="owner">Owner</option></Select></Field>
          <Button type="submit" variant="primary" loading={invite.busy}>Invite</Button>
        </form>
        {link && <p className="mt-3 break-all rounded bg-raised p-2 font-mono text-[11px]">{link}</p>}
      </Card>
      <Card className="mt-4" padded={false}>
        <Table head={["Admin", "Role", "Status", "Authenticator", "Last sign-in", ""]}>
          {(data ?? []).map((a) => {
            const self = a.id === me.admin.id;
            return (
              <tr key={a.id}>
                <Td><span className="font-medium">{a.full_name}</span>{self && <> <Badge>you</Badge></>}<p className="text-xs text-muted">{a.email}</p></Td>
                <Td><Select className="w-28" value={a.role} disabled={self} onChange={(e) => patch.run({ id: a.id, body: { role: e.target.value as "owner" | "support" } })}><option value="support">Support</option><option value="owner">Owner</option></Select></Td>
                <Td><StatusBadge status={a.status} /></Td>
                <Td>{a.mfa_enrolled ? <Badge tone="success">set up</Badge> : <Badge tone="warning">not yet</Badge>}</Td>
                <Td className="whitespace-nowrap text-muted">{ago(a.last_login_at)}</Td>
                <Td className="whitespace-nowrap">
                  {!self && a.status !== "invited" && (
                    <>
                      <Button size="sm" variant="ghost" onClick={() => patch.run({ id: a.id, body: { status: a.status === "disabled" ? "active" : "disabled" } })}>{a.status === "disabled" ? "Enable" : "Disable"}</Button>{" "}
                      {a.mfa_enrolled && <Button size="sm" variant="ghost" onClick={() => resetMfa.run(a.id)}>Reset authenticator</Button>}
                    </>
                  )}
                </Td>
              </tr>
            );
          })}
        </Table>
      </Card>
    </>
  );
}
