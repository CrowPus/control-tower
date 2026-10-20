/* An address field like in Outlook: each address a token, suggestions while typing (people written
   with, and contacts of companies on our list), several pasted at once, Backspace removes the last. */
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, unwrap } from "../api";
import { cn } from "../ui";

const ADDRESS = /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]{2,}$/;

export function bareAddress(token: string): string {
  const match = token.match(/<([^>]+)>/);
  return (match ? match[1] : token).trim();
}

function valid(token: string): boolean {
  return ADDRESS.test(bareAddress(token));
}

function splitTokens(text: string): string[] {
  // "Kim Muster <kim@x.de>, ole@y.de; z@w.de" → three tokens (commas inside quotes stay).
  const out: string[] = [];
  let current = "";
  let quoted = false;
  let angled = false;
  for (const char of text) {
    if (char === '"') quoted = !quoted;
    if (char === "<") angled = true;
    if (char === ">") angled = false;
    if (!quoted && !angled && (char === "," || char === ";" || char === "\n")) {
      if (current.trim()) out.push(current.trim());
      current = "";
    } else current += char;
  }
  if (current.trim()) out.push(current.trim());
  return out;
}

export function Recipients({ label, value, onChange, autoFocus }: { label: string; value: string[]; onChange: (next: string[]) => void; autoFocus?: boolean }) {
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [focused, setFocused] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setQuery(text.trim()), 180);
    return () => clearTimeout(t);
  }, [text]);

  const { data: suggestions } = useQuery({
    queryKey: ["mail-contacts", query],
    queryFn: async () => unwrap(await api.GET("/admin-api/mail/contacts", { params: { query: { q: query } } })),
    enabled: query.length >= 2 && focused,
    staleTime: 60_000,
  });
  const shown = (suggestions ?? []).filter((s) => !value.some((v) => bareAddress(v).toLowerCase() === s.address));

  function add(tokens: string[]) {
    const fresh = tokens.filter((t) => t && !value.some((v) => bareAddress(v).toLowerCase() === bareAddress(t).toLowerCase()));
    if (fresh.length) onChange([...value, ...fresh]);
    setText("");
    setActive(0);
  }

  function choose(index: number) {
    const s = shown[index];
    if (s) add([s.name ? `${s.name} <${s.address}>` : s.address]);
  }

  return (
    <div className="relative flex min-h-9 items-start gap-2 border-b border-border px-3 py-1.5" onClick={() => input.current?.focus()}>
      <span className="w-10 shrink-0 pt-1 text-[13px] text-muted">{label}</span>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
        {value.map((token, i) => (
          <span key={`${token}-${i}`} title={token}
            className={cn("inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-xs", valid(token) ? "bg-raised text-fg" : "bg-danger-soft text-danger")}>
            <span className="truncate">{token.replace(/\s*<[^>]+>/, "") || token}</span>
            <button type="button" aria-label={`Remove ${token}`} className="text-muted hover:text-fg" onClick={(e) => { e.stopPropagation(); onChange(value.filter((_, j) => j !== i)); }}>
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          ref={input}
          autoFocus={autoFocus}
          aria-label={label}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => { setTimeout(() => setFocused(false), 150); if (text.trim()) add(splitTokens(text)); }}
          onPaste={(e) => {
            const pasted = e.clipboardData.getData("text");
            if (/[,;\n]/.test(pasted)) { e.preventDefault(); add(splitTokens(text + pasted)); }
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" && shown.length) { e.preventDefault(); setActive((active + 1) % shown.length); }
            else if (e.key === "ArrowUp" && shown.length) { e.preventDefault(); setActive((active - 1 + shown.length) % shown.length); }
            else if ((e.key === "Enter" || e.key === "Tab") && shown.length && focused && text.trim()) { e.preventDefault(); choose(active); }
            else if (e.key === "Enter" || e.key === "," || e.key === ";") { if (text.trim()) { e.preventDefault(); add(splitTokens(text)); } }
            else if (e.key === "Backspace" && !text && value.length) onChange(value.slice(0, -1));
          }}
          className="min-w-[8rem] flex-1 bg-transparent py-1 text-[13px] text-fg outline-none"
        />
      </div>
      {focused && text.trim().length >= 2 && shown.length > 0 && (
        <ul role="listbox" className="absolute left-12 right-3 top-full z-30 mt-0.5 overflow-hidden rounded-md border border-border bg-surface shadow-[var(--shadow)]">
          {shown.map((s, i) => (
            <li key={s.address} role="option" aria-selected={i === active}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => choose(i)}
                className={cn("block w-full px-3 py-1.5 text-left text-[13px]", i === active ? "bg-raised" : "hover:bg-raised")}>
                <span className="font-medium text-fg">{s.name || s.address}</span>
                {s.name && <span className="ml-1.5 text-muted">{s.address}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
