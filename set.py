#!/usr/bin/env python3
"""Set or clear an emoji without opening the picker.

    python3 set.py <space|tab|agent> <emoji>
    python3 set.py <space|tab|agent> --clear

Ids come from the Herdr invocation context, or from EMOJI_WORKSPACE_ID /
EMOJI_TAB_ID / EMOJI_PANE_ID when you are driving it by hand.
"""

import sys

import lib


def main() -> int:
    if len(sys.argv) < 3:
        lib.die(__doc__.strip())

    target, value = sys.argv[1], sys.argv[2]
    if target not in lib.TARGETS:
        lib.die(f"unknown target {target!r}, expected one of {', '.join(lib.TARGETS)}")

    ident = lib.target_id(lib.context(), target)
    if not ident:
        lib.die(f"no {target} in the current Herdr context")

    try:
        if value == "--clear":
            print(lib.clear_emoji(target, ident))
        else:
            print(lib.apply_emoji(target, ident, value))
    except lib.HerdrError as error:
        lib.die(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
