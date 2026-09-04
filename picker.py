#!/usr/bin/env python3
"""The emoji picker, run inside a Herdr popup pane.

Stdlib only, raw ANSI, no curses: Herdr renders the popup itself and curses'
wide-character handling on macOS is not worth the risk here.
"""

import os
import select
import sys
import termios
import tty

import lib

CELL = 4  # two columns for the emoji, two of padding
GRID_TOP = 2  # header line, then the search line
TARGET_TITLES = {"space": "Space", "tab": "Tab", "agent": "Agent"}

ESC = "\x1b"
RESET = "\x1b[0m"
REVERSE = "\x1b[7m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"

# Herdr forwards mouse events into a popup, but only once the program inside it
# asks for them. 1003 is any-event tracking, which is what makes hover work:
# without it Herdr drops motion and only clicks arrive. 1006 is SGR encoding, so
# columns past 95 still report correctly.
MOUSE_ON = "\x1b[?1003h\x1b[?1006h"
MOUSE_OFF = "\x1b[?1006l\x1b[?1003l"

WHEEL_UP, WHEEL_DOWN = 64, 65
MOTION_BIT = 32


class Click:
    __slots__ = ("button", "column", "row", "pressed")

    def __init__(self, button: int, column: int, row: int, pressed: bool) -> None:
        self.button = button
        self.column = column  # zero-based
        self.row = row
        self.pressed = pressed


