"""Read archived MLSpace logs without loading or scanning entire large files."""

import codecs
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from cryri.statuses import is_terminal

DEFAULT_MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class LogPreview:
    head: int = 40
    tail: int = 200
    max_bytes: int = DEFAULT_MAX_BYTES
    max_files: int = 8
    full: bool = False

    def __post_init__(self):
        if self.head < 0 or self.tail < 0 or self.max_bytes < 1 or self.max_files < 1:
            raise ValueError("Log preview limits must be nonnegative, with positive byte/file limits.")
        if not self.full and self.head + self.tail == 0:
            raise ValueError("Select --head or --tail, or use --full.")


class LogEmitter:
    """Incremental decoder and line cleaner, also reusable across follow polls."""

    def __init__(self, raw=False):
        self.raw = raw
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.buffer = ""

    def feed(self, data):
        from cryri.api import _clean_log_line

        text = self.decoder.decode(data)
        if self.raw:
            sys.stdout.write(text)
        else:
            self.buffer += text
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                cleaned = _clean_log_line(line)
                if cleaned is not None:
                    print(cleaned)
            if len(self.buffer) >= DEFAULT_MAX_BYTES:
                cleaned = _clean_log_line(self.buffer)
                if cleaned is not None:
                    print(cleaned)
                self.buffer = ""
        sys.stdout.flush()

    def finish(self):
        from cryri.api import _clean_log_line

        final = self.decoder.decode(b"", final=True)
        if self.raw:
            sys.stdout.write(final)
        elif self.buffer or final:
            cleaned = _clean_log_line(self.buffer + final)
            if cleaned is not None:
                print(cleaned)
        self.buffer = ""
        sys.stdout.flush()


def emit_chunks(chunks, raw=False, max_bytes=None, finish=True):
    emitter = LogEmitter(raw=raw)
    consumed = 0
    for chunk in chunks:
        if max_bytes is not None:
            chunk = chunk[:max_bytes - consumed]
        consumed += len(chunk)
        emitter.feed(chunk)
        if max_bytes is not None and consumed >= max_bytes:
            break
    if finish:
        emitter.finish()
    if max_bytes is not None and consumed >= max_bytes:
        print("\n... preview byte limit reached; use --full for untruncated logs ...")
    return emitter


def _file_key(path):
    parts = path.parts
    retry = next((int(m.group(1)) for p in parts
                  if (m := re.fullmatch(r"retry\.(\d+)", p))), -1)
    rank = next((int(m.group(1)) for p in parts
                 if (m := re.fullmatch(r"rank\.(\d+)", p))), -1)
    return (-retry, rank, path.name != "stdout", str(path))


