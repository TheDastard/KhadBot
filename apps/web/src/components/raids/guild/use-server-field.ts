// apps/web/src/components/raids/guild/use-server-field.ts
//
// The state of a Server field (the typed text and the server chosen from the list), shared by the guild and the
// character form: it starts from the server in the URL once the list has loaded, a typed edit drops the choice, a
// server named before the list has loaded (a pasted guild URL) is chosen when it arrives, and `resolve` gives the
// server to search at submit (the choice, or the one server the text names exactly).

"use client";

import { useCallback, useState } from "react";

import { exactServer, findServer, serverLabel, type ServerChoice, type ServerList } from "./servers";

export interface ServerField {
  text: string;
  choice: ServerChoice | null;
  /** Shown under the field after a submit that could not use the text. */
  problem: string | null;
  setText: (text: string) => void;
  choose: (server: ServerChoice) => void;
  /** Choose the server with this region and slug, now or once the list has loaded; its slug shows until then. */
  chooseBySlug: (region: string, slug: string) => void;
  /** The server to search, or null after setting `problem`. */
  resolve: () => ServerChoice | null;
}

export function useServerField(list: ServerList | undefined, initial: { region: string; slug: string } | null): ServerField {
  // Until the visitor edits, the field shows the URL's server (found once the list has loaded).
  const [typed, setTyped] = useState<string | null>(null);
  const [picked, setPicked] = useState<ServerChoice | null | undefined>(undefined);
  const [problem, setProblem] = useState<string | null>(null);
  const [named, setNamed] = useState<{ region: string; slug: string } | null>(null);

  const target = named ?? initial;
  const seed = list && target ? findServer(list, target.region, target.slug) : null;
  const choice = picked !== undefined ? picked : seed;
  const text = typed ?? (seed ? serverLabel(seed) : target && (list || named) ? target.slug : "");

  const setText = useCallback((next: string) => {
    setTyped(next);
    setPicked(null);
    setProblem(null);
  }, []);

  const choose = useCallback((server: ServerChoice) => {
    setTyped(serverLabel(server));
    setPicked(server);
    setProblem(null);
  }, []);

  const chooseBySlug = useCallback((region: string, slug: string) => {
    setTyped(null);
    setPicked(undefined);
    setNamed({ region, slug });
    setProblem(null);
  }, []);

  const resolve = useCallback((): ServerChoice | null => {
    if (choice) return choice;
    const exact = exactServer(list, text);
    if (exact) {
      choose(exact);
      return exact;
    }
    setProblem(text.trim() ? "Choose a server from the list." : "Choose the server.");
    return null;
  }, [choice, list, text, choose]);

  return { text, choice, problem, setText, choose, chooseBySlug, resolve };
}
