/* The HQ, the building: a classic headquarters — stone walls with tall arched windows, a
   columned portico, marble halls and oak floors, steel beams and pendant lights. Closed rooms for the founder, Nia, the
   boardroom and the back office; open neighbourhoods where the agents who work together sit together; a library, focus
   rooms, the reception and the café. Walls that face the camera turn to glass so you always see in. Lit by your local
   time of day with reflections from the room around it. */
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, type ReactNode } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { Character, type Place, type Pose } from "./Character";
import {
  AREA, AREAS, BUILDING, MODE_COLOR, OWNER_DESK, OWNER_LOOK, carpet, daylight, humanEvent, lookOf, marble, mat, oak, painting, screenTexture, stone, terrazzo,
  textTexture, towerTexture, type Area, type Desk, type V2,
} from "./kit";

export type OfficeAgent = { key: string; name: string; department: string; mode: string; doing: string; visiting: { to: string; about: string } | null };
export type Fly = { target: V2; distance?: number } | null;

type Props = {
  agents: OfficeAgent[]; desks: Map<string, Desk>; ownerName: string; ownerWith: string | null;
  selected: string | null; onSelect: (key: string | null) => void; labels: boolean; fly: Fly; now: Date;
};

const box = new THREE.BoxGeometry(1, 1, 1);
const cyl = new THREE.CylinderGeometry(1, 1, 1, 28);
const ball = new THREE.SphereGeometry(1, 20, 14);
const leaf = new THREE.SphereGeometry(1, 10, 8);

type P3 = [number, number, number];
function B({ p, s, m, r = 0, shadow = true }: { p: P3; s: P3; m: THREE.Material; r?: number; shadow?: boolean }) {
  return <mesh geometry={box} material={m} position={p} scale={s} rotation={[0, r, 0]} castShadow={shadow} receiveShadow />;
}
function C({ p, r, h, m, shadow = true }: { p: P3; r: number; h: number; m: THREE.Material; shadow?: boolean }) {
  return <mesh geometry={cyl} material={m} position={p} scale={[r, h, r]} castShadow={shadow} receiveShadow />;
}

const M = {
  walnut: () => mat("#5b3a24", { rough: 0.45 }), oakTop: () => mat("#c9a273", { rough: 0.5 }), steel: () => mat("#1f2937", { rough: 0.35, metal: 0.8 }),
  brass: () => mat("#c9a14a", { rough: 0.3, metal: 0.9 }), plaster: () => mat("#f4f1ea", { rough: 0.9 }), leather: () => mat("#3b2416", { rough: 0.5 }),
  fabric: (c: string) => mat(c, { rough: 0.95 }), white: () => mat("#f8fafc", { rough: 0.4 }), black: () => mat("#0b0f19", { rough: 0.3, metal: 0.4 }),
};

// --- text on walls and hanging signs -------------------------------------------------------------------------------
function Sign({ text, p, h, color, bg, r = 0, glow = false, weight = 700, spacing }: { text: string; p: P3; h: number; color: string; bg?: string; r?: number; glow?: boolean; weight?: number; spacing?: number }) {
  const { texture, aspect } = textTexture(text, { fg: color, bg, size: 44, weight, pad: 12, spacing });
  return (
    <mesh position={p} rotation={[0, r, 0]}>
      <planeGeometry args={[h * aspect, h]} />
      <meshBasicMaterial map={texture} transparent toneMapped={!glow} side={THREE.DoubleSide} depthWrite={false} />
    </mesh>
  );
}

function HangingSign({ area }: { area: Area }) {
  const x = (area.x0 + area.x1) / 2, z = (area.z0 + area.z1) / 2;
  return (
    <group position={[x, 3.15, z]}>
      {[-1.6, 1.6].map((dx) => <C key={dx} p={[dx, 0.55, 0]} r={0.006} h={1.1} m={M.steel()} shadow={false} />)}
      <B p={[0, 0, 0]} s={[4.2, 0.5, 0.05]} m={mat("#0f172a", { rough: 0.4 })} shadow={false} />
      <B p={[0, -0.27, 0]} s={[4.2, 0.04, 0.06]} m={mat(area.color, { emissive: area.color, glow: 0.8 })} shadow={false} />
      <Sign text={area.name} p={[0, 0.02, 0.03]} h={0.36} color="#f8fafc" />
      <Sign text={area.name} p={[0, 0.02, -0.03]} h={0.36} color="#f8fafc" r={Math.PI} />
    </group>
  );
}

// Beams, hanging signs and the like stand in the way up close: they are shown only from high above.
function Overhead({ children, above = 13 }: { children: ReactNode; above?: number }) {
  const g = useRef<THREE.Group>(null);
  const { camera } = useThree();
  useFrame(() => { if (g.current) g.current.visible = camera.position.y > above; });
  return <group ref={g}>{children}</group>;
}

// --- walls that turn to glass when they stand between you and the room ----------------------------------------------
type Side = { normal: V2; at: number }; // the wall's outward normal (x or z) and its line

function useFade(side: Side, materials: THREE.Material[], inside = false) {
  const { camera } = useThree();
  useFrame(() => {
    const [nx, nz] = side.normal;
    const pos = nx !== 0 ? camera.position.x : camera.position.z;
    const out = (nx || nz) > 0 ? pos > side.at : pos < side.at;
    const want = out ? (inside ? 0.18 : 0.12) : 1;
    for (const m of materials) {
      const mm = m as THREE.MeshStandardMaterial & { userData: { base?: number } };
      const base = mm.userData.base ?? (mm.userData.base = mm.opacity);
      const target = Math.min(base, want);
      mm.opacity += (target - mm.opacity) * 0.15;
      mm.transparent = true;
      mm.depthWrite = mm.opacity > 0.95;
    }
  });
}

