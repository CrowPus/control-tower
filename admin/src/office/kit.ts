/* The HQ: the floor plan, who sits where, the people's looks, shared materials and the
   textures (oak, marble, carpet, terrazzo, stone, art) — all made here, nothing downloaded. Units are metres; x runs
   west → east, z north → south (the entrance is in the south). */
import * as THREE from "three";

export type V2 = [number, number];

// --- the building ---------------------------------------------------------------------------------------------------
export const BUILDING = { x0: -32, x1: 32, z0: -18, z1: 18, height: 4.4 };
// The two halls everyone walks along, and the crossings between them (gaps between the neighbourhoods).
export const HALL_N = -5.5;
export const HALL_S = 6.5;
export const CROSSINGS = [-16, 0, 16];

export type Rect = { x0: number; x1: number; z0: number; z1: number };
export type Area = Rect & { key: string; name: string; color: string; floor: "oak" | "marble" | "carpet" | "terrazzo" | "dark"; closed?: "solid" | "glass"; door?: V2 };

// Departments that work together sit together; the owner's suite, Nia's office and the back office are closed rooms.
export const AREAS: Area[] = [
  { key: "suite", name: "Founder's suite", color: "#d4a853", x0: -32, x1: -18, z0: -18, z1: -7, floor: "carpet", closed: "solid", door: [-22, -7] },
  { key: "nia", name: "Nia · Chief of staff", color: "#22d3ee", x0: -18, x1: -10, z0: -18, z1: -7, floor: "dark", closed: "solid", door: [-14, -7] },
  { key: "board", name: "Boardroom", color: "#94a3b8", x0: -10, x1: 4, z0: -18, z1: -7, floor: "carpet", closed: "glass", door: [-3, -7] },
  { key: "library", name: "Library", color: "#a16207", x0: 4, x1: 18, z0: -18, z1: -7, floor: "oak" },
  { key: "focus", name: "Outside agents", color: "#a855f7", x0: 18, x1: 32, z0: -18, z1: -7, floor: "oak" },
  { key: "growth", name: "West floor", color: "#14b8a6", x0: -30, x1: -17, z0: -4.2, z1: 5.2, floor: "oak" },
  { key: "success", name: "Centre floor", color: "#ec4899", x0: -15, x1: -2, z0: -4.2, z1: 5.2, floor: "oak" },
  { key: "marketing", name: "Studio floor", color: "#f97316", x0: 2, x1: 15, z0: -4.2, z1: 5.2, floor: "oak" },
  { key: "product", name: "East floor", color: "#3b82f6", x0: 17, x1: 30, z0: -4.2, z1: 5.2, floor: "oak" },
  { key: "back", name: "Quiet room", color: "#ef4444", x0: -30, x1: -12, z0: 8, z1: 18, floor: "oak", closed: "glass", door: [-21, 8] },
  { key: "reception", name: "Reception", color: "#2dd4bf", x0: -8, x1: 8, z0: 8, z1: 18, floor: "marble" },
  { key: "cafe", name: "Café", color: "#eab308", x0: 12, x1: 30, z0: 8, z1: 18, floor: "terrazzo" },
];
export const AREA = Object.fromEntries(AREAS.map((a) => [a.key, a])) as Record<string, Area>;

// The owner names the departments; each gets a colour from its name (the same name, the same colour, every time).
const PALETTE = ["#14b8a6", "#0ea5e9", "#ec4899", "#f97316", "#3b82f6", "#ef4444", "#eab308", "#22c55e", "#f43f5e", "#6366f1"];
const FIXED_COLOR: Record<string, string> = { Leadership: "#8b5cf6", Outside: "#a855f7" };
export function departmentColor(name: string): string {
  if (FIXED_COLOR[name]) return FIXED_COLOR[name];
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}
/** Leadership first, outside agents last, the owner's departments in between as they come. */
export function departmentOrder(names: string[]): string[] {
  const own = [...new Set(names)].filter((n) => n !== "Leadership" && n !== "Outside");
  return ["Leadership", ...own, "Outside"];
}
export const MODE_COLOR: Record<string, string> = { working: "#22c55e", idle: "#38bdf8", waiting: "#f59e0b", off: "#475569" };

