// apps/web/src/components/review/use-review.ts
//
// The review button's data: `GET .../review` (what a review would do now), `POST .../review` (start one, or get the
// cached findings), and the review job followed by polling `GET /v1/jobs/{id}` once a second until it ends. When the
// job is done the pull's findings and review status are refetched, so the findings section fills in without a reload.

"use client";

import { isApiError, unwrap, type ApiError } from "@khadbot/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";

import { isActive, reviewView, type ReviewView } from "./logic";

export const POLL_MS = 1000;

function reviewKey(code: string, fight: number) {
  return [...queryKeys.pull(code, fight), "review"] as const;
}

export interface Review {
  view: ReviewView;
  /** Ask for the review (the viewer clicked the button). */
  start: () => void;
}

export function useReview(code: string, fight: number): Review {
  const client = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [startError, setStartError] = useState<ApiError | null>(null);

  const status = useQuery({
    queryKey: reviewKey(code, fight),
    queryFn: () =>
      unwrap(api().GET("/v1/reports/{code}/pulls/{fight}/review", { params: { path: { code, fight } } })),
    retry: false,
    staleTime: 10_000,
  });

  // A review somebody else started is followed too: the findings are shared.
  const followed = jobId ?? status.data?.job?.id ?? null;
  const job = useQuery({
    queryKey: ["job", followed],
    enabled: !!followed,
    retry: false,
    queryFn: () => unwrap(api().GET("/v1/jobs/{job_id}", { params: { path: { job_id: followed ?? "" } } })),
    refetchInterval: (query) => (!query.state.data || isActive(query.state.data) ? POLL_MS : false),
  });

  const ended = job.data && !isActive(job.data) ? job.data.state : null;
  useEffect(() => {
    if (ended === "done") {
      void client.invalidateQueries({ queryKey: queryKeys.pull(code, fight) });
    }
  }, [ended, client, code, fight]);

  const mutation = useMutation({
    mutationFn: () =>
      unwrap(
        api().POST("/v1/reports/{code}/pulls/{fight}/review", {
          params: { path: { code, fight } },
          body: { depth: "standard" },
        }),
      ),
    onMutate: () => setStartError(null),
    onSuccess: (answer) => {
      if (answer.job) setJobId(answer.job.id);
      void client.invalidateQueries({ queryKey: queryKeys.pull(code, fight) });
    },
    onError: (error) => {
      setStartError(isApiError(error) ? error : null);
      void client.invalidateQueries({ queryKey: reviewKey(code, fight) });
    },
  });

  const view = reviewView({
    status: status.data,
    statusError: status.isError,
    job: job.data,
    startError,
    starting: mutation.isPending,
  });
  return { view, start: () => mutation.mutate() };
}
