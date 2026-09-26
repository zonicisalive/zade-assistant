import json
import os

import httpx
import ollama

MAX_ROUNDS = 4
TOO_MANY = "That took too many steps, so I stopped."
# Cloud SDKs are imported only when a cloud provider is used: they cost ~0.5 s of startup otherwise.


def _errors(name):
    base = (ollama.ResponseError, httpx.HTTPError, ConnectionError)
    if name == "anthropic":
        import anthropic

        return base + (anthropic.AnthropicError,)
    if name == "openai":
        import openai

        return base + (openai.OpenAIError,)
    return base


class ProviderError(Exception):
    pass


def _client(name, cfg):
    if name == "ollama":
        return ollama.Client(host=cfg["llm"]["host"], timeout=120)
    pc = cfg["providers"][name]
    key = os.environ.get(pc["api_key_env"])
    if not key:
        raise ProviderError(f"{pc['api_key_env']} is not set")
    if name == "anthropic":
        import anthropic

        return anthropic.Anthropic(api_key=key, timeout=10, max_retries=1)
    import openai

    return openai.OpenAI(base_url=pc["base_url"], api_key=key, timeout=10, max_retries=1)


def _history(history):
    msgs = []
    for user, assistant in history:
        msgs += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
    return msgs


def _ollama(system, text, tools, run_tool, cfg, extra, history):
    llm = cfg["llm"]
    client = _client("ollama", cfg)
    msgs = [{"role": "system", "content": system}, *_history(history), {"role": "user", "content": text}]
    specs = [{"type": "function", "function": t} for t in tools]
    for _ in range(MAX_ROUNDS):
        r = client.chat(
            model=llm["model"], messages=msgs, tools=specs, think=False,
            options={"num_ctx": llm["num_ctx"], "num_predict": llm.get("max_tokens", 400), **extra},
            keep_alive=llm["keep_alive"],
        )
        if not r.message.tool_calls:
            return (r.message.content or "").strip()
        msgs.append(r.message)
        for tc in r.message.tool_calls:
            out = run_tool(tc.function.name, dict(tc.function.arguments or {}))
            msgs.append({"role": "tool", "content": out, "tool_name": tc.function.name})
    return TOO_MANY


def _anthropic(system, text, tools, run_tool, cfg, extra, history):
    pc = cfg["providers"]["anthropic"]
    client = _client("anthropic", cfg)
    kw = {}
    if pc.get("effort"):
        kw["output_config"] = {"effort": pc["effort"]}
    if pc.get("fallbacks"):
        kw |= {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": pc["fallbacks"]}
    specs = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
    msgs = [*_history(history), {"role": "user", "content": text}]
    for _ in range(MAX_ROUNDS):
        r = client.beta.messages.create(
            model=pc["model"], max_tokens=4096, system=system, tools=specs, messages=msgs, **kw)
        if r.stop_reason == "refusal":
            return "I can't help with that one."
        if r.stop_reason != "tool_use":
            return "".join(b.text for b in r.content if b.type == "text").strip()
        msgs.append({"role": "assistant", "content": r.content})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": b.id, "content": run_tool(b.name, dict(b.input))}
            for b in r.content if b.type == "tool_use"
        ]})
    return TOO_MANY


def _private(client):
    """On OpenRouter: route only to providers with zero data retention (they keep nothing and can't train on it)."""
    return {"provider": {"zdr": True, "data_collection": "deny"}} if "openrouter.ai" in str(client.base_url) else None


def _openai(system, text, tools, run_tool, cfg, extra, history):
    pc = cfg["providers"]["openai"]
    client = _client("openai", cfg)
    specs = [{"type": "function", "function": t} for t in tools]
    msgs = [{"role": "system", "content": system}, *_history(history), {"role": "user", "content": text}]
    for _ in range(MAX_ROUNDS):
        m = client.chat.completions.create(model=pc["model"], messages=msgs, tools=specs, extra_body=_private(client)
                                           ).choices[0].message
        if not m.tool_calls:
            return (m.content or "").strip()
        msgs.append({"role": "assistant", "content": m.content, "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in m.tool_calls
        ]})
        for tc in m.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                out = "error: arguments were not valid JSON"
            else:
                out = run_tool(tc.function.name, args)
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out})
    return TOO_MANY


def chat(name, system, text, tools, run_tool, cfg, extra, history=()):
    fn = {"ollama": _ollama, "anthropic": _anthropic, "openai": _openai}.get(name)
    if not fn:
        raise ProviderError(f"unknown provider {name}")
    try:
        return fn(system, text, tools, run_tool, cfg, extra, history)
    except _errors(name) as e:
        raise ProviderError(f"{name}: {e}") from e