// --- where everyone sits, and how they walk there ------------------------------------------------------------------
// An open neighbourhood is entered straight from its hall; a closed room through its door.
export type Zone = { key: string; open: boolean; laneZ: number; door: V2; inside: V2; lane: V2 };
export type Desk = {
  key: string; area: string; zone: Zone; seat: V2; facing: number; desk: V2; wide?: boolean;
  guest: V2; guestFacing: number; owner: V2; ownerFacing: number;
  via: V2[]; // inside a closed room, the way round the desk to the chair
};

const OPEN_N: Zone = { key: "open-n", open: true, laneZ: HALL_N, door: [0, 0], inside: [0, 0], lane: [0, HALL_N] };
const OPEN_S: Zone = { key: "open-s", open: true, laneZ: HALL_S, door: [0, 0], inside: [0, 0], lane: [0, HALL_S] };
function closedZone(key: string, door: V2, inward: number, hallZ: number): Zone {
  return { key, open: false, laneZ: hallZ, door, inside: [door[0], door[1] + inward * 1.3], lane: [door[0], hallZ] };
}
const SUITE_ZONE = closedZone("suite", AREA.suite.door!, -1, HALL_N);
const NIA_ZONE = closedZone("nia", AREA.nia.door!, -1, HALL_N);
const BACK_ZONE = closedZone("back", AREA.back.door!, 1, HALL_S);

export const face = (from: V2, to: V2) => Math.atan2(to[0] - from[0], to[1] - from[1]);

function openDesk(key: string, area: string, x: number, row: "n" | "s"): Desk {
  // North row faces south (the desk in front of them, the hall behind); south row faces north.
  const s = row === "n" ? 1 : -1;
  const seat: V2 = [x, row === "n" ? -2.8 : 2.8];
  const guest: V2 = [x + 0.95, seat[1] + s * 0.3];
  const owner: V2 = [x - 0.95, seat[1] + s * 0.3];
  return { key, area, zone: row === "n" ? OPEN_N : OPEN_S, seat, facing: row === "n" ? 0 : Math.PI, desk: [x, seat[1] + s * 0.85],
           guest, guestFacing: face(guest, seat), owner, ownerFacing: face(owner, seat), via: [] };
}

function roomDesk(key: string, area: string, zone: Zone, seat: V2, facing: number, opts: { via?: V2[]; wide?: boolean; front?: number } = {}): Desk {
  const dir: V2 = [Math.sin(facing), Math.cos(facing)];
  const front = opts.front ?? 2.3; // where a visitor stands, in front of the desk
  const guest: V2 = opts.via?.length ? [seat[0] + dir[0] * front + 0.7, seat[1] + dir[1] * front] : [seat[0] + 0.95, seat[1] + dir[1] * 0.3];
  const owner: V2 = opts.via?.length ? [seat[0] + dir[0] * front - 0.7, seat[1] + dir[1] * front] : [seat[0] - 0.95, seat[1] + dir[1] * 0.3];
  return { key, area, zone, seat, facing, desk: [seat[0] + dir[0] * 0.9, seat[1] + dir[1] * 0.9], wide: opts.wide, guest, guestFacing: face(guest, seat),
           owner, ownerFacing: face(owner, seat), via: opts.via ?? [] };
}

export const OWNER_DESK = roomDesk("owner", "suite", SUITE_ZONE, [-25, -14.8], 0, { wide: true, via: [[-22.9, -9.6], [-22.9, -14.8]] });

export const DESKS: Record<string, Desk> = Object.fromEntries([
  roomDesk("nia", "nia", NIA_ZONE, [-14, -14.4], 0, { via: [[-11.6, -9.6], [-11.6, -14.4]] }),
].map((d) => [d.key, d]));
// The open desks the owner's agents take, in this order: both rows of each floor, west to east, then the quiet room.
export const SPARE_DESKS: Desk[] = [
  ...["growth", "success", "marketing", "product"].flatMap((area) => {
    const a = AREA[area];
    const xs: number[] = [];
    for (let x = a.x0 + 4; x < a.x1 - 1; x += 4) xs.push(x);
    return (["n", "s"] as const).flatMap((row) => xs.map((x) => openDesk(`spare-${area}-${row}-${x}`, area, x, row)));
  }),
  roomDesk("spare-back", "back", BACK_ZONE, [-21, 11.6], 0),
];

