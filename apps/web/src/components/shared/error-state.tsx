// apps/web/src/components/shared/error-state.tsx
//
// An error or empty state in place of the content it replaces (the canvas's "Errors" artboard): what happened (title),
// the next step (body), the action, and a small mono label naming where it happened and the API error code
// ("Search · report_not_found"). Tones: bad (it failed), warn (wait or retry), info (nothing to show). Announced with
// role="alert" for bad and warn, role="status" for info. Pass the thrown `error` and a 5xx answer with a request ID
// adds the "Reference: 7f3a9c0e" line and its copy button (request-reference.tsx).
//
//   <ErrorState tone="warn" where="Loading" code="rate_limited" title="WarcraftLogs is busy"
//     body="KhadBot can't load more from WarcraftLogs right now." action={{ label: "Retry in 0:42", disabled: true }} />

import { AlertTriangleIcon, InfoIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { RequestReference } from "./request-reference";

export type ErrorTone = "bad" | "warn" | "info";

export interface ErrorAction {
  label: string;
  /** A button's handler; or give `href` for a link. */
  onClick?: () => void;
  href?: string;
  disabled?: boolean;
}

export interface ErrorStateProps {
  tone: ErrorTone;
  title: string;
  body: ReactNode;
  /** The page or area ("Search", "Loading", "Any page"), shown in the small label. */
  where?: string;
  /** The API error code (ApiError.code), shown in the small label. */
  code?: string;
  action?: ErrorAction;
  /** A short note beside the action. */
  note?: ReactNode;
  /** What the query threw: a 5xx ApiError with a request ID adds the "Reference: ..." line (RequestReference). */
  error?: unknown;
  className?: string;
}

const STRIPE: Record<ErrorTone, string> = {
  bad: "shadow-[inset_4px_0_0_var(--bad)]",
  warn: "shadow-[inset_4px_0_0_var(--warn)]",
  info: "shadow-[inset_4px_0_0_var(--info)]",
};

const WELL: Record<ErrorTone, string> = {
  bad: "text-bad bg-badt",
  warn: "text-warn bg-warnt",
  info: "text-info bg-infot",
};

export function ErrorState({ tone, title, body, where, code, action, note, error, className }: ErrorStateProps) {
  const label = [where, code].filter(Boolean).join(" · ");
  return (
    <section
      role={tone === "info" ? "status" : "alert"}
      data-error-code={code}
      className={cn("rounded-sm border border-line bg-s1 px-[18px] py-4", STRIPE[tone], className)}
    >
      <div className="flex items-start gap-2.5">
        <span className={cn("inline-flex size-7 flex-none items-center justify-center rounded-sm", WELL[tone])}>
          {tone === "info" ? (
            <InfoIcon className="size-4" aria-hidden />
          ) : (
            <AlertTriangleIcon className="size-4" aria-hidden />
          )}
        </span>
        <div className="flex min-w-0 flex-col gap-1">
          {label ? <span className="font-mono text-[11px] leading-[1.4] font-medium text-mut">{label}</span> : null}
          <h2 className="m-0 font-display text-[17px] leading-[1.3]">{title}</h2>
          <div className="text-sm">{body}</div>
          {action || note ? (
            <div className="mt-1 flex flex-wrap items-center gap-2">
              {action ? <ErrorActionControl action={action} /> : null}
              {note ? <span className="text-xs text-mut">{note}</span> : null}
            </div>
          ) : null}
          <RequestReference error={error} className="mt-1" />
        </div>
      </div>
    </section>
  );
}

function ErrorActionControl({ action }: { action: ErrorAction }) {
  if (action.href && !action.disabled) {
    return (
      <Button asChild variant="primary">
        <Link href={action.href}>{action.label}</Link>
      </Button>
    );
  }
  return (
    <Button variant="primary" onClick={action.onClick} disabled={action.disabled}>
      {action.label}
    </Button>
  );
}