/** An outside wall of dressed stone with tall arched windows (and the entrance, on the south side). */
function OuterWall({ from, to, side, door }: { from: V2; to: V2; side: Side; door?: [number, number] }) {
  const mats = useMemo(() => {
    const t = stone().clone();
    t.needsUpdate = true;
    t.repeat.set(1, 1);
    return {
      stone: new THREE.MeshStandardMaterial({ map: t, color: "#ffffff", roughness: 0.85 }),
      frame: new THREE.MeshStandardMaterial({ color: "#1f2937", roughness: 0.35, metalness: 0.7 }),
      glass: new THREE.MeshPhysicalMaterial({ color: "#cfe8ff", roughness: 0.05, metalness: 0, transmission: 0, transparent: true, opacity: 0.22 }),
      trim: new THREE.MeshStandardMaterial({ color: "#efe6d4", roughness: 0.7 }),
    };
  }, []);
  useFade(side, [mats.stone, mats.frame, mats.trim]);
  useFade(side, [mats.glass]);
  const horizontal = from[1] === to[1];
  const length = Math.abs(horizontal ? to[0] - from[0] : to[1] - from[1]);
  const start = horizontal ? Math.min(from[0], to[0]) : Math.min(from[1], to[1]);
  const H = BUILDING.height, bay = 4, win = 1.9;
  const parts: ReactNode[] = [];
  for (let i = 0; i < Math.round(length / bay); i++) {
    const c = start + i * bay + bay / 2;
    const isDoor = door && c > door[0] && c < door[1];
    const at = (u: number, y: number, w: number, h: number, m: THREE.Material, d = 0.5, off = 0) => {
      const key = `${i}-${y}-${w}-${off}`;
      return horizontal
        ? <B key={key} p={[u, y, from[1] + off]} s={[w, h, d]} m={m} shadow={m !== mats.glass} />
        : <B key={key} p={[from[0] + off, y, u]} s={[d, h, w]} m={m} shadow={m !== mats.glass} />;
    };
    const pier = (bay - (isDoor ? 3.6 : win)) / 2;
    parts.push(at(c - bay / 2 + pier / 2, H / 2, pier, H, mats.stone), at(c + bay / 2 - pier / 2, H / 2, pier, H, mats.stone));
    if (isDoor) {
      parts.push(at(c, 3.5, 3.6, 0.2, mats.frame, 0.2), at(c, H - 0.35, 3.6, 0.7, mats.stone), at(c, 1.75, 3.6, 3.3, mats.glass, 0.04));
      parts.push(at(c, 1.75, 0.06, 3.3, mats.frame, 0.08));
    } else {
      parts.push(at(c, 0.3, win, 0.6, mats.stone), at(c, 0.63, win + 0.2, 0.06, mats.trim, 0.6)); // sill
      parts.push(at(c, 1.95, win, 2.6, mats.glass, 0.04)); // the window
      parts.push(at(c, 1.95, 0.05, 2.6, mats.frame, 0.08), at(c, 2.2, win, 0.04, mats.frame, 0.08)); // mullion, transom
      parts.push(at(c, 3.92, win, 0.96, mats.stone)); // over the window: the arch is set in it
    }
  }
  // the arches: a half circle of glass over each window, framed
  const arches = [];
  for (let i = 0; i < Math.round(length / bay); i++) {
    const c = start + i * bay + bay / 2;
    if (door && c > door[0] && c < door[1]) continue;
    const pos: P3 = horizontal ? [c, 3.25, from[1] + side.normal[1] * 0.26] : [from[0] + side.normal[0] * 0.26, 3.25, c];
    const rot = horizontal ? (side.normal[1] > 0 ? 0 : Math.PI) : side.normal[0] > 0 ? Math.PI / 2 : -Math.PI / 2;
    arches.push(
      <group key={`a${i}`} position={pos} rotation={[0, rot, 0]}>
        <mesh material={mats.glass}><circleGeometry args={[win / 2, 32, 0, Math.PI]} /></mesh>
        <mesh material={mats.frame}><ringGeometry args={[win / 2 - 0.05, win / 2, 32, 1, 0, Math.PI]} /></mesh>
      </group>,
    );
  }
  return (
    <group>
      {parts}
      {arches}
      {/* the cornice along the top */}
      {horizontal
        ? <B p={[start + length / 2, H + 0.08, from[1]]} s={[length + 0.6, 0.16, 0.75]} m={mats.trim} />
        : <B p={[from[0], H + 0.08, start + length / 2]} s={[0.75, 0.16, length + 0.6]} m={mats.trim} />}
    </group>
  );
}

/** A wall inside: panelled and solid (closed rooms) or wood below and steel-framed glass above (the classic studio window). */
function InnerWall({ from, to, kind, side, door }: { from: V2; to: V2; kind: "solid" | "glass"; side: Side; door?: number }) {
  const mats = useMemo(() => ({
    panel: new THREE.MeshStandardMaterial({ color: "#5b3a24", roughness: 0.5 }),
    plaster: new THREE.MeshStandardMaterial({ color: "#efe9df", roughness: 0.9 }),
    steel: new THREE.MeshStandardMaterial({ color: "#111827", roughness: 0.35, metalness: 0.8 }),
    glass: new THREE.MeshPhysicalMaterial({ color: "#e0f2fe", roughness: 0.05, transparent: true, opacity: 0.18 }),
  }), []);
  useFade(side, [mats.panel, mats.plaster, mats.steel], true);
  const horizontal = from[1] === to[1];
  const a = horizontal ? Math.min(from[0], to[0]) : Math.min(from[1], to[1]);
  const b = horizontal ? Math.max(from[0], to[0]) : Math.max(from[1], to[1]);
  const H = 3.0;
  const spans: [number, number][] = door !== undefined ? [[a, door - 0.6], [door + 0.6, b]] : [[a, b]];
  const seg = (u0: number, u1: number, y: number, h: number, m: THREE.Material, d: number) => {
    const u = (u0 + u1) / 2, w = u1 - u0;
    return horizontal ? <B key={`${u0}-${y}-${d}`} p={[u, y, from[1]]} s={[w, h, d]} m={m} shadow={m !== mats.glass} />
      : <B key={`${u0}-${y}-${d}`} p={[from[0], y, u]} s={[d, h, w]} m={m} shadow={m !== mats.glass} />;
  };
  const out: ReactNode[] = [];
  for (const [u0, u1] of spans) {
    if (u1 - u0 < 0.05) continue;
    out.push(seg(u0, u1, 0.45, 0.9, mats.panel, 0.14));
    if (kind === "solid") {
      out.push(seg(u0, u1, 1.95, 2.1, mats.plaster, 0.12), seg(u0, u1, 0.93, 0.06, mats.steel, 0.16));
    } else {
      out.push(seg(u0, u1, 1.95, 2.1, mats.glass, 0.03), seg(u0, u1, 0.93, 0.06, mats.steel, 0.08), seg(u0, u1, 2.25, 0.04, mats.steel, 0.06));
      for (let u = u0; u <= u1 + 0.01; u += Math.max(0.9, (u1 - u0) / Math.max(1, Math.round((u1 - u0) / 0.9)))) out.push(seg(u - 0.025, u + 0.025, 1.95, 2.1, mats.steel, 0.06));
    }
    out.push(seg(u0, u1, H - 0.02, 0.06, mats.steel, 0.16));
  }
  if (door !== undefined) {
    // the door frame and an open door leaf
    out.push(seg(door - 0.62, door - 0.56, 1.2, 2.4, mats.steel, 0.16), seg(door + 0.56, door + 0.62, 1.2, 2.4, mats.steel, 0.16), seg(door - 0.6, door + 0.6, 2.62, 0.76, kind === "solid" ? mats.plaster : mats.glass, 0.1));
  }
  return <group>{out}</group>;
}