// The outside agents' wing: ten desks in two rows of five, numbered 1–10 as the owner gives them (more outside agents take open desks).
export const OUTSIDE_DESKS: Desk[] = [-15.6, -11].flatMap((z, row) => [19.6, 22.4, 25.2, 28, 30.8].map((x, i) =>
  roomDesk(`outside-${row * 5 + i + 1}`, "focus", OPEN_N, [x, z], 0, { front: 1.6 })));

export function desksFor(keys: string[], outside: Record<string, number | null> = {}): Map<string, Desk> {
  const out = new Map<string, Desk>();
  let spare = 0;
  for (const key of keys) {
    const n = outside[key];
    const d = n ? { ...OUTSIDE_DESKS[n - 1], key } : DESKS[key] ?? (spare < SPARE_DESKS.length ? { ...SPARE_DESKS[spare++], key } : undefined);
    if (d) out.set(key, d);
  }
  return out;
}

// The hall nearest to a point (to turn round when a new place is given on the way).
export function hallZone(x: number, z: number): Zone {
  const laneZ = Math.abs(z - HALL_N) < Math.abs(z - HALL_S) ? HALL_N : HALL_S;
  return { key: "hall", open: true, laneZ, door: [x, laneZ], inside: [x, laneZ], lane: [x, laneZ] };
}

/** The walk from where someone is (in `from`, having come in by `fromVia`) to `target` in `to` (reached by `toVia`). */
export function route(from: Zone, fromVia: V2[], cur: V2, to: Zone, toVia: V2[], target: V2): V2[] {
  if (from.key === to.key && !from.open) return [...[...fromVia].reverse(), ...toVia, target];
  const leave: V2[] = from.open ? [[cur[0], from.laneZ]] : [...[...fromVia].reverse(), from.inside, from.door, from.lane];
  const a = leave[leave.length - 1];
  const b: V2 = to.open ? [(toVia[0] ?? target)[0], to.laneZ] : to.lane;
  const middle: V2[] = [];
  if (a[1] !== b[1]) {
    const c = CROSSINGS.reduce((best, x) => (Math.abs(a[0] - x) + Math.abs(b[0] - x) < Math.abs(a[0] - best) + Math.abs(b[0] - best) ? x : best), CROSSINGS[0]);
    middle.push([c, a[1]], [c, b[1]]);
  }
  const enter: V2[] = to.open ? [b, ...toVia, target] : [to.lane, to.door, to.inside, ...toVia, target];
  const out: V2[] = [cur];
  for (const p of [...leave, ...middle, ...enter]) {
    const prev = out[out.length - 1];
    if (Math.hypot(prev[0] - p[0], prev[1] - p[1]) > 0.05) out.push(p);
  }
  return out.slice(1);
}

// --- the people ------------------------------------------------------------------------------------------------------
export type Hair = "short" | "side" | "buzz" | "bun" | "long" | "ponytail" | "curly" | "afro" | "bald" | "hijab";
export type Look = {
  skin: string; hair: string; hairStyle: Hair; beard?: boolean; build?: number; // build: 0.94 slim … 1.06 broad
  top: string; jacket?: string; trousers: string; shoes: string; tie?: string;
  extras?: ("glasses" | "headset" | "beret" | "cap" | "hood")[]; hologram?: boolean;
};

const SKIN = { fair: "#f2d0b4", light: "#e3b28e", olive: "#c99a6e", tan: "#b07a52", brown: "#8a5636", deep: "#5e3a24" };
const HAIR = { black: "#17110d", dark: "#2e1d12", brown: "#5b3820", auburn: "#7a3523", blond: "#c79b5a", grey: "#9ca3af", silver: "#d1d5db" };

// Nia looks the same in every install; every other agent gets a look made from its key (always the same for the same
// agent), wearing its department's colour.
export const LOOKS: Record<string, Look> = {
  nia: { skin: "#a5f3fc", hair: "#cffafe", hairStyle: "long", top: "#22d3ee", trousers: "#0e7490", shoes: "#155e75", hologram: true },
};
export const OWNER_LOOK: Look = { skin: SKIN.brown, hair: HAIR.black, hairStyle: "short", beard: true, build: 1.04, top: "#ffffff", jacket: "#0f1d3a", tie: "#b8892b",
  trousers: "#0f1d3a", shoes: "#1a1a1a" };

