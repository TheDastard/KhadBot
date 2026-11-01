// apps/web/src/components/timeline/timeline-chart.tsx
//
// The timeline itself: lanes on one time scale, drawn with visx. Loaded only in the browser (timeline-loader.tsx,
// next/dynamic with ssr: false), behind a placeholder of the same height.
//
// Layers, bottom to top:
//   1. an SVG (visx Group, scaleLinear, Brush): lane backgrounds, phase bands, tick lines and labels, the cutoff
//      line and its label, the zoomed window, and the brush that picks a window (30 s at most);
//   2. the markers, real <button>s placed by percentage over the lanes, so they are focusable, named, and need no
//      pixel width to lay out. The group is one tab stop: arrow keys move focus between markers in time order
//      across lanes (roving tabindex), Enter or a click selects (`onSelect`), Escape clears, Z zooms to 10 s.
//
// Focus (the 2 px info ring) and selection (the accent ring with its halo) are separate and may sit on different
// markers. The component takes its state and callbacks as props; the URL lives in pull-timeline.tsx.

"use client";

import { Brush } from "@visx/brush";
import { Group } from "@visx/group";
import { scaleLinear } from "@visx/scale";
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { IconFallback } from "@/components/shared/ability-name";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { TimeWindow } from "@/lib/focus";
import { formatClockTenths } from "@/lib/format";
import { cn } from "@/lib/utils";

import { MarkerTip } from "./marker-tip";
import {
  indexOfRef,
  isStepKey,
  stepIndex,
  type CooldownMark,
  type DeathMark,
  type MechanicMark,
  type Marker,
  type TimelineModel,
  type WindowMark,
} from "./markers";
import { clampWindow, formatTick, makeTimeScale, tickTimes } from "./scale";

// ---------------------------------------------------------------------------
// Layout (the canvas's lane heights, px)
// ---------------------------------------------------------------------------

const H_PHASE = 30;
const H_MECH = 24;
const H_DEATH = 40;
const H_CD = 48;
const H_WINDOW = 30;
const H_TICKS = 18;

export interface TimelineLayout {
  phase: number;
  mech: number[];
  death: number;
  cooldown: number;
  window: number;
  ticks: number;
  total: number;
}

export function layoutLanes(mechanicRows: number): TimelineLayout {
  const phase = 0;
  const mech = Array.from({ length: mechanicRows }, (_, i) => H_PHASE + i * H_MECH);
  const death = H_PHASE + mechanicRows * H_MECH;
  const cooldown = death + H_DEATH;
  const window = cooldown + H_CD;
  const ticks = window + H_WINDOW;
  return { phase, mech, death, cooldown, window, ticks, total: ticks + H_TICKS };
}

/** The chart's height before it has data, so the placeholder and the chart are the same size. */
export const PLACEHOLDER_HEIGHT = layoutLanes(8).total;

function useElementWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(Math.round(el.getBoundingClientRect().width));
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect.width ?? 0);
      setWidth((prev) => (prev === w ? prev : w));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

// ---------------------------------------------------------------------------
// The chart
// ---------------------------------------------------------------------------

export interface TimelineChartProps {
  code: string;
  model: TimelineModel;
  durationMs: number;
  cutoffMs: number | null;
  phases: Array<{ id: string; name: string | null; start_ms: number; end_ms: number }>;
  /** The selected evidence ref (`?ref=`). */
  selectedRef: string | null;
  /** The zoomed window (`?t=`), already fitted to the fight. */
  zoom: TimeWindow | null;
  onSelect: (ref: string | null) => void;
  onZoom: (window: TimeWindow | null) => void;
  /** Z: zoom to 10 s around a marker's time. */
  onZoomAround: (atMs: number) => void;
}

