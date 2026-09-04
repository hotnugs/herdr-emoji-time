#!/usr/bin/env python3
"""Reapply agent emoji after a restart or a fresh agent detection.

Space and tab names are Herdr's own session state and survive on their own. An
agent's display name is runtime metadata that is gone after a server restart,
so we keep our copy and put it back:

  - on the startup hook, for every pane we still know about
  - on pane.agent_detected, when a pane picks up an agent again
"""

import json
import os

import lib


def live_pane_ids() -> set[str]:
    try:
        panes = lib.herdr("pane", "list").get("panes", [])
    except lib.HerdrError:
        return set()
    return {pane.get("pane_id") for pane in panes if pane.get("pane_id")}


def event_pane_id() -> str | None:
    raw = os.environ.get("HERDR_PLUGIN_EVENT_JSON")
    if not raw:
        return None
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    data = event.get("data", event)
    return data.get("pane_id")


def reapply(entry: dict) -> None:
    try:
        lib.apply_emoji("agent", entry["id"], entry["emoji"])
    except (lib.HerdrError, KeyError):
        pass


def main() -> int:
    data = lib.state()
    saved = {
        key: entry
        for key, entry in data.get("targets", {}).items()
        if entry.get("target") == "agent"
    }
    if not saved:
        return 0

    only = event_pane_id()
    if only:
        entry = saved.get(f"agent:{only}")
        if entry:
            reapply(entry)
        return 0

    alive = live_pane_ids()
    changed = False
    for key, entry in list(saved.items()):
        if entry.get("id") in alive:
            reapply(entry)
        else:
            data["targets"].pop(key, None)
            changed = True
    if changed:
        lib.write_state(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
