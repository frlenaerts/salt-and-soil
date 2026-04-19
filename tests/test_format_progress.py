"""
test_format_progress.py — SyncExecutor._format_progress regex/output
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from salt_and_soil.sync.executor import SyncExecutor


def test_final_line_with_xfr_matches():
    line = "  1,234,567,890 100%   50.25MB/s    0:00:23 (xfr#1, to-chk=0/42)"
    out = SyncExecutor._format_progress("movie.mkv", line)
    assert out is not None
    assert out.startswith("movie.mkv —")
    assert "50.25MB/s" in out


def test_intermediate_100pct_without_xfr_rejected():
    line = "  1,234,567,890 100%   50.25MB/s    0:00:23"
    assert SyncExecutor._format_progress("movie.mkv", line) is None


def test_partial_progress_rejected():
    line = "    500,000,000  40%   20.00MB/s    0:00:30"
    assert SyncExecutor._format_progress("movie.mkv", line) is None


def test_ir_chk_variant_matches():
    line = "  1,000,000 100%   10.00MB/s    0:00:01 (xfr#5, ir-chk=1010/2020)"
    out = SyncExecutor._format_progress("sample.mkv", line)
    assert out is not None
    assert "10.00MB/s" in out


def test_unknown_filename_shows_question_mark():
    line = "  1,024 100%   1.00kB/s    0:00:00 (xfr#2, to-chk=0/1)"
    out = SyncExecutor._format_progress(None, line)
    assert out is not None
    assert out.startswith("? —")


def test_output_format_contains_size_and_speed():
    line = "  1,048,576 100%   5.00MB/s    0:00:00 (xfr#1, to-chk=0/1)"
    out = SyncExecutor._format_progress("file.bin", line)
    assert out is not None
    parts = [p.strip() for p in out.split("—")]
    assert parts[0] == "file.bin"
    assert "B" in parts[1]
    assert "/s" in parts[2]
