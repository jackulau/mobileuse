"""Auto skill authoring — write domain skill .md files when the agent discovers non-obvious patterns.

Skills are stored in agent-workspace/domain-skills/<app-id>/<slug>.md
"""
import os
import re
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_WORKSPACE = Path(os.environ.get(
    "MU_AGENT_WORKSPACE",
    os.environ.get("IPH_AGENT_WORKSPACE",
    os.environ.get("ANH_AGENT_WORKSPACE",
    REPO_ROOT / "agent-workspace"))
)).expanduser()
SKILLS_DIR = AGENT_WORKSPACE / "domain-skills"


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def list_skills(app_id):
    """List existing skill files for an app."""
    d = SKILLS_DIR / app_id
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.rglob("*.md"))


def read_skill(app_id, filename):
    """Read a skill file's content."""
    p = SKILLS_DIR / app_id / filename
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def write_skill(app_id, title, content, overwrite=False):
    """Write a domain skill .md file.

    Args:
        app_id: bundle ID (iOS) or package name (Android)
        title: human-readable skill title (e.g. "send-message")
        content: markdown content describing the skill
        overwrite: if False, appends a numeric suffix to avoid clobbering

    Returns:
        Path to the written file.
    """
    d = SKILLS_DIR / app_id
    d.mkdir(parents=True, exist_ok=True)

    slug = _slugify(title)
    filename = f"{slug}.md"
    path = d / filename

    if path.exists() and not overwrite:
        existing = path.read_text(encoding="utf-8")
        if _content_similar(existing, content):
            return str(path)
        i = 2
        while (d / f"{slug}-{i}.md").exists():
            i += 1
        path = d / f"{slug}-{i}.md"

    path.write_text(content, encoding="utf-8")
    return str(path)


def _content_similar(a, b, threshold=0.7):
    """Quick similarity check — if >70% of lines match, consider it a dupe."""
    lines_a = set(a.strip().splitlines())
    lines_b = set(b.strip().splitlines())
    if not lines_a or not lines_b:
        return False
    overlap = len(lines_a & lines_b)
    return overlap / max(len(lines_a), len(lines_b)) > threshold


def merge_skill(app_id, filename, new_content):
    """Merge new observations into an existing skill file.

    Appends new content under a '## Updates' section if the file exists,
    or writes a new file if it doesn't.
    """
    p = SKILLS_DIR / app_id / filename
    if not p.exists():
        return write_skill(app_id, filename.replace(".md", ""), new_content)

    existing = p.read_text(encoding="utf-8")
    if new_content.strip() in existing:
        return str(p)

    timestamp = time.strftime("%Y-%m-%d %H:%M")
    merged = f"{existing.rstrip()}\n\n## Update ({timestamp})\n\n{new_content}\n"
    p.write_text(merged, encoding="utf-8")
    return str(p)


def skill_template(app_id, title, platform, selectors, steps, gotchas=None):
    """Generate a structured skill markdown from components.

    Args:
        app_id: bundle ID or package name
        title: skill title
        platform: 'ios' or 'android'
        selectors: dict of {name: selector_string} for stable UI elements
        steps: list of step strings describing the action sequence
        gotchas: optional list of gotcha strings
    """
    lines = [f"# {title}\n"]
    lines.append(f"**App:** `{app_id}`  ")
    lines.append(f"**Platform:** {platform}\n")

    if selectors:
        lines.append("## Stable Selectors\n")
        lines.append("```")
        for name, sel in selectors.items():
            lines.append(f"{name}: {sel}")
        lines.append("```\n")

    lines.append("## Steps\n")
    for i, step in enumerate(steps, 1):
        lines.append(f"{i}. {step}")
    lines.append("")

    if gotchas:
        lines.append("## Gotchas\n")
        for g in gotchas:
            lines.append(f"- {g}")
        lines.append("")

    return "\n".join(lines)