// --- furniture -----------------------------------------------------------------------------------------------------
function Plant({ p, s = 1, kind = 0 }: { p: P3; s?: number; kind?: number }) {
  const leaves = useMemo(() => Array.from({ length: 16 }, (_, i) => ({ a: i * 2.4, y: 0.7 + (i / 16) * 1.0, r: 0.18 + (i % 3) * 0.06 })), []);
  return (
    <group position={p} scale={s}>
      <mesh geometry={cyl} material={kind ? mat("#1f2937", { rough: 0.4 }) : mat("#e7e1d6", { rough: 0.5 })} position={[0, 0.25, 0]} scale={[0.24, 0.5, 0.24]} castShadow />
      <C p={[0, 0.85, 0]} r={0.02} h={1.1} m={mat("#5b4636")} />
      {leaves.map((l, i) => (
        <mesh key={i} geometry={leaf} material={mat(i % 2 ? "#2f6b2f" : "#3f8f3a", { rough: 0.7 })} position={[Math.cos(l.a) * l.r, l.y, Math.sin(l.a) * l.r]}
          rotation={[0.4, l.a, 0.6]} scale={[0.13, 0.03, 0.2]} castShadow />
      ))}
    </group>
  );
}

function Pendant({ p, on, color = "#fde68a" }: { p: P3; on: boolean; color?: string }) {
  return (
    <group position={p}>
      <C p={[0, (BUILDING.height - p[1]) / 2, 0]} r={0.005} h={BUILDING.height - p[1]} m={M.black()} shadow={false} />
      <mesh position={[0, 0, 0]} material={M.brass()} scale={[0.32, 0.22, 0.32]}><sphereGeometry args={[1, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2]} /></mesh>
      <mesh geometry={ball} position={[0, -0.02, 0]} scale={0.07} material={on ? mat(color, { emissive: color, glow: 3 }) : mat("#e5e7eb")} />
    </group>
  );
}

function Chair({ p, r, color = "#1f2937" }: { p: P3; r: number; color?: string }) {
  const seat = M.fabric(color), frame = M.steel();
  return (
    <group position={p} rotation={[0, r, 0]}>
      <B p={[0, 0.46, 0]} s={[0.5, 0.07, 0.48]} m={seat} />
      <B p={[0, 0.85, -0.25]} s={[0.46, 0.62, 0.05]} m={seat} r={0} />
      {[-0.24, 0.24].map((x) => <B key={x} p={[x, 0.62, -0.02]} s={[0.04, 0.04, 0.32]} m={frame} />)}
      <C p={[0, 0.25, 0]} r={0.025} h={0.4} m={frame} />
      {[0, 1, 2, 3, 4].map((i) => <B key={i} p={[Math.sin(i * 1.2566) * 0.18, 0.05, Math.cos(i * 1.2566) * 0.18]} s={[0.04, 0.03, 0.36]} m={frame} r={i * 1.2566} />)}
    </group>
  );
}

const SCREENS = { working: screenTexture("working"), idle: screenTexture("idle") };
const SCREEN_MAT = {
  working: new THREE.MeshBasicMaterial({ map: SCREENS.working, toneMapped: false, color: new THREE.Color(1.5, 1.5, 1.5) }),
  idle: new THREE.MeshBasicMaterial({ map: SCREENS.idle, toneMapped: false }),
  off: new THREE.MeshStandardMaterial({ color: "#020617", roughness: 0.15, metalness: 0.6 }),
};

/** A workstation: oak top on a steel frame, a monitor on an arm, keyboard, mug, notes, a lamp and the status light. */
function Station({ desk, mode, selected, onSelect, name, wide = false }: { desk: Desk; mode: string; selected: boolean; onSelect: () => void; name: string; wide?: boolean }) {
  const lamp = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (lamp.current) (lamp.current.material as THREE.MeshBasicMaterial).color.set(MODE_COLOR[mode] ?? "#475569").multiplyScalar(mode === "working" ? 1.2 + Math.sin(clock.elapsedTime * 4) * 0.6 : 1);
  });
  const W = wide ? 2.4 : 1.6, D = wide ? 1.0 : 0.8;
  const screen = mode === "working" ? SCREEN_MAT.working : mode === "off" ? SCREEN_MAT.off : SCREEN_MAT.idle;
  const top = wide ? M.walnut() : M.oakTop();
  return (
    <group>
      <group position={[desk.desk[0], 0, desk.desk[1]]} rotation={[0, desk.facing, 0]}>
        <B p={[0, 0.74, 0]} s={[W, 0.045, D]} m={top} />
        {wide ? <B p={[0, 0.37, 0.05]} s={[W - 0.1, 0.72, D - 0.2]} m={M.walnut()} /> : [-1, 1].map((sx) => (
          <group key={sx}>
            <B p={[sx * (W / 2 - 0.06), 0.36, 0]} s={[0.05, 0.72, 0.05]} m={M.steel()} />
            <B p={[sx * (W / 2 - 0.06), 0.03, 0]} s={[0.06, 0.04, D - 0.1]} m={M.steel()} />
          </group>
        ))}
        {/* monitor on its arm, facing the chair */}
        <group position={[0, 0.765, -0.18]}>
          <B p={[0, 0.01, 0]} s={[0.22, 0.02, 0.16]} m={M.steel()} />
          <B p={[0, 0.2, 0.02]} s={[0.035, 0.36, 0.035]} m={M.steel()} />
          <B p={[0, 0.42, 0]} s={[wide ? 0.95 : 0.72, wide ? 0.5 : 0.42, 0.035]} m={M.black()} />
          <mesh material={screen} position={[0, 0.42, -0.019]} rotation={[0, Math.PI, 0]}><planeGeometry args={[wide ? 0.91 : 0.68, wide ? 0.46 : 0.38]} /></mesh>
        </group>
        <B p={[0, 0.77, -0.48 + (wide ? -0.1 : 0) + 0.12]} s={[0.44, 0.018, 0.14]} m={mat("#e5e7eb", { rough: 0.5 })} />
        <B p={[0.33, 0.77, -0.3]} s={[0.06, 0.02, 0.1]} m={mat("#e5e7eb", { rough: 0.5 })} />
        <C p={[-0.55, 0.81, -0.2]} r={0.04} h={0.1} m={mat("#f8fafc", { rough: 0.3 })} />
        <B p={[-0.3, 0.77, -0.25]} s={[0.21, 0.015, 0.29]} m={mat("#fef3c7")} r={0.2} />
        <group position={[W / 2 - 0.2, 0.765, 0.1]}>
          <C p={[0, 0.01, 0]} r={0.07} h={0.02} m={M.brass()} />
          <C p={[0, 0.2, 0]} r={0.008} h={0.4} m={M.brass()} />
          <mesh geometry={ball} position={[0, 0.42, 0]} scale={0.06} material={mode === "off" ? mat("#334155") : mat("#fde68a", { emissive: "#fbbf24", glow: 2.4 })} />
        </group>
        {/* the status light at the desk's edge, seen from the hall */}
        <mesh ref={lamp} position={[0, 0.745, D / 2 + 0.005]}><boxGeometry args={[W * 0.6, 0.02, 0.012]} /><meshBasicMaterial toneMapped={false} /></mesh>
        <Sign text={name} p={[0, 0.84, D / 2 - 0.05]} h={0.075} color="#0f172a" bg="rgba(248,250,252,0.95)" weight={600} />
      </group>
      <Chair p={[desk.seat[0], 0, desk.seat[1]]} r={desk.facing} color={wide ? "#3b2416" : "#1f2937"} />
      {/* the place you click */}
      <mesh position={[desk.desk[0], 0.02, (desk.desk[1] + desk.seat[1]) / 2]} rotation={[-Math.PI / 2, 0, 0]} onClick={(e) => { e.stopPropagation(); onSelect(); }}>
        <planeGeometry args={[W + 1.2, 2.6]} />
        <meshBasicMaterial transparent opacity={selected ? 0.18 : 0} color="#ffffff" depthWrite={false} />
      </mesh>
      {selected && (
        <mesh position={[desk.seat[0], 0.025, desk.seat[1]]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[1.45, 1.5, 64]} />
          <meshBasicMaterial color="#e2e8f0" transparent opacity={0.85} />
        </mesh>
      )}
    </group>
  );
}

