import os
import shlex
from datetime import datetime
from pathlib import Path
from typing import List, Tuple


def _chunk_commands(commands: List[str], parallel: int) -> List[List[Tuple[int, str]]]:
    indexed = list(enumerate(commands, start=1))
    if parallel <= 0:
        return [indexed]
    return [indexed[i:i + parallel] for i in range(0, len(indexed), parallel)]


def _escape_for_echo(cmd: str) -> str:
    return cmd.replace('"', '\\"')


def _append_batch_start(lines: List[str], batch_idx: int, total_batches: int, start_idx: int, end_idx: int) -> None:
    lines.append(f"echo \"[cryri] === Batch {batch_idx}/{total_batches} (commands {start_idx}-{end_idx}) ===\"")


def _append_batch_end(lines: List[str], batch_idx: int, total_batches: int) -> None:
    lines.append(f"echo \"[cryri] === Batch {batch_idx}/{total_batches} done ===\"")


def _append_launch_command(lines: List[str], cmd_idx: int, total: int, cmd: str) -> None:
    escaped = _escape_for_echo(cmd)
    lines.append(f"echo \"[cryri] [{cmd_idx}/{total}] {escaped}\"")
    lines.append(f"bash -lc {shlex.quote(cmd)} &")
    lines.append(f"PID_{cmd_idx}=$!")


def _append_wait_command(lines: List[str], cmd_idx: int, cmd: str) -> None:
    escaped = _escape_for_echo(cmd)
    lines.append(f"wait $PID_{cmd_idx}")
    lines.append("EC=$?")
    lines.append(
        f"if [ $EC -ne 0 ]; then echo \"[cryri] FAILED (exit $EC): {escaped}\"; FAIL=$((FAIL+1)); fi"
    )


def _append_final_summary(lines: List[str]) -> None:
    lines.extend([
        "echo \"[cryri] ===============================\"",
        "if [ $FAIL -ne 0 ]; then",
        "  echo \"[cryri] WARNING: $FAIL/$TOTAL_CMDS commands failed\"",
        "  exit 1",
        "fi",
        "echo \"[cryri] All $TOTAL_CMDS commands completed successfully\"",
    ])


def _build_script_lines(commands: List[str], parallel: int) -> List[str]:
    chunks = _chunk_commands(commands, parallel)
    total = len(commands)
    total_batches = len(chunks)

    lines = [
        "set +e",
        "FAIL=0",
        f"TOTAL_CMDS={total}",
        f"echo \"[cryri] Starting execution: {total} commands, parallel={parallel}, {total_batches} batches\"",
    ]

    for batch_idx, batch in enumerate(chunks, start=1):
        start_idx = batch[0][0]
        end_idx = batch[-1][0]
        _append_batch_start(lines, batch_idx, total_batches, start_idx, end_idx)

        for cmd_idx, cmd in batch:
            _append_launch_command(lines, cmd_idx, total, cmd)

        for cmd_idx, cmd in batch:
            _append_wait_command(lines, cmd_idx, cmd)

        _append_batch_end(lines, batch_idx, total_batches)

    _append_final_summary(lines)
    return lines


def build_script(commands: List[str], parallel: int, work_dir: str) -> str:
    lines = [f"cd {shlex.quote(work_dir)} || exit 1"]
    lines.extend(_build_script_lines(commands, parallel))
    return "bash -c " + shlex.quote("\n".join(lines))


def create_batch_script_file(work_dir: str, commands: List[str], parallel: int) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    script_name = f"{timestamp}_{os.getpid()}.sh"

    scripts_dir = Path(work_dir) / ".cryri" / "batch_scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    script_path = scripts_dir / script_name

    script_lines = ["#!/usr/bin/env bash", * _build_script_lines(commands, parallel)]

    script_path.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
    os.chmod(script_path, 0o755)

    return str(script_path)
