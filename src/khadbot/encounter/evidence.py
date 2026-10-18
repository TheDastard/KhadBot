"""
encounter/evidence.py

Evidence refs: parsing and resolving them, and reading the numbers out of a claim so they can be
checked against the records the refs point at (the verify rules in analyst/verify.py).

Ref kinds::

    ev:<report>:<fight>:death:<n>            n = 1-based, time order
    ev:<report>:<fight>:mech:<mechanic_id>
    ev:<report>:<fight>:cd:<spell_id>:<n>    n = 1-based per spell
    ev:<report>:<fight>:win:<window_id>:<n>  n = 1-based occurrence
    ev:<report>:<fight>:event:<seq>
    ev:<report>:<fight>:pull
    plan:<plan_id>:<assignment_index>        reserved for raid plans; not resolvable yet

``EncounterData`` is the read side the analyst works from. It builds the records for a pull (cached
in-process, keyed by pull and content hashes) and resolves refs against them. A ref resolves to the
same JSON-safe dict a query tool returned for it, so a claim is checked against what the model saw.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import polars as pl

from khadbot.config import EncounterConfig
from khadbot.encounter.analysis.mechanics import blame_reach_yd, mechanic_clusters
from khadbot.encounter.annotate import AnnotationCache
from khadbot.encounter.config_files import CooldownRegistry, encfile_hash, load_encounter_view, load_registry
from khadbot.encounter.digest import PullRecords, build_pull_records, default_annotation_cache
from khadbot.encounter.models import MechanicStat, Pull, PullDigest, Ref, actor_label, make_ref
from khadbot.encounter.store import EventStore, ParquetEventStore, split_pull_key

__all__ = [
    "ClaimNumber",
    "EncounterData",
    "ParsedRef",
    "UnresolvableRef",
    "claim_numbers",
    "event_row",
    "extract_numbers",
    "make_ref",
    "number_supported",
    "numeric_values",
    "parse_ref",
    "pull_summary",
    "resolve",
]


class UnresolvableRef(Exception):
    """A ref is malformed, or names a record that does not exist in the current data."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

RefKind = Literal["death", "mech", "cd", "win", "event", "pull", "plan"]

# Number of parts after ``ev:<report>:<fight>:<kind>``.
_SHAPES: dict[str, int] = {"death": 1, "mech": 1, "cd": 2, "win": 2, "event": 1, "pull": 0}


@dataclass(frozen=True)
class ParsedRef:
    raw: str
    kind: RefKind
    pull_key: str | None  # None for plan refs
    key: str | None = None  # mechanic id, window id, or plan id
    n: int | None = None  # death index, occurrence, event seq, or assignment index
    spell_id: int | None = None


def _int(text: str, ref: str, minimum: int) -> int:
    try:
        value = int(text)
    except ValueError:
        raise UnresolvableRef(f"malformed ref {ref}") from None
    if value < minimum:
        raise UnresolvableRef(f"malformed ref {ref}")
    return value


def parse_ref(ref: str) -> ParsedRef:
    """Split a ref into its parts. Raises ``UnresolvableRef`` if it does not have a known shape."""
    parts = ref.strip().split(":")
    if parts[0] == "plan":
        if len(parts) != 3 or not parts[1]:
            raise UnresolvableRef(f"malformed ref {ref}")
        return ParsedRef(ref, "plan", None, key=parts[1], n=_int(parts[2], ref, 0))
    if parts[0] != "ev" or len(parts) < 4 or not parts[1]:
        raise UnresolvableRef(f"malformed ref {ref}")
    report, fight, kind, rest = parts[1], parts[2], parts[3], parts[4:]
    if kind not in _SHAPES or len(rest) != _SHAPES[kind] or not all(rest):
        raise UnresolvableRef(f"malformed ref {ref}")
    pull_key = f"{report}:{_int(fight, ref, 0)}"
    if kind == "death":
        return ParsedRef(ref, "death", pull_key, n=_int(rest[0], ref, 1))
    if kind == "mech":
        return ParsedRef(ref, "mech", pull_key, key=rest[0])
    if kind == "cd":
        return ParsedRef(ref, "cd", pull_key, spell_id=_int(rest[0], ref, 0), n=_int(rest[1], ref, 1))
    if kind == "win":
        return ParsedRef(ref, "win", pull_key, key=rest[0], n=_int(rest[1], ref, 1))
    if kind == "event":
        return ParsedRef(ref, "event", pull_key, n=_int(rest[0], ref, 0))
    return ParsedRef(ref, "pull", pull_key)


# ---------------------------------------------------------------------------
# Record views shared by resolve() and the query tools
# ---------------------------------------------------------------------------

# Event columns shown to the model, in display order. Actor IDs are replaced by names.
_EVENT_FIELDS = (
    "ability_id",
    "extra_ability_id",
    "amount",
    "absorbed",
    "overkill",
    "overheal",
    "hp_pct",
    "stack",
    "mechanic_id",
    "window_id",
    "phase_id",
)


