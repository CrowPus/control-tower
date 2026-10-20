/* The writing area of Mail: formatted text like in Outlook (bold, lists, colours, links, pictures in the
   text). Pictures are kept in the text itself and go out as part of the mail; files dropped here that
   are not pictures become attachments. The server cleans what is written before it goes out. */
import {
  AlignCenter, AlignLeft, AlignRight, Bold, Image as ImageIcon, Italic, Link2, List, ListOrdered, Quote, RemoveFormatting, Strikethrough, Type, Underline,
} from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ReactNode } from "react";
import { cn } from "../ui";

export interface EditorHandle {
  focus: () => void;
  html: () => string;
}

const COLORS = ["#1f2937", "#6b7280", "#0f6b5c", "#1d4ed8", "#7c3aed", "#be123c", "#b45309", "#15803d"];
const MAX_PICTURE = 2 * 1024 * 1024;

function command(name: string, value?: string): void {
  document.execCommand(name, false, value);
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function Tool({ label, onClick, children, active }: { label: string; onClick: () => void; children: ReactNode; active?: boolean }) {
  return (
    <button type="button" title={label} aria-label={label} onMouseDown={(e) => e.preventDefault()} onClick={onClick}
      className={cn("inline-flex h-7 w-7 items-center justify-center rounded text-muted hover:bg-raised hover:text-fg", active && "bg-raised text-fg")}>
      {children}
    </button>
  );
}

export const Editor = forwardRef<EditorHandle, {
  initial: string;
  onChange: (html: string) => void;
  onFiles?: (files: File[]) => void;
  onSubmit?: () => void;
  minHeight?: number;
  placeholder?: string;
  compact?: boolean;
}>(function Editor({ initial, onChange, onFiles, onSubmit, minHeight = 260, placeholder, compact }, ref) {
  const area = useRef<HTMLDivElement>(null);
  const [colorsOpen, setColorsOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const pictureInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (area.current) area.current.innerHTML = initial;
    // Only on first show: afterwards the text is the writer's.
  }, []);

  useImperativeHandle(ref, () => ({
    focus: () => {
      const el = area.current;
      if (!el) return;
      el.focus();
      const range = document.createRange();
      range.setStart(el, 0);
      range.collapse(true);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
    },
    html: () => area.current?.innerHTML ?? "",
  }));

  const changed = () => onChange(area.current?.innerHTML ?? "");

  async function insertPictures(files: File[]) {
    for (const file of files) {
      if (file.size > MAX_PICTURE) {
        setNotice(`${file.name} is larger than 2 MB — attach it as a file instead.`);
        continue;
      }
      const url = await readAsDataUrl(file);
      area.current?.focus();
      command("insertHTML", `<img src="${url}" alt="" style="max-width:100%">`);
    }
    changed();
  }

  function split(files: File[]) {
    const pictures = files.filter((f) => /^image\/(png|jpe?g|gif|webp)$/.test(f.type));
    const others = files.filter((f) => !pictures.includes(f));
    return { pictures, others };
  }

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-0.5 border-b border-border px-2 py-1">
        <Tool label="Bold (Ctrl+B)" onClick={() => { command("bold"); changed(); }}><Bold className="h-4 w-4" /></Tool>
        <Tool label="Italic (Ctrl+I)" onClick={() => { command("italic"); changed(); }}><Italic className="h-4 w-4" /></Tool>
        <Tool label="Underline (Ctrl+U)" onClick={() => { command("underline"); changed(); }}><Underline className="h-4 w-4" /></Tool>
        {!compact && <Tool label="Strikethrough" onClick={() => { command("strikeThrough"); changed(); }}><Strikethrough className="h-4 w-4" /></Tool>}
        <select aria-label="Text size" className="h-7 rounded border-0 bg-transparent px-1 text-xs text-muted hover:bg-raised" defaultValue=""
          onMouseDown={() => area.current?.focus()} onChange={(e) => { if (e.target.value) { command("fontSize", e.target.value); changed(); } e.target.value = ""; }}>
          <option value="" disabled>Size</option>
          <option value="2">Small</option>
          <option value="3">Normal</option>
          <option value="5">Large</option>
          <option value="6">Huge</option>
        </select>
        <div className="relative">
          <Tool label="Text colour" onClick={() => setColorsOpen(!colorsOpen)}><Type className="h-4 w-4" /></Tool>
          {colorsOpen && (
            <div className="absolute left-0 top-8 z-20 flex gap-1 rounded-md border border-border bg-surface p-1.5 shadow-[var(--shadow)]">
              {COLORS.map((c) => (
                <button key={c} type="button" aria-label={`Colour ${c}`} onMouseDown={(e) => e.preventDefault()} onClick={() => { command("foreColor", c); setColorsOpen(false); changed(); }}
                  className="h-5 w-5 rounded-full border border-border" style={{ background: c }} />
              ))}
            </div>
          )}
        </div>
        <span className="mx-1 h-4 w-px bg-border" />
        <Tool label="Bulleted list" onClick={() => { command("insertUnorderedList"); changed(); }}><List className="h-4 w-4" /></Tool>
        <Tool label="Numbered list" onClick={() => { command("insertOrderedList"); changed(); }}><ListOrdered className="h-4 w-4" /></Tool>
        <Tool label="Quote" onClick={() => { command("formatBlock", "blockquote"); changed(); }}><Quote className="h-4 w-4" /></Tool>
        {!compact && (
          <>
            <Tool label="Align left" onClick={() => { command("justifyLeft"); changed(); }}><AlignLeft className="h-4 w-4" /></Tool>
            <Tool label="Centre" onClick={() => { command("justifyCenter"); changed(); }}><AlignCenter className="h-4 w-4" /></Tool>
            <Tool label="Align right" onClick={() => { command("justifyRight"); changed(); }}><AlignRight className="h-4 w-4" /></Tool>
          </>
        )}
        <span className="mx-1 h-4 w-px bg-border" />
        <Tool label="Link (Ctrl+K)" onClick={() => {
          const url = window.prompt("Link address (https://…, mailto:…)", "https://");
          if (url && /^(https?:\/\/|mailto:|tel:)/i.test(url.trim())) { command("createLink", url.trim()); changed(); }
        }}><Link2 className="h-4 w-4" /></Tool>
        <Tool label="Picture in the text" onClick={() => pictureInput.current?.click()}><ImageIcon className="h-4 w-4" /></Tool>
        <Tool label="Clear formatting" onClick={() => { command("removeFormat"); command("unlink"); changed(); }}><RemoveFormatting className="h-4 w-4" /></Tool>
        <input ref={pictureInput} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden
          onChange={(e) => { void insertPictures(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
      </div>
      {notice && <p className="border-b border-border bg-warning-soft px-3 py-1 text-xs text-warning" role="status" onClick={() => setNotice("")}>{notice}</p>}
      <div
        ref={area}
        role="textbox"
        aria-multiline="true"
        aria-label="Message"
        contentEditable
        suppressContentEditableWarning
        data-placeholder={placeholder}
        className="mail-editor min-h-0 flex-1 overflow-y-auto px-4 py-3 text-[14px] leading-relaxed text-fg focus:outline-none"
        style={{ minHeight }}
        onInput={changed}
        onBlur={changed}
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); onSubmit?.(); }
          if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
            e.preventDefault();
            const url = window.prompt("Link address (https://…, mailto:…)", "https://");
            if (url && /^(https?:\/\/|mailto:|tel:)/i.test(url.trim())) { command("createLink", url.trim()); changed(); }
          }
        }}
        onPaste={(e) => {
          const files = Array.from(e.clipboardData.files);
          if (files.length) {
            e.preventDefault();
            const { pictures, others } = split(files);
            void insertPictures(pictures);
            if (others.length) onFiles?.(others);
          }
        }}
        onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) e.preventDefault(); }}
        onDrop={(e) => {
          const files = Array.from(e.dataTransfer.files);
          if (!files.length) return;
          e.preventDefault();
          const { pictures, others } = split(files);
          void insertPictures(pictures);
          if (others.length) onFiles?.(others);
        }}
      />
    </div>
  );
});
