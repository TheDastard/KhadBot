// apps/web/src/components/raids/character/character-search.tsx
//
// The Character tab (canvas "Raids, character search"): Server, Character and an optional Encounter, "Find pulls"
// and the hint; under the card "Name · Encounter · N pulls on M nights" and the nights newest first, each holding only
// the character's pulls (no night overview slot). The search lives in the URL
// (`?tab=character&region=us&server=skullcrusher&name=Dastard&encounter=3492`).
//
// The API has no encounter list, so the Encounter select offers "Any encounter" plus the encounters seen in the
// character searches made so far in this visit (and the one in the URL).

"use client";

import { unwrap } from "@khadbot/api-client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

import { BeforeSearch } from "../before-search";
import { isApiErrorLike } from "../errors";
import { NightCard } from "../night-card";
import { nightFromLookup, formatNightDate, pluralize, type Night } from "../night";
import { NothingFound, ResultNotes, ResultsSkeleton, SearchError, useSearchUrl } from "../guild/search-parts";
import { ServerCombobox } from "../guild/server-combobox";
import { findServer, serverLabel, useServers } from "../guild/servers";
import { useServerField } from "../guild/use-server-field";

const NAME_ID = "character-name";
const KNOWN_KEY = ["search", "encounters"] as const;

interface KnownEncounter {
  id: number;
  name: string;
}

interface CharacterQuery {
  region: string;
  server: string;
  name: string;
  encounter: number | null;
}

function useCharacterQuery(): CharacterQuery | null {
  const url = useSearchUrl("character");
  const region = url.get("region");
  const server = url.get("server");
  const name = url.get("name");
  const encounter = Number(url.get("encounter"));
  return region && server && name ? { region, server, name, encounter: Number.isInteger(encounter) && encounter > 0 ? encounter : null } : null;
}

export function useCharacterSearch(q: CharacterQuery | null) {
  const client = useQueryClient();
  return useQuery({
    queryKey: ["search", "character", q?.region, q?.server, q?.name.toLowerCase(), q?.encounter],
    enabled: !!q,
    retry: false,
    staleTime: 60_000,
    queryFn: async () => {
      const data = await unwrap(
        api().GET("/v1/search/character", {
          params: { query: { region: q!.region, server: q!.server, name: q!.name, ...(q!.encounter ? { encounter: q!.encounter } : {}) } },
        }),
      );
      const nights = data.nights.map(nightFromLookup);
      // Remember the encounters seen, for the Encounter select.
      const seen = new Map((client.getQueryData<KnownEncounter[]>(KNOWN_KEY) ?? []).map((e) => [e.id, e.name]));
      for (const night of nights) for (const g of night.groups) seen.set(g.encounterId, g.name);
      client.setQueryData<KnownEncounter[]>(
        KNOWN_KEY,
        [...seen].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name)),
      );
      return { data, nights };
    },
  });
}

function useKnownEncounters(): KnownEncounter[] {
  const known = useQuery<KnownEncounter[]>({ queryKey: KNOWN_KEY, queryFn: () => [], enabled: false, initialData: [] });
  return known.data;
}

/** The character's pulls in a night, and the line under its title: "Oct 6, 2026 · 5 Ula'tek pulls with Dastard". */
export function characterMeta(night: Night, character: string, encounter: string | null): string {
  const pulls = night.groups.reduce((n, g) => n + g.pulls.length, 0);
  const what = encounter ? pluralize(pulls, `${encounter} pull`) : pluralize(pulls, "boss pull");
  return [formatNightDate(night.startedAt), `${what} with ${character}`].filter(Boolean).join(" · ");
}

// ----------------------------------------------------------------------------
// The form (the tab's slot)
// ----------------------------------------------------------------------------

export function CharacterSearchForm() {
  const q = useCharacterQuery();
  return <CharacterForm key={q ? `${q.region}|${q.server}|${q.name}|${q.encounter ?? ""}` : ""} initial={q} />;
}

