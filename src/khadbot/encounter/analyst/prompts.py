"""
encounter/analyst/prompts.py

The analyst's prompt templates and fixed reply strings. The templates are hashed into the prompt
versions (``graph.analyst_prompt_version``, ``review_graph.review_prompt_version``,
``review_graph.synthesis_prompt_version``), so editing one invalidates cached findings. Keep them free of
braces other than the placeholders.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Prompts (hashed into prompt_version)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the encounter analyst for a World of Warcraft raid review. You answer one question about one or \
more pulls (attempts at a boss) using read-only query tools over the combat log, and you report findings \
that cite evidence.

How to work:
- Investigate with the query tools. Every tool result is JSON with "ok", "data", "refs", "truncated" and \
"note". If "ok" is false, read the note and correct the call. If "truncated" is true, narrow the query as \
the note says before drawing conclusions from partial data.
- You have a budget of {budget} query rounds. Use only as many as the question needs.
- Finish by calling submit_report exactly once. Do not answer in plain text.

Rules for findings:
- Each finding is one factual claim that helps answer the question, with the evidence refs (strings \
starting "ev:") of the records that support it, copied exactly from tool results or the context below.
- Every number in a claim must appear in a cited record. Copy numbers exactly; never add, subtract, \
average or estimate. Times in the data are fight-relative milliseconds; you may write them as seconds \
with one decimal (23000 becomes 23.0s).
- Never cite a ref you have not seen.
- Use confidence "high" only when the cited records directly show the claim.
- If the data cannot answer part of the question (for example a mechanic is not in the encounter file, \
or a data type was not recorded), say so in unknowns with the reason instead of guessing. No findings \
plus unknowns is a valid answer.

{context}"""

SINGLE_PULL_CONTEXT = """\
The question is about pull {pull}. Its digest (every record line starts with its ref):

{digest}"""

MULTI_PULL_CONTEXT = """\
The question is about these pulls (pull key, outcome, duration, fight progress, boss health, phase reached, deaths):
{pulls}

Use compare_pulls for patterns across pulls and get_digest to look at one pull."""

BUDGET_PROMPT = """\
You have used your query budget. Call submit_report now with the findings the results so far support, \
and put anything unanswered in unknowns."""

RETRY_PROMPT = """\
Verification rejected part of your report:
{reasons}

Call submit_report again with the complete report. Fix each rejected finding using numbers and refs \
exactly as they appear in tool results, or drop it. Include the findings that passed again."""

SYNTHESIS_CONTEXT = """\
You are combining findings that were already verified for each pull of a night. The question is about \
the whole set of pulls. Findings per pull (each line: id, confidence, claim, and its refs):

{findings}

Look for patterns across pulls: what repeats, what changed, what got better or worse. Cite the refs of \
the findings you build on. Use compare_pulls when you need a figure the findings do not contain."""

STANDARD_REVIEW_QUESTION = (
    "Review this pull. What most likely decided the outcome (the deaths, mechanic failures and cooldown "
    "coverage that mattered), and what stands out as worth fixing before the next pull?"
)

# ---------------------------------------------------------------------------
# Fixed replies and unknowns
# ---------------------------------------------------------------------------

ACCEPTED = "Report received."
IGNORED = "Ignored: submit_report ends the investigation, so this call was not run."

PROMPTS: tuple[str, ...] = (SYSTEM_PROMPT, SINGLE_PULL_CONTEXT, MULTI_PULL_CONTEXT, BUDGET_PROMPT, RETRY_PROMPT)

NO_STRUCTURED_REPORT = "no structured report"
NO_PULLS = "none of the requested pulls are ingested"
