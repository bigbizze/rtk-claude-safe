"""Stateful Codex subagent depth enforcement hook."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

try:  # pragma: no cover - exercised on Python 3.9/3.10 by packaging.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from rtk_claude_safe.codex_subagent_depth_settings import default_codex_home

MAX_DEPTH_MAX = 2_147_483_647
SYSTEM_CODEX_CONFIG_PATH = Path("/etc/codex/config.toml")
STATE_DIR_NAME = "rtk-claude-safe"
DATABASE_NAME = "subagent-depth.sqlite"
DISABLED_MARKER_NAME = "subagent-depth.disabled"
LOCK_NAME = "subagent-depth.lock"
UNKNOWN_AGENT_WAIT_SECONDS = 2.0
PENDING_TTL_SECONDS = 10 * 60


@dataclass(frozen=True)
class PolicyResolution:
    max_depth: int | None
    error: str | None = None


@dataclass(frozen=True)
class SessionPolicy:
    max_depth: int | None
    error: str | None = None


def state_dir(codex_home: Path | None = None) -> Path:
    """Return the rtk-claude-safe state directory under CODEX_HOME."""
    return (codex_home or default_codex_home()) / STATE_DIR_NAME


def database_path(codex_home: Path | None = None) -> Path:
    """Return the SQLite database path for subagent depth state."""
    return state_dir(codex_home) / DATABASE_NAME


def disabled_marker_path(codex_home: Path | None = None) -> Path:
    """Return the durable marker that disables already-loaded hooks."""
    return state_dir(codex_home) / DISABLED_MARKER_NAME


def lock_path(codex_home: Path | None = None) -> Path:
    """Return the lifecycle lock path used by handlers and removal."""
    return state_dir(codex_home) / LOCK_NAME


def initialize_subagent_depth_state(codex_home: Path | None = None) -> Path:
    """Create the state database and enable the hook runtime."""
    home = codex_home or default_codex_home()
    state_dir(home).mkdir(parents=True, exist_ok=True)
    marker = disabled_marker_path(home)
    if marker.exists():
        marker.unlink()
    with lifecycle_lock(home, exclusive=True):
        with connect_state(home) as conn:
            initialize_schema(conn)
    return database_path(home)


def mark_subagent_depth_disabled(codex_home: Path | None = None) -> Path:
    """Disable already-loaded hook handlers before removing config entries."""
    home = codex_home or default_codex_home()
    state_dir(home).mkdir(parents=True, exist_ok=True)
    marker = disabled_marker_path(home)
    marker.write_text("disabled\n", encoding="utf-8")
    return marker


def remove_subagent_depth_state(codex_home: Path | None = None) -> None:
    """Remove SQLite state files after the disabled marker is in place."""
    home = codex_home or default_codex_home()
    for path in (
        database_path(home),
        database_path(home).with_name(DATABASE_NAME + "-wal"),
        database_path(home).with_name(DATABASE_NAME + "-shm"),
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def lifecycle_lock(codex_home: Path | None = None, *, exclusive: bool) -> Any:
    """Hold a shared handler lock or an exclusive uninstall lock."""
    home = codex_home or default_codex_home()
    state_dir(home).mkdir(parents=True, exist_ok=True)
    lock_file = lock_path(home)
    handle = lock_file.open("a+", encoding="utf-8")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        except (ImportError, OSError):
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        handle.close()


def connect_state(codex_home: Path | None = None) -> sqlite3.Connection:
    """Open the depth state database."""
    home = codex_home or default_codex_home()
    state_dir(home).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path(home), timeout=5)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    initialize_schema(conn)
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Create or migrate the local state schema."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            cwd TEXT,
            max_depth INTEGER,
            policy_error TEXT,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            depth INTEGER NOT NULL,
            agent_type TEXT,
            parent_agent_id TEXT,
            parent_tool_use_id TEXT,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pending_spawns (
            session_id TEXT NOT NULL,
            tool_use_id TEXT NOT NULL,
            parent_agent_id TEXT,
            parent_depth INTEGER NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (session_id, tool_use_id)
        );

        CREATE TABLE IF NOT EXISTS completed_spawns (
            session_id TEXT NOT NULL,
            tool_use_id TEXT NOT NULL,
            child_agent_id TEXT NOT NULL,
            parent_agent_id TEXT,
            child_depth INTEGER NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (session_id, tool_use_id)
        );

        CREATE INDEX IF NOT EXISTS idx_agents_session_depth
            ON agents(session_id, depth);
        CREATE INDEX IF NOT EXISTS idx_pending_created
            ON pending_spawns(created_at);
        """
    )
    conn.commit()


