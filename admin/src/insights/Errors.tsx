import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api";
import { useAction } from "../hooks";
import { Alert, Badge, Button, ago, cn } from "../ui";
import { nameOf } from "./format";
import { Failed, Loading, Note } from "./parts";

type Group = Record<string, unknown>;
const STATUSES = ["open", "resolved", "ignored", "all"] as const;

export function ErrorsPage({ owner }: { owner: boolean }) {
  const [status, setStatus] = useState<(typeof STATUSES)[number]>("open");
  const { data, error } = useQuery({
    queryKey: ["insights", "errors", status],
    queryFn: async () => unwrap(await api.GET("/admin-api/insights/errors", { params: { query: { status } } })),
    refetchInterval: 60_000,
  });
  const action = useAction(
    async ({ fingerprint, next }: { fingerprint: string; next: "open" | "resolved" | "ignored" }) =>
      unwrap(await api.POST("/admin-api/insights/errors/{fingerprint}", { params: { path: { fingerprint } }, body: { status: next } })),
    { invalidate: [["insights", "errors"]], done: (r) => `Marked ${r.status}.` },
  );
  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-[var(--radius-control)] border border-border bg-surface p-0.5" role="radiogroup" aria-label="Status">
          {STATUSES.map((s) => (
            <button key={s} type="button" role="radio" aria-checked={status === s} onClick={() => setStatus(s)} className={cn("rounded-[calc(var(--radius-control)-2px)] px-3 py-1 text-xs capitalize", status === s ? "bg-primary font-medium text-primary-fg" : "text-muted hover:text-fg")}>{s}</button>
          ))}
        </div>
        <span className="text-xs text-subtle">The same error from many people is one group. Messages are scrubbed: no e-mail addresses, links, tokens or numbers.</span>
      </div>
      {action.notice && <div className="mb-3"><Alert tone={action.notice.tone}>{action.notice.text}</Alert></div>}
      {error ? <Failed error={error} /> : !data ? <Loading /> : !data.length ? (
        <Note>{status === "open" ? "No open errors. Browser and phone errors appear here as soon as the telemetry is live." : "Nothing here."}</Note>
      ) : (
        <ul className="space-y-2">
          {(data as Group[]).map((g) => {
            const fp = String(g.fingerprint);
            const crash = g.kind === "crash";
            return (
              <li key={fp} className="rounded-[var(--radius-card)] border border-border bg-surface px-4 py-3">
                <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2 text-[13px]">
                      <Badge tone={crash ? "danger" : "warning"}>{crash ? "Crash" : "Error"}</Badge>
                      <span className="font-semibold text-fg">{String(g.type || "Error")}</span>
                      <span className="text-xs text-muted">{nameOf(String(g.app))}{g.page ? ` · ${String(g.page)}` : ""}</span>
                    </p>
                    <p className="mt-1 break-words font-mono text-xs text-muted">{String(g.message || "(no message)")}</p>
                  </div>
                  <div className="text-right text-xs">
                    <p className="font-semibold tabular-nums text-fg">{String(g.count)} times</p>
                    <p className="text-muted">{String(g.last_24h)} in the last 24 h</p>
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-subtle">
                  <span>First {ago(String(g.first_seen))}</span>
                  <span>Last {ago(String(g.last_seen))}</span>
                  {(g.versions as string[])?.length > 0 && <span>Versions {(g.versions as string[]).join(", ")}</span>}
                  {g.status !== "open" && <Badge>{String(g.status)}</Badge>}
                  {owner && (
                    <span className="ml-auto flex gap-1.5">
                      {g.status !== "resolved" && <Button size="sm" loading={action.busy && action.variables?.fingerprint === fp && action.variables.next === "resolved"} onClick={() => action.run({ fingerprint: fp, next: "resolved" })}>Fixed</Button>}
                      {g.status !== "ignored" && <Button size="sm" variant="ghost" onClick={() => action.run({ fingerprint: fp, next: "ignored" })}>Ignore</Button>}
                      {g.status !== "open" && <Button size="sm" variant="ghost" onClick={() => action.run({ fingerprint: fp, next: "open" })}>Reopen</Button>}
                    </span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-3 text-[11px] text-subtle">A group marked fixed opens again by itself if the error comes back.</p>
    </>
  );
}
