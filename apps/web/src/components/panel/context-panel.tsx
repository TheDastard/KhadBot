// apps/web/src/components/panel/context-panel.tsx
//
// The context panel's frame: a fixed 380 px, sticky aside beside the main column that shows the focused ref
// (`?ref=` in the URL). It fetches the record, then hands it to its kind's renderer (registry.tsx). Plan refs show
// "Raid plans aren't available yet" without a request; a ref the API can't resolve shows the API's message.
// The frame is the only part that is desktop-specific: the views know nothing of it.

"use client";

import { isApiError } from "@khadbot/api-client";

import { usePullContext } from "@/components/pull/use-pull-context";
import { useEvidence } from "@/components/pull/queries";
import { useFocus } from "@/lib/focus";

import { renderEvidence } from "./registry";
import { EmptyView, ErrorView, LoadingView, PlanView } from "./views";

export function ContextPanel({ code, fight }: { code: string; fight: number }) {
  const { ref, parsedRef, setRef } = useFocus();
  const evidence = useEvidence(ref);
  // Names come from the pull the ref belongs to, which is the page's pull unless a finding cites another one.
  const owner = parsedRef && parsedRef.kind !== "plan" ? parsedRef : { report: code, fight };
  const context = usePullContext(owner.report, owner.fight);
  const close = () => setRef(null);

  let body;
  if (!ref) {
    body = <EmptyView />;
  } else if (parsedRef?.kind === "plan") {
    body = <PlanView evidenceRef={ref} onClose={close} />;
  } else if (evidence.isPending) {
    body = <LoadingView />;
  } else if (evidence.isError) {
    const error = evidence.error;
    const unresolvable = isApiError(error) && error.code === "unresolvable_ref";
    body = (
      <ErrorView
        evidenceRef={ref}
        code={isApiError(error) ? error.code : "unexpected_error"}
        message={error instanceof Error ? error.message : "Unexpected error"}
        unresolvable={unresolvable}
        onRetry={() => void evidence.refetch()}
        onClose={close}
      />
    );
  } else {
    body = renderEvidence(evidence.data, {
      evidenceRef: ref,
      code: owner.report,
      fight: owner.fight,
      names: context.names,
      onClose: close,
    });
  }

  return (
    <aside
      aria-label="Selected evidence"
      data-testid="context-panel"
      className="sticky top-4 max-h-[calc(100vh-32px)] w-[380px] max-w-full overflow-y-auto rounded-sm border border-line bg-s1 px-[18px] py-4 shadow-[0_1px_0_var(--line)]"
    >
      {body}
    </aside>
  );
}
