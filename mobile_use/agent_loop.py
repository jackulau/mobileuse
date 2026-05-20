"""Persistent agent loop — continuous perceive → reason → act cycle.

  mobile-use agent --ios          start iOS agent loop
  mobile-use agent --android      start Android agent loop
  mobile-use agent                auto-detect platform

The agent loop provides infrastructure for LLM agents to continuously
interact with a mobile device. It handles:
  - Perception: screenshots + UI tree on every cycle
  - Action execution with error recovery
  - Session continuity (state persists between runs)
  - Auto skill authoring (writes .md files for discoveries)
  - Action history with rollback support
"""
import os
import sys
import time

from .collector import Collector
from .session import Session, load_session
from .skills import write_skill, list_skills, skill_template


class AgentLoop:
    """Core agent loop infrastructure.

    This provides the perceive/act cycle that an LLM agent hooks into.
    The LLM provides the reasoning; this class provides the device interface.
    """

    def __init__(self, platform="ios", session_name="default", collect=True):
        self.platform = platform
        self.session = load_session(name=session_name, platform=platform)
        self.collector = Collector(session_name=session_name, platform=platform) if collect else None
        self._helpers = None
        self._admin = None
        self._action_stack = []

    def _load_platform(self):
        """Lazy-load the correct platform module."""
        if self._helpers is not None:
            return
        if self.platform == "ios":
            import iphone_harness.helpers as h
            import iphone_harness.admin as a
        elif self.platform == "android":
            import android_harness.helpers as h
            import android_harness.admin as a
        else:
            raise RuntimeError(f"Unknown platform: {self.platform}")
        self._helpers = h
        self._admin = a

    def start(self):
        """Initialize the device connection."""
        self._load_platform()
        self._admin.ensure_daemon()

    def perceive(self):
        """Capture current device state — screenshot + UI tree.

        Returns a dict with:
            - screenshot_path: path to PNG
            - ui_tree: list of elements
            - active_app: current foreground app info
            - window_size: {width, height}
            - alerts: any visible system alerts
        """
        self._load_platform()
        h = self._helpers

        state = {}
        try:
            state["screenshot_path"] = h.screenshot()
        except Exception as e:
            state["screenshot_error"] = str(e)

        try:
            state["ui_tree"] = h.ui_tree(visible_only=True)
        except Exception as e:
            state["ui_tree_error"] = str(e)
            state["ui_tree"] = []

        try:
            state["active_app"] = h.active_app()
            self.session.current_app = state["active_app"]
        except Exception as e:
            state["active_app_error"] = str(e)

        try:
            state["window_size"] = h.window_size()
        except Exception as e:
            state["window_size_error"] = str(e)

        try:
            a = h.alert()
            state["alert"] = a
        except Exception:
            state["alert"] = None

        if self.collector:
            self.collector.record_perception(state)

        return state

    def act(self, action, **kwargs):
        """Execute an action on the device.

        Args:
            action: name of the helper function to call (e.g. 'tap', 'type_text')
            **kwargs: arguments to pass to the function

        Returns:
            dict with 'result' or 'error' key
        """
        self._load_platform()
        h = self._helpers

        fn = getattr(h, action, None)
        if fn is None:
            return {"error": f"Unknown action: {action}"}

        # Auto-dismiss unexpected dialogs before acting
        try:
            h.auto_dismiss_dialog()
        except Exception:
            pass

        try:
            result = fn(**kwargs)
            self.session.record_action(
                f"{action}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})",
                result=result,
            )
            self._action_stack.append({
                "action": action, "kwargs": kwargs,
                "timestamp": time.time(),
            })
            return {"result": result}
        except Exception as e:
            self.session.record_action(
                f"{action}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})",
                error=str(e),
            )
            return {"error": str(e)}

    def undo_last(self):
        """Best-effort undo of the last action (press back / go home)."""
        self._load_platform()
        h = self._helpers

        if not self._action_stack:
            return {"error": "No actions to undo"}

        last = self._action_stack.pop()
        if self.platform == "android":
            h.press_back()
        else:
            h.appium("mobile: pressButton", name="home")
        return {"undone": last["action"]}

    def find_element(self, **criteria):
        """Find a UI element using platform-appropriate criteria."""
        self._load_platform()
        return self._helpers.find(**criteria)

    def get_available_actions(self):
        """List all available actions for the current platform."""
        self._load_platform()
        return [k for k in dir(self._helpers)
                if not k.startswith("_") and callable(getattr(self._helpers, k))]

    def write_discovery(self, app_id, title, selectors=None, steps=None, gotchas=None):
        """Auto-write a domain skill when the agent discovers something non-obvious."""
        content = skill_template(
            app_id=app_id,
            title=title,
            platform=self.platform,
            selectors=selectors or {},
            steps=steps or [],
            gotchas=gotchas,
        )
        path = write_skill(app_id, title, content)
        self.session.learn_pattern(f"Wrote skill: {app_id}/{title}")
        return path

    def get_context(self):
        """Get full context for LLM injection — session state + available skills."""
        app = self.session.current_app
        app_id = None
        if isinstance(app, dict):
            app_id = app.get("bundleId") or app.get("package")

        context = {
            "platform": self.platform,
            "session_summary": self.session.summary(),
            "available_actions": self.get_available_actions(),
        }

        if app_id:
            skills = list_skills(app_id)
            if skills:
                context["domain_skills"] = skills

        return context

    def run_interactive(self):
        """Run an interactive REPL for manual agent testing.

        Type Python expressions using helpers directly:
            > tap(find(text="Send"))
            > screenshot()
            > ui_tree(visible_only=True)[:3]
        """
        self.start()
        h = self._helpers
        ns = {k: v for k, v in vars(h).items() if not k.startswith("_")}
        ns["agent"] = self
        ns["session"] = self.session
        ns["perceive"] = self.perceive
        ns["act"] = self.act

        print(f"mobile-use agent ({self.platform})")
        print(f"Session: {self.session.name}")
        print(f"{self.session.summary()}")
        print("Type Python expressions. Helpers pre-imported. Ctrl+D to exit.\n")

        import code
        code.interact(local=ns, banner="", exitmsg="Session saved.")
        self.session.save()


def run_agent(platform=None, args=None):
    """Entry point from CLI."""
    if platform is None:
        from .cli import _detect_platform
        platform = _detect_platform()
        if platform is None:
            sys.exit(
                "Cannot auto-detect platform. Specify --ios or --android.\n"
                "Run `mobile-use --doctor` to check what's connected."
            )

    session_name = "default"
    if args:
        for i, a in enumerate(args):
            if a == "--session" and i + 1 < len(args):
                session_name = args[i + 1]
                break

    agent = AgentLoop(platform=platform, session_name=session_name)
    agent.run_interactive()
