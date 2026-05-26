# Macros — record / replay device action sequences

mobile_use macros let you capture a sequence of helper calls and re-execute
them later, either **literally** (same calls, same args, byte-for-byte) or
**smart** (intent-aware, LLM re-targets steps when the UI shifts).

## Two tiers

| Tier        | Method                         | Cost        | Brittleness          | When to use                  |
|-------------|--------------------------------|-------------|----------------------|------------------------------|
| Literal     | `record_replay.replay(...)`    | free        | breaks on any UI change | smoke tests, demos, deterministic flows |
| Smart       | `record_replay.replay_smart(...)` | 0–1 LLM call per shifted step | recovers from rename/relayout | flows that need to survive app updates |

Both tiers share the same recording — annotate with intent at record time and
you get both options at replay time for free.

## Quick start

### 1. Record (Python API)

```python
from mobile_use import record_replay
import iphone_harness.helpers as h

with record_replay.recording("compose.py", helpers=h):
    with record_replay.annotate("open compose screen"):
        h.tap(h.find(label="Compose"))
    with record_replay.annotate("type message body"):
        h.type_text("hello world")
        h.tap(h.find(label="Send"))
```

Result: `compose.py` (runnable script) + `compose.py.jsonl` (sidecar with
intent + UI fingerprint per step).

### 2. Record (CLI)

```bash
mobile-use macro record compose --intent "open compose screen"
# Drops you into a Python REPL with `h` (helpers) pre-imported.
# Make calls, then Ctrl+D to stop. The initial intent annotates the first block.
>>> h.tap(h.find(label="Compose"))
>>> h.type_text("hello")
>>> exit()
Saved → /Users/you/.mobile-use/macros/compose.py
```

### 3. Replay literal

```bash
mobile-use macro replay compose          # default: literal
# or
python3 -c 'from mobile_use import record_replay; \
            import iphone_harness.helpers as h; \
            record_replay.replay("compose.py", helpers=h)'
```

### 4. Replay smart

```bash
export MU_MACRO_LLM="my_pkg.llm_client:complete"   # callable(prompt)->str
mobile-use macro replay compose --smart
```

Or programmatically:

```python
def my_llm(prompt: str) -> str:
    response = anthropic_client.messages.create(...)
    return response.content[0].text

record_replay.replay_smart("compose.py", helpers=h, llm=my_llm)
```

## How smart replay decides

For each entry in the journal:

1. If the entry has no intent → run literal.
2. If the entry has intent + a recorded fingerprint:
   - Capture the current UI fingerprint.
   - If recorded fingerprint **matches** current (same app, ≥50% label overlap)
     → run literal.
   - If recorded fingerprint **diverged** + `llm` provided
     → call `agent_loop.retarget_action(intent, recorded_fp, current_ui, ...)`,
       execute the adapted call.
   - If diverged + no `llm` → warn to stderr, run literal anyway.

The fingerprint is captured **once** at each `annotate()` entry, not per call —
so the cost of recording is one `ui_tree()` per intent block, not per tap.

## Storage layout

Default macros directory: `~/.mobile-use/macros/`. Override with `--dir <path>`
or `MU_MACRO_DIR=<path>`.

```
~/.mobile-use/macros/
  compose.py           # runnable literal-replay script
  compose.py.jsonl     # sidecar with intent + fingerprint per step (only present if annotated)
  smoke-test.py
```

## CLI reference

```
mobile-use macro record <name> [--intent <txt>] [--dir <path>] [--ios|--android]
mobile-use macro replay <name> [--smart|--literal] [--on-failure raise|skip] [--dir <path>] [--ios|--android]
mobile-use macro list [--dir <path>]
mobile-use macro show <name> [--dir <path>]
```

## Recovery: when smart replay can't re-target

When `--smart` is on and the LLM returns `{"skip": true}` or unparseable output
for a step, the engine raises `record_replay.MacroStepFailed`. Override with
`--on-failure skip` to log the failure and continue with the next step.

```python
try:
    record_replay.replay_smart("flow.py", helpers=h, llm=my_llm, on_failure="raise")
except record_replay.MacroStepFailed as e:
    print(f"step {e.step_index} ({e.recorded_fn}) intent={e.intent!r}: {e.reason}")
```

## Non-goals (v1)

- **No cross-platform replay** — an iOS macro can't run on Android (different UI
  tree shapes, different helper names).
- **No vision-only re-targeting** — smart replay reads accessibility labels +
  text only; no screenshot OCR fallback yet.
- **No macro editor / GUI** — record + replay via CLI / Python API is the whole
  surface. Edit the `.py` script directly if you need to.
- **No cloud / shared library** — macros live in your local
  `~/.mobile-use/macros/` only.

## Choosing between macros and the agent loop

| Use macros when                              | Use the agent loop when               |
|---------------------------------------------|---------------------------------------|
| Flow is known and deterministic              | Exploring an unfamiliar app           |
| You'll re-run it many times                  | Each run needs different decisions    |
| You want offline reproducibility (no LLM)    | Branching / conditional logic needed  |
| You want a self-contained `.py` artifact     | You want a continuous perceive-act loop|

Smart macros sit in the middle: pre-recorded happy path + LLM repair when
the UI shifts. Cheap when nothing changed, robust when it did.
