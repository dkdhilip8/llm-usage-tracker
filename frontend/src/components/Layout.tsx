import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { DemoBanner } from "./DemoBanner";
import { useTheme } from "../lib/theme";

const linkBase = "px-3 py-1.5 rounded-md text-sm font-medium transition-colors";

function navClass({ isActive }: { isActive: boolean }): string {
  return isActive
    ? `${linkBase} bg-brand-600 text-white`
    : `${linkBase} text-fg-muted hover:bg-fill`;
}

function ThemeToggle() {
  const { isDark, toggle } = useTheme();
  return (
    <button
      onClick={toggle}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      aria-label="Toggle theme"
      className="ml-1 rounded-md border border-line p-1.5 text-fg-muted hover:bg-fill"
    >
      {isDark ? (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      )}
    </button>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-full flex-col">
      <DemoBanner />
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="inline-block h-6 w-6 rounded bg-brand-600" />
            <span className="font-semibold text-fg">LLM Usage Tracker</span>
            <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-700 dark:bg-amber-500/15 dark:text-amber-400">
              Demo
            </span>
          </div>
          <nav className="ml-auto flex items-center gap-1">
            <NavLink to="/dashboard" className={navClass}>
              Dashboard
            </NavLink>
            <NavLink to="/playground" className={navClass}>
              Playground
            </NavLink>
            <NavLink to="/requests" className={navClass}>
              Requests
            </NavLink>
            <NavLink to="/insights" className={navClass}>
              Insights
            </NavLink>
            <NavLink to="/admin" className={navClass}>
              Admin
            </NavLink>
            <ThemeToggle />
          </nav>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>

      <footer className="border-t border-line bg-surface px-4 py-4 text-center text-xs text-fg-subtle">
        Demo project — not for production. Virtual keys only, simulated LLM calls, configured
        pricing, disposable data.
      </footer>
    </div>
  );
}
