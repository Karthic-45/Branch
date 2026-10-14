import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "bmem-theme-mode";

function initialTheme(): Theme {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "light" || saved === "dark") return saved;
  // Light theme is the default across the application
  return "light";
}

/** Applied via both `data-theme` and `.dark` class on <html> to ensure
 * all CSS variables and Tailwind utility classes reflect the current theme cleanly. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    if (theme === "dark") {
      document.documentElement.classList.add("dark");
    } else {
      document.documentElement.classList.remove("dark");
    }
    localStorage.setItem(STORAGE_KEY, theme);
    try {
      localStorage.removeItem("bmem-theme");
    } catch {
      // ignore
    }
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }, []);

  return [theme, toggle];
}
