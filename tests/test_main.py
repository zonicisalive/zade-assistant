import copy

from zade import __main__ as z
from zade import actions, config, memory

DEV = [{"name": "open_app", "args": {"name": "kitty"}}]


def make(said, answers=(), **kw):
    replies = list(answers)

    def confirm(question):
        said.append(question)
        return replies.pop(0)

    kw.setdefault("ask", lambda *a: "answer")
    kw.setdefault("run_action", lambda action, confirm: "")
    return z.Ctx(cfg=copy.deepcopy(config.DEFAULTS), conn=memory.connect(":memory:"),
                 say=said.append, confirm=confirm, find_app=lambda name: None, **kw)


def test_garbage_says_sorry():
    said = []
    z.handle(make(said), "Thank you.")
    assert said == ["Sorry, didn't catch that."]


def test_shortcut_runs_without_llm():
    said, ran = [], []

    def no_llm(*a):
        raise AssertionError("LLM must not be called")

    ctx = make(said, ask=no_llm, run_action=lambda a, c: ran.append(a) or "")
    memory.add_shortcut(ctx.conn, "dev", DEV)
    z.handle(ctx, "Dev.")
    assert ran == DEV and said == ["Done."]


def test_llm_actions_promoted_after_three():
    said = []

    def ask(text, facts, cfg, run_tool):
        run_tool("open_app", {"name": "kitty"})
        return "Opening kitty."

    ctx = make(said, answers=[True], ask=ask)
    for _ in range(3):
        z.handle(ctx, "Terminal please.")
    assert "Want 'terminal' to always do that?" in said
    assert memory.shortcuts(ctx.conn) == {"terminal": DEV}


def test_memory_tools_not_logged_as_actions():
    said = []

    def ask(text, facts, cfg, run_tool):
        return run_tool("remember", {"fact": "my editor is nvim"})

    ctx = make(said, ask=ask)
    z.handle(ctx, "remember my editor is nvim")
    assert memory.facts(ctx.conn) == ["my editor is nvim"]
    assert memory.last_actions(ctx.conn) is None


def test_failed_action_spoken_and_not_promoted():
    said = []

    def fail(action, confirm):
        raise actions.Failed("I couldn't find kitty.")

    def ask(text, facts, cfg, run_tool):
        return run_tool("open_app", {"name": "kitty"})

    ctx = make(said, ask=ask, run_action=fail)
    for _ in range(3):
        z.handle(ctx, "terminal")
    assert said == ["I couldn't find kitty."] * 3


def test_confirm_band_no_goes_to_llm():
    said = []
    predict = lambda s, q: {"answers": {"action": {"choice": "play or pause media", "confidence": 0.7}}}
    ctx = make(said, answers=[False], predict=predict, ask=lambda *a: "llm answer")
    z.handle(ctx, "pause the music")
    assert said == ["Did you mean play or pause media?", "llm answer"]


def test_make_shortcut_uses_previous_actions():
    said = []
    ctx = make(said, ask=lambda text, facts, cfg, run_tool: run_tool("make_shortcut", {"phrase": "Dev"}))
    memory.log(ctx.conn, "start my dev setup", DEV, "llm", True)
    z.handle(ctx, "remember dev means that")
    assert memory.shortcuts(ctx.conn) == {"dev": DEV}


def test_unexpected_action_error_returns_to_llm():
    said = []

    def boom(action, confirm):
        raise AttributeError("'NoneType' object has no attribute 'lower'")

    ctx = make(said, ask=lambda text, facts, cfg, run_tool: run_tool("open_app", {"name": None}), run_action=boom)
    z.handle(ctx, "open something")
    assert said and said[0].startswith("That failed")


def test_safe_handle_survives_any_error():
    said = []

    def boom(*a):
        raise RuntimeError("provider exploded")

    z.safe_handle(make(said, ask=boom), "what is tcp")
    assert said == ["Something went wrong."]
