package depth

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/bigbizze/rtk-claude-safe/internal/store"
)

const schema = `
CREATE TABLE IF NOT EXISTS sessions(session_id TEXT PRIMARY KEY,cwd TEXT,max_depth INTEGER,policy_error TEXT,updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS agents(agent_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,depth INTEGER NOT NULL,agent_type TEXT,parent_agent_id TEXT,parent_tool_use_id TEXT,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pending_spawns(session_id TEXT NOT NULL,tool_use_id TEXT NOT NULL,parent_agent_id TEXT,parent_depth INTEGER NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(session_id,tool_use_id));
CREATE TABLE IF NOT EXISTS completed_spawns(session_id TEXT NOT NULL,tool_use_id TEXT NOT NULL,child_agent_id TEXT NOT NULL,parent_agent_id TEXT,child_depth INTEGER NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(session_id,tool_use_id));
CREATE INDEX IF NOT EXISTS idx_agents_session_depth ON agents(session_id,depth);
CREATE INDEX IF NOT EXISTS idx_pending_created ON pending_spawns(created_at);`

func connect(home string) (*sql.DB, error) {
	if err := os.MkdirAll(stateDir(home), 0700); err != nil {
		return nil, err
	}
	database, err := store.Open(databasePath(home), "rwc", 5000)
	if err != nil {
		return nil, err
	}
	if _, err = database.Exec("PRAGMA journal_mode=WAL;" + schema); err != nil {
		database.Close()
		return nil, err
	}
	return database, nil
}

func text(value any) string { result, _ := value.(string); return result }
func nullable(value string) any {
	if value == "" {
		return nil
	}
	return value
}
func now() float64             { return float64(time.Now().UnixNano()) / 1e9 }
func agentTool(value any) bool { return value == "Agent" || value == "spawn_agent" }
func block(reason string) any  { return map[string]string{"decision": "block", "reason": reason} }

func Run(input io.Reader, output io.Writer) error {
	raw, err := io.ReadAll(input)
	if err != nil {
		return err
	}
	var payload map[string]any
	if json.Unmarshal(raw, &payload) != nil || payload == nil {
		return nil
	}
	var rawFields map[string]json.RawMessage
	if json.Unmarshal(raw, &rawFields) == nil {
		if response, ok := rawFields["tool_response"]; ok {
			payload["tool_response"] = response
		}
	}
	result, err := handle(payload, homeDir(), "/etc/codex/config.toml", 2*time.Second)
	if err != nil {
		if payload["hook_event_name"] == "PreToolUse" && agentTool(payload["tool_name"]) {
			result = block("rtk-claude-safe failed closed while checking Codex subagent depth: " + err.Error())
		} else {
			return nil
		}
	}
	if result != nil {
		return json.NewEncoder(output).Encode(result)
	}
	return nil
}

