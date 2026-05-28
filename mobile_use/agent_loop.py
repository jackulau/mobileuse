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
import json
import os
import sys
import time

from .collector import Collector
from .session import Session, load_session
from .skills import list_skills, skill_template, write_skill


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
            import iphone_harness.admin as a
            import iphone_harness.helpers as h
        elif self.platform == "android":
            import android_harness.admin as a
            import android_harness.helpers as h
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


_RETARGET_PROMPT = """You are a UI re-targeting assistant for replaying a recorded mobile-app action.

Recorded intent: {intent!r}
At record time, the UI looked like:
  app: {recorded_app}
  visible labels (top {n_recorded}): {recorded_labels}
  focused element: {recorded_focused}

Originally invoked: {recorded_call}

The UI is now different:
  app: {current_app}
  visible labels (top {n_current}): {current_labels}
  focused element: {current_focused}

Full current UI tree (compact, top 30):
{current_ui_json}

Pick the SINGLE best adapted action that fulfills the intent on the current UI.
Reply with strict JSON, one of:
  {{"fn": "<helper-fn>", "args": [...], "kwargs": {{...}}}}
  {{"skip": true, "reason": "<short reason>"}}

Rules:
- Prefer label/text-based selectors over xy coordinates when possible.
- Reuse the recorded helper-function name when the same op type still applies.
- Do NOT invent helper functions; stick to names that plausibly exist on the helpers module.
- Return ONLY the JSON object. No markdown, no commentary.
"""


def retarget_action(intent, recorded_fp, current_ui, recorded_call, llm,
                    current_app=None, current_focused=None):
    """Ask an LLM to adapt a recorded action when the UI fingerprint has shifted.

    Args:
        intent:         human label for the recorded segment (from annotate()).
        recorded_fp:    fingerprint dict captured at record time
                        ({"app","labels","focused","count"}).
        current_ui:     list of element dicts from helpers.ui_tree(visible_only=True).
        recorded_call:  dict {fn, args, kwargs} of the literal recorded action.
        llm:            callable(prompt: str) -> str returning strict JSON.
                        Wrap your Anthropic / OpenAI / etc. client to fit.
        current_app:    optional current app bundle id; omitted from prompt if None.
        current_focused: optional currently-focused element label.

    Returns:
        dict with same shape as recorded_call (adapted), or
        None if the LLM signals skip / returns unparseable output / raises.
    """
    if not callable(llm):
        raise TypeError("retarget_action: llm must be callable(prompt) -> str")

    rfp = recorded_fp or {}
    current_labels = []
    seen = set()
    for el in (current_ui or []):
        if not isinstance(el, dict):
            continue
        lbl = (el.get("label") or el.get("name")
               or el.get("text") or el.get("content_desc") or "")
        if lbl and lbl not in seen:
            seen.add(lbl)
            current_labels.append(lbl)
    current_labels = sorted(current_labels)[:20]

    try:
        ui_json = json.dumps((current_ui or [])[:30], default=str)
    except (TypeError, ValueError):
        ui_json = "[]"

    prompt = _RETARGET_PROMPT.format(
        intent=intent,
        recorded_app=rfp.get("app", ""),
        n_recorded=len(rfp.get("labels", []) or []),
        recorded_labels=rfp.get("labels", []),
        recorded_focused=rfp.get("focused"),
        recorded_call=json.dumps(recorded_call, default=str),
        current_app=current_app or "",
        n_current=len(current_labels),
        current_labels=current_labels,
        current_focused=current_focused,
        current_ui_json=ui_json,
    )

    try:
        raw = llm(prompt)
    except Exception:
        return None
    if not isinstance(raw, str):
        return None

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("skip") is True:
        return None
    if not isinstance(parsed.get("fn"), str):
        return None

    return {
        "fn": parsed["fn"],
        "args": list(parsed.get("args") or []),
        "kwargs": dict(parsed.get("kwargs") or {}),
    }


def run_agent(platform=None, args=None):
    """Entry point from CLI."""
    if args and args[0] in {"-h", "--help"}:
        print(
            "mobile-use agent [--ios|--android] [--session NAME]\n"
            "\n"
            "Start the persistent agent REPL loop on the connected device.\n"
            "\n"
            "Options:\n"
            "  --session NAME   Resume / create a named session (default: 'default')\n"
            "  -h, --help       Show this message\n"
            "\n"
            "Environment:\n"
            "  MOBILE_USE_HEADED=1   Spin up live MJPEG viewer in the browser\n"
        )
        return
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

    # --headed: spin up MJPEG viewer + open browser. Stays up for whole REPL.
    viewer = None
    if os.environ.get("MOBILE_USE_HEADED") == "1":
        try:
            from .viewer.server import ViewerServer
            viewer = ViewerServer(platform=platform)
            viewer.start()
            print(f"[mobile-use] live viewer at {viewer.url}", file=sys.stderr)
            try:
                import webbrowser
                webbrowser.open(viewer.url)
            except Exception:
                pass
        except Exception as e:
            print(f"[mobile-use] viewer failed to start: {e} (continuing)",
                  file=sys.stderr)

    agent = AgentLoop(platform=platform, session_name=session_name)
    try:
        agent.run_interactive()
    finally:
        if viewer is not None:
            viewer.stop()
