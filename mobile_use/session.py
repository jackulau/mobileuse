"""Session continuity — persist agent state between runs.

Saves: current app, action history, learned element mappings, navigation stack.
Next `mobile-use agent` picks up where it left off.
"""
import json
import os
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_DIR = Path(os.environ.get("MU_SESSION_DIR", REPO_ROOT / ".claude-workspace" / "sessions")).expanduser()


class Session:
    """Persistent agent session state."""

    def __init__(self, name="default", platform=None):
        self.name = name
        self.platform = platform
        self.path = SESSION_DIR / f"{name}.json"
        self._state = self._load()

    def _load(self):
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "name": self.name,
            "platform": self.platform,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated_at": None,
            "current_app": None,
            "action_history": [],
            "element_mappings": {},
            "navigation_stack": [],
            "learned_patterns": [],
            "goals": [],
            "max_history": 200,
        }

    def save(self):
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self._state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        if self.platform:
            self._state["platform"] = self.platform
        self.path.write_text(json.dumps(self._state, indent=2, default=str), encoding="utf-8")

    @property
    def current_app(self):
        return self._state.get("current_app")

    @current_app.setter
    def current_app(self, app_info):
        # Skip the full-file rewrite when nothing changed — perceive() sets this
        # every step, and the foreground app rarely changes between steps.
        if app_info == self._state.get("current_app"):
            return
        self._state["current_app"] = app_info
        self.save()

    @property
    def action_history(self):
        return self._state.get("action_history", [])

    def record_action(self, action, result=None, error=None, success=None):
        """Record an action taken by the agent.

        success: optional verification outcome (True/False) when the caller
        re-perceived to confirm the action took effect. None = unverified.
        """
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action": action,
            "app": self._state.get("current_app"),
        }
        if result is not None:
            entry["result"] = str(result)[:500]
        if error is not None:
            entry["error"] = str(error)[:500]
        if success is not None:
            entry["success"] = bool(success)
        self._state["action_history"].append(entry)
        max_h = self._state.get("max_history", 200)
        if len(self._state["action_history"]) > max_h:
            self._state["action_history"] = self._state["action_history"][-max_h:]
        self.save()

    @property
    def navigation_stack(self):
        return self._state.get("navigation_stack", [])

    def push_screen(self, screen_id, metadata=None):
        """Record navigation to a new screen."""
        entry = {"screen": screen_id, "timestamp": time.strftime("%H:%M:%S")}
        if metadata:
            entry["metadata"] = metadata
        self._state["navigation_stack"].append(entry)
        if len(self._state["navigation_stack"]) > 50:
            self._state["navigation_stack"] = self._state["navigation_stack"][-50:]
        self.save()

    def pop_screen(self):
        """Record navigation back."""
        if self._state["navigation_stack"]:
            return self._state["navigation_stack"].pop()
        return None

    def learn_element(self, key, selector, platform=None):
        """Remember a stable element selector for future use.

        key: human-readable name (e.g. "messages_compose_button")
        selector: the selector that works (e.g. "name == 'messageBodyField'")
        """
        self._state["element_mappings"][key] = {
            "selector": selector,
            "platform": platform or self.platform,
            "learned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.save()

    def get_element(self, key):
        """Retrieve a learned element selector."""
        mapping = self._state["element_mappings"].get(key)
        if mapping:
            return mapping["selector"]
        return None

    def learn_pattern(self, pattern):
        """Record a non-obvious pattern the agent discovered."""
        self._state["learned_patterns"].append({
            "pattern": pattern,
            "learned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        if len(self._state["learned_patterns"]) > 100:
            self._state["learned_patterns"] = self._state["learned_patterns"][-100:]
        self.save()

    def set_goal(self, goal):
        """Set the current agent goal."""
        self._state["goals"].append({
            "goal": goal,
            "status": "active",
            "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        self.save()

    def complete_goal(self, goal=None):
        """Mark the most recent (or matching) goal as complete."""
        for g in reversed(self._state["goals"]):
            if g["status"] == "active":
                if goal is None or g["goal"] == goal:
                    g["status"] = "complete"
                    g["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                    break
        self.save()

    @property
    def active_goals(self):
        return [g for g in self._state.get("goals", []) if g["status"] == "active"]

    def summary(self):
        """Brief state summary for LLM context injection."""
        parts = []
        if self.current_app:
            parts.append(f"Current app: {self.current_app}")
        if self.active_goals:
            parts.append(f"Active goals: {[g['goal'] for g in self.active_goals]}")
        if self.navigation_stack:
            parts.append(f"Nav stack: {[s['screen'] for s in self.navigation_stack[-5:]]}")
        n_actions = len(self.action_history)
        if n_actions:
            last = self.action_history[-1]
            parts.append(f"Last action: {last['action']} ({n_actions} total)")
        n_elements = len(self._state.get("element_mappings", {}))
        if n_elements:
            parts.append(f"Learned elements: {n_elements}")
        return "\n".join(parts) if parts else "Fresh session — no prior state."

    def reset(self):
        """Clear all state (keep name and platform)."""
        name, platform = self.name, self.platform
        self._state = {
            "name": name,
            "platform": platform,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated_at": None,
            "current_app": None,
            "action_history": [],
            "element_mappings": {},
            "navigation_stack": [],
            "learned_patterns": [],
            "goals": [],
            "max_history": 200,
        }
        self.save()


def list_sessions():
    """List all saved sessions."""
    if not SESSION_DIR.exists():
        return []
    return [p.stem for p in SESSION_DIR.glob("*.json")]


def load_session(name="default", platform=None):
    """Load or create a session."""
    return Session(name=name, platform=platform)
