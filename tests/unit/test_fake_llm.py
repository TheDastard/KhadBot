"""
tests/unit/test_fake_llm.py

Self-test for the tool-capable fake model and the role_fakes fixture.
"""

from helpers.fake_llm import make_fake
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool


@tool
def ping(x: int) -> int:
    """Return x."""
    return x


def test_bind_tools_records_names_and_returns_scripted_tool_call():
    call = AIMessage(content="", tool_calls=[{"name": "ping", "args": {"x": 1}, "id": "c1"}])
    model = make_fake([call])
    bound = model.bind_tools([ping])
    out = bound.invoke([HumanMessage(content="hi")])
    assert bound.bound_tools == ["ping"]
    assert out.tool_calls[0]["name"] == "ping"


def test_role_fakes_patches_get_llm(role_fakes):
    from khadbot import llm_factory

    role_fakes["analyst"] = make_fake(["a"])
    role_fakes["conversation"] = make_fake(["c"])
    assert llm_factory.get_llm(role="analyst").invoke("x").content == "a"
    assert llm_factory.get_llm(role="conversation").invoke("x").content == "c"