export default function TimelineChart(props: TimelineChartProps) {
  const { code, model, durationMs, cutoffMs, phases, selectedRef: urlRef, zoom, onSelect, onZoom, onZoomAround } = props;
  // A click or Escape shows at once; the URL catches up (a router push re-renders the whole page) and then wins.
  const [local, setLocal] = useState<{ from: string | null; value: string | null } | null>(null);
  const selectedRef = local && local.from === urlRef ? local.value : urlRef;
  const chooseRef = useCallback(
    (value: string | null) => {
      setLocal({ from: urlRef, value });
      onSelect(value);
    },
    [urlRef, onSelect],
  );
  const { ordered, rows } = model;
  const layout = useMemo(() => layoutLanes(rows.length), [rows.length]);
  const trackRef = useRef<HTMLDivElement>(null);
  const groupRef = useRef<HTMLDivElement>(null);
  const width = useElementWidth(trackRef);
  const scale = useMemo(() => makeTimeScale(durationMs, width), [durationMs, width]);

  const [activeId, setActiveId] = useState<string | null>(null);
  const selectedIndex = indexOfRef(ordered, selectedRef);
  const tabId = activeId ?? ordered[Math.max(0, selectedIndex)]?.id ?? null;
  const activeIndex = activeId ? ordered.findIndex((m) => m.id === activeId) : -1;

  // A selection made elsewhere (a chip, the panel, Back) moves the tab stop to it, unless focus is in the timeline.
  useEffect(() => {
    if (!selectedRef || selectedIndex < 0) return;
    if (groupRef.current?.contains(document.activeElement)) return;
    setActiveId(ordered[selectedIndex]!.id);
  }, [selectedRef, selectedIndex, ordered]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (isStepKey(event.key)) {
      event.preventDefault();
      const next = ordered[stepIndex(ordered.length, activeIndex, event.key)];
      if (next) focusMarker(groupRef.current, next.id);
    } else if (event.key === "Escape") {
      chooseRef(null);
    } else if (event.key === "z" || event.key === "Z") {
      const at = ordered[activeIndex];
      if (at) {
        event.preventDefault();
        onZoomAround(at.tMs);
      }
    }
  };

  const select = useCallback(
    (m: Marker) => {
      setActiveId(m.id);
      chooseRef(m.ref);
    },
    [chooseRef],
  );

  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-x-auto">
        <div className="grid min-w-[760px] grid-cols-[150px_minmax(0,1fr)] gap-x-2.5">
          <LaneLabels rows={model.rows} layout={layout} />
          <div ref={trackRef} className="relative" style={{ height: layout.total }}>
            {width > 0 && (
              <ChartSvg
                width={width}
                layout={layout}
                scale={scale}
                durationMs={durationMs}
                cutoffMs={cutoffMs}
                phases={phases}
                zoom={zoom}
                onZoom={onZoom}
              />
            )}
            <div
              ref={groupRef}
              role="group"
              aria-label="Timeline markers"
              aria-describedby="timeline-keys"
              onKeyDown={onKeyDown}
              className="pointer-events-none absolute inset-0"
            >
              {model.rows.flatMap((row, i) =>
                row.marks.map((m) => (
                  <MechanicButton
                    key={m.id}
                    m={m}
                    top={layout.mech[i]!}
                    scale={scale}
                    {...state(m, selectedRef, tabId, select, setActiveId)}
                  />
                )),
              )}
              {model.deaths.map((m) => (
                <DeathButton
                  key={m.id}
                  m={m}
                  top={layout.death}
                  scale={scale}
                  code={code}
                  {...state(m, selectedRef, tabId, select, setActiveId)}
                />
              ))}
              {model.cooldowns.map((m) => (
                <CooldownButton
                  key={m.id}
                  m={m}
                  top={layout.cooldown}
                  scale={scale}
                  code={code}
                  {...state(m, selectedRef, tabId, select, setActiveId)}
                />
              ))}
              {model.windows.map((m) => (
                <WindowButton
                  key={m.id}
                  m={m}
                  top={layout.window}
                  scale={scale}
                  {...state(m, selectedRef, tabId, select, setActiveId)}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
      <Legend />
      <MarkerList ordered={ordered} selectedRef={selectedRef} activeId={activeId} />
    </div>
  );
}

/** Focus a marker by id (the buttons carry it as data-marker). */
function focusMarker(group: HTMLElement | null, id: string) {
  group?.querySelectorAll<HTMLElement>("[data-marker]").forEach((el) => {
    if (el.dataset.marker === id) el.focus();
  });
}

function state(
  m: Marker,
  selectedRef: string | null,
  tabId: string | null,
  select: (m: Marker) => void,
  setActiveId: (id: string) => void,
) {
  return {
    selected: m.ref === selectedRef,
    tabbable: m.id === tabId,
    onSelectMarker: select,
    onFocusMarker: setActiveId,
  };
}

// ---------------------------------------------------------------------------
// The SVG layer
// ---------------------------------------------------------------------------

function ChartSvg({
  width,
  layout,
  scale,
  durationMs,
  cutoffMs,
  phases,
  zoom,
  onZoom,
}: {
  width: number;
  layout: TimelineLayout;
  scale: ReturnType<typeof makeTimeScale>;
  durationMs: number;
  cutoffMs: number | null;
  phases: TimelineChartProps["phases"];
  zoom: TimeWindow | null;
  onZoom: (window: TimeWindow | null) => void;
}) {
  const xScale = useMemo(() => scaleLinear<number>({ domain: [0, durationMs], range: [0, width] }), [durationMs, width]);
  const yScale = useMemo(() => scaleLinear<number>({ domain: [0, layout.ticks], range: [0, layout.ticks] }), [layout.ticks]);
  const ticks = useMemo(() => tickTimes(durationMs), [durationMs]);
  const laneRects: Array<{ y: number; h: number; fill: string }> = [
    ...layout.mech.map((y) => ({ y, h: H_MECH, fill: "fill-s1" })),
    { y: layout.death, h: H_DEATH, fill: "fill-s2" },
    { y: layout.cooldown, h: H_CD, fill: "fill-s1" },
    { y: layout.window, h: H_WINDOW, fill: "fill-s2" },
  ];
  const cutoffLabel = cutoffMs === null ? "" : `Cutoff ${formatClockTenths(cutoffMs)}`;
  const cutoffX = cutoffMs === null ? 0 : scale.toX(cutoffMs);
  const labelW = cutoffLabel.length * 6.6 + 12;
  const labelX = cutoffX - labelW - 3 >= 0 ? cutoffX - labelW - 3 : cutoffX + 3;

  return (
    <svg width={width} height={layout.total} className="absolute inset-0 block" aria-hidden="true" focusable="false">
      <Group>
        <rect x={0} y={0} width={width} height={H_PHASE} className="fill-s2 stroke-line" />
        {laneRects.map((r) => (
          <rect key={r.y} x={0} y={r.y} width={width} height={r.h} className={cn(r.fill, "stroke-line")} />
        ))}
        {phases.map((p, i) => {
          const x = scale.toX(p.start_ms);
          const w = Math.max(0, scale.toX(p.end_ms) - x);
          return (
            <g key={`${p.id}-${i}`}>
              <rect x={x} y={0} width={w} height={H_PHASE} className={cn(i % 2 ? "fill-s3" : "fill-s2", "stroke-line")} />
              <svg x={x} y={0} width={w} height={H_PHASE}>
                <text x={8} y={19} className="fill-ink" style={{ fontSize: 12, fontWeight: 500 }}>
                  {p.name ?? p.id}
                </text>
              </svg>
            </g>
          );
        })}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={scale.toX(t)} x2={scale.toX(t)} y1={H_PHASE} y2={layout.ticks} className="stroke-line" opacity={0.45} />
            <text
              x={scale.toX(t)}
              y={layout.ticks + 13}
              textAnchor={t === 0 ? "start" : "middle"}
              className="fill-mut"
              style={{ fontSize: 11, fontFamily: "var(--font-mono)" }}
            >
              {formatTick(t)}
            </text>
          </g>
        ))}
        {zoom && (
          <rect
            data-testid="zoom-window"
            x={scale.toX(zoom.startMs)}
            y={H_PHASE}
            width={Math.max(2, scale.toX(zoom.endMs) - scale.toX(zoom.startMs))}
            height={layout.ticks - H_PHASE}
            className="fill-acct stroke-acc"
            fillOpacity={0.6}
          />
        )}
        {cutoffMs !== null && (
          <g>
            <line x1={cutoffX} x2={cutoffX} y1={0} y2={layout.ticks} className="stroke-acc" strokeWidth={2} strokeDasharray="5 3" />
            <rect x={labelX} y={34} width={labelW} height={17} rx={2} className="fill-acc" />
            <text x={labelX + 6} y={46} className="fill-s1" style={{ fontSize: 11, fontWeight: 600 }}>
              {cutoffLabel}
            </text>
          </g>
        )}
        <Brush
          xScale={xScale}
          yScale={yScale}
          width={width}
          height={layout.ticks}
          margin={{ top: 0, left: 0, right: 0, bottom: 0 }}
          handleSize={8}
          brushDirection="horizontal"
          resizeTriggerAreas={[]}
          disableDraggingSelection
          resetOnEnd
          selectedBoxStyle={{ fill: "var(--acct)", fillOpacity: 0.5, stroke: "var(--acc)", strokeWidth: 1 }}
          onBrushEnd={(bounds) => {
            if (!bounds) return;
            const next = clampWindow(bounds.x0, bounds.x1, durationMs);
            if (next) onZoom(next);
          }}
        />
      </Group>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Labels, legend, list
// ---------------------------------------------------------------------------

function LaneLabels({ rows, layout }: { rows: TimelineModel["rows"]; layout: TimelineLayout }) {
  const plain = "flex items-center text-xs text-mut";
  return (
    <div className="flex flex-col" style={{ height: layout.total }}>
      <span className={plain} style={{ height: H_PHASE }}>
        Phases
      </span>
      {rows.map((row) => (
        <span key={row.mechanicId} className="flex min-w-0 items-center gap-1.5 text-xs" style={{ height: H_MECH }}>
          <IconFallback size={16} className={row.failures ? "" : "opacity-60"} />
          <span className={cn("min-w-0 flex-1 truncate", row.failures ? "font-medium" : "text-mut")} title={row.name}>
            {row.name}
          </span>
          {row.failures > 0 && (
            <span className="shrink-0 font-semibold text-bad">
              {row.failures}
              <span className="sr-only"> failures</span>
            </span>
          )}
        </span>
      ))}
      <span className={plain} style={{ height: H_DEATH }}>
        Deaths
      </span>
      <span className={plain} style={{ height: H_CD }}>
        Cooldowns
      </span>
      <span className={plain} style={{ height: H_WINDOW }}>
        Windows
      </span>
    </div>
  );
}

function Legend() {
  const item = "inline-flex items-center gap-1.5";
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-mut">
      <span className={item}>
        <span className="inline-block h-3.5 w-2.5 rounded-sm bg-bad" />
        Cluster with failures
      </span>
      <span className={item}>
        <span className="inline-block h-3.5 w-2.5 rounded-sm bg-line" />
        Cluster, no failures
      </span>
      <span className={item}>
        <span className="inline-block h-3.5 w-[3px] bg-bad" />
        Death before cutoff
      </span>
      <span className={item}>
        <span className="inline-block h-3.5 w-[3px] bg-mut opacity-50" />
        Death after cutoff
      </span>
      <span className={item}>
        <IconFallback size={16} />
        Cooldown use, at the cast time
      </span>
      <span className={item}>
        <span className="rounded-sm bg-okt px-1.5 font-semibold text-ok">✓ Covered</span>
        <span className="rounded-sm bg-badt px-1.5 font-semibold text-bad">✗ Missed</span>
        Damage window
      </span>
      <span className={item}>
        <span className="inline-block h-2.5 w-3.5 rounded-sm border-2 border-acc" />
        Selected
      </span>
    </div>
  );
}

