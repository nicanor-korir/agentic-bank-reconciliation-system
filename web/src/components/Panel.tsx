/** Layout primitives: cards, section headings, label/value pairs, buttons. */

import type { ButtonHTMLAttributes, ReactNode } from "react";

export function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-slate-200 bg-white shadow-sm ${className}`}
    >
      {children}
    </section>
  );
}

export function PanelHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 px-6 py-4">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">{title}</h2>
        {subtitle !== undefined && (
          <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>
        )}
      </div>
      {actions !== undefined && (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
      )}
    </header>
  );
}

export function PanelBody({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`px-6 py-5 ${className}`}>{children}</div>;
}

/** A dense label-above-value pair, used across every header block. */
export function Field({
  label,
  children,
  mono = false,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">
        {label}
      </dt>
      <dd
        className={`mt-1 truncate text-sm text-slate-900 ${mono ? "font-mono text-xs" : ""}`}
      >
        {children}
      </dd>
    </div>
  );
}

export function FieldGrid({
  children,
  columns = 4,
}: {
  children: ReactNode;
  columns?: 2 | 3 | 4 | 5;
}) {
  const cols: Record<number, string> = {
    2: "sm:grid-cols-2",
    3: "sm:grid-cols-3",
    4: "sm:grid-cols-2 lg:grid-cols-4",
    5: "sm:grid-cols-3 lg:grid-cols-5",
  };
  return (
    <dl className={`grid grid-cols-2 gap-x-6 gap-y-4 ${cols[columns] ?? ""}`}>
      {children}
    </dl>
  );
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "positive";

const BUTTON_CLASS: Record<ButtonVariant, string> = {
  primary:
    "bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-slate-300 disabled:text-slate-500",
  secondary:
    "bg-white text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 disabled:text-slate-400 disabled:hover:bg-white",
  ghost:
    "bg-transparent text-slate-600 hover:bg-slate-100 disabled:text-slate-400 disabled:hover:bg-transparent",
  danger:
    "bg-white text-rose-700 ring-1 ring-inset ring-rose-300 hover:bg-rose-50 disabled:text-slate-400 disabled:ring-slate-200 disabled:hover:bg-white",
  positive:
    "bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-300 disabled:text-slate-500",
};

export function Button({
  variant = "secondary",
  className = "",
  type = "button",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      type={type}
      className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-1 focus-visible:outline-none disabled:cursor-not-allowed ${BUTTON_CLASS[variant]} ${className}`}
      {...rest}
    />
  );
}

export function SectionTitle({
  children,
  hint,
}: {
  children: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-4">
      <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
        {children}
      </h3>
      {hint !== undefined && <span className="text-xs text-slate-400">{hint}</span>}
    </div>
  );
}
