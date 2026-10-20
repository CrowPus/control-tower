/** Numbers for Insights: one place that knows how each unit reads. */

export type Unit = "count" | "eur_cents" | "usd" | "ms" | "hours" | "percent" | "share";
export type Good = "up" | "down" | "neutral";

const whole = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 });
const oneDecimal = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 1 });
const euro = new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const euroCents = new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function fmt(value: number | null | undefined, unit: string = "count", short = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  switch (unit as Unit) {
    case "eur_cents": {
      const eur = value / 100;
      return Math.abs(eur) >= 1000 || short ? euro.format(eur) : euroCents.format(eur);
    }
    case "usd":
      if (value !== 0 && Math.abs(value) < 0.1) return `$${value.toFixed(3)}`;
      return `$${value >= 100 ? whole.format(value) : short && value >= 10 ? whole.format(value) : short ? oneDecimal.format(value) : value.toFixed(2)}`;
    case "ms":
      return value >= 1000 ? `${oneDecimal.format(value / 1000)} s` : `${whole.format(value)} ms`;
    case "hours":
      return value >= 48 ? `${oneDecimal.format(value / 24)} d` : `${oneDecimal.format(value)} h`;
    case "percent":
      return `${oneDecimal.format(value)}%`;
    case "share":
      return `${oneDecimal.format(value * 100)}%`;
    default:
      return short || Math.abs(value) >= 10_000 ? compact.format(value) : Number.isInteger(value) ? whole.format(value) : oneDecimal.format(value);
  }
}

/** Change against the previous period: the text, and whether it is good news. */
export function delta(value: number | null | undefined, previous: number | null | undefined, good: string): { text: string; tone: "good" | "bad" | "flat"; direction: "up" | "down" | "flat" } | null {
  if (value === null || value === undefined || previous === null || previous === undefined) return null;
  if (value === previous) return { text: "no change", tone: "flat", direction: "flat" };
  const direction = value > previous ? "up" : "down";
  const text = previous === 0 ? "new" : `${value > previous ? "+" : "−"}${oneDecimal.format(Math.abs(((value - previous) / Math.abs(previous)) * 100))}%`;
  const tone = good === "neutral" ? "flat" : (direction === "up") === (good === "up") ? "good" : "bad";
  return { text, tone, direction };
}

const shortDay = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const longDay = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const hour = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
const dayHour = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** Axis and tooltip labels: day keys ("2026-10-04") or instants ("2026-10-04T13:00:00+00:00"). */
export function axisLabel(key: string, hourly = false): string {
  return hourly ? hour.format(new Date(key)) : shortDay.format(new Date(`${key.slice(0, 10)}T12:00:00`));
}
export function tooltipLabel(key: string, hourly = false): string {
  return hourly ? dayHour.format(new Date(key)) : longDay.format(new Date(`${key.slice(0, 10)}T12:00:00`));
}

/** Readable names for the machine names the snapshot stores. */
const NAMES: Record<string, string> = {
  admin: "Admin",
  gateway: "Agent gateway",
  approved: "Cleared",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  executed: "Carried out",
};

export function nameOf(key: string): string {
  return NAMES[key] ?? key.replace(/[_.]/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** Fixed identity colours: the same entity always wears the same colour. */
export const IDENTITY: Record<string, string> = {
  admin: "var(--viz-1)",
  gateway: "var(--viz-2)",
};
export const colorOf = (key: string): string => IDENTITY[key] ?? "var(--viz-1)";