function Sofa({ p, r = 0, color = "#1e3a5f", w = 2.2 }: { p: P3; r?: number; color?: string; w?: number }) {
  const f = M.fabric(color);
  return (
    <group position={p} rotation={[0, r, 0]}>
      <B p={[0, 0.25, 0]} s={[w, 0.3, 0.9]} m={f} />
      <B p={[0, 0.42, 0.02]} s={[w - 0.3, 0.14, 0.78]} m={f} />
      <B p={[0, 0.65, -0.37]} s={[w, 0.6, 0.18]} m={f} />
      {[-1, 1].map((s) => <B key={s} p={[s * (w / 2 - 0.08), 0.48, 0]} s={[0.16, 0.36, 0.9]} m={f} />)}
      {[-1, 1].map((s) => <B key={`l${s}`} p={[s * (w / 2 - 0.12), 0.05, 0.3]} s={[0.05, 0.1, 0.05]} m={M.brass()} />)}
    </group>
  );
}

function Rug({ p, w, d, color }: { p: P3; w: number; d: number; color: string }) {
  return (
    <mesh position={p} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
      <planeGeometry args={[w, d]} />
      <meshStandardMaterial map={carpet(color)} roughness={1} />
    </mesh>
  );
}

function Shelf({ p, r = 0, w = 2.4, h = 2.4 }: { p: P3; r?: number; w?: number; h?: number }) {
  const books = useMemo(() => {
    const out: { x: number; y: number; w: number; h: number; c: string }[] = [];
    const palette = ["#7f1d1d", "#1e3a8a", "#14532d", "#78350f", "#f5f5f4", "#0f172a", "#a16207", "#4c1d95"];
    let k = 0;
    for (let row = 0; row < Math.floor(h / 0.42); row++) {
      let x = -w / 2 + 0.06;
      while (x < w / 2 - 0.12) {
        const bw = 0.035 + ((k * 7) % 5) * 0.012, bh = 0.24 + ((k * 3) % 4) * 0.025;
        if ((k * 11) % 13 !== 0) out.push({ x: x + bw / 2, y: 0.08 + row * 0.42 + bh / 2, w: bw, h: bh, c: palette[(k * 5) % palette.length] });
        x += bw + 0.004;
        k++;
      }
    }
    return out;
  }, [w, h]);
  return (
    <group position={p} rotation={[0, r, 0]}>
      <B p={[0, h / 2, -0.17]} s={[w, h, 0.03]} m={M.walnut()} />
      {[-1, 1].map((s) => <B key={s} p={[s * w / 2, h / 2, 0]} s={[0.04, h, 0.36]} m={M.walnut()} />)}
      {Array.from({ length: Math.floor(h / 0.42) + 1 }, (_, i) => <B key={i} p={[0, 0.04 + i * 0.42, 0]} s={[w, 0.03, 0.36]} m={M.walnut()} />)}
      {books.map((b, i) => <mesh key={i} geometry={box} material={mat(b.c, { rough: 0.8 })} position={[b.x, b.y, 0]} scale={[b.w, b.h, 0.26]} />)}
    </group>
  );
}

function Art({ p, r = 0, n, w = 1.6 }: { p: P3; r?: number; n: number; w?: number }) {
  return (
    <group position={p} rotation={[0, r, 0]}>
      <B p={[0, 0, -0.02]} s={[w + 0.1, w * 0.66 + 0.1, 0.04]} m={M.brass()} shadow={false} />
      <mesh position={[0, 0, 0.002]}><planeGeometry args={[w, w * 0.66]} /><meshStandardMaterial map={painting(n)} roughness={0.8} /></mesh>
    </group>
  );
}

// --- the rooms -------------------------------------------------------------------------------------------------------
function Floors() {
  const tex = useMemo(() => {
    const mk = (t: THREE.Texture, w: number, d: number, tile: number) => { const c = t.clone(); c.needsUpdate = true; c.repeat.set(w / tile, d / tile); return c; };
    const W = BUILDING.x1 - BUILDING.x0, D = BUILDING.z1 - BUILDING.z0;
    return { base: mk(marble(), W, D, 4), areas: AREAS.map((a) => {
      const w = a.x1 - a.x0, d = a.z1 - a.z0;
      return a.floor === "oak" ? mk(oak(), w, d, 2.2) : a.floor === "marble" ? mk(marble(), w, d, 4) : a.floor === "terrazzo" ? mk(terrazzo(), w, d, 3) : a.floor === "carpet" ? mk(carpet(a.key === "suite" ? "#2b2118" : "#1f2a3a"), w, d, 2) : null;
    }) };
  }, []);
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, 0]} receiveShadow>
        <planeGeometry args={[BUILDING.x1 - BUILDING.x0, BUILDING.z1 - BUILDING.z0]} />
        <meshStandardMaterial map={tex.base} color="#e9e4da" roughness={0.38} metalness={0.02} />
      </mesh>
      {AREAS.map((a, i) => (
        <mesh key={a.key} rotation={[-Math.PI / 2, 0, 0]} position={[(a.x0 + a.x1) / 2, 0.004, (a.z0 + a.z1) / 2]} receiveShadow>
          <planeGeometry args={[a.x1 - a.x0, a.z1 - a.z0]} />
          {a.floor === "dark" ? <meshStandardMaterial color="#0b1220" roughness={0.18} metalness={0.4} />
            : <meshStandardMaterial map={tex.areas[i]} color={a.floor === "marble" ? "#e9e4da" : "#ffffff"} roughness={a.floor === "oak" ? 0.55 : a.floor === "carpet" ? 1 : 0.38} />}
        </mesh>
      ))}
      {/* brass strips where the floors meet the halls */}
      {AREAS.filter((a) => !a.closed && a.floor === "oak").map((a) => (
        <group key={`s${a.key}`}>
          {[a.z0, a.z1].map((z) => <B key={z} p={[(a.x0 + a.x1) / 2, 0.008, z]} s={[a.x1 - a.x0, 0.006, 0.04]} m={M.brass()} shadow={false} />)}
        </group>
      ))}
    </group>
  );
}

