from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from cryri import api
from cryri.job_manager import JobManager
from cryri.local_logs import LocalLogStore, LogPreview
from cryri.main import app

JOB = "lm-mpi-job-00000000-0000-4000-8000-000000000001"


def archive(root, text="hello\n", retry=0, rank=0, filename="stdout", name=JOB):
    path = root / name / ("retry.%03d" % retry) / ("rank.%d" % rank) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_local_logs_work_offline_without_job_list(tmp_path, monkeypatch):
    archive(tmp_path, "[1,0]<stdout>:READY\n")
    monkeypatch.setattr(api, "list_jobs", Mock(side_effect=AssertionError("No API needed")))
    result = CliRunner().invoke(app, ["logs", "000000000001", "--log-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "READY" in result.output
    assert "[1,0]" not in result.output


def test_explicit_api_bypasses_present_local_archive(tmp_path, monkeypatch):
    archive(tmp_path)
    stream = Mock()
    monkeypatch.setattr(api, "stream_logs", stream)
    result = CliRunner().invoke(
        app, ["logs", JOB, "--source", "api", "--log-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    stream.assert_called_once_with(
        JOB, region="SR006", raw=False, tail=200, max_bytes=65536)


def test_api_fallback_when_archive_missing(tmp_path, monkeypatch):
    stream = Mock()
    monkeypatch.setattr(api, "stream_logs", stream)
    result = CliRunner().invoke(app, ["logs", JOB, "--log-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    stream.assert_called_once()


def test_local_only_missing_does_not_contact_api(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "list_jobs", Mock(side_effect=AssertionError("No API needed")))
    result = CliRunner().invoke(
        app, ["logs", JOB, "--source", "local", "--log-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "No local logs" in result.output


def test_default_root_can_be_configured(tmp_path, monkeypatch):
    archive(tmp_path)
    monkeypatch.setenv("CRYRI_LOG_DIR", str(tmp_path))
    assert LocalLogStore().resolve(JOB) == JOB


def test_head_tail_truncates_middle_without_overlap(tmp_path, capsys):
    archive(tmp_path, "".join("entry-%03d\n" % i for i in range(50)))
    LocalLogStore(tmp_path).show(JOB, LogPreview(head=2, tail=3), raw=True)
    output = capsys.readouterr().out
    assert "entry-000" in output and "entry-001" in output
    assert "entry-020" not in output
    assert "entry-047" in output and "entry-049" in output
    assert "omitted from the middle" in output
    assert output.count("entry-049") == 1


def test_small_files_are_printed_once(tmp_path, capsys):
    archive(tmp_path, "only-once\n")
    LocalLogStore(tmp_path).show(JOB)
    assert capsys.readouterr().out.count("only-once") == 1


def test_raw_preserves_prefixes(tmp_path, capsys):
    archive(tmp_path, "[1,0]<stdout>:hello\n\n")
    LocalLogStore(tmp_path).show(JOB, raw=True)
    assert "[1,0]<stdout>:hello\n\n" in capsys.readouterr().out


def test_sparse_large_log_reads_only_bounded_prefix_and_suffix(tmp_path, monkeypatch, capsys):
    path = archive(tmp_path, "START\n")
    with path.open("r+b") as stream:
        stream.seek(64 * 1024 * 1024)
        stream.write(b"\nEND\n")
    original_open = Path.open
    total = [0]

    class Guard:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            assert 0 <= size <= 1024
            value = self.stream.read(size)
            total[0] += len(value)
            return value

    def guarded_open(self, *args, **kwargs):
        stream = original_open(self, *args, **kwargs)
        return Guard(stream) if self == path and args and args[0] == "rb" else stream

    monkeypatch.setattr(Path, "open", guarded_open)
    LocalLogStore(tmp_path).show(JOB, LogPreview(max_bytes=1024), raw=True)
    output = capsys.readouterr().out
    assert "START" in output and "END" in output
    assert "omitted" in output
    assert total[0] <= 2048


def test_preview_byte_budget_is_shared_between_files(tmp_path, monkeypatch):
    for rank in range(4):
        archive(tmp_path, "X" * 2000, rank=rank)
    original = LocalLogStore._preview
    budgets = []

    def preview(path, options, budget):
        budgets.append(budget)
        return original(path, options, budget)

    monkeypatch.setattr(LocalLogStore, "_preview", staticmethod(preview))
    LocalLogStore(tmp_path).show(JOB, LogPreview(max_bytes=256))
    assert sum(budgets) <= 256


def test_full_streams_all_retries_and_ranks(tmp_path, capsys):
    for rank in range(10):
        archive(tmp_path, "rank-output-%d\n" % rank, rank=rank)
    archive(tmp_path, "newest-retry\n", retry=1)
    store = LocalLogStore(tmp_path)
    store.show(JOB, LogPreview(max_files=2, full=True))
    output = capsys.readouterr().out
    assert output.index("newest-retry") < output.index("rank-output-0")
    assert "rank-output-9" in output
    assert "additional log files omitted" not in output


def test_file_limit_is_visible(tmp_path, capsys):
    for rank in range(4):
        archive(tmp_path, "rank-%d\n" % rank, rank=rank)
    LocalLogStore(tmp_path).show(JOB, LogPreview(max_files=2))
    output = capsys.readouterr().out
    assert "rank-0" in output and "rank-1" in output
    assert "rank-3" not in output
    assert "2 additional log files omitted" in output


def test_ambiguous_hash_is_rejected(tmp_path):
    archive(tmp_path)
    archive(tmp_path, name=JOB[:-1] + "2")
    with pytest.raises(ValueError, match="Ambiguous"):
        LocalLogStore(tmp_path).resolve("lm-mpi")
    assert LocalLogStore(tmp_path).resolve(JOB) == JOB


def test_traversal_and_symlinks_cannot_escape_root(tmp_path):
    outside = tmp_path / "outside"
    archive(outside, "private\n")
    root = tmp_path / "logs"
    root.mkdir()
    store = LocalLogStore(root)
    with pytest.raises(ValueError):
        store.files("../outside")
    (root / JOB).symlink_to(outside / JOB, target_is_directory=True)
    with pytest.raises(ValueError):
        store.files(JOB)


def test_linked_log_files_are_not_read(tmp_path):
    path = archive(tmp_path)
    private = tmp_path / "private"
    private.write_text("secret")
    (path.parent / "stderr").symlink_to(private)
    assert LocalLogStore(tmp_path).files(JOB) == [path]


def test_follow_reads_appended_data_before_terminal_status(tmp_path, monkeypatch, capsys):
    path = archive(tmp_path, "initial\n")
    status = Mock(side_effect=["Running", "Failed"])
    clock = [0]
    monkeypatch.setattr("cryri.local_logs.time.monotonic", lambda: clock[0])

    def append(_):
        with path.open("a") as stream:
            stream.write("final-output\n")
        clock[0] += 6

    monkeypatch.setattr("cryri.local_logs.time.sleep", append)
    LocalLogStore(tmp_path).follow(JOB, get_status=status)
    output = capsys.readouterr().out
    assert output.count("initial") == 1
    assert output.count("final-output") == 1


def test_follow_detects_new_retry_and_rotated_file(tmp_path, monkeypatch, capsys):
    path = archive(tmp_path, "initial\n")
    status = Mock(side_effect=["Running", "Stopped"])
    clock = [0]
    monkeypatch.setattr("cryri.local_logs.time.monotonic", lambda: clock[0])

    def rotate(_):
        replacement = path.parent / "replacement"
        replacement.write_text("rotated\n")
        replacement.replace(path)
        archive(tmp_path, "new-retry\n", retry=1)
        clock[0] += 6

    monkeypatch.setattr("cryri.local_logs.time.sleep", rotate)
    LocalLogStore(tmp_path).follow(JOB, get_status=status)
    output = capsys.readouterr().out
    assert "rotated" in output and "new-retry" in output


def test_newline_free_full_log_is_streamed_in_bounded_chunks(tmp_path, capsys):
    archive(tmp_path, "X" * (2 * 65536))
    LocalLogStore(tmp_path).show(JOB, LogPreview(full=True), raw=True)
    assert capsys.readouterr().out.count("X") == 2 * 65536


def test_follow_preserves_split_utf8_and_lines(tmp_path, monkeypatch, capsys):
    path = archive(tmp_path, "")
    status = Mock(side_effect=["Running", "Running", "Stopped"])
    clock = [0]
    fragments = iter([b"[1,0]<stdout>:par\xe2", b"\x82\xactial\n"])
    monkeypatch.setattr("cryri.local_logs.time.monotonic", lambda: clock[0])

    def append(_):
        with path.open("ab") as stream:
            stream.write(next(fragments))
        clock[0] += 6

    monkeypatch.setattr("cryri.local_logs.time.sleep", append)
    LocalLogStore(tmp_path).follow(JOB, get_status=status)
    output = capsys.readouterr().out
    assert "par€tial" in output
    assert "\ufffd" not in output
    assert "[1,0]" not in output


def test_tiny_byte_limit_still_caps_all_files(tmp_path, monkeypatch):
    for rank in range(4):
        archive(tmp_path, "XXXXX", rank=rank)
    original = LocalLogStore._preview
    budgets = []

    def preview(path, options, budget):
        budgets.append(budget)
        return original(path, options, budget)

    monkeypatch.setattr(LocalLogStore, "_preview", staticmethod(preview))
    LocalLogStore(tmp_path).show(JOB, LogPreview(max_bytes=1))
    assert sum(budgets) <= 1


def test_invalid_preview_options_rejected_before_io():
    with pytest.raises(ValueError):
        LogPreview(head=0, tail=0)
