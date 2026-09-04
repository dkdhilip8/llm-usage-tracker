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
      className={`rounded-xl border border-line bg-surface shadow-sm ${className}`}
    >
      {(title || right) && (
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          {title && (
            <h3 className="text-sm font-semibold text-fg">{title}</h3>
          )}
          {right}
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  );
}
