"""Shared helpers for the Herdr emoji plugin.

Stdlib only. Everything talks to Herdr through the CLI at HERDR_BIN_PATH so the
plugin works the same over a Unix socket or a Windows named pipe.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGETS = ("space", "tab", "agent")
METADATA_SOURCE = "plugin:hotnugs.emoji-time"


class HerdrError(RuntimeError):
    pass


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def herdr(*args: str) -> dict:
    """Run a herdr CLI command and return the parsed `result` object."""
    proc = subprocess.run(
        [herdr_bin(), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise HerdrError((proc.stderr or proc.stdout or "").strip() or f"herdr {' '.join(args)} failed")
    out = proc.stdout.strip()
    if not out:
        return {}
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return {}
    if "error" in payload:
        raise HerdrError(json.dumps(payload["error"]))
    return payload.get("result", {})


# --- emoji data ---------------------------------------------------------


def load_emoji() -> list[tuple[str, str, str, str]]:
    """Return (char, group, subgroup, name) for every bundled emoji."""
    rows = []
    with (ROOT / "emoji.tsv").open(encoding="utf-8") as handle:
        for line in handle:
            # The keycap emoji is literally "#", so a comment is a "#" line
            # with no tabs in it, not any "#" line.
            if not line.strip() or (line.startswith("#") and "\t" not in line):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 4:
                rows.append(tuple(parts))
    return rows


_prefix_lengths: list[int] | None = None
_prefix_set: set[str] | None = None


def strip_emoji_prefix(label: str) -> str:
    """Remove a leading emoji (and the space after it) from a label."""
    global _prefix_lengths, _prefix_set
    if _prefix_set is None:
        _prefix_set = {row[0] for row in load_emoji()}
        _prefix_lengths = sorted({len(item) for item in _prefix_set}, reverse=True)

    text = label.lstrip()
    for length in _prefix_lengths:
        if text[:length] in _prefix_set:
            return text[length:].lstrip()
    return label.strip()


# --- invocation context -------------------------------------------------


def context() -> dict:
    """Herdr's invocation context, with env vars filling any gaps."""
    ctx = {}
    raw = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON")
    if raw:
        try:
            ctx = json.loads(raw)
        except json.JSONDecodeError:
            ctx = {}

    ctx.setdefault("workspace_id", os.environ.get("HERDR_WORKSPACE_ID"))
    ctx.setdefault("tab_id", os.environ.get("HERDR_TAB_ID"))
    ctx.setdefault("focused_pane_id", os.environ.get("HERDR_PANE_ID"))

    # The action shim passes these so the popup targets what the user was
    # looking at, not whatever the popup itself is attached to.
    for key, env in (
        ("workspace_id", "EMOJI_WORKSPACE_ID"),
        ("tab_id", "EMOJI_TAB_ID"),
        ("focused_pane_id", "EMOJI_PANE_ID"),
    ):
        value = os.environ.get(env)
        if value:
            ctx[key] = value

    return ctx


def target_id(ctx: dict, target: str) -> str | None:
    return {
        "space": ctx.get("workspace_id"),
        "tab": ctx.get("tab_id"),
        "agent": ctx.get("focused_pane_id"),
    }.get(target)


def current_label(target: str, ident: str) -> str:
    """The text the emoji sits to the left of, as Herdr currently shows it."""
    if target == "space":
        info = herdr("workspace", "get", ident).get("workspace", {})
        return info.get("label", "")
    if target == "tab":
        info = herdr("tab", "get", ident).get("tab", {})
        return info.get("label", "")
    if target == "agent":
        pane = herdr("pane", "get", ident).get("pane", {})
        return pane.get("display_agent") or pane.get("agent") or ""
    raise ValueError(f"unknown target {target!r}")


def _compose(target: str, emoji: str, base: str) -> str:
    base = base.strip()
    # An untouched tab is labelled with its number; "3" reads better as just
    # the emoji than as "3" wearing one.
    if target == "tab" and base.isdigit():
        return emoji
    return f"{emoji} {base}".strip()


def apply_emoji(target: str, ident: str, emoji: str) -> str:
    """Put `emoji` to the left of the target's label. Returns the new label."""
    base = strip_emoji_prefix(current_label(target, ident))
    label = _compose(target, emoji, base)

    if target == "space":
        herdr("workspace", "rename", ident, label)
    elif target == "tab":
        herdr("tab", "rename", ident, label)
    elif target == "agent":
        herdr(
            "pane",
            "report-metadata",
            ident,
            "--source",
            METADATA_SOURCE,
            "--display-agent",
            label,
        )
    else:
        raise ValueError(f"unknown target {target!r}")

    remember(target, ident, emoji, base)
    return label


def _fallback_label(target: str, ident: str) -> str:
    """What a target should be called once its emoji comes off and nothing is left.

    Herdr stores an empty rename as an empty label rather than falling back to
    its own auto name, so a space called only "🚀" would go blank.
    """
    if target == "space":
        panes = herdr("pane", "list", "--workspace", ident).get("panes", [])
        for pane in panes:
            cwd = pane.get("cwd")
            if cwd:
                return Path(cwd).name
        info = herdr("workspace", "get", ident).get("workspace", {})
        return f"space {info.get('number', '')}".strip()
    if target == "tab":
        info = herdr("tab", "get", ident).get("tab", {})
        return str(info.get("number", "1"))
    return ""


def clear_emoji(target: str, ident: str) -> str:
    """Take the emoji back off. Returns the restored label."""
    saved = state().get("targets", {}).get(f"{target}:{ident}", {})
    base = (
        strip_emoji_prefix(current_label(target, ident))
        or saved.get("base", "")
        or _fallback_label(target, ident)
    )

    if target == "space":
        herdr("workspace", "rename", ident, base)
    elif target == "tab":
        herdr("tab", "rename", ident, base)
    elif target == "agent":
        herdr(
            "pane",
            "report-metadata",
            ident,
            "--source",
            METADATA_SOURCE,
            "--clear-display-agent",
        )
    else:
        raise ValueError(f"unknown target {target!r}")

    forget(target, ident)
    return base


# --- durable state ------------------------------------------------------
#
# Space and tab names live in Herdr's own session file, so they come back on
# their own. An agent's display name is runtime-only metadata, so we keep a
# copy here and reapply it from the startup hook and the agent-detected event.


def state_path() -> Path:
    base = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    directory = Path(base) if base else ROOT / ".state"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "emoji.json"


def state() -> dict:
    path = state_path()
    if not path.exists():
        return {"targets": {}, "recent": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"targets": {}, "recent": []}
    data.setdefault("targets", {})
    data.setdefault("recent", [])
    return data


def write_state(data: dict) -> None:
    path = state_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def remember(target: str, ident: str, emoji: str, base: str) -> None:
    data = state()
    data["targets"][f"{target}:{ident}"] = {
        "target": target,
        "id": ident,
        "emoji": emoji,
        "base": base,
    }
    recent = [item for item in data["recent"] if item != emoji]
    data["recent"] = [emoji, *recent][:32]
    write_state(data)


def forget(target: str, ident: str) -> None:
    data = state()
    data["targets"].pop(f"{target}:{ident}", None)
    write_state(data)


def die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)