def event_row(r: PullRecords, row: dict[str, Any]) -> dict[str, Any]:
    """One annotated event as the model sees it; null fields are dropped."""
    names = {a.actor_id: a.name for a in r.actors}
    out: dict[str, Any] = {
        "ref": make_ref(r.pull.pull_key, "event", row["seq"]),
        "seq": row["seq"],
        "t_ms": row["t_ms"],
        "type": row["type"],
    }
    if row["source_id"] is not None:
        out["source"] = actor_label(names, row["source_id"], row.get("source_instance"))
    if row["target_id"] is not None:
        out["target"] = actor_label(names, row["target_id"], row.get("target_instance"))
    if row["ability_id"] is not None and row["ability_id"] in r.abilities:
        out["ability"] = r.abilities[row["ability_id"]]
    for field in _EVENT_FIELDS:
        value = row.get(field)
        if value is None:
            continue
        out[field] = round(value, 1) if field == "hp_pct" else value
    if row.get("is_failure"):
        out["failure"] = True
        if (blamed := row.get("blamed_id")) is not None:
            out["blamed"] = names.get(blamed, f"actor-{blamed}")
    return out


def pull_summary(r: PullRecords) -> dict[str, Any]:
    """What a ``pull`` ref resolves to: outcome, length, progress, death counts and aggro."""
    d = r.digest
    return {
        "ref": make_ref(d.pull_key, "pull"),
        "pull_key": d.pull_key,
        "encounter_name": d.encounter_name,
        "outcome": d.outcome,
        "duration_ms": d.duration_ms,
        "boss_pct": d.boss_pct,
        "boss_hp_pct": d.boss_hp_pct,
        "phase_reached": d.phase_reached,
        "cutoff_ms": d.cutoff_ms,
        "deaths": len(r.deaths),
        "deaths_before_cutoff": sum(1 for x in r.deaths if not x.after_cutoff),
        "coverage": d.coverage,
        "anomalies": list(d.anomalies),
        "aggro": [g.model_dump(mode="json") for g in d.aggro],
    }


def mechanic_detail(r: PullRecords, stat: MechanicStat) -> dict[str, Any]:
    """What a ``mech`` ref resolves to: the stat plus one row per cluster (with blame context where it has one)."""
    reach = blame_reach_yd(r.enc, stat.mechanic_id)
    clusters = mechanic_clusters(r.annotated, stat.mechanic_id, r.actors, reach_yd=reach)
    return {**stat.model_dump(mode="json"), "cluster_rows": [c.model_dump(mode="json") for c in clusters]}


# ---------------------------------------------------------------------------
# EncounterData
# ---------------------------------------------------------------------------


class EncounterData:
    """
    Read-only access to analysed pulls: records per pull, cached in-process, and ref resolution.

    The cache key is (pull_key, events_hash, positions_hash, encfile_hash), so re-ingesting a pull or editing
    its encounter file rebuilds the records on next use.
    """

    def __init__(
        self,
        store: EventStore,
        *,
        enc_root: Path | None = None,
        registry: CooldownRegistry | None = None,
        cache: AnnotationCache | None = None,
        cutoff_fraction: float | None = None,
        max_cached: int = 32,
    ) -> None:
        self.store = store
        self.enc_root = enc_root
        self.registry = registry or load_registry()
        self.cache = cache
        self.cutoff_fraction = cutoff_fraction
        self._max_cached = max_cached
        self._records: OrderedDict[tuple[str, str, str, str], PullRecords] = OrderedDict()

    @classmethod
    def from_config(cls) -> EncounterData:
        """Store and annotation cache under ``KHADBOT_DATA_DIR``, authored files from ``config/``."""
        root = Path(EncounterConfig().data_dir)
        return cls(ParquetEventStore(root / "events"), cache=default_annotation_cache())

    def pull(self, pull_key: str) -> Pull | None:
        try:
            code, fight = split_pull_key(pull_key)
        except ValueError:
            return None
        return next((p for p in self.store.pulls(code) if p.fight_id == fight), None)

    def records(self, pull_key: str) -> PullRecords:
        """Uncapped analysis records for an ingested pull. Raises ``KeyError`` if it is not ingested."""
        pull = self.pull(pull_key)
        if pull is None:
            raise KeyError(f"pull {pull_key} is not in the event store; ingest it first")
        pull_key = pull.pull_key
        key = (
            pull_key,
            self.store.events_hash(pull_key) or "",
            self.store.positions_hash(pull_key) or "",
            encfile_hash(load_encounter_view(pull.encounter_id, pull.difficulty, self.enc_root)),
        )
        if (hit := self._records.get(key)) is not None:
            self._records.move_to_end(key)
            return hit
        records = build_pull_records(
            pull_key, self.store, self.enc_root, self.registry, cache=self.cache, cutoff_fraction=self.cutoff_fraction
        )
        self._records[key] = records
        while len(self._records) > self._max_cached:
            self._records.popitem(last=False)
        return records

    def digest(self, pull_key: str) -> PullDigest:
        return self.records(pull_key).digest

    def resolve(self, ref: Ref) -> dict[str, Any]:
        """The record a ref names, as JSON-safe data. Raises ``UnresolvableRef``."""
        p = parse_ref(ref)
        if p.kind == "plan" or p.pull_key is None:
            raise UnresolvableRef(f"plan refs cannot be resolved yet: {ref}")
        try:
            r = self.records(p.pull_key)
        except KeyError:
            raise UnresolvableRef(f"unknown pull in ref {ref}") from None

        if p.kind == "pull":
            return pull_summary(r)
        if p.kind == "death" and p.n is not None and p.n <= len(r.deaths):
            return r.deaths[p.n - 1].model_dump(mode="json")
        if p.kind == "mech":
            stat = next((m for m in r.mechanics if m.mechanic_id == p.key), None)
            if stat is not None:
                return mechanic_detail(r, stat)
        if p.kind == "cd" and p.n is not None:
            uses = [c for c in r.cooldowns if c.spell_id == p.spell_id]
            if p.n <= len(uses):
                return uses[p.n - 1].model_dump(mode="json")
        if p.kind == "win":
            win = next((w for w in r.windows if w.window_id == p.key and w.occurrence == p.n), None)
            if win is not None:
                return win.model_dump(mode="json")
        if p.kind == "event":
            rows = r.annotated.frame.filter(pl.col("seq") == p.n)
            if rows.height:
                return event_row(r, rows.row(0, named=True))
        raise UnresolvableRef(f"no record for ref {ref}")


