/* A person in the HQ: real proportions (about 1.75 m), a jointed body (hips, knees, shoulders, elbows) and a face,
   built from shapes (nothing downloaded). Walks the halls to a place, sits and types, waits, talks, listens. */
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { hallZone, mat, route, textTexture, type Look, type V2, type Zone } from "./kit";

export type Pose = "type" | "idle" | "wait" | "talk" | "listen";
export type Place = { zone: Zone; via: V2[]; spot: V2; facing: number };

type Props = {
  look: Look; place: Place; pose: Pose; name: string; nameColor?: string; tag?: string; bubble?: string; labels: boolean;
  onClick?: () => void; selected?: boolean; you?: boolean;
};

const SPEED = 1.45; // a walking pace, metres a second
const STAND_HIP = 0.93, SEAT_HIP = 0.53;

function lathe(points: [number, number][], segments = 28): THREE.LatheGeometry {
  return new THREE.LatheGeometry(points.map(([r, y]) => new THREE.Vector2(r, y)), segments);
}

const G = {
  torso: lathe([[0, 0], [0.135, 0.0], [0.15, 0.06], [0.135, 0.2], [0.155, 0.32], [0.185, 0.44], [0.19, 0.5], [0.16, 0.56], [0.06, 0.6], [0, 0.6]]),
  jacket: lathe([[0, -0.04], [0.15, -0.04], [0.162, 0.06], [0.148, 0.2], [0.168, 0.32], [0.198, 0.44], [0.205, 0.5], [0.172, 0.565], [0.07, 0.61], [0, 0.61]]),
  pelvis: new THREE.SphereGeometry(1, 20, 14),
  neck: new THREE.CylinderGeometry(0.048, 0.054, 0.12, 14),
  head: new THREE.SphereGeometry(1, 32, 24),
  hairCap: new THREE.SphereGeometry(1, 32, 16, 0, Math.PI * 2, 0, Math.PI * 0.58),
  hijab: new THREE.SphereGeometry(1, 32, 20, Math.PI * 0.62, Math.PI * 1.76, 0, Math.PI * 0.8),
  ball: new THREE.SphereGeometry(1, 16, 12),
  upper: new THREE.CapsuleGeometry(0.044, 0.2, 6, 12),
  fore: new THREE.CapsuleGeometry(0.037, 0.18, 6, 12),
  thigh: new THREE.CapsuleGeometry(0.068, 0.32, 6, 14),
  shin: new THREE.CapsuleGeometry(0.052, 0.32, 6, 12),
  shoe: new THREE.BoxGeometry(0.1, 0.07, 0.25),
  box: new THREE.BoxGeometry(1, 1, 1),
  ring: new THREE.TorusGeometry(0.022, 0.0035, 6, 18),
  band: new THREE.TorusGeometry(0.118, 0.008, 6, 24, Math.PI),
  mic: new THREE.CapsuleGeometry(0.006, 0.09, 3, 6),
  disc: new THREE.CylinderGeometry(1, 1, 1, 24),
  halo: new THREE.RingGeometry(0.42, 0.52, 48),
  hood: new THREE.TorusGeometry(0.11, 0.045, 10, 20, Math.PI),
};

function Label({ text, y, color, bg, size, onClick }: { text: string; y: number; color: string; bg: string; size: number; onClick?: () => void }) {
  const { texture, aspect } = textTexture(text, { fg: color, bg, size: 26, weight: 600, border: "rgba(148,163,184,0.35)" });
  return (
    <sprite position={[0, y, 0]} scale={[size * aspect, size, 1]} renderOrder={10} onClick={onClick ? (e) => { e.stopPropagation(); onClick(); } : undefined}>
      <spriteMaterial map={texture} transparent depthTest={false} depthWrite={false} toneMapped={false} />
    </sprite>
  );
}

function darker(color: string, k: number): string {
  return new THREE.Color(color).multiplyScalar(k).getStyle();
}

