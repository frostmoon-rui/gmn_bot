"""Schedule supervised automatic development without joining ordinary chat handling."""
import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

BEIJING = timezone(timedelta(hours=8))
COOLDOWN = 24 * 60 * 60
MIN_SCORE = 3.0
LOG = logging.getLogger(__name__)


def enabled(value):
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def state_db(runtime):
    return runtime / "auto_evolution.local.sqlite3"


def state(runtime):
    path = state_db(runtime)
    if not path.is_file():
        return False, "", None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        return db.execute("SELECT paused, checked_day, triggered_at FROM control WHERE id=1").fetchone() or (False, "", None)


def update(runtime, *, paused=None, checked_day=None, triggered_at=None):
    with closing(sqlite3.connect(state_db(runtime), timeout=5)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS control(
                   id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0,
                   checked_day TEXT NOT NULL DEFAULT '', triggered_at REAL)""")
        db.execute("INSERT OR IGNORE INTO control(id) VALUES(1)")
        if paused is not None:
            db.execute("UPDATE control SET paused=? WHERE id=1", (int(paused),))
        if checked_day is not None:
            db.execute("UPDATE control SET checked_day=? WHERE id=1", (checked_day,))
        if triggered_at is not None:
            db.execute("UPDATE control SET triggered_at=? WHERE id=1", (triggered_at,))


def check_time(now):
    return now.astimezone(BEIJING).replace(hour=4, minute=0, second=0, microsecond=0)


def next_check(now, checked_day=""):
    now = now.astimezone(BEIJING)
    start = check_time(now)
    if checked_day == start.date().isoformat() or now >= start + timedelta(minutes=15):
        return start + timedelta(days=1)
    return max(start, now)


def within_window(now):
    now = now.astimezone(BEIJING)
    start = check_time(now)
    return start <= now < start + timedelta(minutes=15)


def decision(*, configured, paused, now, triggered_at, resume, score, quota):
    if not configured:
        return "disabled"
    if paused:
        return "paused"
    if triggered_at is not None and now.timestamp() - triggered_at < COOLDOWN:
        return "cooldown"
    if not resume and (score is None or score < MIN_SCORE):
        return "insufficient_motivation"
    if not quota:
        return "quota_unavailable"
    return "eligible"


def command_action(message_type, actor, owner, text, aliases=()):
    if message_type != "private" or not owner or actor != owner:
        return None
    names = {"pause automatic evolution": True, "resume automatic evolution": False, **dict(aliases)}
    return names.get(text.strip().rstrip(".!。！").strip().casefold())


async def recover_at_startup(runtime, account, launcher=None, notice=None, codex=None):
    """Run one recovery scan after an authenticated account arrives from NapCat."""
    if not account or os.name != "nt":
        return
    if launcher is None:
        launcher = runtime / "tools" / "autonomy_dev.py"
        if not launcher.is_file():
            launcher = runtime.with_name("gmn_bot_git") / "tools" / "autonomy_dev.py"
    launcher = Path(launcher).resolve()
    if not launcher.is_file():
        return
    args = [sys.executable, str(launcher), "--resume-interrupted", "--runtime", str(runtime),
            "--bot-account", str(account)]
    if notice:
        args += ["--notice-config", str(notice)]
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
                              "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
                              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}}
    if codex:
        env["AUTO_EVOLUTION_CODEX_EXE"] = str(codex)
    try:
        child = await asyncio.create_subprocess_exec(
            *args, cwd=str(launcher.parent.parent), env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        await child.wait()
    except OSError as error:
        LOG.warning("Startup recovery could not run (%s)", type(error).__name__)


def configured(runtime):
    value = os.environ.get("ENABLE_AUTO_EVOLUTION")
    if value is None:
        settings = runtime / "local_settings.json"
        if settings.is_file():
            value = json.loads(settings.read_text(encoding="utf-8-sig")).get("ENABLE_AUTO_EVOLUTION", False)
    return enabled(value)