function MarkerList({
  ordered,
  selectedRef,
  activeId,
}: {
  ordered: Marker[];
  selectedRef: string | null;
  activeId: string | null;
}) {
  return (
    <details className="text-[13px]">
      <summary className="cursor-pointer font-medium text-info">Markers as a list (same order the arrow keys follow)</summary>
      <ol className="m-0 mt-2 grid list-none grid-cols-2 gap-x-6 gap-y-0.5 p-0">
        {ordered.map((m) => {
          const selected = m.ref === selectedRef;
          return (
            <li
              key={m.id}
              aria-current={selected ? "true" : undefined}
              className={cn(
                "grid grid-cols-[56px_minmax(0,1fr)] gap-2 rounded-sm px-1.5 py-0.5",
                selected && "bg-acct",
                m.id === activeId && "outline-2 -outline-offset-2 outline-info",
              )}
            >
              <span className="font-mono text-mut">{formatClockTenths(m.tMs)}</span>
              <span>
                {m.listText}
                {selected && <span className="text-mut"> (selected)</span>}
              </span>
            </li>
          );
        })}
      </ol>
    </details>
  );
}

// ---------------------------------------------------------------------------
// Marker buttons
// ---------------------------------------------------------------------------

interface ButtonState {
  selected: boolean;
  tabbable: boolean;
  onSelectMarker: (m: Marker) => void;
  onFocusMarker: (id: string) => void;
}

