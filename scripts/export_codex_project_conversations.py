from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORT_ROOT = REPO_ROOT / "PROJECT_CONVERSATIONS"
TRANSCRIPTS_DIR = EXPORT_ROOT / "transcripts"
CODEX_ROOT = Path.home() / ".codex"
SESSION_INDEX_PATH = CODEX_ROOT / "session_index.jsonl"
SOURCE_ROOTS = [CODEX_ROOT / "sessions", CODEX_ROOT / "archived_sessions"]
PROJECT_CWDS = {str(REPO_ROOT)}


@dataclass
class SessionRecord:
    session_id: str
    timestamp: str
    cwd: str
    source_file: Path
    thread_name: str | None
    updated_at: str | None


def load_session_index() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not SESSION_INDEX_PATH.exists():
        return result

    for line in SESSION_INDEX_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = row.get("id")
        if session_id:
            result[session_id] = row
    return result


def iter_project_sessions(index: dict[str, dict[str, Any]]) -> list[SessionRecord]:
    sessions: list[SessionRecord] = []
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                first_line = path.open("r", encoding="utf-8").readline()
                row = json.loads(first_line)
            except Exception:
                continue

            if row.get("type") != "session_meta":
                continue

            payload = row.get("payload", {})
            cwd = payload.get("cwd")
            if cwd not in PROJECT_CWDS:
                continue

            session_id = payload.get("id")
            if not session_id:
                continue

            index_row = index.get(session_id, {})
            sessions.append(
                SessionRecord(
                    session_id=session_id,
                    timestamp=payload.get("timestamp", ""),
                    cwd=cwd,
                    source_file=path,
                    thread_name=index_row.get("thread_name"),
                    updated_at=index_row.get("updated_at"),
                )
            )

    sessions.sort(key=lambda item: (item.timestamp, str(item.source_file)))
    return sessions


def flatten_content(content: list[dict[str, Any]] | None) -> str:
    if not content:
        return ""

    parts: list[str] = []
    for item in content:
        item_type = item.get("type")
        if item_type in {"input_text", "output_text"}:
            parts.append(item.get("text", ""))
        elif item_type == "image_url":
            parts.append(f"[image_url] {item.get('image_url', '')}")
        elif item_type == "local_image":
            parts.append(f"[local_image] {item.get('path', '')}")
        elif item_type == "input_audio":
            parts.append("[input_audio]")
        else:
            raw = json.dumps(item, ensure_ascii=False, indent=2)
            parts.append(f"[unhandled_content]\n{raw}")
    return "\n".join(part for part in parts if part).strip()


def sanitize_filename(value: str) -> str:
    slug = re.sub(r"[^\w\-.]+", "_", value, flags=re.UNICODE).strip("_")
    return slug[:80] or "untitled"


def infer_title(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") != "response_item":
                    continue
                payload = row.get("payload", {})
                if payload.get("type") == "message" and payload.get("role") == "user":
                    text = flatten_content(payload.get("content"))
                    for marker in (
                        "## My request for Codex:",
                        "My request for Codex:",
                        "## My request for Codex",
                        "My request for Codex",
                    ):
                        if marker in text:
                            text = text.split(marker, 1)[1]
                            break
                    for candidate in text.splitlines():
                        candidate = candidate.strip()
                        lowered = candidate.lower()
                        if not candidate:
                            continue
                        if lowered.startswith("# agents.md"):
                            continue
                        if lowered.startswith("## files mentioned"):
                            continue
                        if lowered.startswith("## my request"):
                            continue
                        if lowered.startswith("my request for codex"):
                            continue
                        if lowered.startswith("<instructions>"):
                            continue
                        if lowered.startswith("<environment_context>"):
                            continue
                        if lowered.startswith("<app-context>"):
                            continue
                        if "auto-generated" in lowered and "crewai create" in lowered:
                            continue
                        if candidate in {"#", "##"}:
                            continue
                        if candidate:
                            return candidate[:80]
    except Exception:
        pass
    return path.stem


def format_block(text: str) -> str:
    body = text.rstrip() if text else "(empty)"
    return f"```text\n{body}\n```"


def sanitize_tool_output(text: str) -> str:
    if not text:
        return text

    kept_lines: list[str] = []
    omitted_lines = 0
    for line in text.splitlines():
        has_replacement = "\ufffd" in line
        control_chars = [
            ch for ch in line
            if ord(ch) < 32 and ch not in {"\t"}
        ]
        if has_replacement or control_chars:
            omitted_lines += 1
            continue
        line = re.sub(
            r"^(\s*#?\s*[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET)[A-Z0-9_]*\s*=\s*).*$",
            r"\1[REDACTED]",
            line,
        )
        line = re.sub(r"\bsk-proj-[A-Za-z0-9_-]+\b", "[REDACTED]", line)
        line = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", line)
        line = re.sub(
            r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._-]+",
            r"\1[REDACTED]",
            line,
        )
        kept_lines.append(line)

    if omitted_lines:
        kept_lines.append(f"[{omitted_lines} noisy or binary line(s) omitted during export]")

    return "\n".join(kept_lines).strip()


