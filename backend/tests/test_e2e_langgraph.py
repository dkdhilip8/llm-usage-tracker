"""End-to-end proof that a virtual key works as the credential layer for a
real multi-agent application: a genuine `langgraph.prebuilt.create_react_agent`
driven by the real `langchain-openai` / `langchain-anthropic` /
`langchain-google-genai` client libraries, each pointed at this gateway via
`base_url` exactly as a real integration would, running a real multi-turn
tool-calling loop (model requests a tool call -> the agent executes the tool
*locally* -> the result is sent back as a follow-up turn -> the model answers).

This is deliberately NOT a hand-crafted request/response contract test (those
already exist in test_adapters_*.py) — the point is that an off-the-shelf
agent framework, using the vendor's own official SDKs, can drive a gateway
virtual key through a real tool-calling loop with zero special-casing.

Requires optional heavy deps (see requirements-e2e.txt); skips cleanly via
importorskip everywhere else, including the default dev/test image.

The actual provider HTTP calls are still mocked at app.providers.send_request
(the same seam every other adapter test uses) — deterministic and offline,
but the response shapes are realistic enough (including the exact JSON a real
tool call requires) that each real SDK parses them exactly as it would parse
a live response, and the real LangGraph ToolNode executes the tool.
"""

import json
import threading
import time

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_openai")
pytest.importorskip("langchain_anthropic")
pytest.importorskip("langchain_google_genai")

import uvicorn  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

# langgraph.prebuilt.create_react_agent is deprecated in favor of
# langchain.agents.create_agent as of LangGraph v1.0 (still present, removal
# planned for v2.0). Deliberately NOT pulling in the full `langchain` package
# for this: it pins to a langgraph release incompatible with the one this
# harness targets (verified — langchain 1.1.1 imports break against
# langgraph 1.2.12), and this is the LangGraph package's own native
# agent-building entrypoint, which is what an app using LangGraph directly
# (without the heavier langchain umbrella) actually calls.
from langgraph.prebuilt import create_react_agent  # noqa: E402

from app.main import app as fastapi_app  # noqa: E402

FINAL_ANSWER = "It's sunny and 68F in Denver."


@pytest.fixture(scope="module")
def live_server():
    """A real uvicorn server on an OS-assigned localhost port, running the
    exact same FastAPI `app` singleton the rest of the suite uses (same DB
    engine, same in-memory caches). Needed because langchain-anthropic and
    langchain-google-genai don't expose a custom httpx transport the way
    langchain-openai does — base_url is the one mechanism all three support,
    and base_url needs a real socket to point at (proven against this exact
    approach in a throwaway probe before this file was written)."""
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            break
        time.sleep(0.02)
    else:
        raise RuntimeError("uvicorn test server did not start in time")
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _has_tool_result(url: str, json_body: dict) -> bool:
    """True once the incoming request already carries a tool result — i.e.
    this is the agent's follow-up turn, not its first turn. Checked loosely
    (scan every message/content shape) so it's robust to each SDK's exact
    turn-2 wire shape rather than assuming one specific field layout."""
    if "anthropic.com" in url:
        for m in json_body.get("messages", []):
            content = m.get("content")
            if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            ):
                return True
        return False
    if "generativelanguage.googleapis.com" in url:
        for c in json_body.get("contents", []):
            if any(isinstance(p, dict) and "functionResponse" in p for p in (c.get("parts") or [])):
                return True
        return False
    return any(m.get("role") == "tool" for m in json_body.get("messages", []))  # openai chat completions


