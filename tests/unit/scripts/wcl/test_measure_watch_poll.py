"""
tests/unit/scripts/wcl/test_measure_watch_poll.py

The poll-cost measurement against a stub WCL client whose counter moves by known amounts: the rate-limit reads' own
cost is taken out of each poll's change. No network.
"""

from __future__ import annotations

from helpers.scripts import load_script

script = load_script("wcl", "measure_watch_poll")


class CountingClient:
    """A read costs ``read``; the metadata query costs the next value of ``queries``."""

    def __init__(self, read: float, queries: list[float]) -> None:
        self.read = read
        self.queries = list(queries)
        self.spent = 10.0

    async def rate_limit(self) -> dict:
        value = self.spent
        self.spent += self.read
        return {"pointsSpentThisHour": value}

    async def query(self, gql, variables=None) -> dict:
        self.spent += self.queries.pop(0)
        return {"reportData": {"report": {"fights": [{"id": 1}, {"id": 2}]}}}


async def test_each_polls_cost_has_the_reads_taken_out():
    result = await script.measure(CountingClient(1.0, [2.0, 3.0, 2.0]), "abc", 3)
    assert result.read_cost == 1.0 and result.fights == 2
    assert result.per_poll == [2.0, 3.0, 2.0] and result.mean == 7.0 / 3


def test_the_estimate_names_the_polls():
    assert "3 polls" in script.estimate(3)