const STYLES: Hair[] = ["short", "long", "bun", "ponytail", "curly", "afro", "buzz", "side"];
const EXTRAS: Look["extras"][] = [[], [], ["glasses"], ["headset"], [], ["glasses"], ["cap"], []];

export function lookOf(key: string, department: string): Look {
  if (LOOKS[key]) return LOOKS[key];
  let h = 7;
  for (const c of key) h = (h * 33 + c.charCodeAt(0)) >>> 0;
  const pick = <T,>(list: T[], salt: number): T => list[(h >>> salt) % list.length];
  const skins = Object.values(SKIN);
  const hairs = Object.values(HAIR).filter((x) => x !== HAIR.silver);
  return { skin: pick(skins, 1), hair: pick(hairs, 4), hairStyle: pick(STYLES, 7), beard: (h >>> 10) % 4 === 0, top: departmentColor(department),
           trousers: pick(["#1f2937", "#111827", "#334155", "#292524"], 13), shoes: pick(["#111111", "#f8fafc", "#3f2a1d"], 16), extras: pick(EXTRAS, 19) };
}

// --- materials (shared: the office is cheap to draw) ------------------------------------------------------------------
const cache = new Map<string, THREE.Material>();
export function mat(color: string, opts: { rough?: number; metal?: number; emissive?: string; glow?: number; opacity?: number; map?: THREE.Texture; side?: THREE.Side } = {}): THREE.Material {
  const key = JSON.stringify([color, { ...opts, map: opts.map?.uuid }]);
  let m = cache.get(key);
  if (!m) {
    m = new THREE.MeshStandardMaterial({
      color, roughness: opts.rough ?? 0.7, metalness: opts.metal ?? 0, map: opts.map ?? null, side: opts.side ?? THREE.FrontSide,
      emissive: opts.emissive ?? "#000000", emissiveIntensity: opts.glow ?? 1,
      transparent: opts.opacity !== undefined, opacity: opts.opacity ?? 1, depthWrite: opts.opacity === undefined,
    });
    cache.set(key, m);
  }
  return m;
}

// --- textures, drawn once ---------------------------------------------------------------------------------------------
function canvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return [c, c.getContext("2d")!];
}

function texture(c: HTMLCanvasElement, repeat: [number, number] = [1, 1]): THREE.CanvasTexture {
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(...repeat);
  t.anisotropy = 8;
  return t;
}

let seed = 11;
const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };

const made = new Map<string, THREE.CanvasTexture>();
function once(key: string, make: () => THREE.CanvasTexture): THREE.CanvasTexture {
  let t = made.get(key);
  if (!t) { t = make(); made.set(key, t); }
  return t;
}

/** Oak planks, staggered, with grain (one tile = 2 × 2 m). */
export const oak = () => once("oak", () => {
  const [c, x] = canvas(1024, 1024);
  const tones = ["#b98a5a", "#c39262", "#ad7f50", "#c99c6b", "#b4865a", "#bd8f60", "#a77a4d"];
  const rows = 8, h = 1024 / rows;
  for (let r = 0; r < rows; r++) {
    let px = -((r * 389) % 512);
    while (px < 1024) {
      const len = 300 + Math.floor(rnd() * 360);
      x.fillStyle = tones[Math.floor(rnd() * tones.length)];
      x.fillRect(px, r * h, len, h);
      x.globalAlpha = 0.07;
      for (let g = 0; g < 14; g++) {
        x.strokeStyle = rnd() > 0.5 ? "#3b2410" : "#fff3dc";
        x.lineWidth = 0.6 + rnd() * 1.4;
        x.beginPath();
        const y0 = r * h + rnd() * h;
        x.moveTo(px, y0);
        x.bezierCurveTo(px + len * 0.3, y0 + (rnd() - 0.5) * 10, px + len * 0.6, y0 + (rnd() - 0.5) * 10, px + len, y0 + (rnd() - 0.5) * 6);
        x.stroke();
      }
      x.globalAlpha = 1;
      x.fillStyle = "rgba(40,24,10,0.55)";
      x.fillRect(px, r * h, 2, h);
      px += len;
    }
    x.fillStyle = "rgba(40,24,10,0.5)";
    x.fillRect(0, r * h, 1024, 2);
  }
  return texture(c);
});