func handle(payload map[string]any, home, systemPath string, wait time.Duration) (any, error) {
	if err := os.MkdirAll(stateDir(home), 0700); err != nil {
		return nil, err
	}
	unlock, err := store.Lock(lockPath(home), false)
	if err != nil {
		return nil, err
	}
	defer unlock()
	if _, err = os.Stat(markerPath(home)); err == nil {
		return nil, nil
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	database, err := connect(home)
	if err != nil {
		return nil, err
	}
	defer database.Close()
	session := text(payload["session_id"])
	cwd := text(payload["cwd"])
	if cwd == "" {
		cwd, _ = os.Getwd()
	}
	switch payload["hook_event_name"] {
	case "SessionStart":
		if session == "" {
			return nil, nil
		}
		limit, policyErr := resolve(cwd, home, systemPath)
		return nil, storePolicy(database, session, cwd, limit, policyErr)
	case "PreToolUse":
		return pre(database, payload, home, systemPath, wait)
	case "PostToolUse":
		return nil, post(database, payload, wait)
	}
	return nil, nil
}

func storePolicy(database *sql.DB, session, cwd string, limit *int64, policyErr error) error {
	var reason any
	if policyErr != nil {
		reason = policyErr.Error()
	}
	_, err := database.Exec(`INSERT INTO sessions(session_id,cwd,max_depth,policy_error,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET cwd=excluded.cwd,max_depth=excluded.max_depth,policy_error=excluded.policy_error,updated_at=excluded.updated_at`, session, cwd, limit, reason, now())
	return err
}

func refresh(database *sql.DB, session, cwd, home, systemPath string) (*int64, error, error) {
	limit, policyErr := resolve(cwd, home, systemPath)
	if limit != nil || policyErr != nil {
		return limit, policyErr, storePolicy(database, session, cwd, limit, policyErr)
	}
	var stored sql.NullInt64
	var reason sql.NullString
	err := database.QueryRow("SELECT max_depth,policy_error FROM sessions WHERE session_id=?", session).Scan(&stored, &reason)
	if err == sql.ErrNoRows {
		return nil, nil, storePolicy(database, session, cwd, nil, nil)
	}
	if err != nil {
		return nil, nil, err
	}
	if reason.Valid {
		return nil, fmt.Errorf("%s", reason.String), nil
	}
	if stored.Valid {
		return &stored.Int64, nil, nil
	}
	return nil, nil, nil
}

func callerDepth(database *sql.DB, session, agent string, wait time.Duration) (*int64, error) {
	if agent == "" {
		root := int64(0)
		return &root, nil
	}
	deadline := time.Now().Add(wait)
	for {
		var depth int64
		err := database.QueryRow("SELECT depth FROM agents WHERE session_id=? AND agent_id=?", session, agent).Scan(&depth)
		if err == nil {
			return &depth, nil
		}
		if err != sql.ErrNoRows {
			return nil, err
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return nil, nil
		}
		if remaining > 50*time.Millisecond {
			remaining = 50 * time.Millisecond
		}
		time.Sleep(remaining)
	}
}

func pre(database *sql.DB, payload map[string]any, home, systemPath string, wait time.Duration) (any, error) {
	if !agentTool(payload["tool_name"]) {
		return nil, nil
	}
	session, tool := text(payload["session_id"]), text(payload["tool_use_id"])
	if session == "" {
		return block("rtk-claude-safe could not verify Codex subagent depth: missing session_id"), nil
	}
	if tool == "" {
		return block("rtk-claude-safe could not verify Codex subagent depth: missing tool_use_id"), nil
	}
	cwd := text(payload["cwd"])
	if cwd == "" {
		cwd, _ = os.Getwd()
	}
	limit, policyErr, err := refresh(database, session, cwd, home, systemPath)
	if err != nil {
		return nil, err
	}
	if _, err = database.Exec("DELETE FROM pending_spawns WHERE created_at < ?", now()-600); err != nil {
		return nil, err
	}
	if policyErr != nil {
		return block("rtk-claude-safe could not resolve agents.max_depth: " + policyErr.Error()), nil
	}
	agent := text(payload["agent_id"])
	depth, err := callerDepth(database, session, agent, wait)
	if err != nil {
		return nil, err
	}
	if depth == nil {
		if limit != nil {
			return block(fmt.Sprintf("rtk-claude-safe could not verify Codex subagent depth for agent_id=%q", agent)), nil
		}
		return nil, nil
	}
	if limit != nil && *depth >= *limit {
		return block(fmt.Sprintf("rtk-claude-safe blocked Codex subagent spawn: agents.max_depth=%d, caller_depth=%d", *limit, *depth)), nil
	}
	_, err = database.Exec(`INSERT INTO pending_spawns(session_id,tool_use_id,parent_agent_id,parent_depth,created_at) VALUES(?,?,?,?,?) ON CONFLICT(session_id,tool_use_id) DO UPDATE SET parent_agent_id=excluded.parent_agent_id,parent_depth=excluded.parent_depth,created_at=excluded.created_at`, session, tool, nullable(agent), *depth, now())
	return nil, err
}

func post(database *sql.DB, payload map[string]any, wait time.Duration) error {
	if !agentTool(payload["tool_name"]) {
		return nil
	}
	session, tool := text(payload["session_id"]), text(payload["tool_use_id"])
	if session == "" || tool == "" {
		return nil
	}
	child := extract(payload["tool_response"], "agent_id", 0)
	if child == "" {
		return nil
	}
	var parent sql.NullString
	var depth int64
	err := database.QueryRow("SELECT parent_agent_id,parent_depth FROM pending_spawns WHERE session_id=? AND tool_use_id=?", session, tool).Scan(&parent, &depth)
	if err == sql.ErrNoRows {
		parent.String = text(payload["agent_id"])
		parent.Valid = parent.String != ""
		found, err := callerDepth(database, session, parent.String, wait)
		if err != nil {
			return err
		}
		if found == nil {
			return nil
		}
		depth = *found
	} else if err != nil {
		return err
	}
	depth++
	agentType := extract(payload["tool_response"], "agent_type", 0)
	if agentType == "" {
		input, _ := payload["tool_input"].(map[string]any)
		agentType = text(input["agent_type"])
	}
	transaction, err := database.Begin()
	if err != nil {
		return err
	}
	defer transaction.Rollback()
	_, err = transaction.Exec(`INSERT INTO agents(agent_id,session_id,depth,agent_type,parent_agent_id,parent_tool_use_id,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET session_id=excluded.session_id,depth=max(agents.depth,excluded.depth),agent_type=coalesce(agents.agent_type,excluded.agent_type),parent_agent_id=coalesce(agents.parent_agent_id,excluded.parent_agent_id),parent_tool_use_id=coalesce(agents.parent_tool_use_id,excluded.parent_tool_use_id)`, child, session, depth, nullable(agentType), parent, tool, now())
	if err != nil {
		return err
	}
	_, err = transaction.Exec(`INSERT INTO completed_spawns(session_id,tool_use_id,child_agent_id,parent_agent_id,child_depth,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,tool_use_id) DO UPDATE SET child_agent_id=excluded.child_agent_id,parent_agent_id=excluded.parent_agent_id,child_depth=max(completed_spawns.child_depth,excluded.child_depth),created_at=excluded.created_at`, session, tool, child, parent, depth, now())
	if err != nil {
		return err
	}
	if _, err = transaction.Exec("DELETE FROM pending_spawns WHERE session_id=? AND tool_use_id=?", session, tool); err != nil {
		return err
	}
	return transaction.Commit()
}

func extract(value any, key string, nesting int) string {
	if nesting > 128 {
		return ""
	}
	switch typed := value.(type) {
	case json.RawMessage:
		var direct map[string]json.RawMessage
		if json.Unmarshal(typed, &direct) == nil && direct != nil {
			var found string
			if json.Unmarshal(direct[key], &found) == nil && found != "" {
				return found
			}
			decoder := json.NewDecoder(bytes.NewReader(typed))
			decoder.Token()
			for decoder.More() {
				decoder.Token()
				var child json.RawMessage
				if decoder.Decode(&child) != nil {
					break
				}
				if result := extract(child, key, nesting+1); result != "" {
					return result
				}
			}
			return ""
		}
		var list []json.RawMessage
		if json.Unmarshal(typed, &list) == nil && list != nil {
			for _, child := range list {
				if result := extract(child, key, nesting+1); result != "" {
					return result
				}
			}
			return ""
		}
		var str string
		if json.Unmarshal(typed, &str) == nil {
			return extract(str, key, nesting+1)
		}
	case string:
		if json.Valid([]byte(typed)) {
			return extract(json.RawMessage(typed), key, nesting+1)
		}
	case map[string]any:
		if found := text(typed[key]); found != "" {
			return found
		}
		for _, childKey := range sortedKeys(typed) {
			if found := extract(typed[childKey], key, nesting+1); found != "" {
				return found
			}
		}
	case []any:
		for _, child := range typed {
			if found := extract(child, key, nesting+1); found != "" {
				return found
			}
		}
	}
	return ""
}