class Picker:
    def __init__(self, ctx: dict, target: str) -> None:
        self.ctx = ctx
        self.all_emoji = lib.load_emoji()
        self.recent = lib.state().get("recent", [])
        self.query = ""
        self.cursor = 0
        self.offset = 0
        self.message = ""
        self.target = target
        self.results = self.filtered()

    # --- data ---

    def available_targets(self) -> list[str]:
        return [name for name in lib.TARGETS if lib.target_id(self.ctx, name)]

    def filtered(self) -> list[tuple[str, str, str, str]]:
        query = self.query.strip().lower()
        if not query:
            by_char = {row[0]: row for row in self.all_emoji}
            recent = [by_char[char] for char in self.recent if char in by_char]
            seen = {row[0] for row in recent}
            return recent + [row for row in self.all_emoji if row[0] not in seen]

        words = query.split()
        matches = []
        for row in self.all_emoji:
            haystack = f"{row[3]} {row[2]} {row[1]}".lower()
            if all(word in haystack for word in words):
                matches.append(row)
        return matches

    def selected(self):
        if not self.results:
            return None
        return self.results[min(self.cursor, len(self.results) - 1)]

    def signature(self) -> tuple:
        """Everything the screen depends on.

        Any-event tracking sends a report for every cell the pointer crosses, so
        redrawing per event would repaint constantly. Redraw when this changes.
        """
        return (self.cursor, self.query, self.target, self.message)

    # --- rendering ---

    def size(self) -> tuple[int, int]:
        try:
            size = os.get_terminal_size()
            return max(size.columns, 24), max(size.lines, 10)
        except OSError:
            return 64, 20

    def columns(self, width: int) -> int:
        return max(1, (width - 2) // CELL)

    def render(self) -> None:
        width, height = self.size()
        cols = self.columns(width)
        grid_rows = max(1, height - 5)
        visible = cols * grid_rows

        if self.cursor < self.offset:
            self.offset = self.cursor - (self.cursor % cols)
        elif self.cursor >= self.offset + visible:
            row_of_cursor = self.cursor // cols
            self.offset = (row_of_cursor - grid_rows + 1) * cols

        out = ["\x1b[H\x1b[2J"]
        out.append(self.header(width) + "\r\n")
        out.append(self.search_line(width) + "\r\n")

        for row_index in range(grid_rows):
            cells = []
            for col_index in range(cols):
                index = self.offset + row_index * cols + col_index
                if index >= len(self.results):
                    cells.append(" " * CELL)
                    continue
                glyph = self.results[index][0]
                body = f" {glyph} "
                cells.append(f"{REVERSE}{body}{RESET}" if index == self.cursor else body)
            out.append(" " + "".join(cells) + "\r\n")

        out.append(self.caption(width) + "\r\n")
        out.append(self.footer(width))
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def header(self, width: int) -> str:
        parts = []
        for name in self.available_targets():
            title = TARGET_TITLES[name]
            parts.append(f"{REVERSE} {title} {RESET}" if name == self.target else f" {title} ")
        return " " + "".join(parts)

    def target_spans(self) -> list[tuple[str, int, int]]:
        """Where each header chip sits, so a click can land on one."""
        spans = []
        column = 1
        for name in self.available_targets():
            span = len(TARGET_TITLES[name]) + 2
            spans.append((name, column, column + span))
            column += span
        return spans

    # --- mouse ---

    def target_at(self, column: int) -> str | None:
        for name, start, end in self.target_spans():
            if start <= column < end:
                return name
        return None

    def index_at(self, column: int, row: int) -> int | None:
        width, height = self.size()
        cols = self.columns(width)
        grid_row = row - GRID_TOP
        if grid_row < 0 or grid_row >= max(1, height - 5) or column < 1:
            return None
        cell = (column - 1) // CELL
        if cell >= cols:
            return None
        index = self.offset + grid_row * cols + cell
        return index if index < len(self.results) else None

    def click(self, event: Click) -> bool:
        """Return False to quit. A click on an emoji is the pick."""
        if event.button in (WHEEL_UP, WHEEL_DOWN):
            width, _ = self.size()
            step = self.columns(width) * 3
            self.move(-step if event.button == WHEEL_UP else step)
            return True

        if MOTION_BIT <= event.button < WHEEL_UP:
            # Hover. Herdr's own right-click menus move the highlight to
            # whatever the pointer is over, so do the same rather than invent a
            # second kind of highlight.
            index = self.index_at(event.column, event.row)
            if index is not None:
                self.cursor = index
                self.message = ""
            return True

        if not event.pressed or event.button != 0:
            return True

        if event.row == 0:
            name = self.target_at(event.column)
            if name:
                self.target = name
                self.message = ""
            return True

        index = self.index_at(event.column, event.row)
        if index is None:
            return True
        self.cursor = index
        self.message = ""
        return not self.commit()

    def search_line(self, width: int) -> str:
        ident = lib.target_id(self.ctx, self.target)
        if self.message:
            return f" {self.message}"[: width + len(RESET)]
        if self.query:
            return f" {BOLD}/{self.query}{RESET}"
        label = ""
        if ident:
            try:
                label = lib.current_label(self.target, ident)
            except lib.HerdrError:
                label = ident
        return f" {DIM}type to search  ·  {label}{RESET}"

    def caption(self, width: int) -> str:
        row = self.selected()
        if not row:
            return f" {DIM}no match{RESET}"
        name = row[3][: max(1, width - 6)]
        return f" {row[0]} {DIM}{name}{RESET}"

    def footer(self, width: int) -> str:
        return f" {DIM}click or enter apply · tab target · ctrl-d remove · esc cancel{RESET}"

    # --- input ---

    def move(self, delta: int) -> None:
        if not self.results:
            return
        self.cursor = max(0, min(len(self.results) - 1, self.cursor + delta))
        self.message = ""

    def cycle_target(self) -> None:
        targets = self.available_targets()
        if len(targets) < 2:
            return
        self.target = targets[(targets.index(self.target) + 1) % len(targets)]
        self.message = ""

    def commit(self) -> bool:
        row = self.selected()
        ident = lib.target_id(self.ctx, self.target)
        if not row or not ident:
            self.message = "nothing to apply"
            return False
        try:
            lib.apply_emoji(self.target, ident, row[0])
        except lib.HerdrError as error:
            self.message = str(error)[:120]
            return False
        return True

    def remove(self) -> bool:
        ident = lib.target_id(self.ctx, self.target)
        if not ident:
            self.message = "nothing to remove"
            return False
        try:
            lib.clear_emoji(self.target, ident)
        except lib.HerdrError as error:
            self.message = str(error)[:120]
            return False
        return True

    def handle(self, key: str) -> bool:
        """Return False to quit."""
        width, height = self.size()
        cols = self.columns(width)
        page = cols * max(1, height - 5)

        if key in ("\x1b", "\x03", "\x07"):  # esc, ctrl-c, ctrl-g
            return False
        if key in ("\r", "\n"):
            return not self.commit()
        if key == "\x04":  # ctrl-d
            return not self.remove()
        if key == "\t":
            self.cycle_target()
            return True
        if key == "\x7f":  # backspace
            self.query = self.query[:-1]
            self.reset_results()
            return True
        if key == "\x15":  # ctrl-u
            self.query = ""
            self.reset_results()
            return True
        if key == "left":
            self.move(-1)
            return True
        if key == "right":
            self.move(1)
            return True
        if key == "up":
            self.move(-cols)
            return True
        if key == "down":
            self.move(cols)
            return True
        if key == "pageup":
            self.move(-page)
            return True
        if key == "pagedown":
            self.move(page)
            return True
        if key == "home":
            self.cursor = 0
            return True
        if key == "end":
            self.cursor = max(0, len(self.results) - 1)
            return True
        if len(key) == 1 and key.isprintable():
            self.query += key
            self.reset_results()
        return True

    def reset_results(self) -> None:
        self.results = self.filtered()
        self.cursor = 0
        self.offset = 0
        self.message = ""


def parse_sgr_mouse(body: str) -> Click | str:
    """Turn "<0;12;5M" into a Click. Coordinates arrive one-based."""
    final = body[-1]
    try:
        button, column, row = (int(part) for part in body[1:-1].split(";"))
    except ValueError:
        return ""
    return Click(button, column - 1, row - 1, final == "M")


CSI_KEYS = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "H": "home",
    "F": "end",
    "5~": "pageup",
    "6~": "pagedown",
}


