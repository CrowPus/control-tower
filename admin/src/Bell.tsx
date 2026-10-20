import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell as BellIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { cn } from "./ui";

export interface BellItem {
  id: string;
  kind: string;
  at: string;
  unread: boolean;
  link: string;
  params: Record<string, string>;
}

export interface BellFeed {
  unread: number;
  items: BellItem[];
}

/** "3 min ago" in the reader's language. */
function ago(at: string, lang: string): string {
  const seconds = Math.round((new Date(at).getTime() - Date.now()) / 1000);
  const format = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  const steps: [number, Intl.RelativeTimeFormatUnit][] = [[60, "second"], [3600, "minute"], [86400, "hour"], [604800, "day"], [Infinity, "week"]];
  let unit = 1;
  for (const [limit, name] of steps) {
    if (Math.abs(seconds) < limit) return format.format(Math.round(seconds / unit), name);
    unit = limit;
  }
  return "";
}

/**
 * The notification bell: polls the feed, shows the count of new events (also in
 * the browser tab), and marks them seen when opened. Each app words an event.
 */
export function NotificationBell(props: {
  load: () => Promise<BellFeed>;
  markSeen: () => Promise<unknown>;
  text: (item: BellItem) => string;
  title: string;
  empty: string;
  lang: string;
}) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  // What was new when the panel opened stays highlighted while it is open.
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const box = useRef<HTMLDivElement>(null);
  const feed = useQuery({ queryKey: ["bell"], queryFn: props.load, refetchInterval: 30000, refetchIntervalInBackground: true });
  const seen = useMutation({ mutationFn: props.markSeen, onSuccess: () => client.invalidateQueries({ queryKey: ["bell"] }) });
  const unread = feed.data?.unread ?? 0;

  useEffect(() => {
    const base = document.title.replace(/^\(\d+\+?\) /, "");
    document.title = unread ? `(${unread > 99 ? "99+" : unread}) ${base}` : base;
  }, [unread]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !box.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  function toggle() {
    if (open) return setOpen(false);
    setFresh(new Set((feed.data?.items ?? []).filter((i) => i.unread).map((i) => i.id)));
    setOpen(true);
    if (unread) seen.mutate();
  }

  return (
    <div className="relative" ref={box}>
      <button type="button" onClick={toggle} aria-label={unread ? `${props.title} (${unread})` : props.title} aria-expanded={open}
        className="relative grid h-8 w-8 place-items-center rounded-md text-muted hover:bg-raised hover:text-fg">
        <BellIcon className="h-4 w-4" />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 min-w-[18px] rounded-full bg-danger px-1 text-center text-[10px] font-semibold leading-[18px] text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 md:left-0 md:right-auto mt-1 w-[min(360px,calc(100vw-2rem))] overflow-hidden rounded-[var(--radius-card)] border border-border bg-surface shadow-lg">
          <p className="border-b border-border px-3 py-2 text-[13px] font-semibold text-fg">{props.title}</p>
          <ul className="max-h-[60vh] overflow-y-auto">
            {(feed.data?.items ?? []).length === 0 && <li className="px-3 py-4 text-[13px] text-muted">{props.empty}</li>}
            {(feed.data?.items ?? []).map((item) => (
              <li key={item.id}>
                <button type="button" onClick={() => { setOpen(false); navigate(item.link); }}
                  className={cn("flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-raised", fresh.has(item.id) && "bg-raised/60")}>
                  <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", fresh.has(item.id) ? "bg-primary" : "bg-transparent")} aria-hidden />
                  <span className="min-w-0">
                    <span className={cn("block text-[13px] text-fg", fresh.has(item.id) && "font-medium")}>{props.text(item)}</span>
                    <span className="block text-xs text-muted">{ago(item.at, props.lang)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