function Structure({ night }: { night: boolean }) {
  const cols: V2[] = [];
  for (const x of [-24, -8, 8, 24]) for (const z of [-6.7, 7.7]) cols.push([x, z]);
  const column = mat("#f1ebe0", { rough: 0.6 });
  return (
    <group>
      {cols.map(([x, z]) => (
        <group key={`${x}${z}`} position={[x, 0, z]}>
          <B p={[0, 0.12, 0]} s={[0.7, 0.24, 0.7]} m={column} />
          <C p={[0, 2.1, 0]} r={0.26} h={3.8} m={column} />
          {[0.6, 1.2, 1.8, 2.4, 3.0].map((y) => <mesh key={y} position={[0, y, 0]} rotation={[Math.PI / 2, 0, 0]} material={mat("#e6dccb")}><torusGeometry args={[0.262, 0.012, 6, 28]} /></mesh>)}
          <B p={[0, 4.08, 0]} s={[0.72, 0.18, 0.72]} m={column} />
        </group>
      ))}
      {/* steel beams of the roof (the roof itself is left off so you can look in) */}
      <Overhead>
        {[-6.7, 7.7].map((z) => <B key={z} p={[0, BUILDING.height + 0.02, z]} s={[BUILDING.x1 - BUILDING.x0, 0.16, 0.12]} m={mat("#e4ded2", { rough: 0.6 })} shadow={false} />)}
        {[-24, -8, 8, 24].map((x) => <B key={x} p={[x, BUILDING.height + 0.02, 0]} s={[0.12, 0.16, BUILDING.z1 - BUILDING.z0]} m={mat("#e4ded2", { rough: 0.6 })} shadow={false} />)}
      </Overhead>
      {/* pendants over the halls */}
      {[-28, -20, -12, -4, 4, 12, 20, 28].map((x) => <Pendant key={`n${x}`} p={[x, 2.9, -5.5]} on={night} />)}
      {[-28, -20, -12, 12, 20, 28].map((x) => <Pendant key={`s${x}`} p={[x, 2.9, 6.5]} on={night} />)}
    </group>
  );
}

function Rooms({ night }: { night: boolean }) {
  const s = AREA.suite, n = AREA.nia, bd = AREA.board, back = AREA.back;
  return (
    <group>
      {/* the closed rooms' walls (the outside walls are the building's) */}
      <InnerWall from={[s.x0, s.z1]} to={[s.x1, s.z1]} kind="solid" side={{ normal: [0, 1], at: s.z1 }} door={s.door![0]} />
      <InnerWall from={[s.x1, s.z0]} to={[s.x1, s.z1]} kind="solid" side={{ normal: [1, 0], at: s.x1 }} />
      <InnerWall from={[n.x0, n.z1]} to={[n.x1, n.z1]} kind="solid" side={{ normal: [0, 1], at: n.z1 }} door={n.door![0]} />
      <InnerWall from={[n.x1, n.z0]} to={[n.x1, n.z1]} kind="glass" side={{ normal: [1, 0], at: n.x1 }} />
      <InnerWall from={[bd.x0, bd.z1]} to={[bd.x1, bd.z1]} kind="glass" side={{ normal: [0, 1], at: bd.z1 }} door={bd.door![0]} />
      <InnerWall from={[bd.x1, bd.z0]} to={[bd.x1, bd.z1]} kind="glass" side={{ normal: [1, 0], at: bd.x1 }} />
      <InnerWall from={[back.x0, back.z0]} to={[back.x1, back.z0]} kind="glass" side={{ normal: [0, -1], at: back.z0 }} door={back.door![0]} />
      <InnerWall from={[back.x1, back.z0]} to={[back.x1, back.z1]} kind="glass" side={{ normal: [1, 0], at: back.x1 }} />
      {[s, n, bd, back].map((a) => {
        const door = a.door!;
        const south = door[1] === a.z1 || door[1] === a.z0;
        return <Sign key={a.key} text={a.name} p={[door[0], 2.78, door[1] + (a.z0 === door[1] ? -0.09 : 0.09)]} h={0.22} color="#f8fafc" bg="rgba(15,23,42,0.88)" r={south && a.z0 === door[1] ? Math.PI : 0} weight={600} />;
      })}

      {/* the founder's suite: a walnut desk facing the door, a seating corner, books, art and the city behind */}
      <Rug p={[-25, 0.012, -13.6]} w={4.6} d={3.4} color="#6b4a2b" />
      <Shelf p={[-25, 0, -17.6]} w={4.4} h={2.6} />
      <Art p={[-31.7, 1.9, -12.5]} r={Math.PI / 2} n={0} w={2.2} />
      <Rug p={[-29.4, 0.012, -9.6]} w={3.6} d={3.2} color="#334155" />
      <Sofa p={[-29.4, 0, -11.0]} r={0} color="#3b2416" w={2.4} />
      <Sofa p={[-31.1, 0, -9.4]} r={Math.PI / 2} color="#3b2416" w={1.8} />
      <B p={[-29.4, 0.22, -9.5]} s={[1.3, 0.06, 0.8]} m={mat("#1c1917", { rough: 0.15, metal: 0.3 })} />
      {[-1, 1].map((s2) => <B key={s2} p={[-29.4 + s2 * 0.55, 0.1, -9.5]} s={[0.05, 0.2, 0.7]} m={M.brass()} />)}
      <Plant p={[-31.2, 0, -17.2]} s={1.25} kind={1} />
      <Plant p={[-19, 0, -17.2]} s={1.2} kind={1} />
      <Pendant p={[-25, 2.5, -13.6]} on={night} />
      <Pendant p={[-29.4, 2.6, -9.6]} on={night} />

      {/* Nia's office: dark glass, light lines and a holographic table */}
      {[n.x0 + 0.05, n.x1 - 0.05].map((x) => <B key={x} p={[x, 0.05, (n.z0 + n.z1) / 2]} s={[0.04, 0.02, n.z1 - n.z0 - 0.4]} m={mat("#22d3ee", { emissive: "#22d3ee", glow: 2 })} shadow={false} />)}
      <B p={[-14, 1.9, -17.75]} s={[6.5, 2.4, 0.08]} m={mat("#020617", { rough: 0.2, metal: 0.6 })} />
      <Sign text="NIA · EPLIV" p={[-14, 2.7, -17.69]} h={0.32} color="#67e8f9" glow spacing={6} />
      {[-16.2, -14, -11.8].map((x, i) => (
        <mesh key={x} position={[x, 1.7, -17.68]}>
          <planeGeometry args={[1.9, 1.05]} />
          <meshBasicMaterial map={i === 1 ? SCREENS.working : SCREENS.idle} toneMapped={false} color={new THREE.Color(0.8, 1.6, 1.8)} />
        </mesh>
      ))}
      <group position={[-14, 0, -10.2]}>
        <C p={[0, 0.4, 0]} r={0.55} h={0.8} m={mat("#0f172a", { rough: 0.2, metal: 0.6 })} />
        <mesh position={[0, 0.82, 0]} rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[0.3, 0.52, 48]} /><meshBasicMaterial color="#22d3ee" toneMapped={false} transparent opacity={0.8} /></mesh>
        <mesh position={[0, 1.15, 0]} geometry={ball} scale={0.18}><meshBasicMaterial color="#67e8f9" toneMapped={false} transparent opacity={0.45} wireframe /></mesh>
      </group>

      {/* the boardroom */}
      <B p={[-3, 0.76, -12.6]} s={[7.2, 0.07, 1.8]} m={M.walnut()} />
      {[-5.6, -0.4].map((x) => <B key={x} p={[x, 0.37, -12.6]} s={[0.5, 0.74, 1.2]} m={M.steel()} />)}
      {[-6, -4.5, -3, -1.5, 0].map((x) => [-1, 1].map((sd) => <Chair key={`${x}${sd}`} p={[x, 0, -12.6 + sd * 1.3]} r={sd > 0 ? Math.PI : 0} color="#3b2416" />))}
      <B p={[-3, 1.9, -17.7]} s={[4.2, 2.3, 0.08]} m={M.black()} />
      <Sign text="This week" p={[-3, 2.3, -17.64]} h={0.3} color="#5eead4" glow />
      <Sign text="Runs · Proposals · Cleared" p={[-3, 1.7, -17.64]} h={0.18} color="#e2e8f0" glow weight={500} />
      <Pendant p={[-4.5, 2.4, -12.6]} on={night} />
      <Pendant p={[-1.5, 2.4, -12.6]} on={night} />
      <Plant p={[3.3, 0, -17.2]} s={1.1} />

      {/* the back office: finance, trust and budget behind glass */}
      <Shelf p={[-21, 0, 17.6]} r={Math.PI} w={5} h={2.2} />
      <Art p={[-29.7, 1.8, 13]} r={Math.PI / 2} n={2} w={1.8} />
      <Plant p={[-29.2, 0, 17.2]} s={1.1} />
      <Plant p={[-12.8, 0, 17.2]} s={1.1} />
      {[-26, -21, -16].map((x) => <Pendant key={x} p={[x, 2.4, 11.8]} on={night} />)}
    </group>
  );
}

