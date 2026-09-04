import type { ReactNode } from "react";

export function Card({
  title,
  right,
  children,
  className = "",
}: {
  title?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}
    >
      {(title || right) && (
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          {title && (
            <h3 className="text-sm font-semibold text-slate-700">{title}</h3>
          )}
          {right}
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  );
}
