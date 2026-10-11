"""
tests/helpers/review_fakes.py

Replies for a ReactiveFakeModel that plays the analyst in review tests on SYNTH01: one cited
``pull`` ref per pull analysis, and one finding citing every pull for the synthesis.
"""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage, SystemMessage

P1, P2 = "SYNTH01:1", "SYNTH01:2"
_ids = iter(range(10_000))


def query(name: str) -> AIMessage:
    """A turn that calls one no-argument query tool."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": {}, "id": f"c{next(_ids)}"}])


def submit(claim: str, *refs: str) -> AIMessage:
    args = {"findings": [{"claim": claim, "refs": list(refs), "confidence": "high"}], "unknowns": []}
    return AIMessage(content="", tool_calls=[{"name": "submit_report", "args": args, "id": f"c{next(_ids)}"}])


def kind_of(messages) -> str:
    system = next(m.content for m in messages if isinstance(m, SystemMessage))
    if "combining findings" in system:
        return "synthesis"
    return "pull" if re.search(r"The question is about pull ", system) else "other"


def respond(messages, _tools) -> AIMessage:
    system = next(m.content for m in messages if isinstance(m, SystemMessage))
    if kind_of(messages) == "synthesis":
        pulls = re.findall(r"^Pull (\S+):$", system, flags=re.M)
        return submit("The pulls were reviewed", *(f"ev:{p}:pull" for p in pulls))
    pull = re.search(r"The question is about pull (\S+)\.", system).group(1)
    return submit("The pull was reviewed", f"ev:{pull}:pull")
