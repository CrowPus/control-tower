/* The Allow bar (docs: Nia): while Nia works on a page, anything that would save or change
   something waits until you press Allow. Every change request of the admin passes through
   `ask` (api.ts); only requests that follow one of Nia's actions are held. */

const ACTING_MS = 6000;
let actingUntil = 0;

export type Pending = { id: number; method: string; path: string };
type Listener = (pending: Pending[]) => void;

let next = 1;
let waiting: { pending: Pending; resolve: (allowed: boolean) => void }[] = [];
const listeners = new Set<Listener>();

function publish() {
  const list = waiting.map((w) => w.pending);
  listeners.forEach((l) => l(list));
}

/** Nia just clicked, typed or chose something on the page. */
export function markNiaActing(): void {
  actingUntil = Date.now() + ACTING_MS;
}

export function niaIsActing(): boolean {
  return Date.now() < actingUntil;
}

/** Called for every change request: resolves at once for yours, waits for Allow for Nia's. */
export function ask(method: string, url: string): Promise<boolean> {
  if (["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase()) || !niaIsActing()) return Promise.resolve(true);
  const path = new URL(url, location.origin).pathname;
  return new Promise((resolve) => {
    waiting.push({ pending: { id: next++, method: method.toUpperCase(), path }, resolve });
    publish();
  });
}

export function answer(id: number, allowed: boolean): void {
  const found = waiting.find((w) => w.pending.id === id);
  waiting = waiting.filter((w) => w.pending.id !== id);
  // Your Allow is yours: what follows is no longer held.
  if (allowed) actingUntil = 0;
  found?.resolve(allowed);
  publish();
}

export function waitingNow(): Pending[] {
  return waiting.map((w) => w.pending);
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
