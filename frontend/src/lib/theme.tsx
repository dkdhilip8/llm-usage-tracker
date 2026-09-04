import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

type Theme = "light" | "dark";

interface ThemeCtx {
  theme: Theme;
  isDark: boolean;
  toggle: () => void;
}

const Ctx = createContext<ThemeCtx | null>(null);

function readInitial(): Theme {
  try {
    const stored = localStorage.getItem("theme");
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* storage blocked */
  }
  if (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-color-scheme: dark)").matches
  ) {
    return "dark";
  }
  return "light";
}

function apply(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(readInitial);

  useEffect(() => {
    apply(theme);
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* storage blocked */
    }
  }, [theme]);

  const toggle = useCallback(
    () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    [],
  );

  const value = useMemo<ThemeCtx>(
    () => ({ theme, isDark: theme === "dark", toggle }),
    [theme, toggle],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}

/** Recharts colours that adapt to the active theme. */
export function chartTheme(isDark: boolean) {
  return {
    grid: isDark ? "#1e293b" : "#eef2f7",
    axis: isDark ? "#64748b" : "#94a3b8",
    tooltip: {
      contentStyle: {
        background: isDark ? "#0f172a" : "#ffffff",
        border: `1px solid ${isDark ? "#1e293b" : "#e2e8f0"}`,
        borderRadius: 8,
        fontSize: 12,
        color: isDark ? "#e2e8f0" : "#1e293b",
      },
      labelStyle: { color: isDark ? "#94a3b8" : "#64748b" },
      itemStyle: { color: isDark ? "#e2e8f0" : "#1e293b" },
    },
  };
}
