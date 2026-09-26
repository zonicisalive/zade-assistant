import os

from zade import config


def test_missing_file_gives_defaults(tmp_path):
    assert config.load(tmp_path / "nope.toml") == config.DEFAULTS


def test_override_keeps_siblings(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nmodel = "qwen2.5:3b"\n')
    cfg = config.load(p)
    assert cfg["llm"]["model"] == "qwen2.5:3b"
    assert cfg["llm"]["keep_alive"] == "30s"
    assert config.DEFAULTS["llm"]["model"] == "qwen2.5:7b-instruct"  # defaults not mutated


def test_load_env_override_replaces_changed_keys(tmp_path, monkeypatch):
    env = tmp_path / "env"
    env.write_text("ZADE_TEST_KEY=new\n")
    monkeypatch.setenv("ZADE_TEST_KEY", "old")
    config.load_env(env)
    assert os.environ["ZADE_TEST_KEY"] == "old"
    config.load_env(env, override=True)
    assert os.environ["ZADE_TEST_KEY"] == "new"