def resolve_effective_max_depth(
    cwd: Path,
    *,
    codex_home: Path | None = None,
    system_config_path: Path = SYSTEM_CODEX_CONFIG_PATH,
) -> PolicyResolution:
    """Resolve agents.max_depth using project, user, then system precedence."""
    home = codex_home or default_codex_home()
    cwd = _directory_for_lookup(cwd)

    effective: int | None = None

    system_config = _read_toml(system_config_path)
    if system_config.error is not None:
        return PolicyResolution(None, system_config.error)
    system_depth = _max_depth_from_config(system_config.data, system_config_path)
    if system_depth.error is not None:
        return PolicyResolution(None, system_depth.error)
    if system_depth.max_depth is not None:
        effective = system_depth.max_depth

    user_config_path = home / "config.toml"
    user_config = _read_toml(user_config_path)
    if user_config.error is not None:
        return PolicyResolution(None, user_config.error)
    user_depth = _max_depth_from_config(user_config.data, user_config_path)
    if user_depth.error is not None:
        return PolicyResolution(None, user_depth.error)
    if user_depth.max_depth is not None:
        effective = user_depth.max_depth

    trust_map = _project_trust_map(system_config.data, user_config.data)
    for project_config_path in _trusted_project_config_paths(cwd, home, trust_map):
        project_config = _read_toml(project_config_path)
        if project_config.error is not None:
            return PolicyResolution(None, project_config.error)
        project_depth = _max_depth_from_config(project_config.data, project_config_path)
        if project_depth.error is not None:
            return PolicyResolution(None, project_depth.error)
        if project_depth.max_depth is not None:
            effective = project_depth.max_depth

    return PolicyResolution(effective, None)


def handle_payload(
    payload: Any,
    *,
    codex_home: Path | None = None,
    system_config_path: Path = SYSTEM_CODEX_CONFIG_PATH,
    unknown_agent_wait_seconds: float = UNKNOWN_AGENT_WAIT_SECONDS,
) -> dict[str, str] | None:
    """Handle one Codex hook payload and return optional hook stdout JSON."""
    if not isinstance(payload, dict):
        return None

    home = codex_home or default_codex_home()
    with lifecycle_lock(home, exclusive=False):
        if disabled_marker_path(home).exists():
            return None
        with connect_state(home) as conn:
            event_name = payload.get("hook_event_name")
            if event_name == "SessionStart":
                _handle_session_start(conn, payload, home, system_config_path)
                return None
            if event_name == "PreToolUse":
                return _handle_pre_tool_use(
                    conn,
                    payload,
                    home,
                    system_config_path,
                    unknown_agent_wait_seconds,
                )
            if event_name == "PostToolUse":
                _handle_post_tool_use(conn, payload, unknown_agent_wait_seconds)
                return None
    return None


