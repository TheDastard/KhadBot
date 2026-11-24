// apps/web/src/components/shared/request-reference.tsx
//
// The "Reference: 7f3a9c0e" line an error state shows under a server failure: the first 8 characters of the
// response's request ID (ApiError.requestId, from the `X-Request-ID` header), with a button that copies the whole ID,
// so a player reporting the error can quote what finds the matching lines in the API's logs. Only for 5xx answers
// with an ID; a 4xx is the player's to fix, and a call that got no answer has no ID. If the clipboard refuses, the
// full ID is shown as selectable text instead.
//
//   <RequestReference error={query.error} />

"use client";

import { isApiError } from "@khadbot/api-client";
import { CheckIcon, CopyIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** How many characters of the ID the line shows. */
export const REFERENCE_CHARS = 8;

/** The request ID to show for an error: a 5xx ApiError's `requestId`, else null. */
export function requestReferenceOf(error: unknown): string | null {
  if (!isApiError(error) || error.status < 500 || error.status > 599) return null;
  return error.requestId ?? null;
}

type CopyState = "idle" | "copied" | "failed";

export function RequestReference({ error, className }: { error: unknown; className?: string }) {
  const id = requestReferenceOf(error);
  const [copy, setCopy] = useState<CopyState>("idle");
  if (!id) return null;

  const onCopy = async () => {
    try {
      if (!navigator.clipboard) throw new Error("No clipboard");
      await navigator.clipboard.writeText(id);
      setCopy("copied");
    } catch {
      setCopy("failed");
    }
  };

  return (
    <div data-request-id={id} className={cn("flex flex-wrap items-center gap-1.5 text-xs text-mut", className)}>
      <span>
        Reference: <span className="font-mono">{id.slice(0, REFERENCE_CHARS)}</span>
      </span>
      <Button
        type="button"
        variant="link"
        size="sm"
        className="min-h-0 px-1 py-0 text-mut"
        aria-label="Copy the full reference"
        onClick={() => void onCopy()}
      >
        {copy === "copied" ? <CheckIcon className="size-3.5" aria-hidden /> : <CopyIcon className="size-3.5" aria-hidden />}
      </Button>
      <span role="status" className="min-w-0">
        {copy === "copied" ? (
          "Copied"
        ) : copy === "failed" ? (
          <>
            Couldn&apos;t copy. Full reference: <span className="font-mono select-all [overflow-wrap:anywhere]">{id}</span>
          </>
        ) : null}
      </span>
    </div>
  );
}
