import { useEffect, useState } from "react";
import { Toaster as Sonner, toast } from "sonner";

export { toast };

function currentTheme(): "light" | "dark" {
  return typeof document !== "undefined" && document.documentElement.classList.contains("dark")
    ? "dark"
    : "light";
}

export function Toaster() {
  const [theme, setTheme] = useState<"light" | "dark">(currentTheme);

  useEffect(() => {
    const root = document.documentElement;
    const observer = new MutationObserver(() => setTheme(currentTheme()));
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  return (
    <Sonner
      theme={theme}
      position="top-right"
      closeButton
      richColors
      duration={4000}
      toastOptions={{
        classNames: {
          toast: "font-sans",
        },
      }}
    />
  );
}
