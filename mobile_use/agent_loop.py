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
import inspect
import json
import os
import sys
import time

from .collector import Collector
from .perception_cache import screen_signature
from .session import Session, load_session
from .skills import list_skills, skill_template, write_skill

# Action verbs that carry an (x, y) tap point we can map back to a tree element
# for self-labeling. Center-coordinate gestures only.
_XY_VERBS = {"tap_at_xy", "tap", "long_press", "double_tap"}

# Curated set of ACTION verbs the LLM agent is allowed to call. This is the
# agent's action schema — deliberately NOT dir(helpers), which also exposes ~20
# observation/plumbing functions (appium, ui_tree, find, screenshot, window_size,
# alert, retry_on_disconnect, ...) that an LLM shouldn't invoke as actions.
# get_available_actions() filters this to the verbs that actually exist on the
# current platform's helpers and returns their signature + one-line doc.
ACTION_VERBS = [
    # touch / gestures
    "tap", "tap_safe", "tap_at_xy", "long_press", "double_tap",
    "swipe", "scroll", "scroll_by", "swipe_back", "scroll_into_view",
    # text input
    "type_text", "set_value", "clear_text",
    "press_enter", "press_return", "press_search", "key_event", "hide_keyboard",
    # navigation / hardware keys
    "press_home", "press_back", "press_recents", "open_app_switcher",
    "open_notifications", "close_notifications",
    # app lifecycle
    "launch_app", "activate_app", "terminate_app",
    # device control
    "open_url", "set_clipboard", "set_location", "set_orientation",
    # dialogs / waits
    "alert_accept", "alert_dismiss", "auto_dismiss_dialog",
    "wait", "wait_for_element", "wait_for_app",
]