def resolve(ref: Ref, data: EncounterData | EventStore) -> dict[str, Any]:
    """Resolve ``ref`` against ``data``; a bare store uses the authored files under ``config/``."""
    if not isinstance(data, EncounterData):
        data = EncounterData(data)
    return data.resolve(ref)


# ---------------------------------------------------------------------------
# Numbers in claims
# ---------------------------------------------------------------------------

_REF_IN_TEXT = re.compile(r"\b(?:ev|plan):[^\s,;()\[\]]+")
_NUMBER = re.compile(
    r"(?<![\w.:-])"  # not part of a name (Healbot2, p2), a decimal, a clock or a hyphenated word
    r"(?:(?P<clock>\d{1,2}:\d{2}(?:\.\d+)?)|(?P<num>-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?))"
    r"(?:(?P<unit>%|ms|s|k|K|m|M)|\s(?P<word>ms|secs?|seconds?))?"
    r"(?![\w%])"
)

Unit = Literal["", "%", "ms", "s", "k", "m"]
_WORD_UNITS: dict[str, Unit] = {"ms": "ms", "sec": "s", "secs": "s", "second": "s", "seconds": "s"}
# Divisors applied to a record value before comparing it with a claim number in that unit. Seconds
# match a millisecond field (t_ms / 1000) or a value already in seconds.
_SCALES: dict[Unit, tuple[float, ...]] = {"": (1,), "%": (1,), "ms": (1,), "s": (1000, 1), "k": (1000,), "m": (1e6,)}


@dataclass(frozen=True)
class ClaimNumber:
    text: str
    value: float
    decimals: int
    unit: Unit


def _decimals(text: str) -> int:
    return len(text.rsplit(".", 1)[1]) if "." in text else 0


def claim_numbers(text: str) -> list[ClaimNumber]:
    """
    Numbers stated in a claim. Ref strings and digits inside names are ignored. ``1,234`` is 1234;
    ``12.4s``, ``12.4 seconds`` and ``2:14`` are seconds; ``45%`` is a percentage; ``1.2k``/``3.4m``
    are thousands/millions.
    """
    out = []
    for m in _NUMBER.finditer(_REF_IN_TEXT.sub(" ", text)):
        if m["clock"]:
            minutes, seconds = m["clock"].split(":")
            out.append(ClaimNumber(m[0], int(minutes) * 60 + float(seconds), _decimals(seconds), "s"))
            continue
        number = m["num"].replace(",", "")
        unit: Unit = _WORD_UNITS[m["word"]] if m["word"] else (m["unit"] or "").lower()  # type: ignore[assignment]
        out.append(ClaimNumber(m[0].strip(), float(number), _decimals(number), unit))
    return out


def extract_numbers(text: str) -> list[str]:
    """The number tokens in ``text`` as written (``1,234``, ``12.4s``, ``45%``)."""
    return [n.text for n in claim_numbers(text)]


def numeric_values(obj: Any) -> set[float]:
    """Every number in a resolved record: numeric fields, and digit runs inside its strings."""
    out: set[float] = set()
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, int | float):
        out.add(float(obj))
    elif isinstance(obj, str):
        out.update(float(x) for x in re.findall(r"\d+(?:\.\d+)?", obj))
    elif isinstance(obj, dict):
        for value in obj.values():
            out |= numeric_values(value)
    elif isinstance(obj, list | tuple):
        for value in obj:
            out |= numeric_values(value)
    return out


def number_supported(n: ClaimNumber, values: set[float]) -> bool:
    """True if some record value, in the claim's unit and rounded to its precision, equals the claim."""
    tolerance = 0.5 * 10**-n.decimals + 1e-9
    return any(abs(x / scale - n.value) <= tolerance for x in values for scale in _SCALES[n.unit])