const RING = "shadow-[0_0_0_3px_var(--acc),0_0_0_7px_var(--acct)]";
const FOCUS_RING = "focus-visible:outline-2 focus-visible:outline-offset-[3px] focus-visible:outline-info";

function pct(scale: ReturnType<typeof makeTimeScale>, ms: number): string {
  return `${scale.toPct(ms)}%`;
}

function MechanicButtonImpl({
  m,
  top,
  scale,
  selected,
  tabbable,
  onSelectMarker,
  onFocusMarker,
}: ButtonState & { m: MechanicMark; top: number; scale: ReturnType<typeof makeTimeScale> }) {
  const failed = m.failures > 0;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          tabIndex={tabbable ? 0 : -1}
          aria-label={m.label}
          aria-pressed={selected}
          onClick={() => onSelectMarker(m)}
          onFocus={() => onFocusMarker(m.id)}
          data-marker={m.id}
          data-lane="mechanic"
          className={cn(
            "pointer-events-auto absolute min-w-[8px] rounded-sm border-0 p-0",
            FOCUS_RING,
            failed ? "bg-bad opacity-90" : "bg-line",
            selected && RING,
          )}
          style={{
            top: top + (failed ? 3 : 8),
            height: failed ? 18 : 8,
            left: pct(scale, m.tMs),
            width: `max(8px, ${scale.toPct(Math.min(m.endMs, scale.durationMs)) - scale.toPct(m.tMs)}%)`,
          }}
        />
      </TooltipTrigger>
      <TooltipContent>
        <MarkerTip marker={m} />
      </TooltipContent>
    </Tooltip>
  );
}

