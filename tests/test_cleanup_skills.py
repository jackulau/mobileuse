"""Guards for the cleanup-and-organize domain skill library.

Every skill file the goal `002-phone-cleanup-organization` produces must:

  * exist on disk under agent-workspace/domain-skills/<app-id>/
  * be non-trivial (> 500 bytes — protects against a stub markdown file)
  * mention at least one keyword from a workflow-specific keyword set
  * be discoverable by `mobile_use.skills.list_skills(app_id)`

No device or daemon is required — tests read files directly.
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "agent-workspace" / "domain-skills"


# (app_id, filename, keywords-any-of)
REQUIRED_SKILLS = [
    # iOS
    ("com.apple.springboard", "uninstall-app.md", ["Delete App", "Remove App"]),
    ("com.apple.springboard", "organize-home-screen.md", ["jiggle", "folder", "drag"]),
    ("com.apple.springboard", "app-library.md", ["App Library"]),
    ("com.apple.Preferences", "iphone-storage.md", ["Offload", "iPhone Storage"]),
    ("com.apple.Preferences", "clear-safari-data.md", ["Clear History", "Safari"]),
    ("com.apple.Preferences", "screen-time-limits.md", ["Screen Time", "Restrictions"]),
    ("com.apple.mobileslideshow", "bulk-delete-photos.md", ["Select", "Delete"]),
    ("com.apple.mobileslideshow", "empty-recently-deleted.md", ["Recently Deleted"]),
    ("com.apple.mobileslideshow", "delete-by-album.md", ["Screenshots", "Duplicates"]),
    ("com.apple.DocumentsApp", "browse-and-delete.md", ["On My iPhone", "Select"]),
    ("com.apple.DocumentsApp", "empty-downloads.md", ["Downloads"]),
    ("com.apple.DocumentsApp", "empty-files-recently-deleted.md", ["Recently Deleted"]),
    # Android
    ("com.android.settings", "uninstall-app.md", ["Uninstall", "Disable"]),
    ("com.android.settings", "storage-cleanup.md", ["Storage", "Free up"]),
    ("com.android.settings", "clear-app-cache.md", ["Clear cache", "Clear storage"]),
    ("com.google.android.apps.nexuslauncher", "long-press-uninstall.md", ["long-press", "Uninstall"]),
    ("com.google.android.apps.nexuslauncher", "organize-home-screen.md", ["folder", "drag"]),
    ("com.google.android.apps.nexuslauncher", "app-drawer.md", ["drawer", "Search apps"]),
    ("com.google.android.apps.nbu.files", "cleanup.md", ["Clean", "Junk", "Trash"]),
    ("com.google.android.apps.photos", "bulk-delete.md", ["Move to", "Select"]),
    ("com.google.android.apps.photos", "empty-bin.md", ["Empty", "Bin"]),
]


@pytest.mark.parametrize("app_id,filename,keywords", REQUIRED_SKILLS)
def test_skill_file_exists(app_id, filename, keywords):
    path = SKILLS_DIR / app_id / filename
    assert path.exists(), f"missing skill file: {path.relative_to(REPO_ROOT)}"


@pytest.mark.parametrize("app_id,filename,keywords", REQUIRED_SKILLS)
def test_skill_file_is_non_trivial(app_id, filename, keywords):
    path = SKILLS_DIR / app_id / filename
    if not path.exists():
        pytest.skip("file absence reported by sibling test")
    size = path.stat().st_size
    assert size > 500, f"{path.name} too small ({size} bytes) — likely a stub"


@pytest.mark.parametrize("app_id,filename,keywords", REQUIRED_SKILLS)
def test_skill_file_keyword(app_id, filename, keywords):
    path = SKILLS_DIR / app_id / filename
    if not path.exists():
        pytest.skip("file absence reported by sibling test")
    text = path.read_text(encoding="utf-8")
    assert any(kw.lower() in text.lower() for kw in keywords), (
        f"{path.name} contains none of expected keywords {keywords}"
    )


@pytest.mark.parametrize("app_id,filename,keywords", REQUIRED_SKILLS)
def test_skill_listed_by_domain_resolver(app_id, filename, keywords):
    from mobile_use.skills import list_skills
    listed = list_skills(app_id)
    assert filename in listed, (
        f"{filename} not returned by list_skills({app_id!r}); got {listed!r}"
    )


def test_capability_doc_present():
    p = REPO_ROOT / "docs" / "cleanup-capability.md"
    assert p.exists(), "docs/cleanup-capability.md missing"
    text = p.read_text(encoding="utf-8")
    for section in ("## iOS", "## Android", "## Cross-cutting"):
        assert section in text, f"capability doc missing '{section}' section"


def test_agent_helpers_exports_cleanup_api():
    sys.path.insert(0, str(REPO_ROOT / "agent-workspace"))
    try:
        import agent_helpers
    finally:
        sys.path.pop(0)
    for name in (
        "list_installed_apps", "uninstall_app", "storage_summary",
        "bulk_select", "confirm_destructive",
    ):
        assert hasattr(agent_helpers, name), f"agent_helpers.{name} missing"


def test_demos_present_and_parseable():
    import ast
    for f in ("clean-and-organize-ios.py", "clean-and-organize-android.py"):
        p = REPO_ROOT / "docs" / "demos" / f
        assert p.exists(), f"missing demo: {p.relative_to(REPO_ROOT)}"
        ast.parse(p.read_text(encoding="utf-8"))  # raises SyntaxError if invalid


def test_readme_documents_cleanup():
    p = REPO_ROOT / "README.md"
    text = p.read_text(encoding="utf-8")
    assert "Cleaning up" in text or "Cleaning Up" in text, \
        "README has no Cleaning up section"
    assert "cleanup-capability.md" in text, \
        "README does not link to the capability doc"
