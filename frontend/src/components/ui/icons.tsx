import type { HTMLAttributes } from "react";

type IconProps = HTMLAttributes<HTMLSpanElement>;

function icon(glyph: string) {
  return function UiIcon({ className = "", ...props }: IconProps) {
    return <span aria-hidden="true" className={`inline-flex items-center justify-center leading-none ${className}`} {...props}>{glyph}</span>;
  };
}

export const AlertTriangle = icon("!");
export const Check = icon("✓");
export const ChevronDown = icon("⌄");
export const ChevronRight = icon("›");
export const Clipboard = icon("▣");
export const Copy = icon("□");
export const FileSpreadsheet = icon("▦");
export const FileText = icon("▤");
export const Film = icon("▶");
export const Layers3 = icon("≡");
export const LocateFixed = icon("⌖");
export const Menu = icon("☰");
export const Minus = icon("−");
export const PanelRightClose = icon("→|");
export const PanelRightOpen = icon("|←");
export const Pause = icon("Ⅱ");
export const Play = icon("▶");
export const Plus = icon("+");
export const Send = icon("↑");
export const Volume2 = icon("))");
export const VolumeX = icon("×");
export const X = icon("×");
