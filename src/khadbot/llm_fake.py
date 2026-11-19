"""
llm_fake.py

The fake model provider (``LLM_PROVIDER=fake``): a tool-capable scripted chat model with no network and no key,
so reviews and chat run end to end on the local stack and in CI without a paid model. It is a development switch;
``llm_factory.get_llm`` returns it only when the provider (or a role's provider) is ``fake``.

- A **script** is a function ``(messages, bound tool names) -> AIMessage`` that picks the model's next turn. It keeps
  no state: everything it needs comes from the messages, so parallel analyses, retries and replays all work.
- ``SCRIPTS`` maps a role to its named scripts (``default`` always). ``register_script`` is the hook the conversation
  agent's script is added through. A role with no scripts gets a placeholder reply.
- The analyst scripts read the pull through ``list_pulls`` and ``query_deaths`` (or ``compare_pulls`` when
  synthesising), then call ``submit_report`` with findings built only from the records those tools returned, so
  every ref resolves and every number matches. ``failing`` adds one finding whose number no record holds, which
  verify rejects.
- Every reply reports fixed token counts under the model name ``fake`` (``FAKE_MODEL_NAME``), so the usage ledger
  has rows, the price table prices it at zero and findings are cached under their own model key.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from khadbot.errors import ConfigurationError

FAKE_MODEL_NAME = "fake"
FAKE_INPUT_TOKENS = 1_000
FAKE_OUTPUT_TOKENS = 200
DEFAULT_SCRIPT = "default"
FAILING_SCRIPT = "failing"

Script = Callable[[list[BaseMessage], list[str]], AIMessage]

# ---------------------------------------------------------------------------
# Reading the conversation
# ---------------------------------------------------------------------------


def _called(messages: Sequence[BaseMessage]) -> dict[str, str]:
    """Tool call ID -> tool name for every call the model has made."""
    return {c["id"] or "": c["name"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls}


def tool_results(messages: Sequence[BaseMessage]) -> dict[str, dict[str, Any]]:
    """The last successful ``ToolResult`` payload (parsed JSON) of each tool, by tool name."""
    names = _called(messages)
    out: dict[str, dict[str, Any]] = {}
    for m in messages:
        if not isinstance(m, ToolMessage) or m.tool_call_id not in names:
            continue
        try:
            payload = json.loads(m.content) if isinstance(m.content, str) else None
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("ok"):
            out[names[m.tool_call_id]] = payload
    return out


def _rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    data = payload.get("data") if payload else None
    return [r for r in data if isinstance(r, dict) and r.get("ref")] if isinstance(data, list) else []


def call_turn(messages: Sequence[BaseMessage], name: str, **args: Any) -> AIMessage:
    """A turn that calls one tool. The call ID counts the model's earlier turns, so it is unique per conversation."""
    n = sum(isinstance(m, AIMessage) for m in messages)
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"fake-{n}", "type": "tool_call"}])


def submit_turn(
    messages: Sequence[BaseMessage], findings: list[dict[str, Any]], unknowns: list[str] | None = None
) -> AIMessage:
    return call_turn(messages, "submit_report", findings=findings, unknowns=unknowns or [])


def finding(claim: str, refs: list[str], confidence: str = "medium") -> dict[str, Any]:
    return {"claim": claim, "refs": refs, "confidence": confidence}


# ---------------------------------------------------------------------------
# Analyst scripts
# ---------------------------------------------------------------------------

NO_DATA = "the fake analyst could not read any pull records"
# A finding no record can support: verify rejects it with "unsupported number".
UNSUPPORTED_DURATION_MS = 987_654


def _pull_findings(results: dict[str, dict[str, Any]], *, failing: bool) -> list[dict[str, Any]]:
    row = next(iter(_rows(results.get("list_pulls"))), None)
    if row is None:
        return []
    ref = row["ref"]
    out = [
        finding(
            f"The pull ended in a {row['outcome']} after {row['duration_ms']} ms with {row['deaths']} player deaths.",
            [ref],
            "high",
        )
    ]
    deaths = _rows(results.get("query_deaths"))[:2]
    for d in deaths:
        out.append(finding(f"{d['name']} died at {d['t_ms']} ms.", [d["ref"]]))
    if failing:
        out.append(finding(f"The pull lasted {UNSUPPORTED_DURATION_MS} ms.", [ref], "low"))
    return out


def _synthesis_findings(results: dict[str, dict[str, Any]], *, failing: bool) -> list[dict[str, Any]]:
    rows = _rows(results.get("compare_pulls"))
    if not rows:
        return []
    refs = [r["ref"] for r in rows]
    # The number of refs cited counts as a value verify accepts, so this count always matches.
    out = [finding(f"The review compared {len(refs)} pulls.", refs, "high")]
    first = rows[0]
    out.append(finding(f"A pull ended in a {first['outcome']} with {first['deaths']} player deaths.", [first["ref"]]))
    if failing:
        out.append(finding(f"A pull lasted {UNSUPPORTED_DURATION_MS} ms.", [first["ref"]], "low"))
    return out