def _parse_json_block(raw):
    """Parse a strict-JSON object from an LLM reply, tolerating ```json fences.

    Returns the dict, or None if the reply is missing / unparseable / not an object.
    Shared by retarget_action() and the autonomous run() loop.
    """
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
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


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

    def perceive(self, marks=False):
        """Capture current device state — screenshot + UI tree.

        Returns a dict with:
            - screenshot_path: path to PNG
            - ui_tree: list of elements
            - active_app: current foreground app info
            - window_size: {width, height}
            - alert: any visible system alert
            - marks: (when marks=True) a compact, indexed list of interactable
              elements for set-of-marks grounding — each {i, type, label, cx, cy}
              so the LLM can refer to a target by its index.
        """
        self._load_platform()
        h = self._helpers

        # Fast path: one batched snapshot RPC (screenshot+tree+app+size+alert)
        # instead of 5 separate device round-trips. Falls back to per-call
        # perception if the daemon is older / the snapshot fails for any reason.
        state = None
        if hasattr(h, "snapshot"):
            try:
                state = h.snapshot(visible_only=True)
            except Exception:
                state = None
        if state is None:
            state = self._perceive_per_call(h)

        if state.get("active_app") is not None:
            self.session.current_app = state["active_app"]

        if marks:
            state["marks"] = self._set_of_marks(state.get("ui_tree") or [])

        if self.collector:
            self.collector.record_perception(state)

        return state

    def _perceive_per_call(self, h):
        """Per-call perception fallback (older daemon without batched snapshot)."""
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
        except Exception as e:
            state["active_app_error"] = str(e)
        try:
            state["window_size"] = h.window_size()
        except Exception as e:
            state["window_size_error"] = str(e)
        try:
            state["alert"] = h.alert()
        except Exception:
            state["alert"] = None
        return state

    @staticmethod
    def _set_of_marks(tree):
        """Index interactable/labelled elements for set-of-marks grounding.

        Returns a compact list [{i, type, label, cx, cy}] the LLM can reference
        by index (e.g. tap_at_xy at mark 3's cx,cy) — far cheaper and less
        error-prone than feeding the raw uncapped tree.
        """
        marks = []
        for el in tree:
            if not isinstance(el, dict):
                continue
            label = (el.get("label") or el.get("name")
                     or el.get("text") or el.get("content_desc") or "")
            cx, cy = el.get("cx"), el.get("cy")
            if cx is None or cy is None:
                continue
            # Keep elements that are actionable or carry a label.
            if not (label or el.get("clickable") or el.get("accessible")):
                continue
            marks.append({
                "i": len(marks),
                "type": el.get("type", ""),
                "label": label,
                "cx": cx, "cy": cy,
            })
        return marks

    def act(self, action, expect=None, **kwargs):
        """Execute an action on the device.

        Args:
            action: name of the helper function to call (e.g. 'tap', 'type_text')
            expect: optional callable predicate(new_state) -> bool that verifies
                the action took effect. When given, act() re-perceives, checks it,
                retries the action once if unverified, and reports a real
                ``verified`` flag instead of assuming success (a silent no-op tap
                otherwise gets logged as success and corrupts the belief state).
            **kwargs: arguments to pass to the function

        Returns:
            dict with 'result' (+ 'verified' when expect was given) or 'error'.
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
        except Exception as e:
            self.session.record_action(
                f"{action}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})",
                error=str(e),
            )
            return {"error": str(e)}

        verified = None
        if expect is not None:
            verified = self._verify(expect)
            if not verified:
                # Retry the action once before believing it failed.
                try:
                    result = fn(**kwargs)
                    verified = self._verify(expect)
                except Exception:
                    verified = False

        self.session.record_action(
            f"{action}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})",
            result=result,
            success=verified,  # None when unverified, else the real verify outcome
        )
        self._action_stack.append({
            "action": action, "kwargs": kwargs, "timestamp": time.time(),
        })
        out = {"result": result}
        if verified is not None:
            out["verified"] = bool(verified)
        return out

    def _verify(self, expect):
        """Re-perceive and evaluate the expect predicate. False on any error."""
        try:
            return bool(expect(self.perceive()))
        except Exception:
            return False

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
            # In-app back gesture — NOT Home, which abandons the app entirely.
            h.swipe_back()
        return {"undone": last["action"]}

    def find_element(self, **criteria):
        """Find a UI element using platform-appropriate criteria."""
        self._load_platform()
        return self._helpers.find(**criteria)

    def get_available_actions(self):
        """The agent's curated action schema for the current platform.

        Returns {verb: {"signature": "(...)", "doc": "<first doc line>"}} for the
        ACTION_VERBS that actually exist on this platform's helpers — a grounded,
        callable surface, NOT the raw dir(helpers) (which also exposes ~20
        observation/plumbing functions an LLM should never invoke as an action).
        """
        self._load_platform()
        h = self._helpers
        actions = {}
        for verb in ACTION_VERBS:
            fn = getattr(h, verb, None)
            if fn is None or not callable(fn):
                continue
            try:
                sig = str(inspect.signature(fn))
            except (ValueError, TypeError):
                sig = "(...)"
            doc = (inspect.getdoc(fn) or "").strip().splitlines()
            actions[verb] = {"signature": sig, "doc": doc[0] if doc else ""}
        return actions

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

    def run(self, task, llm, max_steps=20):
        """Autonomous perceive → reason → act loop driving the device to a goal.

        Args:
            task: natural-language goal (e.g. "search for 'coffee' in the app").
            llm:  callable(prompt:str) -> str returning ONE strict-JSON action:
                    {"fn": "<verb>", "kwargs": {...}}   — take an action, or
                    {"done": true, "reason": "<why>"}   — task complete.
            max_steps: safety cap on act cycles.

        Returns {"status": "done"|"max_steps", "steps": n, "history": [...]}.
        """
        if not callable(llm):
            raise TypeError("run: llm must be callable(prompt) -> str")
        self.start()
        history = []
        timings = {"perceive_ms": 0.0, "decide_ms": 0.0, "act_ms": 0.0,
                   "llm_calls": 0, "steps": 0}
        for step in range(max_steps):
            t0 = time.perf_counter()
            state = self.perceive(marks=True)
            t1 = time.perf_counter()
            # decide phase = build prompt + the LLM round-trip (the latency hotspot).
            prompt = self._build_agent_prompt(task, state, history)
            try:
                raw = llm(prompt)
            except Exception as e:
                self._accrue(timings, perceive=(t1 - t0), decide=(time.perf_counter() - t1))
                history.append({"step": step, "error": f"llm error: {e}"})
                break
            t2 = time.perf_counter()
            timings["llm_calls"] += 1
            action = _parse_json_block(raw)
            if action is None:
                self._accrue(timings, perceive=(t1 - t0), decide=(t2 - t1))
                history.append({"step": step, "error": "unparseable LLM reply",
                                "raw": str(raw)[:300]})
                continue
            if action.get("done") is True:
                self._accrue(timings, perceive=(t1 - t0), decide=(t2 - t1))
                return {"status": "done", "steps": step, "reason": action.get("reason"),
                        "history": history, "timings": self._finalize_timings(timings)}
            fn = action.get("fn")
            if not isinstance(fn, str):
                self._accrue(timings, perceive=(t1 - t0), decide=(t2 - t1))
                history.append({"step": step, "error": "LLM reply missing 'fn'",
                                "raw": action})
                continue
            t3 = time.perf_counter()
            res = self.act(fn, **(action.get("kwargs") or {}))
            t4 = time.perf_counter()
            self._accrue(timings, perceive=(t1 - t0), decide=(t2 - t1), act=(t4 - t3))
            history.append({"step": step, "action": fn,
                            "kwargs": action.get("kwargs") or {}, "result": res,
                            "timing": {"perceive_ms": (t1 - t0) * 1e3,
                                       "decide_ms": (t2 - t1) * 1e3,
                                       "act_ms": (t4 - t3) * 1e3}})
            if "error" not in res:
                self._maybe_capture_detection(state, fn, action.get("kwargs") or {})
        return {"status": "max_steps", "steps": max_steps, "history": history,
                "timings": self._finalize_timings(timings)}

    @staticmethod
    def _match_element(tree, x, y):
        """Smallest tree element whose box contains (x, y) — the tapped target.

        Smallest-area wins so a tap inside a button nested in a cell labels the
        button, not the enclosing container.
        """
        best, best_area = None, None
        for el in tree or []:
            if not isinstance(el, dict):
                continue
            ex, ey, ew, eh = el.get("x"), el.get("y"), el.get("w"), el.get("h")
            if None in (ex, ey, ew, eh):
                continue
            if ex <= x <= ex + ew and ey <= y <= ey + eh:
                area = ew * eh
                if best_area is None or area < best_area:
                    best, best_area = el, area
        return best

    def _maybe_capture_detection(self, state, fn, kwargs):
        """Self-label the tapped element as a detection sample (free — from the tree).

        Best-effort and fully swallowed: capturing training data must never break
        the action loop.
        """
        if not self.collector or fn not in _XY_VERBS:
            return
        x, y = kwargs.get("x"), kwargs.get("y")
        if x is None or y is None:
            return
        el = self._match_element(state.get("ui_tree") or [], x, y)
        if el is None:
            return
        bbox = (el.get("x"), el.get("y"), el.get("w"), el.get("h"))
        if any(v is None for v in bbox):
            return
        label = (el.get("label") or el.get("name") or el.get("text")
                 or el.get("content_desc") or el.get("type") or "")
        try:
            self.collector.record_detection_sample(
                screenshot_path=state.get("screenshot_path"),
                bbox=bbox, label=label,
                screen_sig=screen_signature(state.get("marks"), state.get("active_app")),
                window_size=state.get("window_size"),
                action=fn, active_app=state.get("active_app"),
            )
        except Exception:
            pass

    @staticmethod
    def _accrue(timings, perceive=0.0, decide=0.0, act=0.0):
        """Add one step's phase durations (seconds) into the running totals (ms)."""
        timings["perceive_ms"] += perceive * 1e3
        timings["decide_ms"] += decide * 1e3
        timings["act_ms"] += act * 1e3
        timings["steps"] += 1

    @staticmethod
    def _finalize_timings(timings):
        """Add per-step averages + total wall time to the accumulated timings."""
        n = max(timings["steps"], 1)
        total = timings["perceive_ms"] + timings["decide_ms"] + timings["act_ms"]
        return {
            **timings,
            "total_ms": round(total, 3),
            "avg_perceive_ms": round(timings["perceive_ms"] / n, 3),
            "avg_decide_ms": round(timings["decide_ms"] / n, 3),
            "avg_act_ms": round(timings["act_ms"] / n, 3),
        }

    def _build_agent_prompt(self, task, state, history):
        """Render the perceive→act prompt: goal + set-of-marks + action schema."""
        actions = {k: v["signature"] for k, v in self.get_available_actions().items()}
        return _AGENT_PROMPT.format(
            task=task,
            app=json.dumps(state.get("active_app"), default=str),
            alert=json.dumps(state.get("alert"), default=str),
            marks=json.dumps(state.get("marks") or [], default=str),
            actions=json.dumps(actions, default=str),
            history=json.dumps(history[-5:], default=str),
        )

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