/** White marble with grey veins (one tile = 4 × 4 m). */
export const marble = () => once("marble", () => {
  const [c, x] = canvas(1024, 1024);
  x.fillStyle = "#f1efea";
  x.fillRect(0, 0, 1024, 1024);
  for (let i = 0; i < 26; i++) {
    x.strokeStyle = `rgba(110,110,120,${0.08 + rnd() * 0.22})`;
    x.lineWidth = 0.6 + rnd() * 2.2;
    x.beginPath();
    let px = rnd() * 1024, py = rnd() * 1024;
    x.moveTo(px, py);
    for (let k = 0; k < 8; k++) { px += (rnd() - 0.3) * 180; py += (rnd() - 0.5) * 160; x.lineTo(px, py); }
    x.stroke();
  }
  x.strokeStyle = "rgba(160,150,140,0.35)";
  x.lineWidth = 2;
  for (let g = 0; g <= 1024; g += 256) { x.beginPath(); x.moveTo(g, 0); x.lineTo(g, 1024); x.moveTo(0, g); x.lineTo(1024, g); x.stroke(); }
  return texture(c);
});

/** Wool carpet with a fine weave and a border-like tone. */
export const carpet = (color: string) => once(`carpet${color}`, () => {
  const [c, x] = canvas(256, 256);
  x.fillStyle = color;
  x.fillRect(0, 0, 256, 256);
  for (let i = 0; i < 9000; i++) { x.fillStyle = rnd() > 0.5 ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.07)"; x.fillRect(rnd() * 256, rnd() * 256, 1, 1); }
  return texture(c);
});

export const terrazzo = () => once("terrazzo", () => {
  const [c, x] = canvas(512, 512);
  x.fillStyle = "#ece7df";
  x.fillRect(0, 0, 512, 512);
  const chips = ["#c2410c", "#334155", "#a8a29e", "#0f766e", "#e5e7eb", "#78716c"];
  for (let i = 0; i < 1400; i++) {
    x.fillStyle = chips[Math.floor(rnd() * chips.length)];
    x.beginPath();
    x.ellipse(rnd() * 512, rnd() * 512, 1 + rnd() * 5, 1 + rnd() * 3, rnd() * 3, 0, Math.PI * 2);
    x.fill();
  }
  return texture(c);
});

/** Dressed sandstone blocks for the outside walls. */
export const stone = () => once("stone", () => {
  const [c, x] = canvas(512, 512);
  x.fillStyle = "#e7dcc8";
  x.fillRect(0, 0, 512, 512);
  for (let row = 0; row < 8; row++) for (let col = -1; col < 5; col++) {
    const w = 128, h = 64, ox = (row % 2) * 64;
    const shade = 210 + Math.floor(rnd() * 30);
    x.fillStyle = `rgb(${shade + 20},${shade + 8},${shade - 12})`;
    x.fillRect(col * w + ox + 2, row * h + 2, w - 4, h - 4);
  }
  return texture(c);
});

/** Abstract art for the walls (a few colour schemes). */
export const painting = (n: number) => once(`art${n}`, () => {
  const [c, x] = canvas(384, 256);
  const schemes = [["#0f172a", "#d4a853", "#f8fafc", "#14b8a6"], ["#f5f0e6", "#c2410c", "#1e3a8a", "#eab308"], ["#1c1917", "#ec4899", "#22d3ee", "#f8fafc"]];
  const p = schemes[n % schemes.length];
  x.fillStyle = p[0];
  x.fillRect(0, 0, 384, 256);
  for (let i = 0; i < 9; i++) {
    x.fillStyle = p[1 + (i % 3)];
    x.globalAlpha = 0.55 + rnd() * 0.4;
    if (i % 2) { x.beginPath(); x.arc(rnd() * 384, rnd() * 256, 20 + rnd() * 70, 0, Math.PI * 2); x.fill(); }
    else x.fillRect(rnd() * 330, rnd() * 220, 30 + rnd() * 120, 8 + rnd() * 60);
  }
  x.globalAlpha = 1;
  return texture(c);
});