function CharacterForm({ initial }: { initial: CharacterQuery | null }) {
  const url = useSearchUrl("character");
  const servers = useServers();
  const known = useKnownEncounters();
  const initialServer = useMemo(() => (initial ? { region: initial.region, slug: initial.server } : null), [initial]);
  const field = useServerField(servers.data, initialServer);
  const [name, setName] = useState(initial?.name ?? "");
  const [nameProblem, setNameProblem] = useState<string | null>(null);
  const [encounter, setEncounter] = useState(initial?.encounter ? String(initial.encounter) : "");

  const options = useMemo(() => {
    const list = [...known];
    if (initial?.encounter && !list.some((e) => e.id === initial.encounter)) list.push({ id: initial.encounter, name: `Encounter ${initial.encounter}` });
    return list;
  }, [known, initial]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) setNameProblem("Enter the character's name.");
    const server = field.resolve();
    if (!server || !trimmed) return;
    url.push({ region: server.region, server: server.slug, name: trimmed, encounter: encounter || null });
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3.5" noValidate>
      <div className="flex flex-wrap items-start gap-3">
        <ServerCombobox
          id="character-server"
          field={field}
          list={servers.data}
          state={servers.isError ? "failed" : servers.isPending ? "loading" : "ready"}
        />
        <div className="flex min-w-0 flex-[1_1_200px] flex-col gap-1.5">
          <label htmlFor={NAME_ID} className="text-sm font-medium">
            Character
          </label>
          <input
            id={NAME_ID}
            name="character"
            type="text"
            autoComplete="off"
            spellCheck={false}
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setNameProblem(null);
            }}
            aria-invalid={nameProblem ? true : undefined}
            aria-describedby={nameProblem ? "character-name-problem" : undefined}
            placeholder="Character name"
            className="min-h-10 w-full rounded-sm border border-line bg-s2 px-3 py-2 text-sm text-ink placeholder:text-mut"
          />
          {nameProblem ? (
            <p id="character-name-problem" role="alert" className="m-0 text-[13px] text-bad">
              {nameProblem}
            </p>
          ) : null}
        </div>
        <div className="flex min-w-0 flex-[1_1_200px] flex-col gap-1.5">
          <label htmlFor="character-encounter" className="text-sm font-medium">
            Encounter <span className="font-normal text-mut">(optional)</span>
          </label>
          <select
            id="character-encounter"
            name="encounter"
            value={encounter}
            onChange={(e) => setEncounter(e.target.value)}
            className="min-h-10 w-full rounded-sm border border-line bg-s2 px-3 py-2 text-sm text-ink"
          >
            <option value="">Any encounter</option>
            {options.map((e) => (
              <option key={e.id} value={e.id}>
                {e.name}
              </option>
            ))}
          </select>
        </div>
        <Button type="submit" size="lg" className="mt-[26px]">
          Find pulls
        </Button>
      </div>
      <p className="m-0 text-[13px] text-mut">
        Searches the character&rsquo;s recent reports on WarcraftLogs and lists the boss pulls they took part in, by report.
      </p>
    </form>
  );
}

// ----------------------------------------------------------------------------
// The results (under the card)
// ----------------------------------------------------------------------------

export function CharacterResults() {
  const q = useCharacterQuery();
  const servers = useServers();
  const known = useKnownEncounters();
  const search = useCharacterSearch(q);
  const edit = () => document.getElementById(NAME_ID)?.focus();

  if (!q) return <BeforeSearch />;
  if (search.isPending) return <ResultsSkeleton label="Searching the character's reports" />;
  const server = findServer(servers.data, q.region, q.server);
  const subject = {
    kind: "character" as const,
    name: q.name,
    server: server ? serverLabel(server) : `${q.server} (${q.region.toUpperCase()})`,
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
  const name = data.character.name;
  const encounterName = q.encounter ? (known.find((e) => e.id === q.encounter)?.name ?? null) : null;
  if (nights.length === 0) {
    return (
      <section aria-label="Character results" className="flex flex-col gap-3">
        <NothingFound
          title="No boss pulls found"
          body={`${name} took part in no boss pulls${encounterName ? ` against ${encounterName}` : ""} in their recent reports on WarcraftLogs. Try another encounter, or look up a report by its code.`}
          onEdit={edit}
        />
        <ResultNotes capped={data.capped} leftOut={data.left_out} shown={0} />
      </section>
    );
  }
  const pulls = nights.reduce((n, night) => n + night.groups.reduce((m, g) => m + g.pulls.length, 0), 0);
  const title = [name, encounterName, `${pluralize(pulls, "pull")} on ${pluralize(nights.length, "night")}`].filter(Boolean).join(" · ");
  return (
    <section aria-labelledby="character-results-h" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id="character-results-h" className="m-0 font-display text-lg leading-[1.3]">
          {title}
        </h2>
        <span className="text-[13px] text-mut">Newest night first; kills first, best pull marked on top</span>
      </div>
      {nights.map((night) => (
        <NightCard key={night.code} night={night} meta={characterMeta(night, name, encounterName)} />
      ))}
      <ResultNotes capped={data.capped} leftOut={data.left_out} shown={nights.length} />
    </section>
  );
}
