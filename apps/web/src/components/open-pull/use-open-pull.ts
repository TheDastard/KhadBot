// apps/web/src/components/open-pull/use-open-pull.ts
//
// Opens one pull and follows its job: `POST /v1/reports` for the pull (after the page renders, never from a link),
// then `GET /v1/jobs/{id}/events` through the API client. A pull that is already loading answers with that job, so a
// reload, or a second visitor, picks it up. When the pull's last step is done the pull's queries are invalidated,
// which makes the route shell's digest query succeed and swap the pull page in (no reload, `?ref=` untouched).

"use client";

import { isApiError, unwrap, type ApiError } from "@khadbot/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useState } from "react";

import { api, apiEvents } from "@/lib/api";
import { queryKeys } from "@/lib/queries";

import {
  INITIAL_STREAM,
  isOpened,
  jobStreamReducer,
  type Job,
  type JobFailure,
  type JobPull,
  type JobStreamState,
} from "./job-events";

type Opening =
  | { status: "opening" }
  | { status: "following"; jobId: string }
  | { status: "ready" }
  | { status: "error"; error: ApiError };

export interface OpenPull {
  stream: JobStreamState;
  /** The POST's own failure (not signed in, report not found, WarcraftLogs unavailable, unreachable). */
  openError: ApiError | null;
  /** The event stream lost its connection and has not come back. */
  connectionLost: boolean;
  /** True once the request to open the pull was answered: the lookup behind the tile can run then. */
  requested: boolean;
  /** Ask again: a new POST, which follows the job that is running or starts a new one. */
  restart: () => void;
}

/** How often the pull's queries are refreshed after the job is done, until the pull page shows. */
const REINVALIDATE_MS = 2000;

export function useOpenPull(code: string, fight: number): OpenPull {
  const queryClient = useQueryClient();
  const [attempt, setAttempt] = useState(0);
  const [opening, setOpening] = useState<Opening>({ status: "opening" });
  const [stream, dispatch] = useReducer(jobStreamReducer, INITIAL_STREAM);
  const [connectionLost, setConnectionLost] = useState(false);
  const opened = isOpened(stream.job, fight) || opening.status === "ready";

  const openThePull = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.pull(code, fight) });
  }, [queryClient, code, fight]);

  // Ask the API to open the pull.
  useEffect(() => {
    let cancelled = false;
    unwrap(api().POST("/v1/reports", { body: { source: code, fights: [fight] } })).then(
      (answer) => {
        if (cancelled) return;
        if (answer.ready || !answer.job) {
          setOpening({ status: "ready" });
          return;
        }
        dispatch({ type: "state", job: answer.job, at: Date.now() });
        setOpening({ status: "following", jobId: answer.job.id });
      },
      (error: unknown) => {
        if (cancelled) return;
        setOpening({ status: "error", error: isApiError(error) ? error : unexpected(error) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [code, fight, attempt]);

  // Follow the job.
  const jobId = opening.status === "following" ? opening.jobId : null;
  useEffect(() => {
    if (!jobId) return;
    const source = apiEvents(`/v1/jobs/${encodeURIComponent(jobId)}/events`);
    let closed = false;
    const close = () => {
      closed = true;
      source.close();
    };
    const parse = <T,>(event: Event): T | null => {
      try {
        return JSON.parse((event as MessageEvent<string>).data) as T;
      } catch {
        return null;
      }
    };
    source.addEventListener("open", () => setConnectionLost(false));
    source.addEventListener("job.state", (event) => {
      const job = parse<Job>(event);
      if (!job) return;
      setConnectionLost(false);
      dispatch({ type: "state", job, at: Date.now() });
      if (job.state === "cancelled") close();
    });
    source.addEventListener("pull.progress", (event) => {
      const pull = parse<JobPull>(event);
      if (pull) dispatch({ type: "progress", pull });
    });
    source.addEventListener("job.done", (event) => {
      const job = parse<Job>(event);
      if (job) dispatch({ type: "done", job, at: Date.now() });
      close();
    });
    // `error` is both the server's named event (a JobFailure, with data) and the browser's own connection error.
    source.addEventListener("error", (event) => {
      if (closed) return;
      const failure = event instanceof MessageEvent && typeof event.data === "string" ? parse<JobFailure>(event) : null;
      if (failure) {
        dispatch({ type: "failure", failure });
        close();
        return;
      }
      // The connection dropped: the browser retries by itself. Ask the job where it is, so a job the server no
      // longer has is told apart from a server that is just not answering.
      unwrap(api().GET("/v1/jobs/{job_id}", { params: { path: { job_id: jobId } } })).then(
        (job) => {
          if (closed) return;
          setConnectionLost(false);
          dispatch({ type: "state", job, at: Date.now() });
        },
        (error: unknown) => {
          if (closed) return;
          if (isApiError(error) && error.status === 404) {
            dispatch({ type: "gone" });
            close();
          } else if (isApiError(error) && error.code === "api_unreachable") {
            setConnectionLost(true);
          }
        },
      );
    });
    return close;
  }, [jobId]);

  // The last step is done (or the pull was loaded already): swap the pull page in. Keep asking until it shows.
  useEffect(() => {
    if (!opened) return;
    openThePull();
    const timer = setInterval(openThePull, REINVALIDATE_MS);
    return () => clearInterval(timer);
  }, [opened, openThePull]);

  const restart = useCallback(() => {
    setOpening({ status: "opening" });
    dispatch({ type: "reset" });
    setConnectionLost(false);
    setAttempt((n) => n + 1);
  }, []);
  return {
    stream,
    openError: opening.status === "error" ? opening.error : null,
    connectionLost,
    requested: opening.status === "following" || opening.status === "ready",
    restart,
  };
}

function unexpected(error: unknown): ApiError {
  // A non-API throw (a bug, not the network): shown like any failed request rather than blanking the page.
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return { name: "ApiError", status: 0, code: "unexpected_error", message, retryAfterS: null, body: null } as ApiError;
}
