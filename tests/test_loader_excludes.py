"""
test_loader_excludes.py — unit tests for the sync.exclude_file loading
"""
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from salt_and_soil.config.loader import load


BASE_TOML = textwrap.dedent("""
    [app]
    role      = "orchestrator"
    node_name = "test-node"

    [server]
    port = 8080

    [mount_defaults]
    mount_root_local  = "/mnt/test"
    mount_root_remote = "/mnt/test"

    [sync]
    exclude_file = "{exclude_path}"

    [state]
    state_file   = "/tmp/state.json"
    snapshot_dir = "/tmp/snapshots"

    [[agents]]
    name = "agent-01"

    [[sources]]
    alias       = "videos"
    agent       = "agent-01"
    local_host  = "1.2.3.4"
    local_share = "/volume1"
    local_path  = "videos"
""")


def _write_cfg(tmp_path: Path, exclude_path: str) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(BASE_TOML.format(exclude_path=exclude_path))
    return cfg


def test_excludes_parsed_from_file(tmp_path):
    exc = tmp_path / "excludes.list"
    exc.write_text("@eaDir\n.DS_Store\nThumbs.db\n")
    cfg = load(_write_cfg(tmp_path, str(exc).replace("\\", "/")))
    assert cfg.sync.excludes == ["@eaDir", ".DS_Store", "Thumbs.db"]


def test_excludes_skips_comments_and_blanks(tmp_path):
    exc = tmp_path / "excludes.list"
    exc.write_text("# Synology\n@eaDir\n\n# macOS\n.DS_Store\n   \n")
    cfg = load(_write_cfg(tmp_path, str(exc).replace("\\", "/")))
    assert cfg.sync.excludes == ["@eaDir", ".DS_Store"]


def test_excludes_missing_file_is_safe(tmp_path):
    cfg = load(_write_cfg(tmp_path, str(tmp_path / "does_not_exist.list").replace("\\", "/")))
    assert cfg.sync.excludes == []
    assert cfg.sync.exclude_file.endswith("does_not_exist.list")


def test_excludes_empty_when_not_configured(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(textwrap.dedent("""
        [app]
        role = "orchestrator"
        [server]
        port = 8080
        [state]
        state_file = "/tmp/state.json"
        snapshot_dir = "/tmp/snapshots"
        [[agents]]
        name = "agent-01"
        [[sources]]
        alias = "videos"
        agent = "agent-01"
        local_host = "1.2.3.4"
        local_share = "/volume1"
        local_path = "videos"
    """))
    cfg = load(cfg_path)
    assert cfg.sync.exclude_file == ""
    assert cfg.sync.excludes == []


def test_excludes_strips_whitespace(tmp_path):
    exc = tmp_path / "excludes.list"
    exc.write_text("  @eaDir  \n\t.DS_Store\n")
    cfg = load(_write_cfg(tmp_path, str(exc).replace("\\", "/")))
    assert cfg.sync.excludes == ["@eaDir", ".DS_Store"]


def test_excludes_escape_for_literal_hash(tmp_path):
    """gitignore-style: '\\#recycle' becomes literal '#recycle' (not a comment)."""
    exc = tmp_path / "excludes.list"
    exc.write_text("# real comment\n\\#recycle\n@eaDir\n")
    cfg = load(_write_cfg(tmp_path, str(exc).replace("\\", "/")))
    assert cfg.sync.excludes == ["#recycle", "@eaDir"]


def test_excludes_unescaped_hash_is_comment(tmp_path):
    """A bare '#recycle' line is still treated as a comment (would silently
    fail to match — the user must use '\\#recycle' to mean a literal '#')."""
    exc = tmp_path / "excludes.list"
    exc.write_text("#recycle\n@eaDir\n")
    cfg = load(_write_cfg(tmp_path, str(exc).replace("\\", "/")))
    assert cfg.sync.excludes == ["@eaDir"]
