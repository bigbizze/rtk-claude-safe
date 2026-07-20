"""Guarded maintenance for the Codex SQLite log database."""

from __future__ import annotations

import builtins
import datetime as dt
import os
import platform
import shlex
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import quote

DEFAULT_CODEX_SQLITE_PATH = Path.home() / ".codex" / "logs_2.sqlite"
LOW_LEVEL_LOG_TRIGGER_NAME = "codex_ignore_low_level_logs"
LOW_LEVEL_LOG_TRIGGER_SQL = """CREATE TRIGGER codex_ignore_low_level_logs
BEFORE INSERT ON logs
WHEN upper(NEW.level) IN ('TRACE', 'DEBUG', 'INFO')
BEGIN
  SELECT RAISE(IGNORE);
END"""


class CodexSqliteError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexProcess:
    pid: int
    command: str


@dataclass(frozen=True)
class VacuumResult:
    database: Path
    backup: Path | None
    before_bytes: int
    after_bytes: int
    wal_before_bytes: int
    wal_after_bytes: int
    checkpoint_busy: int
    checkpoint_log: int
    checkpointed: int


ProcessDetector = Callable[[], list[CodexProcess]]


def resolve_database_path(
    database: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    if database is not None:
        return Path(database).expanduser()

    values = os.environ if env is None else env
    sqlite_home = values.get("CODEX_SQLITE_HOME")
    if sqlite_home:
        return _sqlite_home_to_database_path(sqlite_home)

    codex_home = values.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "logs_2.sqlite"

    return DEFAULT_CODEX_SQLITE_PATH


def _sqlite_home_to_database_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.name == "logs_2.sqlite" or path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        return path
    return path / "logs_2.sqlite"


def active_codex_processes() -> list[CodexProcess]:
    _ensure_supported_platform()
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=", "-o", "command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise CodexSqliteError(f"could not inspect process list: {e}") from e

    if result.returncode != 0:
        message = result.stderr.strip() or f"ps exited with status {result.returncode}"
        raise CodexSqliteError(f"could not inspect process list: {message}")

    return parse_codex_processes(result.stdout.splitlines(), current_pid=os.getpid())


def parse_codex_processes(lines: list[str], current_pid: int) -> list[CodexProcess]:
    processes: list[CodexProcess] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _sep, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid or not command:
            continue
        if _is_codex_process_command(command):
            processes.append(CodexProcess(pid=pid, command=command.strip()))
    return processes


def confirm_codex_sessions_closed(
    *,
    input_func: Callable[[str], str] | None = None,
    output: object | None = None,
    process_detector: ProcessDetector = active_codex_processes,
) -> None:
    out = sys.stdout if output is None else output
    reader = builtins.input if input_func is None else input_func

    print("[rtk-claude-safe] Close all Codex CLI sessions before continuing.", file=out)
    while True:
        try:
            answer = reader('[rtk-claude-safe] Enter exactly "Y" when they are closed: ')
        except EOFError as e:
            raise CodexSqliteError("confirmation aborted before receiving Y") from e
        if answer != "Y":
            raise CodexSqliteError('aborted; enter exactly "Y" to continue')

        active = process_detector()
        if not active:
            return

        print("[rtk-claude-safe] Codex still appears to be running:", file=out)
        for process in active[:10]:
            print(f"[rtk-claude-safe]   {process.pid} {process.command}", file=out)
        if len(active) > 10:
            print(f"[rtk-claude-safe]   ... {len(active) - 10} more", file=out)
        print("[rtk-claude-safe] Close those sessions, then try Y again.", file=out)


def assert_codex_sessions_closed(
    process_detector: ProcessDetector = active_codex_processes,
) -> None:
    active = process_detector()
    if active:
        first = active[0]
        raise CodexSqliteError(
            "Codex is still running; close all Codex sessions before mutating "
            f"the log database. First active process: {first.pid} {first.command}"
        )


def install_low_level_log_trigger(database: Path) -> bool:
    try:
        with closing(_connect_rw(database)) as conn:
            _validate_logs_schema(conn)
            existing = _trigger_sql(conn)
            if existing is not None:
                if _is_managed_trigger_sql(existing):
                    return False
                raise CodexSqliteError(
                    f"trigger {LOW_LEVEL_LOG_TRIGGER_NAME!r} already exists with different SQL"
                )
            conn.executescript(LOW_LEVEL_LOG_TRIGGER_SQL + ";")
            conn.commit()
            return True
    except sqlite3.Error as e:
        raise CodexSqliteError(f"failed to install Codex SQLite trigger: {e}") from e


def revert_low_level_log_trigger(database: Path) -> bool:
    try:
        with closing(_connect_rw(database)) as conn:
            existing = _trigger_sql(conn)
            if existing is None:
                return False
            if not _is_managed_trigger_sql(existing):
                raise CodexSqliteError(
                    f"trigger {LOW_LEVEL_LOG_TRIGGER_NAME!r} exists with different SQL"
                )
            conn.execute(f"DROP TRIGGER {LOW_LEVEL_LOG_TRIGGER_NAME}")
            conn.commit()
            return True
    except sqlite3.Error as e:
        raise CodexSqliteError(f"failed to revert Codex SQLite trigger: {e}") from e


def vacuum_codex_logs(
    database: Path,
    *,
    backup: bool = False,
    now: dt.datetime | None = None,
) -> VacuumResult:
    _ensure_existing_database(database)
    before_bytes = _file_size(database)
    wal_before_bytes = _file_size(_wal_path(database))
    _ensure_vacuum_space(database, backup=backup, active_bytes=before_bytes + wal_before_bytes)

    backup_path: Path | None = None
    try:
        with closing(_connect_rw(database)) as conn:
            if backup:
                backup_path = _backup_database(conn, database, now=now)
            checkpoint_busy, checkpoint_log, checkpointed = _checkpoint_wal(conn)
            if checkpoint_busy:
                raise CodexSqliteError(
                    "WAL checkpoint was busy; another process still has the database open"
                )
            conn.execute("VACUUM")
            conn.execute("PRAGMA optimize")
    except sqlite3.Error as e:
        raise CodexSqliteError(f"failed to vacuum Codex SQLite logs: {e}") from e

    return VacuumResult(
        database=database,
        backup=backup_path,
        before_bytes=before_bytes,
        after_bytes=_file_size(database),
        wal_before_bytes=wal_before_bytes,
        wal_after_bytes=_file_size(_wal_path(database)),
        checkpoint_busy=checkpoint_busy,
        checkpoint_log=checkpoint_log,
        checkpointed=checkpointed,
    )


def format_bytes(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{size} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _ensure_supported_platform() -> None:
    if platform.system() == "Windows":
        raise CodexSqliteError(
            "native Windows Codex SQLite maintenance is not supported by this command"
        )


def _ensure_existing_database(path: Path) -> None:
    if not path.exists():
        raise CodexSqliteError(f"Codex SQLite log database was not found: {path}")
    if not path.is_file():
        raise CodexSqliteError(f"Codex SQLite log database is not a regular file: {path}")


def _connect_rw(path: Path) -> sqlite3.Connection:
    _ensure_existing_database(path)
    try:
        return sqlite3.connect(_sqlite_uri(path, mode="rw"), uri=True, timeout=2)
    except sqlite3.Error as e:
        raise CodexSqliteError(f"could not open Codex SQLite log database {path}: {e}") from e


def _sqlite_uri(path: Path, *, mode: str) -> str:
    return f"file:{quote(str(path.resolve()), safe='/')}?mode={mode}"


def _validate_logs_schema(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(logs)").fetchall()
    if not rows:
        raise CodexSqliteError("Codex SQLite database has no logs table")
    columns = {str(row[1]) for row in rows}
    if "level" not in columns:
        raise CodexSqliteError("Codex SQLite logs table has no level column")


def _trigger_sql(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
        (LOW_LEVEL_LOG_TRIGGER_NAME,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0])


def _is_managed_trigger_sql(sql: str) -> bool:
    return _normalize_sql(sql) == _normalize_sql(LOW_LEVEL_LOG_TRIGGER_SQL)


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.strip().rstrip(";").split())


def _is_codex_process_command(command: str) -> bool:
    if "codex-code-mode-host" in command:
        return False
    parts = _command_parts(command)
    if not parts:
        return False

    names = [_basename(part).lower() for part in parts]
    if names[0] in {"codex", "codex.exe"}:
        return True
    if names[0] not in {"node", "node.exe"}:
        return False
    for part, name in zip(parts[1:], names[1:]):
        if part.startswith("-"):
            continue
        return name in {"codex", "codex.exe"}
    return False


def _command_parts(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _basename(value: str) -> str:
    cleaned = value.replace("\\", "/").rstrip("/")
    return cleaned.rsplit("/", 1)[-1]


def _wal_path(path: Path) -> Path:
    return path.with_name(path.name + "-wal")


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _ensure_vacuum_space(database: Path, *, backup: bool, active_bytes: int) -> None:
    multiplier = 2 if backup else 1
    required = active_bytes * multiplier
    free = shutil.disk_usage(database.parent).free
    if free < required:
        raise CodexSqliteError(
            "not enough free disk space for VACUUM"
            f" (need at least {format_bytes(required)}, have {format_bytes(free)})"
        )


def _backup_database(
    conn: sqlite3.Connection,
    database: Path,
    *,
    now: dt.datetime | None,
) -> Path:
    timestamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = database.with_name(f"{database.name}.backup-{timestamp}")
    backup_path = base
    suffix = 2
    while backup_path.exists():
        backup_path = database.with_name(f"{base.name}-{suffix}")
        suffix += 1

    with closing(sqlite3.connect(str(backup_path))) as backup_conn:
        conn.backup(backup_conn)
    return backup_path


def _checkpoint_wal(conn: sqlite3.Connection) -> tuple[int, int, int]:
    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if row is None or len(row) != 3:
        raise CodexSqliteError("SQLite did not return a WAL checkpoint result")
    return int(row[0]), int(row[1]), int(row[2])
