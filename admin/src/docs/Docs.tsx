/* Admin → Docs: what every admin page and function does (backend/app/admin_docs/*.md, the same
   text Nia reads). The "?" in the sidebar opens the section for the page you are on. */
import { useQuery } from "@tanstack/react-query";
import { BookOpen, HelpCircle, Search } from "lucide-react";
import { Fragment, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, unwrap } from "../api";
import { Alert, Card, Dialog, Empty, Input, PageTitle, cn } from "../ui";

/** Links in the docs: "/opportunities" opens an admin page, "docs:opportunities" another section. */
function href(target: string): { to: string; external: boolean } {
  if (target.startsWith("docs:")) return { to: `/docs/${target.slice(5)}`, external: false };
  if (target.startsWith("/")) return { to: target, external: false };
  return { to: target, external: true };
}

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let i = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index! > last) out.push(text.slice(last, match.index));
    const token = match[0];
    const id = `${key}-${i++}`;
    if (token.startsWith("**")) out.push(<strong key={id} className="font-semibold text-fg">{token.slice(2, -2)}</strong>);
    else if (token.startsWith("`")) out.push(<code key={id} className="rounded bg-raised px-1 py-0.5 font-mono text-[12px]">{token.slice(1, -1)}</code>);
    else {
      const [, label, target] = /\[([^\]]+)\]\(([^)]+)\)/.exec(token)!;
      const link = href(target);
      out.push(link.external
        ? <a key={id} href={link.to} target="_blank" rel="noreferrer noopener" className="text-primary hover:underline">{label}</a>
        : <Link key={id} to={link.to} className="text-primary hover:underline">{label}</Link>);
    }
    last = match.index! + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** The small part of Markdown the docs use: headings, paragraphs, lists, tables, notes. */
export function DocText({ body }: { body: string }) {
  const blocks: ReactNode[] = [];
  const lines = body.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${i}`;
    if (!line.trim()) { i++; continue; }
    if (line.startsWith("### ")) { blocks.push(<h3 key={key} className="mt-5 text-[14px] font-semibold text-fg">{inline(line.slice(4), key)}</h3>); i++; continue; }
    if (line.startsWith("## ")) { blocks.push(<h2 key={key} className="mt-7 border-b border-border pb-1 text-[16px] font-semibold text-fg first:mt-0">{inline(line.slice(3), key)}</h2>); i++; continue; }
    if (line.startsWith("> ")) {
      const note: string[] = [];
      while (i < lines.length && lines[i].startsWith("> ")) note.push(lines[i++].slice(2));
      blocks.push(<div key={key} className="mt-3 rounded-[var(--radius-control)] border-l-4 border-primary bg-raised px-3 py-2 text-[13px]">{inline(note.join(" "), key)}</div>);
      continue;
    }
    if (line.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-+:?$/.test(c))) rows.push(cells);
        i++;
      }
      const [head, ...rest] = rows;
      blocks.push(
        <div key={key} className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <thead><tr>{head.map((c, n) => <th key={n} className="border-b border-border px-2 py-1.5 font-medium text-muted">{inline(c, `${key}h${n}`)}</th>)}</tr></thead>
            <tbody>{rest.map((r, rn) => <tr key={rn} className="align-top">{r.map((c, n) => <td key={n} className="border-b border-border px-2 py-1.5">{inline(c, `${key}r${rn}c${n}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }
    const bullet = /^(\s*)([-*]|\d+\.) (.*)$/;
    if (bullet.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: { depth: number; text: string }[] = [];
      while (i < lines.length && bullet.test(lines[i])) {
        const [, indent, , text] = bullet.exec(lines[i])!;
        items.push({ depth: indent.length >= 2 ? 1 : 0, text });
        i++;
      }
      const List = ordered ? "ol" : "ul";
      blocks.push(
        <List key={key} className={cn("mt-2 space-y-1 pl-5 text-[13px]", ordered ? "list-decimal" : "list-disc")}>
          {items.map((item, n) => <li key={n} className={item.depth ? "ml-5 list-[circle]" : ""}>{inline(item.text, `${key}i${n}`)}</li>)}
        </List>,
      );
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{2,3} |> |\||\s*([-*]|\d+\.) )/.test(lines[i])) para.push(lines[i++]);
    blocks.push(<p key={key} className="mt-2 text-[13px] leading-relaxed">{inline(para.join(" "), key)}</p>);
  }
  return <div className="text-fg">{blocks.map((b, n) => <Fragment key={n}>{b}</Fragment>)}</div>;
}