def _analyst(messages: list[BaseMessage], tools: list[str], *, failing: bool) -> AIMessage:
    called = set(_called(messages).values())
    results = tool_results(messages)
    synthesis = "compare_pulls" in called or ("compare_pulls" in tools and "list_pulls" not in tools + list(called))

    def ask(name: str, **args: Any) -> AIMessage | None:
        return call_turn(messages, name, **args) if name in tools and name not in called else None

    if synthesis:
        turn = ask("compare_pulls", metric="deaths")
        findings = _synthesis_findings(results, failing=failing)
    else:
        row = next(iter(_rows(results.get("list_pulls"))), None)
        turn = ask("list_pulls")
        if turn is None and row is not None and row.get("deaths"):
            turn = ask("query_deaths", pull=row["pull_key"])
        findings = _pull_findings(results, failing=failing)
    if turn is not None:
        return turn
    return submit_turn(messages, findings, [] if findings else [NO_DATA])


def analyst_script(messages: list[BaseMessage], tools: list[str]) -> AIMessage:
    """Reads the pull, then submits findings every one of which verify accepts."""
    return _analyst(messages, tools, failing=False)


def failing_analyst_script(messages: list[BaseMessage], tools: list[str]) -> AIMessage:
    """As ``analyst_script``, plus one finding verify rejects (a number no record holds), kept on the retry."""
    return _analyst(messages, tools, failing=True)


def placeholder_script(messages: list[BaseMessage], tools: list[str]) -> AIMessage:
    """The reply of a role with no script yet."""
    return AIMessage(content="(fake model) No fake script is registered for this role yet.")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Role -> script name -> script. The conversation agent adds ``SCRIPTS["conversation"]`` through ``register_script``.
SCRIPTS: dict[str, dict[str, Script]] = {
    "analyst": {DEFAULT_SCRIPT: analyst_script, FAILING_SCRIPT: failing_analyst_script},
}


def register_script(role: str, name: str, script: Script) -> None:
    """Add (or replace) a role's named script. A role needs a ``default`` for other names to fall back to."""
    SCRIPTS.setdefault(role, {})[name] = script


def resolve_script(role: str | None, name: str = DEFAULT_SCRIPT) -> Script:
    """
    The script for ``role``: ``name`` when the role has it, else its ``default``, else the placeholder.

    Raises:
        ConfigurationError: No role has a script called ``name``.
    """
    known = {n for scripts in SCRIPTS.values() for n in scripts}
    if name not in known:
        raise ConfigurationError(f"Unknown fake model script {name!r}. Valid scripts: {', '.join(sorted(known))}.")
    scripts = SCRIPTS.get(role or "", {})
    return scripts.get(name) or scripts.get(DEFAULT_SCRIPT) or placeholder_script


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


def fake_usage_metadata() -> dict[str, Any]:
    """The fixed ``AIMessage.usage_metadata`` every fake reply carries."""
    return {
        "input_tokens": FAKE_INPUT_TOKENS,
        "output_tokens": FAKE_OUTPUT_TOKENS,
        "total_tokens": FAKE_INPUT_TOKENS + FAKE_OUTPUT_TOKENS,
        "input_token_details": {"cache_read": 0, "cache_creation": 0},
    }


class FakeChatModel(BaseChatModel):
    """
    Scripted chat model. ``bind_tools`` records the tool names (the script only offers calls to bound tools) and
    returns a copy; every reply carries ``fake_usage_metadata()`` and ``response_metadata["model_name"] == "fake"``.
    ``delay_seconds`` pauses before each reply so progress is visible.
    """

    model_name: str = FAKE_MODEL_NAME
    role: str | None = None
    script: Script = placeholder_script
    delay_seconds: float = 0.0
    bound_tools: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> FakeChatModel:  # type: ignore[override]
        names = [getattr(t, "name", None) or getattr(t, "__name__", None) or t["name"] for t in tools]
        return self.model_copy(update={"bound_tools": names})

    def _reply(self, messages: list[BaseMessage]) -> ChatResult:
        turn = self.script(list(messages), list(self.bound_tools))
        message = AIMessage(
            content=turn.content,
            tool_calls=turn.tool_calls,
            usage_metadata=fake_usage_metadata(),  # type: ignore[arg-type]
            response_metadata={"model_name": self.model_name},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return self._reply(messages)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return self._reply(messages)


def build_fake_model(role: str | None, *, script: str = DEFAULT_SCRIPT, delay_seconds: float = 0.0) -> FakeChatModel:
    """A fake model for ``role`` playing the named script."""
    return FakeChatModel(role=role, script=resolve_script(role, script), delay_seconds=delay_seconds)


__all__ = [
    "DEFAULT_SCRIPT",
    "FAILING_SCRIPT",
    "FAKE_MODEL_NAME",
    "SCRIPTS",
    "FakeChatModel",
    "build_fake_model",
    "fake_usage_metadata",
    "register_script",
    "resolve_script",
]
