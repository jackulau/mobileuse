"""mobile-use macro — record / replay device action sequences.

Subcommands:
  record <name> [--intent <txt>]   Open Python REPL with helpers pre-imported
                                   and recording active. Ctrl+D / exit() to stop.
                                   `--intent` opens an initial annotate(...) block.
  replay <name> [--smart] [--literal]
                                   Default: literal replay (re-runs the .py script).
                                   `--smart` enables fingerprint-aware LLM re-targeting
                                   (requires MU_MACRO_LLM env to point at a callable).
  list                             Show saved macros with mtime + step count.
  show <name>                      Print the recorded .py script.

Common flags:
  --dir <path>      Macro storage directory. Default: ~/.mobile-use/macros/
                    (override globally via MU_MACRO_DIR env).
  --ios / --android Platform selector. Auto-detects when one device connected.
  --on-failure raise|skip
                    For --smart replay: what to do when LLM can't re-target a step.
                    Default: raise (abort on first failure).
"""
import argparse
import datetime
import importlib
import os
import sys
from pathlib import Path

from . import record_replay


def _default_dir() -> Path:
    return Path(os.environ.get(
        "MU_MACRO_DIR", str(Path.home() / ".mobile-use" / "macros")
    ))


def _resolve_path(directory: Path, name: str) -> Path:
    if not name.endswith(".py"):
        name = name + ".py"
    return directory / name


def _load_helpers(platform: str):
    if platform == "ios":
        import iphone_harness.helpers as h
        return h
    if platform == "android":
        import android_harness.helpers as h
        return h
    raise ValueError(f"unknown platform: {platform!r}")


def _resolve_llm():
    """Look up MU_MACRO_LLM env: dotted import path 'module:callable' or 'module'.

    Returns the callable or None. Callable must accept(prompt: str) -> str.
    """
    spec = os.environ.get("MU_MACRO_LLM")
    if not spec:
        return None
    mod_name, _, attr = spec.partition(":")
    if not mod_name:
        return None
    try:
        mod = importlib.import_module(mod_name)
    except ImportError:
        return None
    target = getattr(mod, attr, None) if attr else getattr(mod, "llm", None)
    return target if callable(target) else None


def _count_steps(path: Path) -> int:
    n = 0
    try:
        for line in path.read_text().splitlines():
            if line.strip().startswith("h."):
                n += 1
    except OSError:
        return -1
    return n


def cmd_record(parsed, platform):
    directory = Path(parsed.dir or str(_default_dir()))
    directory.mkdir(parents=True, exist_ok=True)
    out = _resolve_path(directory, parsed.name)

    helpers = _load_helpers(platform)

    print(f"Recording → {out}")
    print("Helpers pre-imported as `h`. Make calls; Ctrl+D / exit() to stop.")
    if parsed.intent:
        print(f"Initial intent: {parsed.intent!r}")

    record_replay.start_recording(str(out), helpers=helpers)
    initial_block = None
    if parsed.intent:
        initial_block = record_replay.annotate(parsed.intent)
        initial_block.__enter__()
    try:
        ns = {
            "h": helpers, "helpers": helpers,
            "record_replay": record_replay,
            "annotate": record_replay.annotate,
        }
        import code
        code.interact(local=ns, banner="", exitmsg="")
    finally:
        if initial_block is not None:
            try:
                initial_block.__exit__(None, None, None)
            except Exception:
                pass
        path = record_replay.stop_recording()
        print(f"Saved → {path}")
    return 0


def cmd_replay(parsed, platform):
    directory = Path(parsed.dir or str(_default_dir()))
    path = _resolve_path(directory, parsed.name)
    if not path.exists():
        print(f"Macro not found: {path}", file=sys.stderr)
        return 2

    helpers = _load_helpers(platform)

    if parsed.smart and parsed.literal:
        print("Pass either --smart or --literal, not both.", file=sys.stderr)
        return 2

    if parsed.smart:
        llm = _resolve_llm()
        if llm is None:
            print(
                "[record_replay] --smart enabled but MU_MACRO_LLM is unset or "
                "points to a non-callable — running literal-with-warnings.",
                file=sys.stderr,
            )
        results = record_replay.replay_smart(
            str(path), helpers, llm=llm, on_failure=parsed.on_failure,
        )
        for r in results:
            extra = ""
            if r.get("intent"):
                extra = f"  intent={r['intent']!r}"
            print(f"  step {r['step']:>3}  {r['fn']:<20}  → {r['outcome']}{extra}")
    else:
        record_replay.replay(str(path), helpers=helpers)
    return 0


def cmd_list(parsed):
    directory = Path(parsed.dir or str(_default_dir()))
    if not directory.exists():
        print(f"No macros directory at {directory}")
        return 0
    macros = sorted(directory.glob("*.py"))
    if not macros:
        print(f"No macros in {directory}")
        return 0
    print(f"Macros in {directory}:")
    for p in macros:
        mt = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        steps = _count_steps(p)
        sidecar = p.with_suffix(".py.jsonl")
        annotated = " (annotated)" if sidecar.exists() else ""
        print(f"  {p.stem:30}  {mt}  {steps} steps{annotated}")
    return 0


def cmd_show(parsed):
    directory = Path(parsed.dir or str(_default_dir()))
    path = _resolve_path(directory, parsed.name)
    if not path.exists():
        print(f"Macro not found: {path}", file=sys.stderr)
        return 2
    print(path.read_text())
    return 0


def _build_parser():
    p = argparse.ArgumentParser(prog="mobile-use macro", add_help=False)
    p.add_argument("subcommand", nargs="?", default=None,
                   choices=[None, "record", "replay", "list", "show"])
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--smart", action="store_true")
    p.add_argument("--literal", action="store_true")
    p.add_argument("--intent", default=None)
    p.add_argument("--dir", default=None)
    p.add_argument("--on-failure", dest="on_failure",
                   choices=["raise", "skip"], default="raise")
    p.add_argument("-h", "--help", action="store_true")
    return p


def main(args=None, platform=None):
    args = list(args) if args is not None else sys.argv[1:]
    parser = _build_parser()

    # argparse can't gracefully handle "no subcommand" with choices=[None, ...],
    # so peek manually for help/empty.
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        return 0

    parsed = parser.parse_args(args)

    if parsed.help:
        print(__doc__)
        return 0

    sub = parsed.subcommand
    if sub is None:
        print(__doc__)
        return 0

    if sub == "list":
        return cmd_list(parsed)
    if sub == "show":
        if not parsed.name:
            print("mobile-use macro show: requires <name>", file=sys.stderr)
            return 2
        return cmd_show(parsed)

    # record / replay need a name + platform
    if not parsed.name:
        print(f"mobile-use macro {sub}: requires <name>", file=sys.stderr)
        return 2

    if platform is None:
        from .cli import _detect_platform
        platform = _detect_platform()
        if platform is None:
            print(
                "Cannot detect platform. Pass --ios or --android.",
                file=sys.stderr,
            )
            return 2

    if sub == "record":
        return cmd_record(parsed, platform)
    if sub == "replay":
        return cmd_replay(parsed, platform)

    print(f"Unknown subcommand: {sub}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
