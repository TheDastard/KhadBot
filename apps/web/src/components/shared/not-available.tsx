// apps/web/src/components/shared/not-available.tsx
//
// What a direct link to an area shows when this server doesn't offer it (its `features` flag is off): the notice
// "Not available on this server yet", never a blank page or an error (.claude/rules/web.md).

import { ErrorState } from "./error-state";

export const NOT_AVAILABLE_TITLE = "Not available on this server yet";

export function NotAvailable({ area, detail }: { area: string; detail?: string }) {
  return (
    <ErrorState
      tone="info"
      where={area}
      title={NOT_AVAILABLE_TITLE}
      body={detail ?? `This KhadBot server doesn't offer ${area.toLowerCase()} yet.`}
      action={{ label: "Back to Raids", href: "/" }}
    />
  );
}
