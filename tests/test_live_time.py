"""Unit tests for the live-time Hermes plugin.

The repo directory name contains a hyphen, so the plugin module is loaded
dynamically by file path instead of a plain import.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PLUGIN_FILE = Path(__file__).resolve().parents[1] / "__init__.py"
_spec = importlib.util.spec_from_file_location("live_time", _PLUGIN_FILE)
assert _spec and _spec.loader
_live_time = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_live_time)
_on_pre_llm_call = _live_time._on_pre_llm_call
register = _live_time.register


def test_on_pre_llm_call_returns_live_timestamp_context() -> None:
    out = _on_pre_llm_call()
    assert out is not None
    text = out["context"]
    assert "[LIVE-TIME] Now:" in text

    m = re.search(r"Now: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text)
    assert m, f"missing timestamp in {text!r}"
    parsed = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")

    # The block renders either a named zone ("TZ Asia/Jakarta") or a bare offset
    # ("TZ UTC+8"), so compare against whichever one it reported.
    mz = re.search(r"TZ ([A-Za-z_]+/[A-Za-z_+\-0-9]+)", text)
    if mz:
        from zoneinfo import ZoneInfo

        expected = datetime.now(ZoneInfo(mz.group(1))).replace(tzinfo=None)
        assert abs((expected - parsed).total_seconds()) < 30
        return

    mo = re.search(r"UTC([+-])(\d{1,2})(?::(\d{2}))?", text)
    assert mo, f"missing UTC offset in {text!r}"
    offset_minutes = int(mo.group(2)) * 60 + int(mo.group(3) or 0)
    if mo.group(1) == "-":
        offset_minutes = -offset_minutes
    parsed_utc = parsed - timedelta(minutes=offset_minutes)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((now_utc - parsed_utc).total_seconds()) < 30


def test_context_marks_itself_authoritative() -> None:
    text = _on_pre_llm_call()["context"]
    assert "Use THIS as the authoritative current time" in text
    assert "Conversation started" in text


def test_register_hooks_pre_llm_call() -> None:
    ctx = MagicMock()
    register(ctx)
    ctx.register_hook.assert_called_once_with("pre_llm_call", _on_pre_llm_call)


def test_env_timezone_is_respected(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    monkeypatch.setenv("HERMES_TIMEZONE", "America/New_York")
    text = _on_pre_llm_call()["context"]
    assert "TZ America/New_York" in text
    # The stamped time must actually be New York time, not system-local.
    from zoneinfo import ZoneInfo

    m = re.search(r"Now: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text)
    assert m, f"missing timestamp in {text!r}"
    parsed = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    now_ny = datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    assert abs((now_ny - parsed).total_seconds()) < 30


def test_config_timezone_is_respected(monkeypatch, tmp_path) -> None:
    if not _yaml_available():
        pytest.skip("no YAML library importable in this environment")
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text('timezone: "Asia/Tokyo"\n', encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    text = _on_pre_llm_call()["context"]
    assert "TZ Asia/Tokyo" in text
    # Tokyo is +1h from the test machine (China Standard Time) — the stamped
    # timestamp must follow the configured zone, not the system one.
    from zoneinfo import ZoneInfo

    m = re.search(r"Now: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text)
    assert m, f"missing timestamp in {text!r}"
    parsed = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    now_tokyo = datetime.now(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None)
    assert abs((now_tokyo - parsed).total_seconds()) < 30


def test_nested_timezone_key_is_ignored(monkeypatch, tmp_path) -> None:
    """A timezone key nested under another section must not be picked up."""
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "model: deepseek\n"
        "plugins:\n"
        "  entries:\n"
        "    time-gap:\n"
        "      timezone: Europe/Berlin\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert _live_time._resolve_timezone_name() == ""


def test_invalid_timezone_falls_back_to_system_local(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    monkeypatch.setenv("HERMES_TIMEZONE", "Mars/Olympus_Mons")
    out = _on_pre_llm_call()
    assert out is not None  # invalid tz must not raise
    text = out["context"]
    assert "TZ Mars/Olympus_Mons" not in text
    assert re.search(r"TZ UTC[+-]\d+", text), f"no system-local fallback in {text!r}"


def test_weekday_label_is_locale_neutral() -> None:
    text = _on_pre_llm_call()["context"]
    m = re.search(r"\(\s*Weekday \d/7, ([A-Za-z]{3})\)", text)
    assert m and m.group(1) in {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}, (
        f"weekday label not locale-neutral: {text!r}"
    )


def _yaml_available() -> bool:
    """True when a YAML module the plugin can actually use is importable here."""
    for module in ("hermes_yaml", "yaml"):
        try:
            __import__(module)
        except ImportError:
            continue
        return True
    return False


def test_hermes_resolver_is_preferred(monkeypatch) -> None:
    """Hermes' own resolver wins over both env and config file.

    It is the only source that knows the *routed* profile: under a multiplexed gateway
    ``HERMES_HOME`` still points at the host gateway's home, so reading the config file
    resolves the default profile's ``timezone`` instead of the session's, and one env
    value can only ever describe one profile.
    """
    monkeypatch.setenv("HERMES_TIMEZONE", "America/New_York")
    fake = types.ModuleType("hermes_time")
    fake._resolve_timezone_name = lambda: "Asia/Jakarta"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hermes_time", fake)
    assert _live_time._resolve_timezone_name() == "Asia/Jakarta"


def test_config_file_is_read_via_the_runtime_yaml_shim(monkeypatch, tmp_path) -> None:
    """Outside a Hermes runtime the config file is the fallback — read with the runtime's
    YAML module (``hermes_yaml``, ruamel-based); PyYAML is not installed there."""
    if not _yaml_available():
        pytest.skip("no YAML library importable in this environment")
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    monkeypatch.setitem(sys.modules, "hermes_time", None)  # force the config-file path
    (tmp_path / "config.yaml").write_text('timezone: "Asia/Tokyo"\n', encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert _live_time._resolve_timezone_name() == "Asia/Tokyo"