def main(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run the hook from stdin/stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    raw = stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    try:
        output = handle_payload(payload)
    except Exception as e:  # pragma: no cover - unit tests cover helper behavior.
        if _is_pre_agent_spawn_payload(payload):
            output = _block_output(
                "rtk-claude-safe failed closed while checking Codex subagent depth: "
                f"{type(e).__name__}: {e}"
            )
        else:
            return 0

    if output is not None:
        json.dump(output, stdout, separators=(",", ":"))
        stdout.write("\n")
    return 0


def _handle_session_start(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    codex_home: Path,
    system_config_path: Path,
) -> None:
    session_id = _string_value(payload.get("session_id"))
    if session_id is None:
        return
    cwd = _payload_cwd(payload)
    resolution = resolve_effective_max_depth(
        cwd,
        codex_home=codex_home,
        system_config_path=system_config_path,
    )
    _store_session_policy(conn, session_id, cwd, resolution, clear_when_absent=True)


def _handle_pre_tool_use(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    codex_home: Path,
    system_config_path: Path,
    unknown_agent_wait_seconds: float,
) -> dict[str, str] | None:
    if not _is_agent_tool_name(payload.get("tool_name")):
        return None

    session_id = _string_value(payload.get("session_id"))
    if session_id is None:
        return _block_output(
            "rtk-claude-safe could not verify Codex subagent depth: missing session_id"
        )

    tool_use_id = _string_value(payload.get("tool_use_id"))
    if tool_use_id is None:
        return _block_output(
            "rtk-claude-safe could not verify Codex subagent depth: missing tool_use_id"
        )

    cwd = _payload_cwd(payload)
    policy = _refresh_policy_for_spawn(conn, session_id, cwd, codex_home, system_config_path)
    _expire_pending_spawns(conn)

    if policy.error is not None:
        return _block_output(f"rtk-claude-safe could not resolve agents.max_depth: {policy.error}")

    caller_agent_id = _string_value(payload.get("agent_id"))
    caller_depth = _resolve_caller_depth(
        conn,
        session_id,
        caller_agent_id,
        wait_seconds=unknown_agent_wait_seconds,
    )

    policy_active = policy.max_depth is not None
    if caller_depth is None:
        if policy_active:
            return _block_output(
                "rtk-claude-safe could not verify Codex subagent depth for "
                f"agent_id={caller_agent_id!r}"
            )
        return None

    if policy.max_depth is not None and caller_depth >= policy.max_depth:
        return _block_output(
            "rtk-claude-safe blocked Codex subagent spawn: "
            f"agents.max_depth={policy.max_depth}, caller_depth={caller_depth}"
        )

    _record_pending_spawn(conn, session_id, tool_use_id, caller_agent_id, caller_depth)
    return None


def _handle_post_tool_use(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    unknown_agent_wait_seconds: float,
) -> None:
    if not _is_agent_tool_name(payload.get("tool_name")):
        return
    session_id = _string_value(payload.get("session_id"))
    tool_use_id = _string_value(payload.get("tool_use_id"))
    if session_id is None or tool_use_id is None:
        return

    child_agent_id = _extract_child_agent_id(payload.get("tool_response"))
    if child_agent_id is None:
        return

    pending = conn.execute(
        """
        SELECT parent_agent_id, parent_depth
        FROM pending_spawns
        WHERE session_id = ? AND tool_use_id = ?
        """,
        (session_id, tool_use_id),
    ).fetchone()

    if pending is not None:
        parent_agent_id, parent_depth = pending
    else:
        parent_agent_id = _string_value(payload.get("agent_id"))
        parent_depth = _resolve_caller_depth(
            conn,
            session_id,
            parent_agent_id,
            wait_seconds=unknown_agent_wait_seconds,
        )
        if parent_depth is None:
            return

    child_depth = int(parent_depth) + 1
    tool_input = payload.get("tool_input")
    input_agent_type = tool_input.get("agent_type") if isinstance(tool_input, dict) else None
    agent_type = _extract_child_agent_type(payload.get("tool_response")) or _string_value(
        input_agent_type
    )
    now = time.time()
    conn.execute(
        """
        INSERT INTO agents (
            agent_id, session_id, depth, agent_type, parent_agent_id,
            parent_tool_use_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            session_id = excluded.session_id,
            depth = max(agents.depth, excluded.depth),
            agent_type = coalesce(agents.agent_type, excluded.agent_type),
            parent_agent_id = coalesce(agents.parent_agent_id, excluded.parent_agent_id),
            parent_tool_use_id = coalesce(agents.parent_tool_use_id, excluded.parent_tool_use_id)
        """,
        (child_agent_id, session_id, child_depth, agent_type, parent_agent_id, tool_use_id, now),
    )
    conn.execute(
        """
        INSERT INTO completed_spawns (
            session_id, tool_use_id, child_agent_id, parent_agent_id, child_depth, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id, tool_use_id) DO UPDATE SET
            child_agent_id = excluded.child_agent_id,
            parent_agent_id = excluded.parent_agent_id,
            child_depth = max(completed_spawns.child_depth, excluded.child_depth),
            created_at = excluded.created_at
        """,
        (session_id, tool_use_id, child_agent_id, parent_agent_id, child_depth, now),
    )
    conn.execute(
        "DELETE FROM pending_spawns WHERE session_id = ? AND tool_use_id = ?",
        (session_id, tool_use_id),
    )
    conn.commit()


def _refresh_policy_for_spawn(
    conn: sqlite3.Connection,
    session_id: str,
    cwd: Path,
    codex_home: Path,
    system_config_path: Path,
) -> SessionPolicy:
    resolution = resolve_effective_max_depth(
        cwd,
        codex_home=codex_home,
        system_config_path=system_config_path,
    )
    if resolution.error is not None or resolution.max_depth is not None:
        _store_session_policy(conn, session_id, cwd, resolution, clear_when_absent=False)
        return SessionPolicy(resolution.max_depth, resolution.error)

    row = conn.execute(
        "SELECT max_depth, policy_error FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        _store_session_policy(conn, session_id, cwd, resolution, clear_when_absent=False)
        return SessionPolicy(None, None)
    max_depth, error = row
    return SessionPolicy(max_depth, error)


def _store_session_policy(
    conn: sqlite3.Connection,
    session_id: str,
    cwd: Path,
    resolution: PolicyResolution,
    *,
    clear_when_absent: bool,
) -> None:
    existing = conn.execute(
        "SELECT max_depth, policy_error FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if (
        resolution.error is None
        and resolution.max_depth is None
        and existing is not None
        and not clear_when_absent
    ):
        return

    max_depth = resolution.max_depth
    policy_error = resolution.error

    conn.execute(
        """
        INSERT INTO sessions (session_id, cwd, max_depth, policy_error, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            cwd = excluded.cwd,
            max_depth = excluded.max_depth,
            policy_error = excluded.policy_error,
            updated_at = excluded.updated_at
        """,
        (session_id, str(cwd), max_depth, policy_error, time.time()),
    )
    conn.commit()


def _record_pending_spawn(
    conn: sqlite3.Connection,
    session_id: str,
    tool_use_id: str,
    parent_agent_id: str | None,
    parent_depth: int,
) -> None:
    conn.execute(
        """
        INSERT INTO pending_spawns (session_id, tool_use_id, parent_agent_id, parent_depth, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(session_id, tool_use_id) DO UPDATE SET
            parent_agent_id = excluded.parent_agent_id,
            parent_depth = excluded.parent_depth,
            created_at = excluded.created_at
        """,
        (session_id, tool_use_id, parent_agent_id, parent_depth, time.time()),
    )
    conn.commit()


def _resolve_caller_depth(
    conn: sqlite3.Connection,
    session_id: str,
    agent_id: str | None,
    *,
    wait_seconds: float,
) -> int | None:
    if agent_id is None:
        return 0

    deadline = time.monotonic() + wait_seconds
    while True:
        row = conn.execute(
            "SELECT depth FROM agents WHERE session_id = ? AND agent_id = ?",
            (session_id, agent_id),
        ).fetchone()
        if row is not None:
            return int(row[0])
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.05, remaining))