function Commons({ night }: { night: boolean }) {
  return (
    <group>
      {/* reception: a curved desk, the logo wall, a waiting corner */}
      <group position={[0, 0, 11.2]}>
        <mesh position={[0, 0.55, 0]} material={M.walnut()} castShadow><cylinderGeometry args={[2.2, 2.2, 1.1, 48, 1, true, -Math.PI * 0.38, Math.PI * 0.76]} /></mesh>
        <mesh position={[0, 1.12, 0]} rotation={[-Math.PI / 2, 0, 0]} material={mat("#f1efea", { rough: 0.2 })}><ringGeometry args={[1.95, 2.3, 48, 1, -Math.PI * 0.88, Math.PI * 0.76]} /></mesh>
      </group>
      <group position={[0, 0, 8.5]}>
        <B p={[0, 1.9, 0]} s={[7, 3.8, 0.25]} m={M.walnut()} />
        {Array.from({ length: 23 }, (_, i) => <B key={i} p={[-3.3 + i * 0.3, 1.9, 0.14]} s={[0.12, 3.8, 0.05]} m={mat("#7a5232", { rough: 0.5 })} />)}
        <Sign text="HQ" p={[0, 2.35, 0.2]} h={0.95} color="#5eead4" glow weight={800} />
        <Sign text="Real opportunities. Legal travel." p={[0, 1.65, 0.2]} h={0.26} color="#f8fafc" weight={500} />
      </group>
      <Rug p={[5, 0.012, 14.5]} w={3.2} d={2.4} color="#475569" />
      <Sofa p={[5, 0, 15.3]} r={Math.PI} color="#0f766e" w={2.4} />
      <Plant p={[-7.2, 0, 17.2]} s={1.4} kind={1} />
      <Plant p={[7.2, 0, 17.2]} s={1.4} kind={1} />
      <Plant p={[-7.2, 0, 9]} s={1.2} />
      <Plant p={[7.2, 0, 9]} s={1.2} />
      {/* the portico at the entrance */}
      {[-4.5, -1.5, 1.5, 4.5].map((x) => (
        <group key={x} position={[x, 0, 19.4]}>
          <B p={[0, 0.12, 0]} s={[0.75, 0.24, 0.75]} m={mat("#f1ebe0", { rough: 0.6 })} />
          <C p={[0, 2.2, 0]} r={0.3} h={4.0} m={mat("#f1ebe0", { rough: 0.6 })} />
          <B p={[0, 4.28, 0]} s={[0.8, 0.2, 0.8]} m={mat("#f1ebe0", { rough: 0.6 })} />
        </group>
      ))}
      <B p={[0, 4.55, 19.2]} s={[10.5, 0.4, 1.6]} m={mat("#efe6d4", { rough: 0.7 })} />
      <Sign text="EPLIV" p={[0, 4.55, 20.01]} h={0.32} color="#1f2937" spacing={10} />
      <B p={[0, 0.06, 19.6]} s={[11, 0.12, 3.2]} m={mat("#d6cfc2", { rough: 0.7 })} />

      {/* the café */}
      <group position={[21, 0, 16.6]}>
        <B p={[0, 0.5, 0]} s={[8, 1.0, 0.7]} m={mat("#1c1917", { rough: 0.4 })} />
        <B p={[0, 1.03, 0]} s={[8.1, 0.06, 0.8]} m={mat("#f5f5f4", { rough: 0.2 })} />
        <B p={[-2.5, 1.35, -0.1]} s={[0.5, 0.6, 0.45]} m={mat("#a8a29e", { metal: 0.85, rough: 0.25 })} />
        <B p={[2.8, 1.0, 0.95]} s={[0.8, 2.0, 0.6]} m={mat("#e7e5e4", { metal: 0.6, rough: 0.3 })} />
        <Sign text="☕ Café" p={[0, 2.4, 0.3]} h={0.42} color="#fde68a" bg="rgba(28,25,23,0.92)" />
      </group>
      {[[15.5, 11.2], [19.5, 11.2], [23.5, 11.2], [27.5, 11.2], [17.5, 13.8], [21.5, 13.8], [25.5, 13.8]].map(([x, z]) => (
        <group key={`${x}${z}`} position={[x, 0, z]}>
          <C p={[0, 0.74, 0]} r={0.45} h={0.04} m={mat("#f8fafc", { rough: 0.3 })} />
          <C p={[0, 0.37, 0]} r={0.04} h={0.72} m={M.steel()} />
          {[0, 1].map((i) => <Chair key={i} p={[i ? 0.75 : -0.75, 0, 0]} r={i ? -Math.PI / 2 : Math.PI / 2} color={i ? "#c2410c" : "#0f766e"} />)}
        </group>
      ))}
      {[16, 22, 28].map((x) => <Pendant key={x} p={[x, 2.6, 12.4]} on={night} color="#fdba74" />)}
      <Plant p={[29.4, 0, 9]} s={1.3} />

      {/* the library: shelves, armchairs, a long reading table */}
      <Shelf p={[7.5, 0, -17.6]} w={5} h={2.8} />
      <Shelf p={[13.5, 0, -17.6]} w={5} h={2.8} />
      <Rug p={[11, 0.012, -12]} w={6} d={4} color="#7c2d12" />
      <B p={[11, 0.75, -12.4]} s={[4, 0.07, 1.2]} m={M.walnut()} />
      {[9.4, 12.6].map((x) => <B key={x} p={[x, 0.37, -12.4]} s={[0.1, 0.74, 1.0]} m={M.walnut()} />)}
      {[9.8, 11, 12.2].map((x) => [-1, 1].map((sd) => <Chair key={`${x}${sd}`} p={[x, 0, -12.4 + sd * 0.95]} r={sd > 0 ? Math.PI : 0} color="#7c2d12" />))}
      <Sofa p={[6, 0, -9.2]} r={Math.PI / 2} color="#365314" w={1.2} />
      <Sofa p={[16, 0, -9.2]} r={-Math.PI / 2} color="#365314" w={1.2} />
      <Pendant p={[10, 2.5, -12.4]} on={night} />
      <Pendant p={[12, 2.5, -12.4]} on={night} />

      {/* the outside agents' wing: a sign; their desks come with them (Station) */}
      <B p={[25, 2.2, -17.9]} s={[5, 0.5, 0.06]} m={mat("#a855f7", { rough: 0.4 })} />
    </group>
  );
}