export function Character({ look, place, pose, name, nameColor, tag, bubble, labels, onClick, selected, you }: Props) {
  const root = useRef<THREE.Group>(null);
  const hips = useRef<THREE.Group>(null);
  const spine = useRef<THREE.Group>(null);
  const head = useRef<THREE.Group>(null);
  const lids = useRef<THREE.Group>(null);
  const j = {
    thighL: useRef<THREE.Group>(null), thighR: useRef<THREE.Group>(null), kneeL: useRef<THREE.Group>(null), kneeR: useRef<THREE.Group>(null),
    armL: useRef<THREE.Group>(null), armR: useRef<THREE.Group>(null), elbowL: useRef<THREE.Group>(null), elbowR: useRef<THREE.Group>(null),
  };
  const halo = useRef<THREE.Mesh>(null);
  const walk = useRef<{ path: V2[]; zone: Zone; via: V2[]; walking: boolean; seed: number; placed: boolean }>({ path: [], zone: place.zone, via: place.via, walking: false, seed: Math.random() * 10, placed: false });
  const build = look.build ?? 1;

  const m = useMemo(() => {
    if (look.hologram) {
      const holo = new THREE.MeshStandardMaterial({ color: "#67e8f9", emissive: "#06b6d4", emissiveIntensity: 1.1, transparent: true, opacity: 0.68, roughness: 0.15, metalness: 0.2 });
      const solid = new THREE.MeshStandardMaterial({ color: "#ecfeff", emissive: "#a5f3fc", emissiveIntensity: 1.6 });
      return { skin: holo, lips: holo, hair: holo, top: holo, jacket: holo, trousers: holo, shoes: holo, white: solid, iris: mat("#083344"), brow: holo, acc: holo, tie: holo };
    }
    const fabric = (c: string) => new THREE.MeshPhysicalMaterial({ color: c, roughness: 0.82, sheen: 0.6, sheenRoughness: 0.8, sheenColor: new THREE.Color(c).lerp(new THREE.Color("#ffffff"), 0.3) });
    return {
      skin: new THREE.MeshPhysicalMaterial({ color: look.skin, roughness: 0.55, sheen: 0.25, sheenColor: new THREE.Color("#ffd9c4"), clearcoat: 0.05 }),
      lips: mat(darker(look.skin, 0.72), { rough: 0.45 }), hair: mat(look.hair, { rough: 0.62, metal: 0.05 }), brow: mat(darker(look.hair, 0.9), { rough: 0.8 }),
      top: fabric(look.top), jacket: fabric(look.jacket ?? look.top), trousers: fabric(look.trousers), shoes: mat(look.shoes, { rough: 0.35, metal: 0.1 }),
      white: mat("#f8fafc", { rough: 0.2 }), iris: mat("#3b2412", { rough: 0.15 }),
      acc: mat(look.extras?.includes("beret") ? "#9f1239" : look.extras?.includes("cap") ? "#1d4ed8" : "#18181b", { rough: 0.45, metal: 0.25 }),
      tie: mat(look.tie ?? "#7f1d1d", { rough: 0.4 }),
    };
  }, [look]);

  // A new place: walk there along the halls, or step there inside the same room.
  const placeKey = `${place.zone.key}:${place.spot[0].toFixed(2)}:${place.spot[1].toFixed(2)}`;
  useEffect(() => {
    const w = walk.current, g = root.current;
    if (!g) return;
    if (!w.placed) {
      g.position.set(place.spot[0], 0, place.spot[1]);
      g.rotation.y = place.facing;
      w.placed = true;
      w.zone = place.zone;
      w.via = place.via;
      return;
    }
    const cur: V2 = [g.position.x, g.position.z];
    const from = w.walking ? hallZone(cur[0], cur[1]) : w.zone;
    w.path = route(from, w.walking ? [] : w.via, cur, place.zone, place.via, place.spot);
    w.zone = place.zone;
    w.via = place.via;
    w.walking = w.path.length > 0;
  }, [placeKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useFrame((state, delta) => {
    const g = root.current, w = walk.current;
    if (!g || !hips.current || !spine.current || !head.current) return;
    const J = Object.fromEntries(Object.entries(j).map(([k, r]) => [k, r.current])) as Record<keyof typeof j, THREE.Group | null>;
    if (Object.values(J).some((v) => !v)) return;
    const t = state.clock.elapsedTime + w.seed;
    const dt = Math.min(delta, 0.25);
    let moving = false;
    if (w.walking && w.path.length) {
      const [tx, tz] = w.path[0];
      const dx = tx - g.position.x, dz = tz - g.position.z, dist = Math.hypot(dx, dz);
      const step = SPEED * dt;
      if (dist <= step) {
        g.position.set(tx, 0, tz);
        w.path.shift();
        if (!w.path.length) w.walking = false;
      } else {
        g.position.x += (dx / dist) * step;
        g.position.z += (dz / dist) * step;
        const want = Math.atan2(dx, dz);
        g.rotation.y += Math.atan2(Math.sin(want - g.rotation.y), Math.cos(want - g.rotation.y)) * Math.min(1, dt * 8);
      }
      moving = true;
    } else {
      g.rotation.y += Math.atan2(Math.sin(place.facing - g.rotation.y), Math.cos(place.facing - g.rotation.y)) * Math.min(1, dt * 5);
    }
    const ease = (obj: THREE.Object3D, axis: "x" | "y" | "z", v: number, k = 9) => { obj.rotation[axis] += (v - obj.rotation[axis]) * Math.min(1, dt * k); };
    const seated = !moving && (pose === "type" || pose === "idle" || pose === "wait");
    const phase = t * 6.2; // two steps a second
    // Hips: at the chair, or walking with a gentle rise and sway.
    const hipY = seated ? SEAT_HIP : STAND_HIP + (moving ? Math.abs(Math.cos(phase)) * 0.025 - 0.012 : 0);
    hips.current.position.y += (hipY - hips.current.position.y) * Math.min(1, dt * 7);
    hips.current.position.z += ((seated ? -0.08 : 0) - hips.current.position.z) * Math.min(1, dt * 7);
    hips.current.rotation.y = moving ? Math.sin(phase) * 0.08 : 0;
    spine.current.scale.y = 1 + Math.sin(t * 1.5) * 0.008; // breathing
    const { thighL, thighR, kneeL, kneeR, armL, armR, elbowL, elbowR } = J as Record<string, THREE.Group>;
    if (moving) {
      const s = Math.sin(phase);
      ease(thighL, "x", -s * 0.5, 22); ease(thighR, "x", s * 0.5, 22);
      ease(kneeL, "x", Math.max(0, Math.sin(phase + 1.3)) * 0.75, 22); ease(kneeR, "x", Math.max(0, Math.sin(phase + 1.3 + Math.PI)) * 0.75, 22);
      ease(armL, "x", s * 0.42, 22); ease(armR, "x", -s * 0.42, 22);
      ease(elbowL, "x", -0.25 - Math.max(0, s) * 0.3, 22); ease(elbowR, "x", -0.25 - Math.max(0, -s) * 0.3, 22);
      ease(spine.current, "x", 0.04); ease(head.current, "y", 0); ease(head.current, "x", 0);
    } else if (seated) {
      ease(thighL, "x", -1.52); ease(thighR, "x", -1.52); ease(kneeL, "x", 1.5); ease(kneeR, "x", 1.5);
      ease(spine.current, "x", pose === "type" ? 0.12 : -0.04);
      if (pose === "type") {
        ease(armL, "x", -0.55); ease(armR, "x", -0.55);
        ease(elbowL, "x", -1.05 + Math.sin(t * 13) * 0.07, 20); ease(elbowR, "x", -1.05 + Math.sin(t * 13 + 1.9) * 0.07, 20);
        ease(head.current, "x", 0.18 + Math.sin(t * 0.6) * 0.03); ease(head.current, "y", Math.sin(t * 0.37) * 0.1);
      } else if (pose === "wait") {
        ease(armL, "x", -0.3); ease(elbowL, "x", -1.2); ease(armR, "x", -0.75); ease(elbowR, "x", -2.1); // chin on hand
        ease(head.current, "x", 0.08); ease(head.current, "y", Math.sin(t * 0.5) * 0.25);
      } else {
        ease(armL, "x", -0.35); ease(armR, "x", -0.35); ease(elbowL, "x", -0.9); ease(elbowR, "x", -0.9); // hands in the lap
        ease(head.current, "x", -0.02); ease(head.current, "y", Math.sin(t * 0.22) * 0.55);
      }
    } else {
      ease(thighL, "x", 0); ease(thighR, "x", 0); ease(kneeL, "x", 0.02); ease(kneeR, "x", 0.02); ease(spine.current, "x", 0);
      if (pose === "talk") {
        ease(armL, "x", -0.1); ease(elbowL, "x", -0.2);
        ease(armR, "x", -0.55 + Math.sin(t * 2.6) * 0.3, 10); ease(elbowR, "x", -1.0 + Math.sin(t * 3.4) * 0.35, 10);
        ease(head.current, "x", Math.sin(t * 3.1) * 0.05); ease(head.current, "y", Math.sin(t * 0.9) * 0.15);
      } else {
        ease(armL, "x", 0.05); ease(armR, "x", 0.05); ease(elbowL, "x", -0.15); ease(elbowR, "x", -0.15);
        ease(head.current, "x", Math.max(0, Math.sin(t * 1.1)) * 0.12); ease(head.current, "y", 0); // nodding
      }
    }
    if (lids.current) lids.current.scale.y = (t % 3.7) < 0.11 ? 0.12 : 1; // blinking
    if (look.hologram) g.position.y = 0.04 + Math.sin(t * 1.4) * 0.03;
    if (halo.current) halo.current.rotation.z = t * 0.5;
  });

  const hairY = 0;
  const jacketed = Boolean(look.jacket);
  const sleeve = jacketed ? m.jacket : m.top;
  return (
    <group ref={root} onClick={onClick ? (e) => { e.stopPropagation(); onClick(); } : undefined}>
      {(selected || you || look.hologram) && (
        <mesh ref={halo} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]}>
          <primitive object={G.halo} attach="geometry" />
          <meshBasicMaterial color={you ? "#fbbf24" : look.hologram ? "#22d3ee" : "#ffffff"} transparent opacity={0.8} toneMapped={false} />
        </mesh>
      )}
      <group ref={hips} position={[0, STAND_HIP, 0]} scale={[build, 1, build]}>
        <mesh geometry={G.pelvis} material={m.trousers} scale={[0.15, 0.1, 0.1]} castShadow />
        {([[-0.085, j.thighL, j.kneeL], [0.085, j.thighR, j.kneeR]] as const).map(([x, thigh, knee], i) => (
          <group key={i} ref={thigh} position={[x, -0.03, 0]}>
            <mesh geometry={G.thigh} material={m.trousers} position={[0, -0.2, 0]} castShadow />
            <group ref={knee} position={[0, -0.41, 0]}>
              <mesh geometry={G.shin} material={m.trousers} position={[0, -0.19, 0]} castShadow />
              <mesh geometry={G.shoe} material={m.shoes} position={[0, -0.43, 0.045]} castShadow />
            </group>
          </group>
        ))}
        <group ref={spine} position={[0, 0.02, 0]}>
          <mesh geometry={G.torso} material={m.top} scale={[1, 1, 0.64]} castShadow />
          {jacketed && (
            <>
              <mesh geometry={G.jacket} material={m.jacket} scale={[1, 1, 0.66]} castShadow />
              {/* the shirt and tie in the jacket's opening */}
              <mesh geometry={G.box} material={m.top} position={[0, 0.45, 0.128]} rotation={[-0.18, 0, 0]} scale={[0.07, 0.2, 0.01]} />
              {look.tie && <mesh geometry={G.box} material={m.tie} position={[0, 0.42, 0.135]} rotation={[-0.18, 0, 0]} scale={[0.028, 0.2, 0.012]} />}
            </>
          )}
          {look.extras?.includes("hood") && <mesh geometry={G.hood} material={m.top} position={[0, 0.58, -0.06]} rotation={[0.9, 0, 0]} />}
          {([[-1, j.armL, j.elbowL], [1, j.armR, j.elbowR]] as const).map(([side, arm, elbow], i) => (
            <group key={i} ref={arm} position={[side * 0.205, 0.53, 0]} rotation={[0, 0, side * 0.06]}>
              <mesh geometry={G.ball} material={sleeve} scale={0.058} />
              <mesh geometry={G.upper} material={sleeve} position={[0, -0.14, 0]} castShadow />
              <group ref={elbow} position={[0, -0.28, 0]}>
                <mesh geometry={G.fore} material={sleeve} position={[0, -0.12, 0]} castShadow />
                <mesh geometry={G.ball} material={m.skin} position={[0, -0.26, 0.008]} scale={[0.035, 0.07, 0.022]} />
              </group>
            </group>
          ))}
          <mesh geometry={G.neck} material={look.hairStyle === "hijab" ? m.hair : m.skin} position={[0, 0.65, 0]} />
          <group ref={head} position={[0, 0.775, 0.005]}>
            <mesh geometry={G.head} material={m.skin} scale={[0.093, 0.118, 0.108]} castShadow />
            {/* the jaw and chin */}
            <mesh geometry={G.head} material={m.skin} position={[0, -0.045, 0.018]} scale={[0.078, 0.07, 0.085]} />
            <group ref={lids} position={[0, 0.012, 0.088]}>
              {[-1, 1].map((s) => (
                <group key={s} position={[s * 0.034, 0, 0]}>
                  <mesh geometry={G.ball} material={m.white} scale={[0.015, 0.011, 0.008]} />
                  <mesh geometry={G.ball} material={m.iris} position={[0, 0, 0.006]} scale={[0.0075, 0.0075, 0.004]} />
                </group>
              ))}
            </group>
            {[-1, 1].map((s) => <mesh key={s} geometry={G.box} material={m.brow} position={[s * 0.034, 0.038, 0.097]} rotation={[0, 0, s * -0.12]} scale={[0.03, 0.0055, 0.008]} />)}
            <mesh geometry={G.ball} material={m.skin} position={[0, -0.012, 0.105]} scale={[0.013, 0.024, 0.018]} />
            <mesh geometry={G.box} material={m.lips} position={[0, -0.05, 0.094]} scale={[0.032, 0.007, 0.008]} />
            {look.hairStyle !== "hijab" && [-1, 1].map((s) => <mesh key={s} geometry={G.ball} material={m.skin} position={[s * 0.092, 0.002, -0.004]} scale={[0.012, 0.026, 0.017]} />)}
            {look.beard && <mesh geometry={G.head} material={m.hair} position={[0, -0.058, 0.03]} scale={[0.082, 0.062, 0.084]} />}
            {/* hair */}
            {look.hairStyle === "hijab" ? (
              <>
                <mesh geometry={G.hijab} material={m.hair} scale={[0.112, 0.135, 0.124]} position={[0, 0.0, -0.004]} rotation={[0.1, 0, 0]} castShadow />
                <mesh geometry={G.head} material={m.hair} position={[0, -0.13, -0.02]} scale={[0.11, 0.08, 0.1]} />
              </>
            ) : look.hairStyle !== "bald" && (
              <mesh geometry={G.hairCap} material={m.hair} position={[0, hairY + (look.hairStyle === "buzz" ? 0.002 : 0.006), -0.008]} rotation={[-0.38, 0, 0]}
                scale={look.hairStyle === "buzz" ? [0.097, 0.122, 0.112] : look.hairStyle === "afro" ? [0.13, 0.15, 0.14] : [0.1, 0.127, 0.116]} castShadow />
            )}
            {look.hairStyle === "side" && <mesh geometry={G.ball} material={m.hair} position={[-0.03, 0.09, 0.05]} rotation={[0, 0, 0.4]} scale={[0.07, 0.03, 0.06]} />}
            {look.hairStyle === "short" && <mesh geometry={G.ball} material={m.hair} position={[0.01, 0.095, 0.045]} scale={[0.08, 0.03, 0.06]} />}
            {look.hairStyle === "bun" && <mesh geometry={G.ball} material={m.hair} position={[0, 0.11, -0.08]} scale={0.045} />}
            {look.hairStyle === "ponytail" && <mesh geometry={G.upper} material={m.hair} position={[0, -0.02, -0.13]} rotation={[0.35, 0, 0]} scale={[0.9, 0.75, 0.9]} />}
            {look.hairStyle === "long" && <mesh geometry={G.head} material={m.hair} position={[0, -0.08, -0.05]} scale={[0.105, 0.17, 0.075]} />}
            {look.hairStyle === "curly" && [[-0.07, 0.08], [0, 0.11], [0.07, 0.08], [-0.05, 0.04], [0.05, 0.04]].map(([x, y], i) => <mesh key={i} geometry={G.ball} material={m.hair} position={[x, y, -0.02]} scale={0.042} />)}
            {look.extras?.includes("glasses") && (
              <group position={[0, 0.012, 0.106]}>
                {[-1, 1].map((s) => <mesh key={s} geometry={G.ring} material={m.acc} position={[s * 0.034, 0, 0]} />)}
                <mesh geometry={G.box} material={m.acc} scale={[0.024, 0.003, 0.003]} />
              </group>
            )}
            {look.extras?.includes("headset") && (
              <group>
                <mesh geometry={G.band} material={m.acc} position={[0, 0.01, 0]} />
                {[-1, 1].map((s) => <mesh key={s} geometry={G.disc} material={m.acc} position={[s * 0.1, 0, 0]} rotation={[0, 0, Math.PI / 2]} scale={[0.035, 0.02, 0.035]} />)}
                <mesh geometry={G.mic} material={m.acc} position={[-0.075, -0.06, 0.07]} rotation={[0.3, 0.8, 1.1]} />
              </group>
            )}
            {look.extras?.includes("beret") && <mesh geometry={G.disc} material={m.acc} position={[0.015, 0.11, -0.01]} rotation={[0.12, 0, -0.18]} scale={[0.12, 0.03, 0.12]} />}
            {look.extras?.includes("cap") && (
              <group position={[0, 0.045, 0]}>
                <mesh geometry={G.hairCap} material={m.acc} scale={[0.106, 0.11, 0.118]} />
                <mesh geometry={G.box} material={m.acc} position={[0, 0.01, 0.11]} rotation={[-0.15, 0, 0]} scale={[0.15, 0.01, 0.09]} />
              </group>
            )}
          </group>
        </group>
      </group>
      {labels && <Label text={you ? `★ ${name}` : name} y={2.12} color={nameColor ?? "#f1f5f9"} bg="rgba(15,23,42,0.8)" size={0.24} onClick={onClick} />}
      {tag && <Label text={tag} y={1.88} color="#5eead4" bg="rgba(4,47,46,0.85)" size={0.19} />}
      {bubble && <Label text={bubble} y={tag ? 2.4 : 2.42 + (labels ? 0.26 : 0)} color="#0f172a" bg="rgba(248,250,252,0.95)" size={0.26} />}
    </group>
  );
}
