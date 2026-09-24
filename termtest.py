#!/usr/bin/env python3
"""Check cursor positions after terminal control sequences; run inside the terminal under test."""
import os, re, select, sys, termios, tty

ESC = "\x1b"
fd = sys.stdin.fileno()
R, C = os.get_terminal_size(fd).lines, os.get_terminal_size(fd).columns

def cpr():
    sys.stdout.write(ESC + "[6n"); sys.stdout.flush()
    buf = b""
    while not buf.endswith(b"R"):
        if not select.select([fd], [], [], 1)[0]:
            return None
        buf += os.read(fd, 64)
    m = re.search(rb"\x1b\[(\d+);(\d+)R", buf)
    return (int(m.group(1)), int(m.group(2))) if m else None

# name, sequence written after ESC[r ESC[2J ESC[H, expected (row, col); None = don't care
TESTS = [
    ("cup",                 ESC + "[5;7H",                          (5, 7)),
    ("cud + cuf",           ESC + "[3B" + ESC + "[4C",              (4, 5)),
    ("cuu at top clamps",   ESC + "[5A",                            (1, 1)),
    ("cup clamps",          ESC + "[999;999H",                      (R, C)),
    ("vpa + hpa",           ESC + "[7d" + ESC + "[9G",              (7, 9)),
    ("cr",                  ESC + "[3;5H\r",                        (3, 1)),
    ("lf keeps column",     ESC + "[3;5H\n",                        (4, 5)),
    ("ind (ESC D)",         ESC + "[3;5H" + ESC + "D",              (4, 5)),
    ("nel (ESC E)",         ESC + "[3;5H" + ESC + "E",              (4, 1)),
    ("ri at top",           ESC + "[H" + ESC + "M",                 (1, 1)),
    ("bs at col 1",         ESC + "[3;1H\b",                        (3, 1)),
    ("tab",                 ESC + "[3;1H\t",                        (3, 9)),
    ("lf at bottom scrolls", ESC + "[%d;3H\n" % R,                  (R, 3)),
    ("wrap pending",        ESC + "[3;1H" + "x" * C,                (3, C)),
    ("wrap on next char",   ESC + "[3;1H" + "x" * (C + 1),          (4, 2)),
    ("full line then crlf", ESC + "[3;1H" + "x" * C + "\r\n",       (4, 1)),
    ("decsc/decrc",         ESC + "[3;5H" + ESC + "7" + ESC + "[8;8H" + ESC + "8", (3, 5)),
    ("el keeps cursor",     ESC + "[3;5H" + ESC + "[2K",            (3, 5)),
    ("ed keeps cursor",     ESC + "[3;5H" + ESC + "[J",             (3, 5)),
    ("2J keeps cursor",     ESC + "[3;5H" + ESC + "[2J",            (3, 5)),
    ("ich keeps cursor",    ESC + "[3;5H" + ESC + "[3@",            (3, 5)),
    ("dch keeps cursor",    ESC + "[3;5H" + ESC + "[2P",            (3, 5)),
    ("ech keeps cursor",    ESC + "[3;5H" + ESC + "[4X",            (3, 5)),
    ("il keeps row",        ESC + "[4;3H" + ESC + "[2L",            (4, None)),
    ("dl keeps row",        ESC + "[4;3H" + ESC + "[M",             (4, None)),
    ("su keeps cursor",     ESC + "[4;4H" + ESC + "[2S",            (4, 4)),
    ("decstbm homes",       ESC + "[5;5H" + ESC + "[2;10r",         (1, 1)),
    ("lf at region bottom", ESC + "[3;6r" + ESC + "[6;2H\n",        (6, 2)),
    ("lf below region",     ESC + "[3;6r" + ESC + "[%d;2H\n" % R,   (R, 2)),
    ("ri at region top",    ESC + "[3;6r" + ESC + "[3;2H" + ESC + "M", (3, 2)),
    ("cud stops at region bottom", ESC + "[3;6r" + ESC + "[4;1H" + ESC + "[9B", (6, 1)),
    ("cup ignores region",  ESC + "[3;6r" + ESC + "[9;1H",          (9, 1)),
]

old = termios.tcgetattr(fd)
tty.setraw(fd)
results = []
try:
    for name, seq, want in TESTS:
        sys.stdout.write(ESC + "[r" + ESC + "[2J" + ESC + "[H" + ESC + "[?7h" + seq)
        got = cpr()
        ok = got is not None and all(w is None or w == g for w, g in zip(want, got))
        results.append((ok, name, want, got))
finally:
    sys.stdout.write(ESC + "[r" + ESC + "[2J" + ESC + "[H"); sys.stdout.flush()
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
for ok, name, want, got in results:
    print("%s %-27s want %-9s got %s" % ("ok  " if ok else "FAIL", name, want, got))
print("%d/%d ok, %dx%d" % (sum(r[0] for r in results), len(results), C, R))
