"""Unit tests for mobile_use.skills — stateless functions only."""
import tempfile
import os

import pytest


def test_slugify():
    from mobile_use.skills import _slugify
    assert _slugify("Hello World!") == "hello-world"
    assert _slugify("buy-now") == "buy-now"
    assert _slugify("  spaces  ") == "spaces"
    assert _slugify("UPPER_case_123") == "upper-case-123"


def test_content_similar_identical():
    from mobile_use.skills import _content_similar
    text = "line 1\nline 2\nline 3"
    assert _content_similar(text, text) is True


def test_content_similar_different():
    from mobile_use.skills import _content_similar
    assert _content_similar("a\nb\nc", "x\ny\nz") is False


def test_content_similar_empty():
    from mobile_use.skills import _content_similar
    assert _content_similar("", "") is False


def test_skill_template():
    from mobile_use.skills import skill_template
    md = skill_template(
        app_id="com.example.app",
        title="Do Thing",
        platform="ios",
        selectors={"button": "name == 'go'"},
        steps=["Open app", "Tap button"],
        gotchas=["Watch for alerts"],
    )
    assert "# Do Thing" in md
    assert "com.example.app" in md
    assert "name == 'go'" in md
    assert "1. Open app" in md
    assert "Watch for alerts" in md


def test_list_skills_nonexistent_app():
    from mobile_use.skills import list_skills
    assert list_skills("com.nonexistent.app.xyz") == []


def test_write_and_read_skill(tmp_path, monkeypatch):
    monkeypatch.setattr("mobile_use.skills.SKILLS_DIR", tmp_path)

    from mobile_use.skills import write_skill, read_skill, list_skills

    path = write_skill("com.test.app", "my-skill", "# My Skill\nContent here.")
    assert os.path.exists(path)

    content = read_skill("com.test.app", "my-skill.md")
    assert "# My Skill" in content

    skills = list_skills("com.test.app")
    assert "my-skill.md" in skills


def test_write_skill_no_overwrite_creates_numbered(tmp_path, monkeypatch):
    monkeypatch.setattr("mobile_use.skills.SKILLS_DIR", tmp_path)

    from mobile_use.skills import write_skill

    write_skill("com.test.app", "action", "Version 1")
    path2 = write_skill("com.test.app", "action", "Totally different content here")
    assert "action-2.md" in path2