def render_transcript(record: SessionRecord) -> str:
    entries: list[str] = []
    call_names: dict[str, str] = {}
    entry_number = 1

    with record.source_file.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            if row.get("type") != "response_item":
                continue

            payload = row.get("payload", {})
            payload_type = payload.get("type")

            if payload_type == "message":
                role = payload.get("role")
                if role not in {"user", "assistant"}:
                    continue
                phase = payload.get("phase")
                label = role.upper()
                if phase:
                    label = f"{label} ({phase})"
                text = flatten_content(payload.get("content"))
                entries.append(f"### {entry_number:04d} {label}\n\n{format_block(text)}")
                entry_number += 1
                continue

            if payload_type == "function_call":
                call_id = payload.get("call_id", "")
                name = payload.get("name", "unknown_tool")
                arguments = payload.get("arguments", "")
                call_names[call_id] = name
                entries.append(
                    f"### {entry_number:04d} TOOL CALL `{name}`\n\n{format_block(arguments)}"
                )
                entry_number += 1
                continue

            if payload_type == "function_call_output":
                call_id = payload.get("call_id", "")
                tool_name = call_names.get(call_id, "unknown_tool")
                output = sanitize_tool_output(payload.get("output", ""))
                entries.append(
                    f"### {entry_number:04d} TOOL OUTPUT `{tool_name}`\n\n{format_block(output)}"
                )
                entry_number += 1
                continue

    title = record.thread_name or infer_title(record.source_file)
    header = [
        f"# {title}",
        "",
        f"- Session ID: `{record.session_id}`",
        f"- Started At: `{record.timestamp}`",
        f"- Last Indexed At: `{record.updated_at or 'unknown'}`",
        f"- Workspace CWD: `{record.cwd}`",
        f"- Source Session File: `{record.source_file}`",
        "",
        "## Transcript",
        "",
    ]

    if not entries:
        entries.append("No user/assistant/tool transcript entries were parsed from this session file.")

    return "\n".join(header + entries) + "\n"


def build_index(sessions: list[SessionRecord], transcript_paths: dict[str, Path]) -> str:
    lines = [
        "# Project Conversations Index",
        "",
        "This directory contains the exported Codex conversation history for this project.",
        "",
        f"- Exported session count: `{len(sessions)}`",
        f"- Included workspace: `{REPO_ROOT}`",
        "- Preserved content types: user messages, assistant messages, tool calls, tool outputs",
        "- Omitted boilerplate: repeated system/developer prompts and encrypted reasoning payloads",
        "- Sanitization: noisy binary-like tool-output lines are omitted and detected secrets in tool output are redacted",
        "- Note: the active thread transcript is a snapshot at export time, so it may not include the final confirmation message after export.",
        "",
        "## Sessions",
        "",
    ]

    for index_number, record in enumerate(sessions, start=1):
        title = record.thread_name or infer_title(record.source_file)
        transcript_path = transcript_paths[record.session_id].relative_to(REPO_ROOT).as_posix()
        lines.extend(
            [
                f"### {index_number:02d}. {title}",
                f"- Session ID: `{record.session_id}`",
                f"- Started At: `{record.timestamp}`",
                f"- Workspace CWD: `{record.cwd}`",
                f"- Transcript: `{transcript_path}`",
                f"- Original Codex File: `{record.source_file}`",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    index = load_session_index()
    sessions = iter_project_sessions(index)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    for stale_file in TRANSCRIPTS_DIR.glob("*.md"):
        try:
            stale_file.unlink()
        except OSError:
            pass
    for stale_file in [EXPORT_ROOT / "INDEX.md", EXPORT_ROOT / "manifest.json"]:
        if stale_file.exists():
            try:
                stale_file.unlink()
            except OSError:
                pass

    transcript_paths: dict[str, Path] = {}
    for record in sessions:
        filename = f"{record.timestamp[:10]}_{record.session_id}.md"
        output_path = TRANSCRIPTS_DIR / filename
        output_path.write_text(render_transcript(record), encoding="utf-8-sig")
        transcript_paths[record.session_id] = output_path

    (EXPORT_ROOT / "INDEX.md").write_text(
        build_index(sessions, transcript_paths),
        encoding="utf-8-sig",
    )

    manifest = [
        {
            "session_id": record.session_id,
            "timestamp": record.timestamp,
            "updated_at": record.updated_at,
            "cwd": record.cwd,
            "thread_name": record.thread_name,
            "source_file": str(record.source_file),
            "transcript_file": str(transcript_paths[record.session_id].relative_to(REPO_ROOT).as_posix()),
        }
        for record in sessions
    ]
    (EXPORT_ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Exported {len(sessions)} sessions to {EXPORT_ROOT}")


if __name__ == "__main__":
    main()
