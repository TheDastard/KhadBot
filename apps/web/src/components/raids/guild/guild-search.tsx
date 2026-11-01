// apps/web/src/components/raids/guild/guild-search.tsx
//
// The Guild tab (canvas "Raids, guild search"): the Server and Guild fields with "Find reports" and the hint, and under
// the card the guild header, "Last 30 days · N nights · N boss pulls" and the nights newest first as night cards. The
// search lives in the URL (`?tab=guild&region=us&server=skullcrusher&name=...`), so reload and Back restore it.

"use client";

import { unwrap } from "@khadbot/api-client";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

import { BeforeSearch } from "../before-search";
import { isApiErrorLike } from "../errors";
import { NightCard } from "../night-card";
import { nightFromLookup, pluralize, summaryLine } from "../night";
import { GuildHeader } from "./guild-header";
import { ResultNotes, NothingFound, ResultsSkeleton, SearchError, useSearchUrl } from "./search-parts";
import { ServerCombobox } from "./server-combobox";
import { looksLikeLink, parseGuildUrl, findServer, serverLabel, useServers } from "./servers";
import { useServerField } from "./use-server-field";

const NAME_ID = "guild-name";

interface GuildQuery {
  region: string;
  server: string;
  name: string;
}

function useGuildQuery(): GuildQuery | null {
  const url = useSearchUrl("guild");
  const region = url.get("region");
  const server = url.get("server");
  const name = url.get("name");
  return region && server && name ? { region, server, name } : null;
}

export function useGuildSearch(q: GuildQuery | null) {
  return useQuery({
    queryKey: ["search", "guild", q?.region, q?.server, q?.name.toLowerCase()],
    enabled: !!q,
    retry: false,
    staleTime: 60_000,
    queryFn: async () => {
      const data = await unwrap(
        api().GET("/v1/search/guild", { params: { query: { region: q!.region, server: q!.server, name: q!.name } } }),
      );
      return { data, nights: data.nights.map(nightFromLookup) };
    },
  });
}

// ----------------------------------------------------------------------------
// The form (the tab's slot)
// ----------------------------------------------------------------------------

export function GuildSearchForm() {
  const q = useGuildQuery();
  // Remount on a changed URL so Back and Forward restore the fields.
  return <GuildForm key={q ? `${q.region}|${q.server}|${q.name}` : ""} initial={q} />;
}

function GuildForm({ initial }: { initial: GuildQuery | null }) {
  const url = useSearchUrl("guild");
  const servers = useServers();
  const initialServer = useMemo(() => (initial ? { region: initial.region, slug: initial.server } : null), [initial]);
  const field = useServerField(servers.data, initialServer);
  const [name, setName] = useState(initial?.name ?? "");
  const [nameProblem, setNameProblem] = useState<string | null>(null);

  // A pasted guild URL fills both fields.
  function onName(value: string) {
    setNameProblem(null);
    const parsed = parseGuildUrl(value);
    if (parsed) {
      setName(parsed.name);
      const found = findServer(servers.data, parsed.region, parsed.server);
      if (found) field.choose(found);
      else field.setText(parsed.server);
      return;
    }
    setName(value);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (looksLikeLink(trimmed)) {
      setNameProblem("That isn't a WarcraftLogs guild link.");
      return;
    }
    if (!trimmed) setNameProblem("Enter the guild's name.");
    const server = field.resolve();
    if (!server || !trimmed) return;
    url.push({ region: server.region, server: server.slug, name: trimmed });
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3.5" noValidate>
      <div className="flex flex-wrap items-start gap-3">
        <ServerCombobox
          id="guild-server"
          field={field}
          list={servers.data}
          state={servers.isError ? "failed" : servers.isPending ? "loading" : "ready"}
        />
        <div className="flex min-w-0 flex-[2_1_240px] flex-col gap-1.5">
          <label htmlFor={NAME_ID} className="text-sm font-medium">
            Guild
          </label>
          <input
            id={NAME_ID}
            name="guild"
            type="text"
            autoComplete="off"
            spellCheck={false}
            value={name}
            onChange={(e) => onName(e.target.value)}
            aria-invalid={nameProblem ? true : undefined}
            aria-describedby={nameProblem ? "guild-name-problem" : undefined}
            placeholder="Guild name, or a warcraftlogs.com/guild/… URL"
            className="min-h-10 w-full rounded-sm border border-line bg-s2 px-3 py-2 text-sm text-ink placeholder:text-mut"
          />
          {nameProblem ? (
            <p id="guild-name-problem" role="alert" className="m-0 text-[13px] text-bad">
              {nameProblem}
            </p>
          ) : null}
        </div>
        <Button type="submit" size="lg" className="mt-[26px]">
          Find reports
        </Button>
      </div>
      <p className="m-0 text-[13px] text-mut">
        Lists the guild&rsquo;s raids on WarcraftLogs from the last 30 days, newest first. A guild URL fills in both fields.
      </p>
    </form>
  );
}

// ----------------------------------------------------------------------------
// The results (under the card)
// ----------------------------------------------------------------------------

export function GuildResults() {
  const q = useGuildQuery();
  const servers = useServers();
  const search = useGuildSearch(q);
  const edit = () => document.getElementById(NAME_ID)?.focus();

  if (!q) return <BeforeSearch />;
  if (search.isPending) return <ResultsSkeleton label="Searching the guild's reports" />;
  const serverName = findServer(servers.data, q.region, q.server);
  const subject = {
    kind: "guild" as const,
    name: q.name,
    server: serverName ? serverLabel(serverName) : `${q.server} (${q.region.toUpperCase()})`,
  };
  if (search.isError) {
    return (
      <SearchError
        error={isApiErrorLike(search.error)}
        failedAt={search.errorUpdatedAt}
        subject={subject}
        onRetry={() => void search.refetch()}
        onEdit={edit}
      />
    );
  }

  const { data, nights } = search.data;
  const days = Math.round((Date.parse(data.window_end) - Date.parse(data.window_start)) / 86_400_000);
  return (
    <section aria-labelledby="guild-results-h" className="flex flex-col gap-3">
      <GuildHeader guild={data.guild} />
      {nights.length === 0 ? (
        <NothingFound
          title={`No raids in the last ${days} days`}
          body={`WarcraftLogs lists no boss pulls for ${data.guild.name} in that time. Check the guild is the right one, or look up a report by its code.`}
          onEdit={edit}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <h2 id="guild-results-h" className="m-0 font-display text-lg leading-[1.3]">
              {`Last ${pluralize(days, "day")} · ${summaryLine(nights)}`}
            </h2>
            <span className="text-[13px] text-mut">Newest night first; kills first, best pull marked on top</span>
          </div>
          {nights.map((night) => (
            <NightCard key={night.code} night={night} />
          ))}
        </>
      )}
      <ResultNotes capped={data.capped} leftOut={data.left_out} shown={nights.length} />
    </section>
  );
}
