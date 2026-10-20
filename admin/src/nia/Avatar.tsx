/* Nia's video in the admin: the same character as in the member app, idle
   while she listens and talking while her voice plays. Silent; her voice is the live voice. */
import { useEffect, useRef, useState } from "react";

export type NiaState = "idle" | "speaking";
type Shade = "light" | "dark";

const clips: Record<Shade, Record<NiaState, string>> = {
  light: { idle: "/assets/nia/nia-light-idle-v1.mp4", speaking: "/assets/nia/nia-light-talking-v1.mp4" },
  dark: { idle: "/assets/nia/nia-dark-idle-v1.mp4", speaking: "/assets/nia/nia-dark-talking-v1.mp4" },
};
const posters: Record<Shade, string> = { light: "/assets/nia/nia-light-v1.jpg", dark: "/assets/nia/nia-dark-v1.jpg" };

/** The admin's theme follows the system (main.tsx toggles .dark on the page). */
function useShade(): Shade {
  const read = (): Shade => (document.documentElement.classList.contains("dark") ? "dark" : "light");
  const [shade, setShade] = useState<Shade>(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setShade(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return shade;
}

export function NiaAvatar({ state, size }: { state: NiaState; size: "panel" | "page" | "bar" }) {
  const shade = useShade();
  return <Clips key={shade} shade={shade} state={state} size={size} />;
}

function Clips({ shade, state, size }: { shade: Shade; state: NiaState; size: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState<Record<NiaState, boolean>>({ idle: false, speaking: false });
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const visible = reduced ? null : ready[state] ? state : ready.idle ? "idle" : null;
  useEffect(() => {
    const videos = Array.from(box.current?.querySelectorAll("video") ?? []);
    const play = () => videos.forEach((v) => { v.muted = true; if (reduced || document.hidden) v.pause(); else void v.play().catch(() => { /* the still stays */ }); });
    play();
    document.addEventListener("visibilitychange", play);
    document.addEventListener("pointerdown", play);
    return () => { document.removeEventListener("visibilitychange", play); document.removeEventListener("pointerdown", play); videos.forEach((v) => v.pause()); };
  }, [reduced]);
  return (
    <div ref={box} className={`nia-avatar nia-avatar--${size} nia-avatar--${shade}`} aria-hidden="true">
      <img className="nia-avatar-poster" src={posters[shade]} alt="" />
      {(Object.keys(clips[shade]) as NiaState[]).map((key) => (
        <video key={key} src={clips[shade][key]} poster={posters[shade]} className={`nia-avatar-video ${visible === key ? "is-visible" : ""}`}
          autoPlay={!reduced} muted loop playsInline preload="auto" controls={false} disablePictureInPicture tabIndex={-1}
          onPlaying={() => setReady((r) => (r[key] ? r : { ...r, [key]: true }))} onError={() => setReady((r) => ({ ...r, [key]: false }))} />
      ))}
    </div>
  );
}
