import pytest

from zade import memory

A = [{"name": "open_app", "args": {"name": "firefox"}}]


@pytest.fixture
def conn():
    return memory.connect(":memory:")


def test_offer_after_threshold(conn):
    for _ in range(2):
        memory.log(conn, "browser", A, "llm", True)
    assert not memory.should_offer(conn, "browser", A, 3)
    memory.log(conn, "browser", A, "llm", True)
    assert memory.should_offer(conn, "browser", A, 3)


def test_failed_runs_do_not_count(conn):
    for _ in range(3):
        memory.log(conn, "browser", A, "llm", False)
    assert not memory.should_offer(conn, "browser", A, 3)


def test_declined_never_offered_again(conn):
    for _ in range(3):
        memory.log(conn, "browser", A, "llm", True)
    memory.decline(conn, "browser", A)
    memory.log(conn, "browser", A, "llm", True)
    assert not memory.should_offer(conn, "browser", A, 3)


def test_no_offer_once_shortcut_exists(conn):
    for _ in range(3):
        memory.log(conn, "browser", A, "llm", True)
    memory.add_shortcut(conn, "browser", A)
    assert not memory.should_offer(conn, "browser", A, 3)
    assert memory.shortcuts(conn) == {"browser": A}


def test_arg_order_does_not_matter(conn):
    a1 = [{"name": "volume", "args": {"delta": -10}}]
    a2 = [{"args": {"delta": -10}, "name": "volume"}]
    for _ in range(3):
        memory.log(conn, "quieter", a1, "llm", True)
    assert memory.should_offer(conn, "quieter", a2, 3)


def test_facts_newest_first_and_forget(conn):
    memory.add_fact(conn, "my editor is nvim")
    memory.add_fact(conn, "projects are in ~/code")
    assert memory.facts(conn) == ["projects are in ~/code", "my editor is nvim"]
    assert memory.forget_fact(conn, "projects") == 1
    assert memory.facts(conn) == ["my editor is nvim"]


def test_forget_empty_query_deletes_nothing(conn):
    memory.add_fact(conn, "my editor is nvim")
    assert memory.forget_fact(conn, "  ") == 0
    assert memory.facts(conn) == ["my editor is nvim"]


def test_last_actions_skips_failures(conn):
    assert memory.last_actions(conn) is None
    memory.log(conn, "browser", A, "llm", True)
    memory.log(conn, "broken", [{"name": "open_app", "args": {"name": "x"}}], "pattern", False)
    assert memory.last_actions(conn) == A


def test_use_shortcut_counts(conn):
    memory.add_shortcut(conn, "dev", A)
    memory.use_shortcut(conn, "dev")
    assert conn.execute("SELECT uses FROM shortcuts WHERE phrase='dev'").fetchone()[0] == 1
