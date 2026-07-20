from __future__ import annotations

import datetime as dt
import io
import sqlite3
from pathlib import Path

import pytest

from rtk_claude_safe import codex_sqlite
from rtk_claude_safe.codex_sqlite import (
    CodexProcess,
    CodexSqliteError,
    confirm_codex_sessions_closed,
    install_low_level_log_trigger,
    parse_codex_processes,
    resolve_database_path,
    revert_low_level_log_trigger,
    vacuum_codex_logs,
)


def _logs_db(path: Path) -> Path:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, level TEXT, message TEXT)")
    return path


def _count_logs(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0])


def test_resolve_database_path_precedence(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "rtk_claude_safe.codex_sqlite.DEFAULT_CODEX_SQLITE_PATH",
        tmp_path / "default.sqlite",
    )
    extensionless_db = tmp_path / "codex-log"
    extensionless_db.write_bytes(b"sqlite")

    assert resolve_database_path(tmp_path / "explicit.sqlite") == tmp_path / "explicit.sqlite"
    assert resolve_database_path(env={"CODEX_SQLITE_HOME": str(tmp_path / "sqlite-home")}) == (
        tmp_path / "sqlite-home" / "logs_2.sqlite"
    )
    assert resolve_database_path(env={"CODEX_SQLITE_HOME": str(tmp_path / "custom.sqlite")}) == (
        tmp_path / "custom.sqlite"
    )
    assert resolve_database_path(env={"CODEX_SQLITE_HOME": str(extensionless_db)}) == (
        extensionless_db
    )
    assert resolve_database_path(env={"CODEX_HOME": str(tmp_path / "codex-home")}) == (
        tmp_path / "codex-home" / "logs_2.sqlite"
    )
    assert resolve_database_path(env={}) == tmp_path / "default.sqlite"


def test_parse_codex_processes_detects_native_and_node_wrappers() -> None:
    processes = parse_codex_processes(
        [
            "101 node /home/u/.nvm/versions/node/v22/bin/codex resume abc --yolo",
            "102 /home/u/.npm/codex/vendor/x86_64-unknown-linux-musl/bin/codex resume abc",
            "103 python -m rtk_claude_safe repair-codex-sqlite",
            "104 /home/u/.npm/bin/codex-code-mode-host --stdio",
            "105 grep codex",
            "106 node server.js codex",
        ],
        current_pid=999,
    )

    assert [(process.pid, "codex" in process.command) for process in processes] == [
        (101, True),
        (102, True),
    ]


def test_confirm_codex_sessions_closed_requires_exact_y_and_loops() -> None:
    answers = iter(["Y", "Y"])
    calls = 0

    def detector() -> list[CodexProcess]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [CodexProcess(pid=123, command="/bin/codex resume")]
        return []

    output = io.StringIO()

    confirm_codex_sessions_closed(
        input_func=lambda _prompt: next(answers),
        output=output,
        process_detector=detector,
    )

    assert calls == 2
    assert "Codex still appears to be running" in output.getvalue()


def test_confirm_codex_sessions_closed_rejects_non_exact_confirmation() -> None:
    with pytest.raises(CodexSqliteError, match="exactly"):
        confirm_codex_sessions_closed(
            input_func=lambda _prompt: "y",
            output=io.StringIO(),
            process_detector=lambda: [],
        )


def test_install_low_level_log_trigger_filters_trace_debug_info(tmp_path) -> None:
    db = _logs_db(tmp_path / "logs_2.sqlite")

    assert install_low_level_log_trigger(db)
    assert not install_low_level_log_trigger(db)

    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO logs(level, message) VALUES('TRACE', 'trace')")
        conn.execute("INSERT INTO logs(level, message) VALUES('debug', 'debug')")
        conn.execute("INSERT INTO logs(level, message) VALUES('INFO', 'info')")
        conn.execute("INSERT INTO logs(level, message) VALUES('WARN', 'warn')")

    assert _count_logs(db) == 1