export function DocsPage() {
  const { key } = useParams();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["docs", q], queryFn: async () => unwrap(await api.GET("/admin-api/docs", { params: { query: { q } } })) });
  const current = key ?? list.data?.[0]?.key;
  const doc = useQuery({ queryKey: ["doc", current], enabled: Boolean(current) && !q, queryFn: async () => unwrap(await api.GET("/admin-api/docs/{key}", { params: { path: { key: current! } } })) });
  return (
    <>
      <PageTitle title="Docs" description="What every admin page and function does, who may use it and what happens. Nia reads the same text." />
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <Card padded={false}>
          <div className="relative p-3">
            <Search className="pointer-events-none absolute left-5 top-5 h-4 w-4 text-muted" aria-hidden />
            <Input className="pl-8" placeholder="Search the docs" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <nav className="max-h-[70vh] overflow-y-auto pb-2">
            {(list.data ?? []).map((s) => (
              <button key={s.key} type="button" onClick={() => { setQ(""); navigate(`/docs/${s.key}`); }}
                className={cn("block w-full px-3 py-1.5 text-left text-[13px] hover:bg-raised", s.key === current && !q ? "bg-raised font-medium text-fg" : "text-muted")}>
                {s.title}
                {q && s.match && <span className="mt-0.5 block text-[11px] text-muted">{s.match}</span>}
              </button>
            ))}
            {list.data && list.data.length === 0 && <p className="px-3 py-2 text-[12px] text-muted">Nothing found.</p>}
          </nav>
        </Card>
        <Card>
          {q ? <Empty>Choose a result on the left.</Empty> : doc.error ? <Alert>{doc.error.message}</Alert> : !doc.data ? <Empty>…</Empty> : (
            <article>
              <h1 className="text-[18px] font-semibold text-fg">{doc.data.title}</h1>
              <p className="mt-1 text-[13px] text-muted">{doc.data.summary}</p>
              {doc.data.screens.length > 0 && (
                <p className="mt-2 text-[12px] text-muted">Pages: {doc.data.screens.filter((s) => !s.includes(":")).map((s, n) => <Fragment key={s}>{n > 0 && " · "}<Link to={s} className="text-primary hover:underline">{s}</Link></Fragment>)}</p>
              )}
              <div className="mt-4"><DocText body={doc.data.body} /></div>
            </article>
          )}
        </Card>
      </div>
    </>
  );
}

/** The "?" in the sidebar: the section for the page you are on. */
export function HelpButton() {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const doc = useQuery({
    queryKey: ["doc-screen", location.pathname],
    enabled: open,
    retry: false,
    queryFn: async () => unwrap(await api.GET("/admin-api/docs/for-screen", { params: { query: { path: location.pathname } } })),
  });
  return (
    <>
      <button type="button" title="Help for this page" aria-label="Help for this page" onClick={() => setOpen(true)}
        className="rounded-full p-1.5 text-muted hover:bg-raised hover:text-fg">
        <HelpCircle className="h-4 w-4" aria-hidden />
      </button>
      <Dialog open={open} onClose={() => setOpen(false)} title={doc.data?.title ?? "Help"}
        footer={doc.data ? <Link to={`/docs/${doc.data.key}`} onClick={() => setOpen(false)} className="inline-flex items-center gap-1.5 text-[13px] text-primary hover:underline"><BookOpen className="h-4 w-4" aria-hidden />Open in Docs</Link> : undefined}>
        <div className="max-h-[65vh] overflow-y-auto">
          {doc.error ? <Alert tone="info">{doc.error.message}</Alert> : !doc.data ? <Empty>…</Empty> : <><p className="text-[13px] text-muted">{doc.data.summary}</p><div className="mt-3"><DocText body={doc.data.body} /></div></>}
        </div>
      </Dialog>
    </>
  );
}