class LocalLogStore:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("CRYRI_LOG_DIR", "~/.mlspace-logs")).expanduser().resolve()

    def job_dir(self, name):
        if not name or name in {".", ".."} or Path(name).name != name:
            raise ValueError("Job name must be a single directory name.")
        directory = self.root / name
        try:
            directory.resolve().relative_to(self.root)
        except ValueError:
            raise ValueError("Job log directory points outside the configured log root.")
        return directory

    def files(self, name):
        directory = self.job_dir(name)
        result = []
        if not directory.is_dir():
            return result
        for parent, dirs, names in os.walk(directory, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(parent) / d).is_symlink()]
            for filename in names:
                if filename not in {"stdout", "stderr"} and not filename.endswith(".log"):
                    continue
                path = Path(parent) / filename
                if not path.is_symlink() and path.is_file():
                    result.append(path)
        return sorted(result, key=_file_key)

    def jobs(self):
        if not self.root.is_dir():
            return []
        records = [
            {"job_name": path.name, "created_at": path.stat().st_mtime,
             "status": "Archived", "status_source": "local"}
            for path in self.root.iterdir()
            if path.is_dir() and not path.is_symlink() and not path.name.startswith(".")
        ]
        records.sort(key=lambda item: item["created_at"])
        return [dict(record, index=i) for i, record in enumerate(records, start=1)]

    def resolve(self, partial):
        # An exact archive name takes precedence over substring matches.
        if self.files(partial):
            return partial
        if not self.root.is_dir():
            return None
        matches = [p.name for p in self.root.iterdir()
                   if partial in p.name and p.is_dir() and not p.is_symlink()
                   and self.files(p.name)]
        if len(matches) > 1:
            raise ValueError("Ambiguous local job hash; matches: " + ", ".join(sorted(matches)))
        return matches[0] if matches else None

    @staticmethod
    def _preview(path, options, budget):
        with path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            size = stat.st_size
            if size <= budget:
                data = stream.read(size)
                lines = data.splitlines(keepends=True)
                if len(lines) <= options.head + options.tail:
                    return data, b"", 0, stat
                head = b"".join(lines[:options.head]) if options.head else b""
                tail = b"".join(lines[-options.tail:]) if options.tail else b""
            else:
                head_budget = budget * options.head // (options.head + options.tail)
                prefix = stream.read(head_budget) if options.head else b""
                head = b"".join(prefix.splitlines(keepends=True)[:options.head])
                tail_budget = budget - len(head) if options.tail else 0
                stream.seek(max(len(head), size - tail_budget))
                suffix = stream.read(tail_budget)
                tail = b"".join(suffix.splitlines(keepends=True)[-options.tail:]) if options.tail else b""
            return head, tail, max(0, size - len(head) - len(tail)), stat

    def show(self, name, options=None, raw=False, emitters=None):
        options = options or LogPreview()
        files = self.files(name)
        if not files:
            raise FileNotFoundError("No local logs found for job '{}' under {}".format(name, self.root))
        selected = files if options.full else files[:min(options.max_files, options.max_bytes)]
        # This budget is shared by all previewed files, not repeated per file.
        budget = max(1, options.max_bytes // len(selected))
        positions = {}
        for path in files:
            stat = path.stat()
            positions[path] = (stat.st_dev, stat.st_ino, stat.st_size)
        for path in selected:
            relative = path.relative_to(self.job_dir(name))
            print("--- local: {}/{} ---".format(name, relative))
            keep_state = emitters is not None
            if options.full:
                with path.open("rb") as stream:
                    stat = os.fstat(stream.fileno())
                    emitter = emit_chunks(iter(lambda: stream.read(DEFAULT_MAX_BYTES), b""),
                                          raw=raw, finish=not keep_state)
                    offset = stream.tell()
                reaches_eof = True
            else:
                head, tail, omitted, stat = self._preview(path, options, budget)
                emitter = emit_chunks([head], raw=raw,
                                      finish=not keep_state or bool(tail) or bool(omitted))
                if omitted:
                    print("\n... {} bytes omitted from the middle; use --full ...".format(omitted))
                if tail:
                    emitter = emit_chunks([tail], raw=raw, finish=not keep_state)
                offset = stat.st_size
                reaches_eof = bool(tail) or not omitted
            if keep_state and reaches_eof:
                emitters[path] = emitter
            positions[path] = (stat.st_dev, stat.st_ino, offset)
        if len(selected) < len(files):
            print("... {} additional log files omitted; increase --max-files or use --full ...".format(
                len(files) - len(selected)))
        return positions

    def follow(self, name, options=None, raw=False, get_status=None, poll_interval=1):
        options = options or LogPreview()
        emitters = {}
        positions = self.show(name, options=options, raw=raw, emitters=emitters)
        last_path = next(reversed(emitters), None)
        next_status = 0.0
        try:
            while True:
                for path in self.files(name):
                    try:
                        stream = path.open("rb")
                    except FileNotFoundError:
                        continue  # Rotation between discovery and opening.
                    with stream:
                        stat = os.fstat(stream.fileno())
                        previous = positions.get(path)
                        identity = (stat.st_dev, stat.st_ino)
                        replaced = previous and (previous[:2] != identity or stat.st_size < previous[2])
                        offset = previous[2] if previous and not replaced else 0
                        if replaced and path in emitters:
                            emitters.pop(path).finish()
                        if stat.st_size > offset:
                            if last_path != path or replaced:
                                print("\n--- local: {}/{} ---".format(name, path.relative_to(self.job_dir(name))))
                                last_path = path
                            emitter = emitters.setdefault(path, LogEmitter(raw=raw))
                            stream.seek(offset)
                            remaining = stat.st_size - offset
                            while remaining > 0:
                                chunk = stream.read(min(DEFAULT_MAX_BYTES, remaining))
                                if not chunk:
                                    break
                                remaining -= len(chunk)
                                emitter.feed(chunk)
                            offset = stream.tell()
                        positions[path] = (*identity, offset)
                if get_status and time.monotonic() >= next_status:
                    next_status = time.monotonic() + 5
                    try:
                        if is_terminal(get_status()):
                            return
                    except Exception as exc:
                        from cryri.api import ApiError
                        if not isinstance(exc, ApiError):
                            raise
                        # Archived logs remain usable during API outages.
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            return
        finally:
            for emitter in emitters.values():
                emitter.finish()