_AGENT_PROMPT = """You are driving a real mobile device to accomplish a task.

TASK: {task}

CURRENT SCREEN
  foreground app: {app}
  system alert: {alert}
  interactable elements (set-of-marks — refer to one by its cx,cy):
{marks}

AVAILABLE ACTIONS (verb -> signature). Call ONLY these:
{actions}

RECENT STEPS (most recent last):
{history}

Reply with ONE strict-JSON object, no markdown, no commentary:
  {{"fn": "<verb>", "kwargs": {{...}}}}   to take an action
  {{"done": true, "reason": "<why the task is complete>"}}   when finished

Rules:
- Tap a mark by passing its coordinates, e.g. {{"fn": "tap_at_xy", "kwargs": {{"x": 120, "y": 480}}}}.
- To type then submit, type_text then press_enter (don't put '\\n' in the text).
- Prefer launch_app/open_url to reach a screen directly over many taps.
- If a system alert blocks you, use auto_dismiss_dialog / alert_accept / alert_dismiss.
- Return ONLY the JSON object.
"""


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

    parsed = _parse_json_block(raw)
    if parsed is None or parsed.get("skip") is True or not isinstance(parsed.get("fn"), str):
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
            "mobile-use agent [--ios|--android] [--session NAME] [--task 'GOAL']\n"
            "\n"
            "With --task, runs the autonomous perceive→reason→act loop toward GOAL.\n"
            "Without --task, opens the interactive REPL (helpers pre-imported).\n"
            "\n"
            "Options:\n"
            "  --task 'GOAL'    Run autonomously toward GOAL (needs an LLM, see below)\n"
            "  --session NAME   Resume / create a named session (default: 'default')\n"
            "  -h, --help       Show this message\n"
            "\n"
            "Environment:\n"
            "  MOBILE_USE_HEADED=1        Spin up live MJPEG viewer in the browser\n"
            "  ANTHROPIC_API_KEY=...      LLM for --task (also `pip install anthropic`)\n"
            "  MOBILE_USE_AGENT_MODEL     Model for --task (default claude-sonnet-4-6)\n"
            "  MOBILE_USE_AGENT_MAX_STEPS Cap on autonomous act cycles (default 20)\n"
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
    task = None
    if args:
        for i, a in enumerate(args):
            if a == "--session" and i + 1 < len(args):
                session_name = args[i + 1]
            elif a == "--task" and i + 1 < len(args):
                task = args[i + 1]

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
        if task:
            llm = _default_llm()
            if llm is None:
                sys.exit(
                    "Autonomous --task needs an LLM. Set ANTHROPIC_API_KEY and "
                    "`pip install anthropic`, or call AgentLoop.run(task, llm) "
                    "from Python with your own llm callable."
                )
            max_steps = int(os.environ.get("MOBILE_USE_AGENT_MAX_STEPS", "20"))
            result = agent.run(task, llm, max_steps=max_steps)
            print(json.dumps(result, indent=2, default=str))
        else:
            agent.run_interactive()
    finally:
        if viewer is not None:
            viewer.stop()


def _default_llm():
    """Build a callable(prompt)->str backed by Anthropic, when configured.

    Returns None (caller prints a hint) if ANTHROPIC_API_KEY is unset or the
    anthropic SDK isn't installed — the harness keeps anthropic an OPTIONAL dep,
    so --task degrades gracefully and AgentLoop.run() still accepts any llm.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    client = anthropic.Anthropic(api_key=key)
    model = os.environ.get("MOBILE_USE_AGENT_MODEL", "claude-sonnet-4-6")

    def _llm(prompt):
        msg = client.messages.create(
            model=model, max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")

    return _llm
