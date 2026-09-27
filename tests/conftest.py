import pytest


@pytest.fixture(autouse=True)
def no_real_discord(monkeypatch, tmp_path):
    """Tests never reach the Discord running on this machine: the plugin's token is never found."""
    from zade import discord

    monkeypatch.setattr(discord, "TOKEN", tmp_path / "no-discord-token")
