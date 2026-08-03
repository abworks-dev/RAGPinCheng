import { useEffect, useState } from "react";

export type ThemePreference = "system" | "light" | "dark";
const KEY = "pincheng-theme";

function detectPreference(): ThemePreference {
  if (typeof window === "undefined") return "system";
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark" || saved === "system") return saved;
  } catch {
    /* use the system preference */
  }
  return "system";
}

export function useTheme(): [ThemePreference, (theme: ThemePreference) => void] {
  const [theme, setTheme] = useState<ThemePreference>(detectPreference);

  useEffect(() => {
    const root = document.documentElement;
    const media = typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
    const apply = () => root.classList.toggle("dark", theme === "dark" || (theme === "system" && Boolean(media?.matches)));

    apply();
    if (theme === "system") media?.addEventListener("change", apply);
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      /* noop */
    }
    return () => media?.removeEventListener("change", apply);
  }, [theme]);

  return [theme, setTheme];
}
