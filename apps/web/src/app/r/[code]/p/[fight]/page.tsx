// apps/web/src/app/r/[code]/p/[fight]/page.tsx
//
// The pull page, /r/{code}/p/{fight}, addressed by WCL report code and fight ID (`?ref=`, `?t=` and `?full=1` hold its
// focus, lib/focus.ts). The route shell is pull-route.tsx; its three slots are in slots.tsx.

import { Suspense } from "react";

import { PullRoute, PullRouteSkeleton } from "./pull-route";

export default async function PullPage({ params }: { params: Promise<{ code: string; fight: string }> }) {
  const { code, fight } = await params;
  return (
    <Suspense fallback={<PullRouteSkeleton />}>
      <PullRoute code={decodeURIComponent(code)} fightParam={fight} />
    </Suspense>
  );
}
