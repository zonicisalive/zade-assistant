import pytest


@pytest.fixture(autouse=True)
def no_real_discord(monkeypatch, tmp_path):
    """Tests never reach the Discord running on this machine: the plugin's token is never found."""
    from zade import discord

    monkeypatch.setattr(discord, "TOKEN", tmp_path / "no-discord-token")
    monkeypatch.setattr(discord, "SOCKET", tmp_path / "no-discord.sock")
    monkeypatch.setattr(discord, "wait_ready", lambda seconds=30: None)  # Discord on this machine: never waited for