function Neighbourhoods({ night }: { night: boolean }) {
  return (
    <group>
      {["growth", "success", "marketing", "product"].map((k) => {
        const a = AREA[k];
        return (
          <group key={k}>
            <Overhead above={10}><HangingSign area={a} /></Overhead>
            {/* low cabinets with plants along the hall edges, and pendants over the desks */}
            {[a.x0 + 0.7, a.x1 - 0.7].map((x) => <Plant key={x} p={[x, 0, 0]} s={1.15} kind={x > 0 ? 1 : 0} />)}
            {[-2.8, 2.8].map((z) => [a.x0 + 3, a.x1 - 3].map((x) => <Pendant key={`${x}${z}`} p={[x, 2.6, z * 0.7]} on={night} />))}
            <B p={[(a.x0 + a.x1) / 2, 0.38, 0]} s={[a.x1 - a.x0 - 3.4, 0.76, 0.5]} m={mat("#efe9df", { rough: 0.7 })} />
            <B p={[(a.x0 + a.x1) / 2, 0.77, 0]} s={[a.x1 - a.x0 - 3.3, 0.03, 0.56]} m={M.oakTop()} />
            {[0.25, 0.5, 0.75].map((f) => <Plant key={f} p={[a.x0 + 1.7 + f * (a.x1 - a.x0 - 3.4), 0.79, 0]} s={0.38} kind={1} />)}
          </group>
        );
      })}
    </group>
  );
}

function City({ night }: { night: boolean }) {
  const towers = useMemo(() => {
    const list: { x: number; z: number; w: number; d: number; h: number }[] = [];
    let s = 7;
    const r = () => { s = (s * 16807) % 2147483647; return s / 2147483647; };
    for (let i = 0; i < 70; i++) {
      const a = r() * Math.PI * 2, d = 62 + r() * 50;
      list.push({ x: Math.cos(a) * d * 1.25, z: Math.sin(a) * d, w: 5 + r() * 8, d: 5 + r() * 8, h: 10 + r() * 40 });
    }
    return list;
  }, []);
  const meshes = useMemo(() => {
    const base = towerTexture(night);
    return towers.map((t) => {
      const map = base.clone();
      map.repeat.set(Math.max(1, Math.round(t.w / 3)), Math.max(1, Math.round(t.h / 4)));
      map.needsUpdate = true;
      return { t, m: new THREE.MeshStandardMaterial({ map, emissiveMap: night ? map : null, emissive: night ? "#ffffff" : "#000000", emissiveIntensity: night ? 0.9 : 0, roughness: 0.8 }) };
    });
  }, [night, towers]);
  useEffect(() => () => meshes.forEach(({ m }) => { m.map?.dispose(); m.dispose(); }), [meshes]);
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.03, 0]} receiveShadow>
        <circleGeometry args={[170, 64]} />
        <meshStandardMaterial color={night ? "#0b1220" : "#9aa58f"} roughness={1} />
      </mesh>
      {/* the paved square around the building, and lawn with trees */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.015, 2]} receiveShadow>
        <planeGeometry args={[84, 52]} />
        <meshStandardMaterial color={night ? "#1e293b" : "#d6d3d1"} roughness={0.9} />
      </mesh>
      {[-38, -30, 30, 38].flatMap((x) => [-22, 24].map((z) => (
        <group key={`${x}${z}`} position={[x, 0, z]}>
          <C p={[0, 1.2, 0]} r={0.15} h={2.4} m={mat("#5b4636")} />
          <mesh geometry={ball} material={mat("#2f5d2f", { rough: 0.9 })} position={[0, 3.4, 0]} scale={[1.8, 2.2, 1.8]} castShadow />
        </group>
      )))}
      {meshes.map(({ t, m }, i) => <mesh key={i} geometry={box} material={m} position={[t.x, t.h / 2, t.z]} scale={[t.w, t.h, t.d]} />)}
    </group>
  );
}

// --- light, reflections, glow, camera -------------------------------------------------------------------------------
function Environment() {
  const { gl, scene } = useThree();
  useEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl);
    const env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = env;
    scene.environmentIntensity = 0.32;
    return () => { scene.environment = null; env.dispose(); pmrem.dispose(); };
  }, [gl, scene]);
  return null;
}

function Effects({ night }: { night: boolean }) {
  const { gl, scene, camera, size } = useThree();
  const composer = useMemo(() => {
    const c = new EffectComposer(gl);
    c.addPass(new RenderPass(scene, camera));
    c.addPass(new UnrealBloomPass(new THREE.Vector2(size.width, size.height), night ? 0.6 : 0.18, 0.4, 0.96));
    c.addPass(new OutputPass());
    return c;
  }, [gl, scene, camera, night]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { composer.setSize(size.width, size.height); }, [composer, size]);
  useEffect(() => () => composer.dispose(), [composer]);
  useFrame((_, dt) => {
    SCREENS.working.offset.y -= dt * 0.08;
    composer.render(dt);
  }, 1);
  return null;
}

