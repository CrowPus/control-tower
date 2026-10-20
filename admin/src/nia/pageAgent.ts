/* Nia works the admin the way you do (docs: Nia): she reads the open page — its text and every
   button, link, field, checkbox and list with its current value — and clicks, types, ticks and
   chooses on it, in your browser with your sign-in. Looking needs no confirmation; the moment
   one of her actions would save or change something, the admin asks you first (changeGate.ts). */
import { markNiaActing } from "./changeGate";

type Control = { ref: number; kind: string; label: string; value?: string; checked?: boolean; options?: string[]; disabled?: boolean };

const SELECTOR = "button, a[href], input, select, textarea, [role='button'], [role='tab'], [role='checkbox'], summary";
let refs = new Map<number, HTMLElement>();

function visible(el: HTMLElement): boolean {
  const box = el.getBoundingClientRect();
  return box.width > 0 && box.height > 0 && getComputedStyle(el).visibility !== "hidden";
}

function clean(text: string | null | undefined, max = 120): string {
  return (text ?? "").replace(/\s+/g, " ").trim().slice(0, max);
}

/** The words that name a control: its label, aria-label, the field it sits in, or its text. */
function labelOf(el: HTMLElement): string {
  const own = el.getAttribute("aria-label") || el.getAttribute("title");
  if (own) return clean(own);
  if (el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement) {
    const label = el.labels?.[0]?.innerText || el.closest("label")?.innerText;
    if (label) return clean(label);
    // The admin's Field: a label element followed by the control in the same block.
    const field = el.closest("div")?.parentElement?.querySelector("label");
    if (field && field.contains(el) === false && field.parentElement?.contains(el)) return clean(field.innerText);
    return clean(el.getAttribute("placeholder") || el.name || "");
  }
  return clean(el.innerText || el.textContent);
}

function roots(): HTMLElement[] {
  const found: HTMLElement[] = [];
  const main = document.querySelector("main");
  if (main instanceof HTMLElement) found.push(main);
  document.querySelectorAll<HTMLElement>("[role='dialog'], dialog[open]").forEach((d) => found.push(d));
  return found;
}

/** What Nia reads: the page's text and its controls, each with a number she uses to act. */
export function snapshot(): { page: string; title: string; text: string; controls: Control[] } {
  refs = new Map();
  const controls: Control[] = [];
  let n = 0;
  for (const root of roots()) {
    for (const el of Array.from(root.querySelectorAll<HTMLElement>(SELECTOR))) {
      if (!visible(el) || el.closest("[data-nia-skip]")) continue;
      n += 1;
      refs.set(n, el);
      const control: Control = { ref: n, kind: el.tagName.toLowerCase(), label: labelOf(el) };
      if (el instanceof HTMLInputElement) {
        control.kind = el.type === "checkbox" || el.type === "radio" ? el.type : `input:${el.type}`;
        if (el.type === "checkbox" || el.type === "radio") control.checked = el.checked;
        else if (el.type !== "password") control.value = el.value;
      } else if (el instanceof HTMLTextAreaElement) {
        control.value = el.value.slice(0, 2000);
      } else if (el instanceof HTMLSelectElement) {
        control.value = el.selectedOptions[0]?.text ?? el.value;
        control.options = Array.from(el.options).map((o) => o.text).slice(0, 40);
      } else if (el.getAttribute("role") === "checkbox" || el.getAttribute("role") === "switch") {
        control.kind = "switch";
        control.checked = el.getAttribute("aria-checked") === "true";
      } else if (el instanceof HTMLAnchorElement) {
        control.kind = "link";
      }
      if ((el as HTMLButtonElement).disabled) control.disabled = true;
      controls.push(control);
      if (n >= 250) break;
    }
  }
  const text = roots().map((r) => r.innerText).join("\n\n").replace(/\n{3,}/g, "\n\n").slice(0, 14000);
  return { page: location.pathname + location.search, title: document.title, text, controls };
}

function setValue(el: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) {
  // React listens to the native setter, so set it the way typing would.
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(el, value);
  el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
  if (!(el instanceof HTMLSelectElement)) el.dispatchEvent(new Event("change", { bubbles: true }));
}

function show(el: HTMLElement) {
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("nia-highlight");
  setTimeout(() => el.classList.remove("nia-highlight"), 1500);
}

/** One action of Nia's on the open page. Returns what happened and the page after it. */
export async function act(action: string, ref: number, value = ""): Promise<Record<string, unknown>> {
  const el = refs.get(ref);
  if (!el || !document.contains(el)) return { error: "That control is not on the page any more. Read the screen again." };
  if ((el as HTMLButtonElement).disabled) return { error: "That control is greyed out (not available now)." };
  markNiaActing();
  show(el);
  if (action === "click") {
    el.click();
  } else if (action === "fill") {
    if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) return { error: "fill is for text fields." };
    el.focus();
    setValue(el, value);
  } else if (action === "select") {
    if (!(el instanceof HTMLSelectElement)) return { error: "select is for drop-down lists." };
    const option = Array.from(el.options).find((o) => o.text.trim().toLowerCase() === value.trim().toLowerCase() || o.value === value);
    if (!option) return { error: `No option "${value}". Options: ${Array.from(el.options).map((o) => o.text).join(", ")}` };
    setValue(el, option.value);
  } else if (action === "check" || action === "uncheck") {
    const on = action === "check";
    const checked = el instanceof HTMLInputElement ? el.checked : el.getAttribute("aria-checked") === "true";
    if (checked !== on) el.click();
  } else {
    return { error: "action is click, fill, select, check or uncheck." };
  }
  await new Promise((r) => setTimeout(r, 900));
  return { done: action, ...snapshot() };
}