class KeyReader:
    """Read keys straight off the fd.

    Reading through sys.stdin does not work here: the text wrapper pulls a whole
    arrow sequence into its own buffer, so the select() that separates a lone
    Escape from the start of a CSI sequence sees an idle fd and every arrow key
    looks like a cancel.
    """

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.buf = b""

    def fill(self, timeout: float | None) -> bool:
        if not select.select([self.fd], [], [], timeout)[0]:
            return False
        chunk = os.read(self.fd, 1024)
        if not chunk:
            return False
        self.buf += chunk
        return True

    def take(self, count: int) -> bytes:
        head, self.buf = self.buf[:count], self.buf[count:]
        return head

    def read_key(self) -> str | None:
        while not self.buf:
            if not self.fill(None):
                return None

        if self.buf[0:1] != b"\x1b":
            return self.read_char()

        if len(self.buf) == 1:
            self.fill(0.03)
        if len(self.buf) == 1 or self.buf[1:2] != b"[":
            self.take(1)
            return ESC

        index = 2
        while True:
            if index >= len(self.buf):
                if not self.fill(0.03):
                    self.take(len(self.buf))
                    return ""
                continue
            if 0x40 <= self.buf[index] <= 0x7E:
                break
            index += 1

        body = self.take(index + 1)[2:].decode("ascii", "replace")
        if body.startswith("<"):
            return parse_sgr_mouse(body)
        return CSI_KEYS.get(body, "")

    def read_char(self) -> str:
        lead = self.buf[0]
        if lead < 0x80:
            width = 1
        elif lead >= 0xF0:
            width = 4
        elif lead >= 0xE0:
            width = 3
        elif lead >= 0xC0:
            width = 2
        else:
            self.take(1)
            return ""
        while len(self.buf) < width:
            if not self.fill(0.03):
                break
        return self.take(width).decode("utf-8", "replace")


def main() -> int:
    ctx = lib.context()
    targets = [name for name in lib.TARGETS if lib.target_id(ctx, name)]
    if not targets:
        lib.die("no space, tab or agent in context")

    wanted = os.environ.get("EMOJI_TARGET", "space")
    picker = Picker(ctx, wanted if wanted in targets else targets[0])

    if not sys.stdin.isatty():
        lib.die("the picker needs a terminal; run it through `herdr plugin action invoke`")

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    keys = KeyReader(fd)
    sys.stdout.write("\x1b[?25l" + MOUSE_ON)  # hide cursor, take the mouse
    try:
        tty.setraw(fd)
        picker.render()
        while True:
            key = keys.read_key()
            if key is None:
                break
            before = picker.signature()
            if isinstance(key, Click):
                if not picker.click(key):
                    break
            elif key and not picker.handle(key):
                break
            if picker.signature() != before:
                picker.render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write(MOUSE_OFF + "\x1b[?25h\x1b[0m\x1b[H\x1b[2J")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