function Controls({ fly }: { fly: Fly }) {
  const { camera, gl } = useThree();
  const controls = useMemo(() => {
    const c = new OrbitControls(camera, gl.domElement);
    c.enableDamping = true;
    c.dampingFactor = 0.08;
    c.maxPolarAngle = 1.36;
    c.minDistance = 3;
    c.maxDistance = 95;
    c.target.set(-2, 0, 0);
    return c;
  }, [camera, gl]);
  const goal = useRef<{ target: THREE.Vector3; position: THREE.Vector3 } | null>(null);
  useEffect(() => {
    const stop = () => { goal.current = null; };
    controls.addEventListener("start", stop);
    return () => { controls.removeEventListener("start", stop); controls.dispose(); };
  }, [controls]);
  useEffect(() => {
    if (!fly) return;
    const target = new THREE.Vector3(fly.target[0], 0.9, fly.target[1]);
    const d = fly.distance ?? 8;
    goal.current = { target, position: new THREE.Vector3(fly.target[0] + d * 0.3, d * 0.92 + 1, fly.target[1] + d * 0.68) };
  }, [fly]);
  useFrame((_, dt) => {
    if (goal.current) {
      const k = Math.min(1, dt * 2.2);
      controls.target.lerp(goal.current.target, k);
      camera.position.lerp(goal.current.position, k);
      if (camera.position.distanceTo(goal.current.position) < 0.05) goal.current = null;
    }
    controls.update();
  });
  return null;
}

function Lights({ light }: { light: number }) {
  const night = light < 0.15;
  const sun = useRef<THREE.DirectionalLight>(null);
  useEffect(() => {
    const s = sun.current;
    if (!s) return;
    s.shadow.mapSize.set(2048, 2048);
    Object.assign(s.shadow.camera, { left: -40, right: 40, top: 26, bottom: -26, near: 1, far: 140 });
    s.shadow.camera.updateProjectionMatrix();
    s.shadow.bias = -0.0003;
    s.shadow.normalBias = 0.02;
  }, []);
  return (
    <>
      <hemisphereLight args={[night ? "#334155" : "#dbeafe", night ? "#0f172a" : "#8f826f", night ? 0.4 : 0.5]} />
      <directionalLight ref={sun} castShadow position={night ? [-25, 50, -30] : [30, 48, 26]} intensity={night ? 0.4 : 0.55 + light * 0.95} color={night ? "#93c5fd" : "#fff4e0"} />
      {night && [[-25, -12], [-14, -12], [-3, -12], [11, -12], [-20, 0], [8, 0], [23, 0], [-21, 13], [0, 13], [21, 13]].map(([x, z]) => (
        <pointLight key={`${x}${z}`} position={[x, 3.4, z]} intensity={14} distance={13} decay={1.6} color="#ffd59a" />
      ))}
    </>
  );
}

// --- the people -------------------------------------------------------------------------------------------------------
function People({ agents, desks, ownerName, ownerWith, selected, onSelect, labels }: Omit<Props, "fly" | "now">) {
  const guests = new Map<string, number>();
  const at = (d: Desk): Place => ({ zone: d.zone, via: d.via, spot: d.seat, facing: d.facing });
  return (
    <>
      {agents.map((a) => {
        const home = desks.get(a.key);
        if (!home || a.mode === "off") return null;
        const host = a.visiting ? desks.get(a.visiting.to) : undefined;
        if (host) {
          const n = guests.get(host.key) ?? 0;
          guests.set(host.key, n + 1);
          const place: Place = { zone: host.zone, via: [], spot: [host.guest[0] + n * 0.6, host.guest[1] + n * 0.2], facing: host.guestFacing };
          return <Character key={a.key} look={lookOf(a.key, a.department)} place={place} pose="talk" name={a.name} bubble={`${humanEvent(a.visiting!.about)} 👋`}
            labels={labels} onClick={() => onSelect(a.key)} selected={selected === a.key} />;
        }
        const pose: Pose = a.mode === "working" ? "type" : a.mode === "waiting" ? "wait" : "idle";
        return <Character key={a.key} look={lookOf(a.key, a.department)} place={at(home)} pose={pose} name={a.name} tag={a.mode === "working" ? a.doing : undefined}
          bubble={a.mode === "waiting" ? "?" : ownerWith === a.key ? "💬" : undefined} labels={labels} onClick={() => onSelect(a.key)} selected={selected === a.key} />;
      })}
      {(() => {
        const d = ownerWith ? desks.get(ownerWith) : undefined;
        const place: Place = d ? { zone: d.zone, via: [], spot: d.owner, facing: d.ownerFacing } : at(OWNER_DESK);
        return <Character key="owner" look={OWNER_LOOK} place={place} pose={d ? "talk" : "type"} name={ownerName || "You"} nameColor="#fde68a" bubble={d ? "…" : undefined}
          labels={labels} you onClick={() => onSelect("owner")} selected={selected === "owner"} />;
      })()}
    </>
  );
}

export function OfficeScene(props: Props) {
  const light = daylight(props.now);
  const night = light < 0.15;
  const sky = new THREE.Color(night ? "#0a0f1e" : "#c7dcf2").lerp(new THREE.Color("#f6b980"), light > 0.15 && light < 0.4 ? 0.35 : 0);
  const B0 = BUILDING;
  return (
    <Canvas shadows dpr={[1, 1.75]} camera={{ position: [10, 46, 52], fov: 40, near: 0.3, far: 500 }}
      gl={{ antialias: true, powerPreference: "high-performance" }} onCreated={({ gl }) => { gl.toneMappingExposure = 0.88; }} onPointerMissed={() => props.onSelect(null)}>
      <color attach="background" args={[sky.getStyle()]} />
      <fog attach="fog" args={[sky.getStyle(), 90, 230]} />
      <Environment />
      <Lights light={light} />
      <City night={night} />
      <Floors />
      <Structure night={night} />
      <OuterWall from={[B0.x0, B0.z0]} to={[B0.x1, B0.z0]} side={{ normal: [0, -1], at: B0.z0 }} />
      <OuterWall from={[B0.x0, B0.z1]} to={[B0.x1, B0.z1]} side={{ normal: [0, 1], at: B0.z1 }} door={[-2.5, 2.5]} />
      <OuterWall from={[B0.x0, B0.z0]} to={[B0.x0, B0.z1]} side={{ normal: [-1, 0], at: B0.x0 }} />
      <OuterWall from={[B0.x1, B0.z0]} to={[B0.x1, B0.z1]} side={{ normal: [1, 0], at: B0.x1 }} />
      <Rooms night={night} />
      <Commons night={night} />
      <Neighbourhoods night={night} />
      <Station desk={OWNER_DESK} mode="working" selected={props.selected === "owner"} onSelect={() => props.onSelect("owner")} name={props.ownerName || "Founder"} wide />
      {props.agents.map((a) => {
        const d = props.desks.get(a.key);
        return d ? <Station key={a.key} desk={d} mode={a.mode} selected={props.selected === a.key} onSelect={() => props.onSelect(a.key)} name={a.name} /> : null;
      })}
      <People {...props} />
      <Controls fly={props.fly} />
      <Effects night={night} />
    </Canvas>
  );
}