function DeathButtonImpl({
  m,
  top,
  scale,
  code,
  selected,
  tabbable,
  onSelectMarker,
  onFocusMarker,
}: ButtonState & { m: DeathMark; top: number; scale: ReturnType<typeof makeTimeScale>; code: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          tabIndex={tabbable ? 0 : -1}
          aria-label={m.label}
          aria-pressed={selected}
          onClick={() => onSelectMarker(m)}
          onFocus={() => onFocusMarker(m.id)}
          data-marker={m.id}
          data-lane="death"
          className="group pointer-events-auto absolute flex w-2 -translate-x-1/2 items-end justify-center border-0 bg-transparent p-0 outline-none"
          style={{ top: top + 2, height: H_DEATH - 6, left: pct(scale, m.tMs) }}
        >
          <span
            className={cn(
              "block w-[3px] rounded-[1px]",
              m.afterCutoff ? "h-[22px] bg-mut opacity-50" : "h-8 bg-bad",
              "group-focus-visible:outline-2 group-focus-visible:outline-offset-[3px] group-focus-visible:outline-info",
              selected && RING,
            )}
          />
        </button>
      </TooltipTrigger>
      <TooltipContent>
        <MarkerTip marker={m} code={code} />
      </TooltipContent>
    </Tooltip>
  );
}