def _expire_pending_spawns(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM pending_spawns WHERE created_at < ?",
        (time.time() - PENDING_TTL_SECONDS,),
    )
    conn.commit()


@dataclass(frozen=True)
class _TomlResult:
    data: dict[str, Any] | None
    error: str | None = None


def _read_toml(path: Path) -> _TomlResult:
    if not path.exists():
        return _TomlResult(None, None)
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as e:
        return _TomlResult(None, f"{path}: {e}")
    if not isinstance(data, dict):
        return _TomlResult(None, f"{path}: expected TOML table")
    return _TomlResult(data, None)


def _max_depth_from_config(data: dict[str, Any] | None, source: Path) -> PolicyResolution:
    if data is None or "agents" not in data:
        return PolicyResolution(None, None)
    agents = data.get("agents")
    if not isinstance(agents, dict):
        return PolicyResolution(None, f"{source}: [agents] must be a table")
    if "max_depth" not in agents:
        return PolicyResolution(None, None)

    value = agents["max_depth"]
    if isinstance(value, bool) or not isinstance(value, int):
        return PolicyResolution(None, f"{source}: agents.max_depth must be an integer")
    if value < 0 or value > MAX_DEPTH_MAX:
        return PolicyResolution(
            None,
            f"{source}: agents.max_depth must be between 0 and {MAX_DEPTH_MAX}",
        )
    return PolicyResolution(value, None)


def _project_trust_map(
    system_config: dict[str, Any] | None,
    user_config: dict[str, Any] | None,
) -> dict[str, str]:
    trust: dict[str, str] = {}
    for config in (system_config, user_config):
        projects = config.get("projects") if isinstance(config, dict) else None
        if not isinstance(projects, dict):
            continue
        for key, project_config in projects.items():
            if not isinstance(key, str) or not isinstance(project_config, dict):
                continue
            level = project_config.get("trust_level")
            if level in {"trusted", "untrusted"}:
                trust[key] = level
    return trust


def _trusted_project_config_paths(
    cwd: Path,
    codex_home: Path,
    trust_map: dict[str, str],
) -> list[Path]:
    candidates: list[Path] = []
    ancestors = list(_ancestors_inclusive(_directory_for_lookup(cwd)))
    ancestors.reverse()

    for directory in ancestors:
        dot_codex = directory / ".codex"
        config_path = dot_codex / "config.toml"
        if _same_path(dot_codex, codex_home):
            continue
        if not config_path.exists():
            continue
        if _trust_level_for_directory(directory, cwd, trust_map) == "trusted":
            candidates.append(config_path)
    return candidates


