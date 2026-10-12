"""
encounter/annotate.py

Annotation: tag a pull's normalized events with what the encounter file says they mean.

Added columns:

    phase_id           Utf8   phase active at t_ms (null before the pull or without an encounter file)
    mechanic_id        Utf8   mechanic owning ability_id (any event type)
    cluster_id         Int32  1-based cluster within the mechanic; set on hits only
    is_failure         Bool   row is a failure under the mechanic's rule (plan 6.2)
    blamed_id          Int32  on failure rows, the player charged with the failure: the player hit for most
                              rules, the player a ``blame`` spec points at for ``per_cluster``; null if no one

Who an NPC is attacking comes from the Threat data (``aggro_events``): its melee swings name its target,
and a taunt debuff landing on it hands it to the taunting player. ``per_cluster`` blame and
``hits_on_non_tank`` read the NPC's latest such event.
    window_id          Utf8   damage window owning ability_id; set on hits only
    window_occurrence  Int32  1-based occurrence of the window; set on hits only

A "hit" is a ``damage`` event on a friendly target. Clusters are consecutive hits of one mechanic
(or window) whose gap is at most its ``cluster_window_ms``. The wipe cutoff is the time cumulative
player deaths reach ``ceil(cutoff_fraction * raid_size)``; kills have no cutoff.

Annotated frames are cached under ``$KHADBOT_DATA_DIR/annotated/<report>/<fight>__<encfile_hash>.parquet``
with a JSON sidecar holding the phases, cutoff, the ``events_hash`` they were built from and
``ANNOTATION_VERSION``, which is bumped whenever the columns or their rules change.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

import orjson
import polars as pl

from khadbot.encounter.config_files import Blame, EncounterFile, Mechanic, encfile_hash
from khadbot.encounter.models import Actor, PhaseSpan, Pull
from khadbot.encounter.phases import boss_ids_of, detect_phases, unmatched_phase
from khadbot.encounter.store import EventStore, split_pull_key

ANNOTATION_COLUMNS: dict[str, pl.DataType] = {
    "phase_id": pl.Utf8(),
    "mechanic_id": pl.Utf8(),
    "cluster_id": pl.Int32(),
    "is_failure": pl.Boolean(),
    "blamed_id": pl.Int32(),
    "window_id": pl.Utf8(),
    "window_occurrence": pl.Int32(),
}

# Interrupt matching tolerance for not_interrupted: an interrupt 0-100 ms before the cast counts.
INTERRUPT_TOLERANCE_MS = 100

# Bump when annotation columns or failure rules change, so cached frames are rebuilt.
ANNOTATION_VERSION = 3


@dataclass(frozen=True)
class Annotated:
    pull: Pull
    frame: pl.DataFrame
    phases: list[PhaseSpan]
    cutoff_ms: int | None
    encfile_hash: str
    unmatched_phase: str | None = None

    @property
    def hits(self) -> pl.DataFrame:
        """Damage events on friendly targets."""
        return self.frame.filter(is_hit())


def is_hit() -> pl.Expr:
    return (pl.col("type").cast(pl.Utf8) == "damage") & pl.col("target_friendly")


def type_is(*types: str) -> pl.Expr:
    return pl.col("type").cast(pl.Utf8).is_in(list(types))


# ---------------------------------------------------------------------------
# Column builders
# ---------------------------------------------------------------------------


def _phase_expr(phases: list[PhaseSpan]) -> pl.Expr:
    expr: pl.Expr = pl.lit(None, dtype=pl.Utf8)
    for span in phases:  # later phases override earlier ones
        expr = pl.when(pl.col("t_ms") >= span.start_ms).then(pl.lit(span.phase_id)).otherwise(expr)
    return expr.alias("phase_id")


def _ability_map(groups: list[tuple[str, list[int]]], column: str) -> pl.Expr:
    mapping = {ability_id: gid for gid, ids in groups for ability_id in ids}
    if not mapping:
        return pl.lit(None, dtype=pl.Utf8).alias(column)
    return pl.col("ability_id").replace_strict(mapping, default=None, return_dtype=pl.Utf8).alias(column)


def _clusters(frame: pl.DataFrame, group_col: str, windows: dict[str, int], out_col: str) -> pl.DataFrame:
    """``seq`` -> 1-based gap cluster per group, over hits that have ``group_col`` set."""
    hits = frame.filter(is_hit() & pl.col(group_col).is_not_null()).select("seq", "t_ms", group_col)
    if hits.is_empty():
        return pl.DataFrame(schema={"seq": pl.UInt32(), out_col: pl.Int32()})
    gap = pl.col(group_col).replace_strict(windows, return_dtype=pl.Int32)
    new_cluster = (pl.col("t_ms").diff().over(group_col) > gap).fill_null(True)
    return hits.sort("seq").select(
        "seq", new_cluster.cast(pl.Int32).cum_sum().over(group_col).cast(pl.Int32).alias(out_col)
    )


def _victims(rows: pl.DataFrame) -> dict[int, int | None]:
    """Failure rows charged to the friendly player they hit (None for a non-friendly target)."""
    return {
        seq: (target if friendly else None)
        for seq, target, friendly in rows.select("seq", "target_id", "target_friendly").rows()
    }


def aggro_events(frame: pl.DataFrame) -> pl.DataFrame:
    """
    Threat rows that give an NPC a target or end it, in ``seq`` order: ``seq``, ``t_ms``, ``npc_id``,
    ``npc_instance``, ``player_id`` and ``kind``. ``melee`` is an NPC swing at a player, ``taunt`` a taunt
    debuff landing on the NPC, ``death`` the NPC dying (``player_id`` null).
    """
    threat = frame.filter(pl.col("data_type").cast(pl.Utf8) == "Threat")
    cols = ("seq", "t_ms")
    swings = threat.filter(type_is("cast") & ~pl.col("source_friendly") & pl.col("target_friendly")).select(
        *cols,
        pl.col("source_id").alias("npc_id"),
        pl.col("source_instance").alias("npc_instance"),
        pl.col("target_id").alias("player_id"),
        pl.lit("melee").alias("kind"),
    )
    taunts = threat.filter(type_is("applydebuff") & pl.col("source_friendly") & ~pl.col("target_friendly")).select(
        *cols,
        pl.col("target_id").alias("npc_id"),
        pl.col("target_instance").alias("npc_instance"),
        pl.col("source_id").alias("player_id"),
        pl.lit("taunt").alias("kind"),
    )
    deaths = threat.filter(type_is("death") & ~pl.col("target_friendly")).select(
        *cols,
        pl.col("target_id").alias("npc_id"),
        pl.col("target_instance").alias("npc_instance"),
        pl.lit(None, dtype=pl.Int32).alias("player_id"),
        pl.lit("death").alias("kind"),
    )
    return pl.concat([swings, taunts, deaths]).sort("seq")


def _aggro_holder(events: pl.DataFrame, npc_ids: set[int], t_ms: int, ms: int) -> int | None:
    """The player behind the latest swing or taunt on any of ``npc_ids`` in the ``ms`` up to ``t_ms``."""
    recent = events.filter(pl.col("npc_id").is_in(npc_ids) & pl.col("t_ms").is_between(t_ms - ms, t_ms))
    return recent["player_id"][-1] if recent.height else None  # null after the NPC's death


def _blamed_player(frame: pl.DataFrame, events: pl.DataFrame, blame: Blame, t0: int, npc_ids: set[int]) -> int | None:
    """The player ``blame`` points at for an occurrence whose first hit is at ``t0``; None if nobody."""
    if blame.by == "aggro_holder":
        return _aggro_holder(events, npc_ids, t0, blame.ms)
    cand = frame.filter(
        pl.col("t_ms").is_between(t0 - blame.ms, t0)
        & pl.col("target_friendly")
        & type_is("cast")
        & ~pl.col("source_friendly")
        & (pl.col("ability_id") == blame.ability_id)
    )
    return cand.sort("seq")["target_id"][-1] if cand.height else None


def _failures(
    frame: pl.DataFrame, mech: Mechanic, duration_ms: int, game_ids: dict[int, int | None], aggro: pl.DataFrame
) -> dict[int, int | None]:
    """Failure rows under ``mech.failure``: row ``seq`` -> the player charged with it (None if no one)."""
    if mech.failure is None:
        return {}
    rule = mech.failure
    rows = frame.filter(pl.col("mechanic_id") == mech.id)
    hits = rows.filter(is_hit())

    if rule.rule == "any_hit":
        return _victims(hits)

    if rule.rule == "hits_per_cluster_gt":
        # One failure per (player, cluster): the hit that takes the player past `value`.
        nth = hits.sort("seq").with_columns(pl.int_range(1, pl.len() + 1).over("cluster_id", "target_id").alias("_n"))
        return _victims(nth.filter(pl.col("_n") == (rule.value or 0) + 1))

    if rule.rule == "soak_count_lt":
        per_cluster = hits.group_by("cluster_id").agg(
            pl.col("target_id").n_unique().alias("_players"), pl.col("seq").min().alias("_first")
        )
        return dict.fromkeys(per_cluster.filter(pl.col("_players") < (rule.value or 0))["_first"].to_list())

    if rule.rule == "not_interrupted":
        # Stateful: small Python loop over this mechanic's enemy casts and matching interrupts only.
        ids = mech.ability_ids
        casts = rows.filter(type_is("cast") & ~pl.col("source_friendly")).select("seq", "t_ms").rows()
        kicks = frame.filter(type_is("interrupt") & pl.col("extra_ability_id").is_in(ids))["t_ms"].to_list()
        return {seq: None for seq, t in casts if not any(t - INTERRUPT_TOLERANCE_MS <= k <= t for k in kicks)}

    if rule.rule == "debuff_duration_gt":
        auras = rows.filter(type_is("applydebuff", "removedebuff") & pl.col("target_friendly")).sort("seq")
        open_since: dict[tuple[int, int], tuple[int, int]] = {}
        failures: dict[int, int | None] = {}
        limit = rule.ms or 0
        for seq, t, typ, target, ability in auras.select(
            "seq", "t_ms", pl.col("type").cast(pl.Utf8), "target_id", "ability_id"
        ).rows():
            key = (target, ability)
            if typ == "applydebuff":
                open_since.setdefault(key, (seq, t))
            elif key in open_since:
                apply_seq, apply_t = open_since.pop(key)
                if t - apply_t > limit:
                    failures[apply_seq] = target
        failures |= {seq: target for (target, _), (seq, t) in open_since.items() if duration_ms - t > limit}
        return failures

    if rule.rule == "hit_amount_gt":
        size = pl.coalesce("unmitigated", pl.col("amount").fill_null(0) + pl.col("absorbed").fill_null(0))
        return _victims(hits.filter(size > (rule.value or 0)))

    if rule.rule == "aura_applied":
        return _victims(rows.filter(type_is("applydebuff", "applybuff") & pl.col("target_friendly")))

    if rule.rule == "hits_on_non_tank":
        # Each player is judged once per cluster, at their first hit, against the aggro holder of the NPC
        # instance that hit them: the latest swing or taunt on it within `ms` before. Later ticks don't
        # count. A player hit while the NPC had no holder is a failure too.
        holders = (
            aggro.filter(pl.col("kind") != "death")
            .select(
                pl.col("npc_id").alias("source_id"),
                pl.col("npc_instance").fill_null(-1).alias("_inst"),
                pl.col("t_ms").alias("_at"),
                pl.col("player_id").alias("_holder"),
            )
            .sort("_at")
        )
        first_hits = hits.sort("seq").filter(pl.int_range(1, pl.len() + 1).over("cluster_id", "target_id") == 1)
        judged = (
            first_hits.with_columns(pl.col("source_instance").fill_null(-1).alias("_inst"))
            .sort("t_ms")
            .join_asof(
                holders,
                left_on="t_ms",
                right_on="_at",
                by=["source_id", "_inst"],
                strategy="backward",
                check_sortedness=False,
            )
        )
        is_tank = (pl.col("_holder") == pl.col("target_id")) & (pl.col("t_ms") - pl.col("_at") <= (rule.ms or 0))
        return _victims(judged.filter(~is_tank.fill_null(False)))

    if rule.rule == "per_cluster":
        # One failure per occurrence, on its first hit, charged to the player the blame spec points at.
        blame = rule.blame
        assert blame is not None  # enforced by FailureRule
        npc_ids = {a for a, g in game_ids.items() if g == blame.npc_game_id}
        firsts = hits.group_by("cluster_id").agg(pl.col("seq").min(), pl.col("t_ms").min())
        return {
            seq: _blamed_player(frame, aggro, blame, t0, npc_ids) for seq, t0 in firsts.select("seq", "t_ms").rows()
        }

    return {}  # pragma: no cover - RuleName is exhaustive


def wipe_cutoff(pull: Pull, frame: pl.DataFrame, cutoff_fraction: float) -> int | None:
    if pull.kill or not pull.friendly_player_ids:
        return None
    needed = math.ceil(cutoff_fraction * len(pull.friendly_player_ids))
    deaths = (
        frame.filter(type_is("death") & pl.col("target_id").is_in(pull.friendly_player_ids))
        .sort("seq")["t_ms"]
        .to_list()
    )
    return int(deaths[needed - 1]) if needed >= 1 and len(deaths) >= needed else None


# ---------------------------------------------------------------------------
# annotate
# ---------------------------------------------------------------------------


def annotate(
    pull: Pull,
    events: pl.DataFrame,
    enc: EncounterFile | None,
    actors: list[Actor],
    *,
    cutoff_fraction: float = 0.5,
) -> Annotated:
    """Tag ``events`` with phases, mechanics, clusters, failures and damage windows."""
    frame = events.sort("seq")
    phases = detect_phases(pull, frame, enc, boss_ids_of(actors)) if enc else []
    mechanics = enc.mechanics if enc else []
    windows = enc.damage_windows if enc else []

    frame = frame.with_columns(
        _phase_expr(phases),
        _ability_map([(m.id, m.ability_ids) for m in mechanics], "mechanic_id"),
        _ability_map([(w.id, w.ability_ids) for w in windows], "window_id"),
    )
    mech_clusters = _clusters(frame, "mechanic_id", {m.id: m.cluster_window_ms for m in mechanics}, "cluster_id")
    win_clusters = _clusters(frame, "window_id", {w.id: w.cluster_window_ms for w in windows}, "window_occurrence")
    frame = (
        frame.join(mech_clusters, on="seq", how="left")
        .join(win_clusters, on="seq", how="left")
        .sort("seq")
        .with_columns(
            # window_id stays only on window hits; casts of the ability are not occurrences.
            pl.when(pl.col("window_occurrence").is_not_null()).then(pl.col("window_id")).alias("window_id")
        )
    )

    game_ids = {a.actor_id: a.game_id for a in actors}
    failures: dict[int, int | None] = {}
    aggro = aggro_events(frame)
    for mech in mechanics:
        failures |= _failures(frame, mech, pull.duration_ms, game_ids, aggro)
    blamed = pl.DataFrame(
        {"seq": list(failures), "blamed_id": list(failures.values())},
        schema={"seq": frame.schema["seq"], "blamed_id": pl.Int32()},
    )
    frame = (
        frame.join(blamed, on="seq", how="left")
        .sort("seq")
        .with_columns(pl.col("seq").is_in(list(failures)).alias("is_failure"))
    )
    frame = frame.select(*events.columns, *[pl.col(c).cast(t) for c, t in ANNOTATION_COLUMNS.items()])

    return Annotated(
        pull=pull,
        frame=frame,
        phases=phases,
        cutoff_ms=wipe_cutoff(pull, frame, cutoff_fraction),
        encfile_hash=encfile_hash(enc),
        unmatched_phase=unmatched_phase(phases, enc) if enc else None,
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class AnnotationCache:
    """Annotated frames on disk, keyed by pull and ``encfile_hash``; stale if events or cutoff changed."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _paths(self, pull: Pull, enc_hash: str) -> tuple[Path, Path]:
        base = self.root / pull.report_code / f"{pull.fight_id}__{enc_hash}"
        return base.with_suffix(".parquet"), base.with_suffix(".json")

    def get(self, pull: Pull, enc_hash: str, events_hash: str, cutoff_fraction: float) -> Annotated | None:
        frame_path, meta_path = self._paths(pull, enc_hash)
        if not (frame_path.exists() and meta_path.exists()):
            return None
        meta = orjson.loads(meta_path.read_bytes())
        if (
            meta.get("events_hash") != events_hash
            or meta.get("cutoff_fraction") != cutoff_fraction
            or meta.get("annotation_version") != ANNOTATION_VERSION
        ):
            return None
        return Annotated(
            pull=pull,
            frame=pl.read_parquet(frame_path),
            phases=[PhaseSpan.model_validate(p) for p in meta["phases"]],
            cutoff_ms=meta["cutoff_ms"],
            encfile_hash=enc_hash,
            unmatched_phase=meta.get("unmatched_phase"),
        )

    def put(self, a: Annotated, events_hash: str, cutoff_fraction: float) -> None:
        frame_path, meta_path = self._paths(a.pull, a.encfile_hash)
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "events_hash": events_hash,
            "cutoff_fraction": cutoff_fraction,
            "annotation_version": ANNOTATION_VERSION,
            "cutoff_ms": a.cutoff_ms,
            "phases": [p.model_dump() for p in a.phases],
            "unmatched_phase": a.unmatched_phase,
        }
        for path, write in (
            (frame_path, lambda tmp: a.frame.write_parquet(tmp)),
            (meta_path, lambda tmp: tmp.write_bytes(orjson.dumps(meta))),
        ):
            tmp = path.with_name(path.name + ".tmp")
            try:
                write(tmp)
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)


def annotate_pull(
    pull_key: str,
    store: EventStore,
    enc: EncounterFile | None,
    *,
    cache: AnnotationCache | None = None,
    cutoff_fraction: float = 0.5,
) -> Annotated:
    """Annotate one stored pull, reusing ``cache`` when it holds a fresh result."""
    code, fight = split_pull_key(pull_key)
    pull = next((p for p in store.pulls(code) if p.fight_id == fight), None)
    if pull is None:
        raise KeyError(f"pull {pull_key} is not in the event store; ingest it first")
    ev_hash = store.events_hash(pull_key) or ""
    enc_hash = encfile_hash(enc)
    if cache and (hit := cache.get(pull, enc_hash, ev_hash, cutoff_fraction)) is not None:
        return hit
    a = annotate(pull, store.events(pull_key), enc, store.actors(code), cutoff_fraction=cutoff_fraction)
    if cache:
        cache.put(a, ev_hash, cutoff_fraction)
    return a