@pytest.fixture()
def agentic_provider(monkeypatch):
    """Like conftest's mock_provider, but stateful: turn 1 always returns a
    get_weather tool call, any later turn (once a tool result is present in
    the request) returns the final text answer. Shaped realistically enough
    that each real provider SDK parses it exactly as it would a live
    response, and the real LangGraph ToolNode drives the loop."""
    calls: list[tuple] = []

    def fake_send(url, headers, json_body, *, timeout=60.0):
        calls.append((url, headers, json_body))
        n = len(calls)
        done = _has_tool_result(url, json_body)

        if "anthropic.com" in url:
            content = (
                [{"type": "text", "text": FINAL_ANSWER}]
                if done
                else [{"type": "tool_use", "id": "toolu_e2e_1", "name": "get_weather", "input": {"city": "Denver"}}]
            )
            return {
                "id": f"msg_e2e_{n}",
                "type": "message",
                "role": "assistant",
                "content": content,
                "model": json_body.get("model"),
                "stop_reason": "end_turn" if done else "tool_use",
                "usage": {"input_tokens": 20, "output_tokens": 8},
            }

        if "generativelanguage.googleapis.com" in url:
            parts = (
                [{"text": FINAL_ANSWER}]
                if done
                else [{"functionCall": {"name": "get_weather", "args": {"city": "Denver"}}}]
            )
            return {
                "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}],
                "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 8, "totalTokenCount": 28},
            }

        # openai chat completions
        message = (
            {"role": "assistant", "content": FINAL_ANSWER}
            if done
            else {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_e2e_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": json.dumps({"city": "Denver"})},
                    }
                ],
            }
        )
        return {
            "id": f"chatcmpl_e2e_{n}",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json_body.get("model"),
            "choices": [{"index": 0, "message": message, "finish_reason": "stop" if done else "tool_calls"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        }

    monkeypatch.setattr("app.providers.send_request", fake_send)
    return calls


def _weather_tool(log: list):
    @tool
    def get_weather(city: str) -> str:
        """Get the current weather for a city."""
        log.append(city)
        return FINAL_ANSWER

    return get_weather


def _run_agent(llm, log: list) -> str:
    agent = create_react_agent(llm, tools=[_weather_tool(log)])
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "What's the weather in Denver? Use the get_weather tool."}]}
    )
    return result["messages"][-1].content


def test_openai_agent_tool_loop(live_server, admin_client, make_live_key, agentic_provider):
    from langchain_openai import ChatOpenAI

    k = make_live_key(allowed_providers=["openai"], default_provider="openai")
    llm = ChatOpenAI(base_url=f"{live_server}/v1", api_key=k["key"], model="gpt-4o-mini")

    log: list = []
    final = _run_agent(llm, log)

    assert final == FINAL_ANSWER
    assert log == ["Denver"]  # the tool ran client-side, exactly once — the gateway never executed it
    assert len(agentic_provider) == 2  # one request per turn, both through the real gateway

    items = admin_client.get("/api/requests").json()["items"]
    assert len(items) == 2
    assert all(i["provider"] == "openai" and i["status"] == "success" for i in items)


def test_anthropic_agent_tool_loop(live_server, admin_client, make_live_key, agentic_provider):
    from langchain_anthropic import ChatAnthropic

    k = make_live_key(allowed_providers=["anthropic"])
    llm = ChatAnthropic(base_url=live_server, api_key=k["key"], model="claude-haiku-4-5-20251001")

    log: list = []
    final = _run_agent(llm, log)

    assert final == FINAL_ANSWER
    assert log == ["Denver"]
    assert len(agentic_provider) == 2

    items = admin_client.get("/api/requests").json()["items"]
    assert len(items) == 2
    assert all(i["provider"] == "anthropic" and i["status"] == "success" for i in items)


def test_gemini_agent_tool_loop(live_server, admin_client, make_live_key, agentic_provider):
    from langchain_google_genai import ChatGoogleGenerativeAI

    k = make_live_key(allowed_providers=["gemini"])
    llm = ChatGoogleGenerativeAI(base_url=live_server, google_api_key=k["key"], model="gemini-2.5-flash")

    log: list = []
    final = _run_agent(llm, log)

    assert final == FINAL_ANSWER
    assert log == ["Denver"]
    assert len(agentic_provider) == 2

    items = admin_client.get("/api/requests").json()["items"]
    assert len(items) == 2
    assert all(i["provider"] == "gemini" and i["status"] == "success" for i in items)


def test_paused_key_blocks_agent_before_any_provider_call(live_server, admin_client, make_live_key, agentic_provider):
    """The credential-layer guarantee holds even when a real agent framework
    is driving the call, not just a raw HTTP client: a paused key must fail
    closed on the very first turn, with zero upstream calls made."""
    from langchain_openai import ChatOpenAI

    k = make_live_key(allowed_providers=["openai"], default_provider="openai", allow_live=False)
    llm = ChatOpenAI(base_url=f"{live_server}/v1", api_key=k["key"], model="gpt-4o-mini")

    log: list = []
    with pytest.raises(Exception, match="key_paused|403"):
        _run_agent(llm, log)

    assert log == []
    assert agentic_provider == []