def _trust_level_for_directory(directory: Path, cwd: Path, trust_map: dict[str, str]) -> str | None:
    lookup_dirs = [directory]
    project_root = _find_project_root(cwd)
    if project_root is not None:
        lookup_dirs.append(project_root)
    checkout_root, repo_root = _git_roots(cwd)
    if checkout_root is not None:
        lookup_dirs.append(checkout_root)
    if repo_root is not None:
        lookup_dirs.append(repo_root)

    for lookup_dir in lookup_dirs:
        for key in _normalized_project_trust_keys(lookup_dir):
            direct = trust_map.get(key)
            if direct is not None:
                return direct
            normalized_matches = [
                (configured_key, level)
                for configured_key, level in trust_map.items()
                if _normalize_project_trust_lookup_key(configured_key) == key
            ]
            if normalized_matches:
                normalized_matches.sort(key=lambda item: item[0])
                return normalized_matches[0][1]
    return None


def _find_project_root(cwd: Path) -> Path | None:
    for ancestor in _ancestors_inclusive(_directory_for_lookup(cwd)):
        if (ancestor / ".git").exists():
            return ancestor
    return _directory_for_lookup(cwd)


def _git_roots(cwd: Path) -> tuple[Path | None, Path | None]:
    checkout_root = None
    git_marker = None
    for ancestor in _ancestors_inclusive(_directory_for_lookup(cwd)):
        marker = ancestor / ".git"
        if marker.exists():
            checkout_root = ancestor
            git_marker = marker
            break
    if checkout_root is None or git_marker is None:
        return None, None
    if git_marker.is_dir():
        return checkout_root, checkout_root

    try:
        first_line = git_marker.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return checkout_root, checkout_root
    prefix = "gitdir:"
    if not first_line.startswith(prefix):
        return checkout_root, checkout_root
    gitdir = Path(first_line[len(prefix) :].strip())
    if not gitdir.is_absolute():
        gitdir = (checkout_root / gitdir).resolve()
    common_dir_file = gitdir / "commondir"
    try:
        common_dir_text = common_dir_file.read_text(encoding="utf-8").strip()
    except OSError:
        return checkout_root, checkout_root
    common_dir = Path(common_dir_text)
    if not common_dir.is_absolute():
        common_dir = (gitdir / common_dir).resolve()
    if common_dir.name == ".git":
        return checkout_root, common_dir.parent
    return checkout_root, common_dir


def _directory_for_lookup(path: Path) -> Path:
    path = Path(path).expanduser()
    if path.exists() and path.is_file():
        path = path.parent
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def _ancestors_inclusive(path: Path) -> list[Path]:
    return [path, *path.parents]


def _normalized_project_trust_keys(path: Path) -> list[str]:
    absolute = Path(path).expanduser()
    if not absolute.is_absolute():
        absolute = (Path.cwd() / absolute).resolve()
    raw = _normalize_project_trust_lookup_key(str(absolute))
    try:
        canonical = _normalize_project_trust_lookup_key(str(absolute.resolve()))
    except OSError:
        canonical = raw
    if raw == canonical:
        return [canonical]
    return [canonical, raw]


def _normalize_project_trust_lookup_key(key: str) -> str:
    return key.lower() if os.name == "nt" else key


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def _payload_cwd(payload: dict[str, Any]) -> Path:
    cwd = _string_value(payload.get("cwd"))
    return Path(cwd) if cwd is not None else Path.cwd()


def _string_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _is_agent_tool_name(value: Any) -> bool:
    return value in {"Agent", "spawn_agent"}


def _is_pre_agent_spawn_payload(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("hook_event_name") == "PreToolUse"
        and _is_agent_tool_name(payload.get("tool_name"))
    )


def _block_output(reason: str) -> dict[str, str]:
    return {"decision": "block", "reason": reason}


def _extract_child_agent_id(value: Any) -> str | None:
    parsed = _parse_json_string(value)
    if parsed is not value:
        return _extract_child_agent_id(parsed)
    if isinstance(value, dict):
        agent_id = _string_value(value.get("agent_id"))
        if agent_id is not None:
            return agent_id
        for child in value.values():
            found = _extract_child_agent_id(child)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = _extract_child_agent_id(child)
            if found is not None:
                return found
    return None


def _extract_child_agent_type(value: Any) -> str | None:
    parsed = _parse_json_string(value)
    if parsed is not value:
        return _extract_child_agent_type(parsed)
    if isinstance(value, dict):
        agent_type = _string_value(value.get("agent_type"))
        if agent_type is not None:
            return agent_type
        for child in value.values():
            found = _extract_child_agent_type(child)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = _extract_child_agent_type(child)
            if found is not None:
                return found
    return None


def _parse_json_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


if __name__ == "__main__":
    raise SystemExit(main())
