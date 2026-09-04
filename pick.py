#!/usr/bin/env python3
"""Action shim: open the emoji picker in a Herdr popup.

Action commands run headless, so they cannot draw a UI themselves. This one
captures the context Herdr handed it, then asks Herdr to open the picker pane
with that context pinned into the popup's environment.
"""

import sys

import lib


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "space"
    if target not in lib.TARGETS:
        lib.die(f"unknown target {target!r}, expected one of {', '.join(lib.TARGETS)}")

    ctx = lib.context()
    args = [
        "plugin",
        "pane",
        "open",
        "--plugin",
        "hotnugs.emoji-time",
        "--entrypoint",
        "picker",
        "--env",
        f"EMOJI_TARGET={target}",
    ]
    for env, key in (
        ("EMOJI_WORKSPACE_ID", "workspace_id"),
        ("EMOJI_TAB_ID", "tab_id"),
        ("EMOJI_PANE_ID", "focused_pane_id"),
    ):
        value = ctx.get(key)
        if value:
            args += ["--env", f"{env}={value}"]

    try:
        lib.herdr(*args)
    except lib.HerdrError as error:
        lib.die(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