function initials(spell: string): string {
  const words = spell.split(/[\s'-]+/).filter(Boolean);
  return (words.length > 1 ? words[0]!.charAt(0) + words[1]!.charAt(0) : spell.slice(0, 2)).toUpperCase();
}

function CooldownButtonImpl({
  m,
  top,
  scale,
  code,
  selected,
  tabbable,
  onSelectMarker,
  onFocusMarker,
}: ButtonState & { m: CooldownMark; top: number; scale: ReturnType<typeof makeTimeScale>; code: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          tabIndex={tabbable ? 0 : -1}
          aria-label={m.label}
          aria-pressed={selected}
          onClick={() => onSelectMarker(m)}
          onFocus={() => onFocusMarker(m.id)}
          data-marker={m.id}
          data-lane="cooldown"
          data-initials={initials(m.spell)}
          className={cn(
            "pointer-events-auto absolute flex h-[22px] w-[22px] -translate-x-1/2 items-center justify-center rounded-sm border border-line bg-s3 p-0",
            "font-mono text-[9px] font-semibold text-mut before:content-[attr(data-initials)] hover:border-info",
            FOCUS_RING,
            selected && RING,
          )}
          style={{ top: top + (m.tier ? 25 : 3), left: pct(scale, m.tMs) }}
        >
        </button>
      </TooltipTrigger>
      <TooltipContent className="w-72 max-w-72 border border-line bg-s1 p-3 text-ink shadow-lg">
        <MarkerTip marker={m} code={code} />
      </TooltipContent>
    </Tooltip>
  );
}

function WindowButtonImpl({
  m,
  top,
  scale,
  selected,
  tabbable,
  onSelectMarker,
  onFocusMarker,
}: ButtonState & { m: WindowMark; top: number; scale: ReturnType<typeof makeTimeScale> }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          tabIndex={tabbable ? 0 : -1}
          aria-label={m.label}
          aria-pressed={selected}
          onClick={() => onSelectMarker(m)}
          onFocus={() => onFocusMarker(m.id)}
          data-marker={m.id}
          data-lane="window"
          data-text={`${m.covered ? "✓" : "✗"} ${m.short} ${m.occurrence}`}
          className={cn(
            "pointer-events-auto absolute inline-flex h-5 after:content-[attr(data-text)] items-center gap-1 whitespace-nowrap rounded-sm border-0 py-0 pr-1.5 pl-[3px] text-[11px] font-semibold",
            m.covered ? "bg-okt text-ok" : "bg-badt text-bad shadow-[inset_0_0_0_1px_var(--bad)]",
            FOCUS_RING,
            selected && RING,
          )}
          style={{ top: top + 4, left: pct(scale, m.tMs) }}
        >
          <IconFallback size={16} className="align-middle" />
        </button>
      </TooltipTrigger>
      <TooltipContent>
        <MarkerTip marker={m} />
      </TooltipContent>
    </Tooltip>
  );
}

const MechanicButton = memo(MechanicButtonImpl);

const DeathButton = memo(DeathButtonImpl);

const CooldownButton = memo(CooldownButtonImpl);

const WindowButton = memo(WindowButtonImpl);