def test_install_low_level_log_trigger_refuses_conflicting_same_name(tmp_path) -> None:
    db = _logs_db(tmp_path / "logs_2.sqlite")
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TRIGGER codex_ignore_low_level_logs
            BEFORE INSERT ON logs
            BEGIN
              SELECT RAISE(IGNORE);
            END;
            """
        )

    with pytest.raises(CodexSqliteError, match="different SQL"):
        install_low_level_log_trigger(db)


def test_install_low_level_log_trigger_does_not_create_missing_database(tmp_path) -> None:
    db = tmp_path / "missing.sqlite"

    with pytest.raises(CodexSqliteError, match="not found"):
        install_low_level_log_trigger(db)

    assert not db.exists()


def test_install_low_level_log_trigger_validates_logs_level_schema(tmp_path) -> None:
    db = tmp_path / "logs_2.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, message TEXT)")

    with pytest.raises(CodexSqliteError, match="level column"):
        install_low_level_log_trigger(db)


def test_revert_low_level_log_trigger_only_drops_managed_trigger(tmp_path) -> None:
    db = _logs_db(tmp_path / "logs_2.sqlite")

    assert not revert_low_level_log_trigger(db)
    assert install_low_level_log_trigger(db)
    assert revert_low_level_log_trigger(db)

    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO logs(level, message) VALUES('TRACE', 'trace')")

    assert _count_logs(db) == 1


def test_revert_low_level_log_trigger_refuses_conflicting_same_name(tmp_path) -> None:
    db = _logs_db(tmp_path / "logs_2.sqlite")
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TRIGGER codex_ignore_low_level_logs
            BEFORE INSERT ON logs
            BEGIN
              SELECT RAISE(IGNORE);
            END;
            """
        )

    with pytest.raises(CodexSqliteError, match="different SQL"):
        revert_low_level_log_trigger(db)


def test_vacuum_codex_logs_runs_without_trigger_and_can_write_backup(tmp_path) -> None:
    db = _logs_db(tmp_path / "logs_2.sqlite")
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executemany(
            "INSERT INTO logs(level, message) VALUES('WARN', ?)",
            [("x" * 2048,) for _ in range(600)],
        )
        conn.execute("DELETE FROM logs")

    result = vacuum_codex_logs(
        db,
        backup=True,
        now=dt.datetime(2026, 7, 20, 10, 30, 0),
    )

    assert result.backup == tmp_path / "logs_2.sqlite.backup-20260720-103000"
    assert result.backup.exists()
    assert (
        result.after_bytes + result.wal_after_bytes
        <= result.before_bytes + result.wal_before_bytes
    )
    assert result.checkpoint_busy == 0


def test_vacuum_codex_logs_free_space_check_counts_wal_bytes(
    tmp_path,
    monkeypatch,
) -> None:
    db = _logs_db(tmp_path / "logs_2.sqlite")
    wal = tmp_path / "logs_2.sqlite-wal"
    wal.write_bytes(b"x" * 9000)

    class Usage:
        free = 8000

    monkeypatch.setattr(codex_sqlite.shutil, "disk_usage", lambda _path: Usage())

    with pytest.raises(CodexSqliteError, match="not enough free disk space"):
        vacuum_codex_logs(db)


def test_vacuum_space_check_requires_two_active_copies(tmp_path, monkeypatch) -> None:
    free_space = {"bytes": 1999}

    class Usage:
        @property
        def free(self) -> int:
            return free_space["bytes"]

    monkeypatch.setattr(codex_sqlite.shutil, "disk_usage", lambda _path: Usage())

    with pytest.raises(CodexSqliteError, match="not enough free disk space"):
        codex_sqlite._ensure_vacuum_space(tmp_path, backup=False, active_bytes=1000)

    free_space["bytes"] = 2000
    codex_sqlite._ensure_vacuum_space(tmp_path, backup=False, active_bytes=1000)


def test_vacuum_space_check_requires_backup_plus_two_active_copies(
    tmp_path,
    monkeypatch,
) -> None:
    free_space = {"bytes": 2999}

    class Usage:
        @property
        def free(self) -> int:
            return free_space["bytes"]

    monkeypatch.setattr(codex_sqlite.shutil, "disk_usage", lambda _path: Usage())

    with pytest.raises(CodexSqliteError, match="not enough free disk space"):
        codex_sqlite._ensure_vacuum_space(tmp_path, backup=True, active_bytes=1000)

    free_space["bytes"] = 3000
    codex_sqlite._ensure_vacuum_space(tmp_path, backup=True, active_bytes=1000)
