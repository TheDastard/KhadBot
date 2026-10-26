"""
jobs/model_prices.py

The model price table (``config/pricing/models.yaml``) and the cost estimate built on it. Prices are US dollars per
million tokens and are never written in code: the file names the pricing page they came from and the date they were
checked. A model with no entry gets no estimate (``None``), never a guess.

Anthropic reports a call's input tokens as one number that includes cache reads and cache writes, so the estimate
bills the three parts at their own rates: plain input, cache reads and cache writes, plus output.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from khadbot.errors import ConfigurationError

DEFAULT_PRICES_PATH = Path(__file__).resolve().parents[3] / "config" / "pricing" / "models.yaml"
PER_MILLION = 1_000_000

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Rates(BaseModel):
    """Dollars per million tokens for one kind of prompt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input: float
    cache_write: float
    cache_read: float
    output: float


class LongContext(Rates):
    """Rates that replace the base ones when the prompt is longer than ``above_input_tokens``."""

    above_input_tokens: int


class ModelPrice(Rates):
    long_context: LongContext | None = None

    def rates_for(self, input_tokens: int) -> Rates:
        lc = self.long_context
        return lc if lc is not None and input_tokens > lc.above_input_tokens else self


class PriceTable(BaseModel):
    """The whole file: when and where the prices were checked, and the price of each model ID."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    checked: date
    source: str
    models: dict[str, ModelPrice]

    def price_of(self, model: str) -> ModelPrice | None:
        """The entry for ``model``: an exact ID, else the longest listed ID it extends (a dated snapshot suffix)."""
        if model in self.models:
            return self.models[model]
        known = [name for name in self.models if model.startswith(name + "-")]
        return self.models[max(known, key=len)] if known else None

    def estimate_cost(
        self,
        model: str,
        *,
        input_tokens: int,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
        output_tokens: int = 0,
    ) -> float | None:
        """Estimated dollars for one call, or None when the model has no price."""
        price = self.price_of(model)
        if price is None:
            return None
        rates = price.rates_for(input_tokens)
        plain = max(input_tokens - cached_input_tokens - cache_write_tokens, 0)
        return (
            plain * rates.input
            + cached_input_tokens * rates.cache_read
            + cache_write_tokens * rates.cache_write
            + output_tokens * rates.output
        ) / PER_MILLION


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_price_table(path: Path | str | None = None) -> PriceTable:
    """
    Read and validate the price file.

    Raises:
        ConfigurationError: The file is missing, is not YAML or does not match the schema.
    """
    target = Path(path) if path else DEFAULT_PRICES_PATH
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        return PriceTable.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(f"Invalid model price table {target}: {exc}") from exc


_table: PriceTable | None = None


def get_price_table() -> PriceTable:
    """The default table, read once per process."""
    global _table
    if _table is None:
        _table = load_price_table()
    return _table


__all__ = ["PriceTable", "Rates", "get_price_table", "load_price_table"]
