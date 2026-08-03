import { useEffect, useRef, useState } from "react";
import { Check, Monitor, Moon, Sun } from "lucide-react";
import { useTheme, type ThemePreference } from "../hooks/useTheme";

const options: Array<{ value: ThemePreference; label: string; icon: typeof Monitor }> = [
  { value: "system", label: "跟随系统", icon: Monitor },
  { value: "light", label: "明亮", icon: Sun },
  { value: "dark", label: "夜间", icon: Moon },
];

export function ThemeMenu({ collapsed = false }: { collapsed?: boolean }) {
  const [theme, setTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const current = options.find((option) => option.value === theme) || options[0];
  const CurrentIcon = current.icon;

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label={`主题：${current.label}`}
        title={`主题：${current.label}`}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={`flex h-10 items-center rounded-ui-md text-muted-foreground hover:bg-secondary hover:text-foreground ${collapsed ? "w-full justify-center" : "w-full gap-3 px-3"}`}
      >
        <CurrentIcon className="size-4 shrink-0" />
        {!collapsed && <span className="text-sm">{current.label}</span>}
      </button>
      {open && (
        <div className={`absolute bottom-11 z-dropdown w-44 rounded-ui-md border border-border bg-popover p-1.5 text-popover-foreground shadow-overlay ${collapsed ? "left-12" : "left-0"}`}>
          {options.map((option) => {
            const OptionIcon = option.icon;
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => {
                  setTheme(option.value);
                  setOpen(false);
                }}
                className="flex w-full items-center gap-2 rounded-ui-sm px-2.5 py-2 text-left text-sm hover:bg-secondary"
              >
                <OptionIcon className="size-4" />
                <span className="flex-1">{option.label}</span>
                {theme === option.value && <Check className="size-4 text-primary" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