// --- text as textures (names, speech bubbles, signs) ---------------------------------------------------------------
const texts = new Map<string, { texture: THREE.CanvasTexture; aspect: number }>();
export function textTexture(text: string, o: { fg?: string; bg?: string; border?: string; size?: number; weight?: number; pad?: number; radius?: number; font?: string; spacing?: number } = {}) {
  const key = JSON.stringify([text, o]);
  const hit = texts.get(key);
  if (hit) return hit;
  const size = (o.size ?? 28) * 2, pad = (o.pad ?? 14) * 2;
  const [c, ctx] = canvas(16, 16);
  const font = `${o.weight ?? 600} ${size}px ${o.font ?? "Inter, system-ui, sans-serif"}`;
  ctx.font = font;
  if (o.spacing) (ctx as unknown as { letterSpacing: string }).letterSpacing = `${o.spacing}px`;
  const w = Math.ceil(ctx.measureText(text).width) + pad * 2, h = Math.ceil(size * 1.45) + pad;
  c.width = w;
  c.height = h;
  ctx.font = font;
  if (o.spacing) (ctx as unknown as { letterSpacing: string }).letterSpacing = `${o.spacing}px`;
  if (o.bg) {
    ctx.fillStyle = o.bg;
    ctx.beginPath();
    ctx.roundRect(2, 2, w - 4, h - 4, Math.min((o.radius ?? 12) * 2, h / 2 - 2));
    ctx.fill();
    if (o.border) { ctx.lineWidth = 3; ctx.strokeStyle = o.border; ctx.stroke(); }
  }
  ctx.fillStyle = o.fg ?? "#f8fafc";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, w / 2, h / 2 + size * 0.04);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  const out = { texture: t, aspect: w / h };
  if (texts.size > 400) texts.clear();
  texts.set(key, out);
  return out;
}

// The screens: lines of "code" that scroll while an agent works, a calm logo when it does not.
export function screenTexture(kind: "working" | "idle"): THREE.CanvasTexture {
  const [c, ctx] = canvas(256, 512);
  ctx.fillStyle = kind === "working" ? "#04131a" : "#0b1220";
  ctx.fillRect(0, 0, 256, 512);
  if (kind === "working") {
    const colors = ["#2dd4bf", "#38bdf8", "#a78bfa", "#facc15", "#4ade80", "#e2e8f0"];
    for (let y = 8; y < 512; y += 14) {
      let x = 10 + ((y * 7) % 5) * 12;
      for (let i = 0; i < 2 + ((y * 13) % 5); i++) {
        const len = 14 + ((y * (i + 3)) % 50);
        ctx.fillStyle = colors[(y + i * 3) % colors.length];
        ctx.fillRect(x, y, len, 6);
        x += len + 8;
        if (x > 236) break;
      }
    }
  } else {
    ctx.fillStyle = "#2dd4bf";
    ctx.font = "700 40px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("R365", 128, 270);
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(1, kind === "working" ? 0.5 : 1);
  return t;
}

// The city outside the windows: towers with lit windows (more of them lit at night).
export function towerTexture(night: boolean): THREE.CanvasTexture {
  const [c, ctx] = canvas(64, 128);
  ctx.fillStyle = night ? "#0b1220" : "#94a3b8";
  ctx.fillRect(0, 0, 64, 128);
  for (let y = 4; y < 124; y += 8) for (let x = 4; x < 60; x += 8) {
    const lit = ((x * 31 + y * 17) % 7) < (night ? 3 : 1);
    ctx.fillStyle = lit ? (night ? "#fde68a" : "#e0f2fe") : night ? "#111827" : "#64748b";
    ctx.fillRect(x, y, 5, 5);
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

// Local daylight (the viewer's clock): 0 at night, 1 at noon.
export function daylight(now: Date): number {
  const hour = Number(new Intl.DateTimeFormat("en-GB", { hour: "numeric", hour12: false }).format(now)) + now.getMinutes() / 60;
  return Math.max(0, Math.min(1, Math.sin(((hour - 6) / 14) * Math.PI)));
}

export function humanEvent(type: string): string {
  const text = type.replace(/[._]/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
