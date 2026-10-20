/* Mail: shared pieces — types, dates, sizes, people's initials, the safe frame a
   mail's HTML is shown in, and the reader's own preferences (kept in this browser only). */
import type { Schemas } from "../api";

export type Overview = Schemas["MailOverview"];
export type Account = Schemas["AccountOut"];
export type Folder = Schemas["FolderOut"];
export type Summary = Schemas["MessageSummary"];
export type Detail = Schemas["MessageDetail"];
export type Draft = Schemas["DraftOut"];
export type Outbox = Schemas["OutboxOut"];

/** Where the list is: one folder, or a view across accounts. */
export type Place = { kind: "folder"; folderId: number; accountId: number } | { kind: "view"; view: "inbox" | "flagged" | "unread" } | { kind: "drafts" } | { kind: "outbox" };

export function placeKey(place: Place): string {
  return place.kind === "folder" ? `f${place.folderId}` : place.kind === "view" ? `v-${place.view}` : place.kind;
}

export function parsePlace(value: string | null, overview: Overview | undefined): Place {
  if (value?.startsWith("f")) {
    const id = Number(value.slice(1));
    const account = overview?.accounts.find((a) => a.folders.some((f) => f.id === id));
    if (account) return { kind: "folder", folderId: id, accountId: account.id };
  }
  if (value === "v-flagged" || value === "v-unread" || value === "v-inbox") return { kind: "view", view: value.slice(2) as "inbox" | "flagged" | "unread" };
  if (value === "drafts" || value === "outbox") return { kind: value };
  const accounts = overview?.accounts ?? [];
  if (accounts.length === 1) {
    const inbox = accounts[0].folders.find((f) => f.role === "inbox");
    if (inbox) return { kind: "folder", folderId: inbox.id, accountId: accounts[0].id };
  }
  return { kind: "view", view: "inbox" };
}

const timeOnly = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });
const weekday = new Intl.DateTimeFormat("en-GB", { weekday: "short" });
const shortDate = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit" });
const full = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

/** As mail programs show it: the time today, the weekday this week, else the date. */
export function listDate(value: string): string {
  const date = new Date(value);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) return timeOnly.format(date);
  if (now.getTime() - date.getTime() < 6 * 86400_000 && date < now) return `${weekday.format(date)} ${timeOnly.format(date)}`;
  return shortDate.format(date);
}

export function fullDate(value: string | null | undefined): string {
  return value ? full.format(new Date(value)) : "";
}

export function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function initials(name: string, address: string): string {
  const source = (name || address.split("@")[0] || "?").replace(/[^\p{L}\p{N} ]/gu, " ").trim();
  const parts = source.split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

const AVATAR = ["#0f766e", "#7c3aed", "#b45309", "#be123c", "#1d4ed8", "#4d7c0f", "#a21caf", "#0369a1"];
export function avatarColor(address: string): string {
  let hash = 0;
  for (const char of address.toLowerCase()) hash = (hash * 31 + char.charCodeAt(0)) | 0;
  return AVATAR[Math.abs(hash) % AVATAR.length];
}

export function person(pair: string[] | undefined): string {
  if (!pair) return "";
  const [name, address] = pair;
  return name ? `${name} <${address}>` : address;
}

export function people(list: string[][]): string {
  return list.map((p) => p[0] || p[1]).join(", ");
}

/** A mail's HTML in a frame without scripts: its own content policy blocks pictures from the internet
 *  (how senders see who opened a mail) until the reader asks for them. Links open in a new tab. */
export function frameDocument(html: string, { remote, dark }: { remote: boolean; dark: boolean }): string {
  const origin = window.location.origin;
  const images = remote ? `data: ${origin} https: http:` : `data: ${origin}`;
  const policy = `default-src 'none'; img-src ${images}; style-src 'unsafe-inline'; font-src data:${remote ? " https:" : ""}; media-src 'none'; form-action 'none'`;
  const base = `body{margin:0;padding:2px 2px 12px;font:14px/1.55 Arial,Helvetica,sans-serif;color:${dark ? "#e4e4e7" : "#1f2937"};background:transparent;word-wrap:break-word;overflow-wrap:anywhere}
img{max-width:100%;height:auto}table{max-width:100%}pre{white-space:pre-wrap}blockquote{margin:0 0 0 .8ex;border-left:2px solid #c7d2fe;padding-left:1ex}a{color:${dark ? "#5eead4" : "#0f6b5c"}}`;
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}"><base target="_blank"><style>${base}</style></head><body>${html}</body></html>`;
}

export function textAsHtml(text: string): string {
  const escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return `<div style="white-space:pre-wrap">${escaped.replace(/(https?:\/\/[^\s<>"]+)/g, '<a href="$1">$1</a>')}</div>`;
}

export function unsubscribeLink(header: string): string | null {
  const match = header.match(/<(https:\/\/[^>]+)>/);
  return match ? match[1] : null;
}

// --- the reader's own preferences (this browser) ---------------------------------------------------------
export interface Prefs {
  undoSeconds: number;
  markReadOnOpen: boolean;
  trustedSenders: string[];
}

const PREFS_KEY = "r365-mail-prefs";
const DEFAULTS: Prefs = { undoSeconds: 10, markReadOnOpen: true, trustedSenders: [] };

export function loadPrefs(): Prefs {
  try {
    return { ...DEFAULTS, ...(JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}") as Partial<Prefs>) };
  } catch {
    return DEFAULTS;
  }
}

export function savePrefs(prefs: Prefs): void {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* private window: preferences last for this visit */
  }
}

export const ROLE_LABEL: Record<string, string> = { inbox: "Inbox", sent: "Sent", drafts: "Drafts", archive: "Archive", junk: "Junk", trash: "Trash" };
