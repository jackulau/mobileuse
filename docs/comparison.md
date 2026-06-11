# Why mobile_use — honest comparison

How mobile_use stacks up against the field. Every mobile_use cell names the
shipping command or module — if it isn't named here, we don't claim it.

| Capability | mobile_use | raw Appium | Maestro | mobile-mcp | DroidRun | AppAgent | scrcpy |
|---|---|---|---|---|---|---|---|
| **Install** | `pip install` + `mobile-use bootstrap` (one command installs Appium, drivers, system deps — mac/linux/windows branches; `mobile_use/bootstrap.py`) | npm + drivers + caps boilerplate by hand | one binary (Java) | npm package; needs Appium-like setup per platform | pip; Android-only deps | research repo; manual setup | one binary |
| **iOS + Android, one API** | yes — same verbs both platforms, parity-enforced (`tests/test_api_parity.py` CORE set) | yes (two drivers, you write the abstraction) | yes (flows) | yes | Android only | Android only | Android only (mirror) |
| **Host OS coverage** | macOS, Linux, Windows (`mobile_use/_platform.py`; Windows winget bootstrap; CI matrix all three) | all three | all three | all three | linux/mac | linux/mac | all three |
| **iOS without a Mac at runtime** | yes — `mobile-use ios install-wda <ipa>` (pre-signed WDA via pymobiledevice3) or remote-Mac daemon (`--remote-daemon`) | no (Mac required for WDA) | no real-device iOS without Mac | no | n/a | n/a | n/a |
| **Wireless devices** | first-class: `mobile-use android pair` (reboot-surviving), `android wifi --persist`, `ios wifi --persist` (mDNS), cable-free iOS 17+ tunnel helper (`mobile-use ios tunnel`) | manual adb/WDA wiring | adb-level | adb-level | adb-level | adb-level | `scrcpy --tcpip` |
| **Remembers devices + auto-reconnect** | yes — remember-store (`mobile_use/wifi_store.py`), `mobile-use wifi reconnect`, self-healing ensure hooks on both daemons (`_maybe_reconnect_wifi_device` / `_maybe_refresh_wifi_wda`) | no | no | no | no | no | last-device flag only |
| **Multi-device simultaneously** | yes — named daemons + `DevicePool` (`mobile_use/multibox.py`: shared Appium server, auto per-device driver ports, `from_connected()` / `from_remembered()`) | manual port juggling | sequential flows | one device per server | no | no | one window per device |
| **MCP server** | built-in, dependency-free stdio: `mobile-use mcp` (`mobile_use/mcp_server.py`; tools generated from the curated action set, screenshot returns MCP image) | no | no | yes (its whole product) | yes | no | no |
| **LLM agent loop** | built-in: `mobile-use agent --task ...` (`mobile_use/agent_loop.py`; perceive→reason→act, session continuity, perception cache) | no | no (you script flows) | no (client's LLM drives) | yes | yes | no |
| **Multimodal grounding** | yes — screenshots flow to images-capable LLMs (`_default_llm` base64 image blocks; `pip install mobile-use[agent]`), set-of-marks from the accessibility tree, optional local detector (`[detection]`/`[yolo]` extras) | no | no | screenshots via client | yes | yes | n/a |
| **Agent safety** | curated verb allowlist + destructive gate (`MU_ALLOW_DESTRUCTIVE`, `agent_loop.act()`); TCP RPC token auth (`IPH/ANH_TOKEN`, auto-token files) | n/a | n/a | no gate | no gate | no | n/a |
| **Live viewer** | interactive browser viewer: click-to-tap, type, key passthrough (`--headed`, `mobile-use devices view`; `mobile_use/viewer/`); `--read-only` for mirror | no | Maestro Studio | no | no | no | yes (gold standard for fps) |
| **File/app management verbs** | `install_app` / `uninstall_app` / `push_file` / `pull_file` on both platforms (daemon RPC, remote-transport safe) | yes (driver methods) | partial | yes | partial | no | drag-drop apk |
| **Macros / record-replay** | `mobile-use macro record/replay [--smart]` (literal + LLM-adaptive replay) | no | yes (flows = its model) | no | no | partial | no |
| **Diagnostics** | `mobile-use --doctor` (14+ checks, actionable fixes, reads your .env), `mobile-use selfcheck` (validates the harness itself, device-free), `mobile-use quickstart` (end-to-end smoke) | appium-doctor (basic) | `maestro doctor` | no | no | no | no |
| **Tested install path** | CI runs a REAL fresh ubuntu container install (`Dockerfile.linux-test`) + non-editable `pip install .` smoke + 3-OS × 3-python matrix (`.github/workflows/ci.yml`) | n/a | binary release | npm publish | pip publish | no | binary release |

## Where others win

Honesty cuts both ways:

- **scrcpy** mirrors at 30–60fps with near-zero latency; our viewer is MJPEG at
  ~6fps — fine for watching an agent, not for gaming. Use scrcpy when you want
  a screen, mobile_use when you want an agent.
- **Maestro** has a YAML flow language with a big ecosystem of CI integrations.
  If your team writes declarative UI test flows by hand, Maestro's DSL is more
  mature than our macro recorder.
- **raw Appium** is the substrate — anything it can do, you can do by dropping
  to `appium("mobile: ...")` (the escape hatch every helpers module ships).
  Direct Appium gives you the full client ecosystem (Java/JS/Ruby clients).
- **mobile-mcp** is MCP-native end-to-end; ours is one subcommand of a larger
  harness. If MCP is ALL you need, both work — ours adds doctor/bootstrap/
  multi-device/wireless plumbing around it.

## The pitch in one paragraph

mobile_use is the shortest path from "phone in hand" to "LLM agent driving it,
reliably, on any desktop OS": one bootstrap command, a doctor that tells the
truth, wireless devices that reconnect themselves, multiple devices without
port juggling, and an agent loop (CLI, Python API, or MCP) with guardrails —
all verified by a 1300+ test suite that runs the real install in CI.
