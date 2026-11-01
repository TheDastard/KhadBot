// apps/web/src/components/raids/guild/server-combobox.tsx
//
// The Server field of the guild and character searches (canvas "Raids, guild search", the open list): one input over
// the US and EU servers that filters as you type, bolds the typed prefix, shows the region beside each name and keeps
// "United States" and "Europe" as dividers that cannot be chosen. ARIA combobox pattern: the input keeps focus and
// names the active option with aria-activedescendant; arrow keys move, Enter chooses, Escape closes.

"use client";

import { ChevronDownIcon } from "lucide-react";
import { useEffect, useId, useMemo, useState, type KeyboardEvent } from "react";

import { cn } from "@/lib/utils";

import { filterServers, foldName, prefixLength, type ServerChoice, type ServerList } from "./servers";
import type { ServerField } from "./use-server-field";

export interface ServerComboboxProps {
  id: string;
  field: ServerField;
  list: ServerList | undefined;
  /** True while the list is loading, false when it failed (the field then says so). */
  state: "loading" | "ready" | "failed";
}

export function ServerCombobox({ id, field, list, state }: ServerComboboxProps) {
  const listId = useId();
  const errorId = `${id}-problem`;
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const groups = useMemo(() => filterServers(list, field.text), [list, field.text]);
  const options = useMemo(() => groups.flatMap((g) => g.servers), [groups]);
  const optionId = (index: number) => `${id}-opt-${index}`;
  const showList = open && options.length > 0;

  useEffect(() => {
    if (!showList) return;
    document.getElementById(optionId(active))?.scrollIntoView?.({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, showList]);

  function pick(server: ServerChoice | undefined) {
    if (!server) return;
    field.choose(server);
    setOpen(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open) setOpen(true);
        else setActive((a) => Math.min(a + 1, options.length - 1));
        break;
      case "ArrowUp":
        event.preventDefault();
        if (!open) setOpen(true);
        else setActive((a) => Math.max(a - 1, 0));
        break;
      case "Enter":
        if (showList) {
          event.preventDefault();
          pick(options[active]);
        }
        break;
      case "Escape":
        if (open) {
          event.preventDefault();
          setOpen(false);
        }
        break;
      default:
    }
  }

  const typed = foldName(field.text);
  const placeholder = state === "failed" ? "The server list didn't load" : "Search US and EU servers";
  const starts = groups.map((_, i) => groups.slice(0, i).reduce((n, g) => n + g.servers.length, 0));

  return (
    <div className="relative flex min-w-0 flex-[1_1_240px] flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        Server
      </label>
      <span className="relative block">
        <input
          id={id}
          name="server"
          type="text"
          role="combobox"
          autoComplete="off"
          spellCheck={false}
          aria-autocomplete="list"
          aria-expanded={showList}
          aria-controls={listId}
          aria-activedescendant={showList ? optionId(active) : undefined}
          aria-invalid={field.problem ? true : undefined}
          aria-describedby={field.problem ? errorId : undefined}
          value={field.text}
          placeholder={placeholder}
          onChange={(e) => {
            field.setText(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onClick={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
          className={cn(
            "min-h-10 w-full rounded-sm border bg-s2 py-2 pr-[34px] pl-3 text-sm text-ink placeholder:text-mut",
            showList ? "border-info" : field.problem ? "border-bad" : "border-line",
          )}
        />
        <ChevronDownIcon
          className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-mut"
          aria-hidden
        />
      </span>
      <div
        id={listId}
        role="listbox"
        aria-label="Servers"
        hidden={!showList}
        data-testid="server-listbox"
        className="absolute top-full right-0 left-0 z-20 mt-1 max-h-[280px] overflow-y-auto rounded-sm border border-line bg-s1 py-1 text-sm shadow-[0_8px_24px_rgba(0,0,0,0.22)]"
      >
        {groups.map((group, gi) => (
          <div key={group.region} role="group" aria-labelledby={`${id}-head-${group.region}`}>
            <div
              id={`${id}-head-${group.region}`}
              role="presentation"
              className="mt-1 border-t border-line px-3 pt-2 pb-1 text-[11px] leading-tight font-semibold tracking-[0.06em] text-mut uppercase first:mt-0 first:border-t-0"
            >
              {group.heading}
            </div>
            {group.servers.map((server, si) => {
              const at = starts[gi]! + si;
              const lead = prefixLength(server.name, typed);
              const isActive = at === active;
              return (
                <div
                  key={`${server.region}-${server.slug}`}
                  id={optionId(at)}
                  role="option"
                  aria-selected={isActive}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(server)}
                  onMouseMove={() => setActive(at)}
                  className={cn(
                    "flex min-h-9 cursor-pointer items-center justify-between gap-2 px-3 py-2",
                    isActive && "bg-infot shadow-[inset_3px_0_0_var(--info)]",
                  )}
                >
                  <span>
                    <b className="font-semibold">{server.name.slice(0, lead)}</b>
                    {server.name.slice(lead)}
                  </span>
                  <span className="text-xs text-mut">{server.regionName}</span>
                </div>
              );
            })}
          </div>
        ))}
      </div>
      {field.problem ? (
        <p id={errorId} role="alert" className="m-0 text-[13px] text-bad">
          {field.problem}
        </p>
      ) : state === "failed" ? (
        <p className="m-0 text-[13px] text-bad">KhadBot couldn&rsquo;t load the server list. Reload the page to try again.</p>
      ) : null}
    </div>
  );
}
