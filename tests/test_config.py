from zade import config


def test_missing_file_gives_defaults(tmp_path):
    assert config.load(tmp_path / "nope.toml") == config.DEFAULTS


def test_override_keeps_siblings(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nmodel = "qwen2.5:3b"\n')
    cfg = config.load(p)
    assert cfg["llm"]["model"] == "qwen2.5:3b"
    assert cfg["llm"]["keep_alive"] == "60s"
    assert config.DEFAULTS["llm"]["model"] == "qwen3:4b"  # defaults not mutated
