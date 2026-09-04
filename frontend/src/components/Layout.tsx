import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { DemoBanner } from "./DemoBanner";

const linkBase =
  "px-3 py-1.5 rounded-md text-sm font-medium transition-colors";

function navClass({ isActive }: { isActive: boolean }): string {
  return isActive
    ? `${linkBase} bg-brand-600 text-white`
    : `${linkBase} text-slate-600 hover:bg-slate-100`;
}

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-full flex-col">
      <DemoBanner />
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="inline-block h-6 w-6 rounded bg-brand-600" />
            <span className="font-semibold text-slate-800">LLM Usage Tracker</span>
            <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-700">
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
            <NavLink to="/admin" className={navClass}>
              Admin
            </NavLink>
          </nav>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>

      <footer className="border-t border-slate-200 bg-white px-4 py-4 text-center text-xs text-slate-400">
        Demo project — not for production. Virtual keys only, simulated LLM calls, configured
        pricing, disposable data.
      </footer>
    </div>
  );
}
