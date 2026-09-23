#!/usr/bin/env python3
"""
Two Column Virtual Terminal.
"""
# Copyright 2011 Helmut Grohne <helmut@subdivi.de>. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#    1. Redistributions of source code must retain the above copyright notice,
#       this list of conditions and the following disclaimer.
#
#    2. Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY HELMUT GROHNE ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO
# EVENT SHALL HELMUT GROHNE OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
# OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE,
# EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# The views and conclusions contained in the software and documentation are
# those of the authors and should not be interpreted as representing official
# policies, either expressed or implied, of Helmut Grohne.

# pylint: disable=invalid-name, missing-docstring
import pty
import sys
import os
import select
import fcntl
import termios
import struct
import curses
import signal
import time
import optparse
import codecs
import locale

def init_color_pairs(invert):
    """
    Set color pairs for ncurses where each color is between 0 and COLORS.
    """
    foreground = curses.COLOR_BLACK
    if invert:
        foreground = curses.COLOR_WHITE
    backgrounds = (-1, curses.COLOR_RED,  # -1: the terminal's own background
                   curses.COLOR_GREEN, curses.COLOR_YELLOW,
                   curses.COLOR_BLUE, curses.COLOR_MAGENTA,
                   curses.COLOR_CYAN, foreground)
    foregrounds = (curses.COLOR_WHITE, curses.COLOR_BLACK,
                   curses.COLOR_RED, curses.COLOR_GREEN,
                   curses.COLOR_YELLOW, curses.COLOR_BLUE,
                   curses.COLOR_MAGENTA, curses.COLOR_CYAN)
    for bi, bc in enumerate(backgrounds):
        for fi, fc in enumerate(foregrounds):
            if fi != 0 or bi != 0:
                curses.init_pair(fi*8+bi, fc, bc)
            if has_bright():  # pairs 64..127: bright (8..15) foregrounds
                curses.init_pair(64+fi*8+bi, fc+8, bc)

def has_bright():
    return curses.COLORS >= 16 and curses.COLOR_PAIRS >= 128

def get_color(fg=1, bg=0):
    """fg 0-15 (8-15 bright, plain if unsupported), bg 0-7."""
    bright = 64 if fg >= 8 and has_bright() else 0
    return curses.color_pair(((fg % 8 + 1) % 8) * 8 + bg + bright)

class Simple:
    def __init__(self, curseswindow):
        self.screen = curseswindow
        self.screen.scrollok(1)

    def getmaxyx(self):
        return self.screen.getmaxyx()

    def move(self, ypos, xpos):
        ym, xm = self.getmaxyx()
        self.screen.move(max(0, min(ym - 1, ypos)), max(0, min(xm - 1, xpos)))

    def relmove(self, yoff, xoff):
        y, x = self.getyx()
        self.move(y + yoff, x + xoff)

    def addch(self, char):
        self.screen.addch(char)

    def refresh(self):
        self.screen.refresh()

    def getyx(self):
        return self.screen.getyx()

    def scroll(self):
        self.screen.scroll()

    def clrtobot(self):
        self.screen.clrtobot()

    def attron(self, attr):
        self.screen.attron(attr)

    def attroff(self, attr):
        self.screen.attroff(attr)

    def clrtoeol(self):
        self.screen.clrtoeol()

    def delch(self):
        self.screen.delch()

    def attrset(self, attr):
        self.screen.attrset(attr)

    def insertln(self):
        self.screen.insertln()

    def insch(self, char):
        self.screen.insch(char)

    def deleteln(self):
        self.screen.deleteln()

    def inch(self):
        return self.screen.inch()

class BadWidth(Exception):
    pass

class Columns:
    def __init__(self, curseswindow, numcolumns=2, reverse=False):
        self.screen = curseswindow
        self.height, width = self.screen.getmaxyx()
        if numcolumns < 1:
            raise BadWidth("need at least two columns")
        self.numcolumns = numcolumns
        self.columnwidth = (width - (numcolumns - 1)) // numcolumns
        if self.columnwidth <= 0:
            raise BadWidth("resulting column width too small")
        self.windows = []
        for i in range(numcolumns):
            window = self.screen.derwin(self.height, self.columnwidth,
                                        0, i * (self.columnwidth + 1))
            window.scrollok(1)
            self.windows.append(window)
        if reverse:
            self.windows.reverse()
        self.ypos, self.xpos = 0, 0
        for i in range(1, numcolumns):
            self.screen.vline(0, i * (self.columnwidth + 1) - 1,
                              curses.ACS_VLINE, self.height)
        self.attrs = 0

    @property
    def curwin(self):
        return self.windows[self.ypos // self.height]

    @property
    def curypos(self):
        return self.ypos % self.height

    @property
    def curxpos(self):
        return self.xpos

    def getmaxyx(self):
        return (self.height * self.numcolumns, self.columnwidth)

    def move(self, ypos, xpos):
        height, width = self.getmaxyx()
        self.ypos = max(0, min(height - 1, ypos))
        self.xpos = max(0, min(width - 1, xpos))
        self.fix_cursor()

    def fix_cursor(self):
        self.curwin.move(self.curypos, self.curxpos)

    def relmove(self, yoff, xoff):
        self.move(self.ypos + yoff, self.xpos + xoff)

    def addch(self, char):
        # ponytail: one cell per char; East Asian wide / combining chars drift xpos
        if self.xpos == self.columnwidth - 1:
            if isinstance(char, str):  # insch() cannot take a wide char
                self.curwin.insstr(self.curypos, self.curxpos, char, self.attrs)
            else:
                self.curwin.insch(self.curypos, self.curxpos, char, self.attrs)
            if self.ypos + 1 == 2 * self.height:
                self.scroll()
                self.move(self.ypos, 0)
            else:
                self.move(self.ypos + 1, 0)
        else:
            self.curwin.addch(self.curypos, self.curxpos, char, self.attrs)
            self.xpos += 1

    def refresh(self):
        self.screen.refresh()
        for window in self.windows:
            if window is not self.curwin:
                window.refresh()
        self.curwin.refresh()

    def getyx(self):
        return (self.ypos, self.xpos)

    def scroll_up(self, index):
        """Copy first line of the window with given index to last line of the
        previous window and scroll up the given window."""
        assert index > 0
        previous = self.windows[index - 1]
        previous.move(self.height - 1, 0)
        for x in range(self.columnwidth - 1):
            previous.addch(self.windows[index].inch(0, x))
        previous.insch(self.windows[index].inch(0, self.columnwidth - 1))
        self.fix_cursor()
        self.windows[index].scroll()

    def scroll_down(self, index):
        """Scroll down the window with given index and copy the last line of
        the previous window to the first line of the given window."""
        assert index > 0
        current = self.windows[index]
        previous = self.windows[index - 1]
        current.scroll(-1)
        current.move(0, 0)
        for x in range(self.columnwidth - 1):
            current.addch(previous.inch(self.height - 1, x))
        current.insch(previous.inch(self.height - 1, self.columnwidth - 1))
        self.fix_cursor()

    def scroll(self):
        self.windows[0].scroll()
        for i in range(1, self.numcolumns):
            self.scroll_up(i)

    def clrtobot(self):
        index = self.ypos // self.height
        for i in range(index + 1, self.numcolumns):
            self.windows[i].clear()
        self.windows[index].clrtobot()

    def attron(self, attr):
        self.attrs |= attr

    def attroff(self, attr):
        self.attrs &= ~attr

    def clrtoeol(self):
        self.curwin.clrtoeol()

    def delch(self):
        self.curwin.delch(self.curypos, self.curxpos)

    def attrset(self, attr):
        self.attrs = attr

    def insertln(self):
        index = self.ypos // self.height
        for i in reversed(range(index + 1, self.numcolumns)):
            self.scroll_down(i)
        self.curwin.insertln()

    def insch(self, char):
        self.curwin.insch(self.curypos, self.curxpos, char, self.attrs)

    def deleteln(self):
        index = self.ypos // self.height
        self.windows[index].deleteln()
        for i in range(index + 1, self.numcolumns):
            self.scroll_up(i)

    def inch(self):
        return self.curwin.inch(self.curypos, self.curxpos)

def acs_map():
    """call after curses.initscr"""
    # can this mapping be obtained from curses?
    return {
        ord(b'l'): curses.ACS_ULCORNER,
        ord(b'm'): curses.ACS_LLCORNER,
        ord(b'k'): curses.ACS_URCORNER,
        ord(b'j'): curses.ACS_LRCORNER,
        ord(b't'): curses.ACS_LTEE,
        ord(b'u'): curses.ACS_RTEE,
        ord(b'v'): curses.ACS_BTEE,
        ord(b'w'): curses.ACS_TTEE,
        ord(b'q'): curses.ACS_HLINE,
        ord(b'x'): curses.ACS_VLINE,
        ord(b'n'): curses.ACS_PLUS,
        ord(b'o'): curses.ACS_S1,
        ord(b's'): curses.ACS_S9,
        ord(b'`'): curses.ACS_DIAMOND,
        ord(b'a'): curses.ACS_CKBOARD,
        ord(b'f'): curses.ACS_DEGREE,
        ord(b'g'): curses.ACS_PLMINUS,
        ord(b'~'): curses.ACS_BULLET,
        ord(b','): curses.ACS_LARROW,
        ord(b'+'): curses.ACS_RARROW,
        ord(b'.'): curses.ACS_DARROW,
        ord(b'-'): curses.ACS_UARROW,
        ord(b'h'): curses.ACS_BOARD,
        ord(b'i'): curses.ACS_LANTERN,
        ord(b'p'): curses.ACS_S3,
        ord(b'r'): curses.ACS_S7,
        ord(b'y'): curses.ACS_LEQUAL,
        ord(b'z'): curses.ACS_GEQUAL,
        ord(b'{'): curses.ACS_PI,
        ord(b'|'): curses.ACS_NEQUAL,
        ord(b'}'): curses.ACS_STERLING,
    }

def compose_dicts(dct1, dct2):
    result = {}
    for key, value in dct1.items():
        try:
            result[key] = dct2[value]
        except KeyError:
            pass
    return result

SIMPLE_CHARACTERS = bytearray(
    b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ' +
    b'0123456789@:~$ .#!/_(),[]=-+*\'"|<>%&\\?;`^{}')

class Terminal:
    def __init__(self, acsc, columns, reverse=False, invert=False):
        self.mode = (self.feed_simple,)
        self.masterfd = None  # set by main; used to answer terminal queries
        self.realscreen = None
        self.screen = None
        self.fg = self.bg = 0
        self.graphics_font = False
        self.graphics_chars = acsc # really initialized after
        self.lastchar = ord(b' ')
        self.saved = (0, 0)  # ponytail: DECSC/DECRC keep the cursor only, not SGR
        self.colors = {}  # (r, g, b) -> curses color slot 16..COLORS-1
        self.pairs = {}  # (fg, bg) -> color pair 128..255 for colors outside the table
        self.utf8 = codecs.getincrementaldecoder('utf-8')('replace')
        self.columns = columns
        self.reverse = reverse
        self.invert = invert

    def switchmode(self):
        if isinstance(self.screen, Columns):
            self.screen = Simple(self.realscreen)
        else:
            self.screen = Columns(self.realscreen, self.columns)
        self.screen.refresh()

    def resized(self):
        # main() has already called curses.resize_term() with the new size.
        self.realscreen.refresh()
        self.realscreen.clear()
        try:
            self.screen = Columns(self.realscreen, self.columns,
                                  reverse=self.reverse)
        except BadWidth:
            self.screen = Simple(self.realscreen)

    def resizepty(self, ptyfd):
        ym, xm = self.screen.getmaxyx()
        fcntl.ioctl(ptyfd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", ym, xm, 0, 0))

    def addch(self, char):
        self.lastchar = char
        self.screen.addch(char)

    def start(self):
        self.realscreen = curses.initscr()
        self.realscreen.nodelay(1)
        self.realscreen.keypad(1)
        curses.start_color()
        curses.use_default_colors()
        init_color_pairs(self.invert)
        self.screen = Columns(self.realscreen, self.columns,
                              reverse=self.reverse)
        curses.noecho()
        curses.raw()
        self.graphics_chars = compose_dicts(self.graphics_chars, acs_map())

    def stop(self):
        if self.colors:  # ncurses does not always reset the palette itself
            sys.stdout.write("\x1b]104\x07")
            sys.stdout.flush()
        curses.noraw()
        curses.echo()
        curses.endwin()

    def do_bel(self):
        curses.beep()

    def do_blink(self):
        self.screen.attron(curses.A_BLINK)

    def do_bold(self):
        self.screen.attron(curses.A_BOLD)

    def do_dim(self):
        self.screen.attron(curses.A_DIM)

    def set_color(self):
        self.screen.attroff(curses.A_COLOR)  # attron() would OR the pairs
        if self.fg < 16 and self.bg < 16:
            self.screen.attron(get_color(self.fg, self.bg))
        else:
            self.screen.attron(self.dynamic_pair(self.fg, self.bg))

    def true_color(self, red, green, blue):
        """Curses color for an RGB triple; palette slots 16+ are redefined on first use."""
        key = (red, green, blue)
        if key not in self.colors:
            slot = 16 + len(self.colors)
            if not curses.can_change_color() or slot >= curses.COLORS:
                return None  # ponytail: no palette access or out of slots, keep the color
            curses.init_color(slot, red * 1000 // 255, green * 1000 // 255,
                              blue * 1000 // 255)
            self.colors[key] = slot
        return self.colors[key]

    def dynamic_pair(self, fg, bg):
        """Color pair for fg/bg outside the fixed table, allocated on first use."""
        if fg < 16 and not has_bright():
            fg %= 8
        if bg == 0:
            bg = -1  # the terminal's own background, as in the fixed table
        elif bg == 7:
            bg = curses.COLOR_WHITE if self.invert else curses.COLOR_BLACK  # as in the fixed table
        key = (fg, bg)
        if key not in self.pairs:
            pair = 128 + len(self.pairs)
            if pair >= min(curses.COLOR_PAIRS, 256):  # attron() carries 8 pair bits
                return 0  # ponytail: out of pairs, default colors
            curses.init_pair(pair, fg, bg)
            self.pairs[key] = pair
        return curses.color_pair(self.pairs[key])

    def do_cr(self):
        self.screen.relmove(0, -9999)

    def do_cub(self, n):
        self.screen.relmove(0, -n)

    def do_cub1(self):
        self.do_cub(1)

    def do_cud(self, n):
        self.screen.relmove(n, 0)

    def do_cud1(self):
        self.do_cud(1)

    def do_cuf(self, n):
        self.screen.relmove(0, n)

    def do_cuf1(self):
        self.do_cuf(1)

    def do_cuu(self, n):
        self.screen.relmove(-n, 0)

    def do_cuu1(self):
        self.do_cuu(1)

    def do_dch(self, n):
        for _ in range(n):
            self.screen.delch()

    def do_dch1(self):
        self.do_dch(1)

    def do_dl(self, n):
        for _ in range(n):
            self.screen.deleteln()

    def do_dl1(self):
        self.do_dl(1)

    def do_ech(self, n):
        for _ in range(n):
            self.screen.addch(ord(b' '))

    def do_ed(self):
        self.screen.clrtobot()

    def do_el(self):
        self.screen.clrtoeol()

    def do_el1(self):
        y, x = self.screen.getyx()
        self.screen.move(y, 0)
        for _ in range(x):
            self.screen.addch(ord(b' '))

    def do_home(self):
        self.screen.move(0, 0)

    def do_hpa(self, n):
        y, _ = self.screen.getyx()
        self.screen.move(y, n)

    def do_ht(self):
        y, x = self.screen.getyx()
        _, xm = self.screen.getmaxyx()
        x = min(x + 8 - x % 8, xm - 1)
        self.screen.move(y, x)

    def do_ich(self, n):
        for _ in range(n):
            self.screen.insch(ord(b' '))

    def do_il(self, n):
        for _ in range(n):
            self.screen.insertln()

    def do_il1(self):
        self.do_il(1)

    def do_ind(self):
        y, _ = self.screen.getyx()
        ym, _ = self.screen.getmaxyx()
        if y + 1 == ym:
            self.screen.scroll()
            self.screen.move(y, 0)
        else:
            self.screen.move(y+1, 0)

    def do_invis(self):
        self.screen.attron(curses.A_INVIS)

    def do_smul(self):
        self.screen.attron(curses.A_UNDERLINE)

    def do_vpa(self, n):
        _, x = self.screen.getyx()
        self.screen.move(n, x)

    def feed_reset(self):
        if self.graphics_font:
            self.mode = (self.feed_graphics,)
        else:
            self.mode = (self.feed_simple,)

    def feed(self, char):
        self.mode[0](char, *self.mode[1:])

    def feed_simple(self, char):
        func = {
            ord('\a'): self.do_bel,
            ord('\b'): self.do_cub1,
            ord('\n'): self.do_ind,
            ord('\r'): self.do_cr,
            ord('\t'): self.do_ht,
            0x0e: self.do_smacs,
            0x0f: self.do_rmacs,
            }.get(char)
        if func:
            func()
        elif char in SIMPLE_CHARACTERS:
            self.addch(char)
        elif char == 0x1b:
            self.mode = (self.feed_esc,)
        elif char >= 0x80:
            for c in self.utf8.decode(bytes((char,))):
                self.addch(c)
        else:
            raise ValueError("feed %r" % char)

    def feed_graphics(self, char):
        if char in self.graphics_chars:
            self.addch(self.graphics_chars[char])
        elif char == ord(b'q'):  # some applications appear to use VT100 names?
            self.addch(curses.ACS_HLINE)
        else:
            self.feed_simple(char)  # controls, escapes and text work as usual

    def do_smacs(self):
        self.graphics_font = True
        self.feed_reset()

    def do_rmacs(self):
        self.graphics_font = False
        self.feed_reset()

    def feed_charset(self, char, gset):
        # ESC ( X designates G0, the set in use; ESC ) X designates G1.
        # ponytail: G1 assumed to be line drawing, SO/SI just toggle graphics.
        if gset == ord(b'('):
            self.graphics_font = char == ord(b'0')
        self.feed_reset()

    def do_sc(self):
        self.saved = self.screen.getyx()

    def do_rc(self):
        self.screen.move(*self.saved)

    def do_da1(self):
        os.write(self.masterfd, b'\x1b[?6c')  # "I am a VT102"; fish 4 waits for this

    def feed_esc(self, char):
        func = {
            ord('7'): self.do_sc,
            ord('8'): self.do_rc,
            }.get(char)
        if func:
            self.feed_reset()
            func()
        elif char == ord(b'['):
            self.mode = (self.feed_esc_opbr,)
        elif char in bytearray(b']P'):
            self.mode = (self.feed_string, False)
        elif char in bytearray(b'=>'):  # keypad modes
            self.feed_reset()
        elif char in bytearray(b'()'):
            self.mode = (self.feed_charset, char)
        else:
            raise ValueError("feed esc %r" % char)

    def feed_string(self, char, esc):
        # ponytail: OSC/DCS (fish 4 terminal probes) swallowed until BEL or ESC \
        if char == 0x07 or (esc and char == ord(b'\\')):
            self.feed_reset()
        else:
            self.mode = (self.feed_string, char == 0x1b)

    def feed_esc_opbr(self, char):
        self.feed_reset()
        func = {
            ord('A'): self.do_cuu1,
            ord('B'): self.do_cud1,
            ord('C'): self.do_cuf1,
            ord('D'): self.do_cub1,
            ord('H'): self.do_home,
            ord('J'): self.do_ed,
            ord('L'): self.do_il1,
            ord('M'): self.do_dl1,
            ord('K'): self.do_el,
            ord('P'): self.do_dch1,
            ord('c'): self.do_da1,
            }.get(char)
        if func:
            func()
        elif char == ord(b'm'):
            self.feed_esc_opbr_next(char, bytearray(b'0'))
        elif char in bytearray(b'0123456789'):
            self.mode = (self.feed_esc_opbr_next, bytearray((char,)))
        elif char in bytearray(b'?>=<'):
            self.mode = (self.feed_esc_private,)
        elif char == ord(b'r'):
            pass  # DECSTBM reset to full screen: already the only region
        else:
            raise ValueError("feed esc [ %r" % char)

    def feed_esc_private(self, char):
        # ponytail: DEC private modes and xterm queries (ESC [ ? 1049 h, ESC [ > 0 q) ignored
        if char not in bytearray(b'0123456789;'):
            self.feed_reset()

    def feed_color(self, code):
        func = {
            1: self.do_bold,
            2: self.do_dim,
            4: self.do_smul,
            5: self.do_blink,
            8: self.do_invis,
            }.get(code)
        off = {
            22: curses.A_BOLD | curses.A_DIM,
            24: curses.A_UNDERLINE,
            25: curses.A_BLINK,
            27: curses.A_REVERSE,
            28: curses.A_INVIS,
            }.get(code)
        if func:
            func()
        elif off:
            self.screen.attroff(off)
        elif code == 0:
            self.fg = self.bg = 0
            self.screen.attrset(0)
        elif code == 7:
            self.screen.attron(curses.A_REVERSE)
        elif code == 10:
            self.do_rmacs()
        elif code == 11:
            self.do_smacs()
        elif 30 <= code <= 37:
            self.fg = code - 30
            self.set_color()
        elif 90 <= code <= 97:
            self.fg = code - 90 + 8
            self.set_color()
        elif code == 39:
            self.fg = 7
            self.set_color()
        elif 40 <= code <= 47 or 100 <= code <= 107:
            self.bg = code % 10  # ponytail: bright backgrounds shown as normal
            self.set_color()
        elif code == 49:
            self.bg = 0
            self.set_color()
        else:
            raise ValueError("feed esc [ %r m" % code)

    def feed_esc_opbr_next(self, char, prev):
        self.feed_reset()
        func = {
            ord('A'): self.do_cuu,
            ord('B'): self.do_cud,
            ord('C'): self.do_cuf,
            ord('D'): self.do_cub,
            ord('L'): self.do_il,
            ord('M'): self.do_dl,
            ord('P'): self.do_dch,
            ord('X'): self.do_ech,
            ord('@'): self.do_ich,
            }.get(char)
        if func and prev.isdigit():
            func(int(prev))
        elif char in bytearray(b'0123456789;'):
            self.mode = (self.feed_esc_opbr_next, prev + bytearray((char,)))
        elif char == ord(b'm'):
            parts = [int(p) for p in prev.split(b';')]
            while parts:
                code = parts.pop(0)
                if code in (38, 48):  # 38;5;n / 38;2;r;g;b
                    sub = parts[:2] if parts[:1] == [5] else parts[:4]
                    del parts[:len(sub)]
                    color = None
                    if sub[:1] == [5] and len(sub) == 2 and sub[1] < max(curses.COLORS, 16):
                        color = sub[1]
                    elif sub[:1] == [2] and len(sub) == 4:
                        color = self.true_color(*sub[1:])
                    if color is not None:
                        if code == 38:
                            self.fg = color
                        else:
                            self.bg = color if color >= 16 else color % 8
                        self.set_color()
                else:
                    self.feed_color(code)
        elif char == ord(b'H'):
            parts = prev.split(b';')
            if len(parts) != 2:
                raise ValueError("feed esc [ %r H" % parts)
            self.screen.move(*map((-1).__add__, map(int, parts)))
        elif prev == bytearray(b'2') and char == ord(b'J'):
            self.screen.move(0, 0)
            self.screen.clrtobot()
        elif char == ord(b'd') and prev.isdigit():
            self.do_vpa(int(prev) - 1)
        elif char == ord(b'b') and prev.isdigit():
            for _ in range(int(prev)):
                self.screen.addch(self.lastchar)
        elif char == ord(b'G') and prev.isdigit():
            self.do_hpa(int(prev) - 1)
        elif char == ord(b'K') and prev == b'1':
            self.do_el1()
        elif char == ord(b'c'):
            self.do_da1()
        elif char == ord(b'n'):
            pass  # cursor position query, unanswered
        else:
            raise ValueError("feed esc [ %r %r" % (prev, char))

SYMBOLIC_KEYMAPPING = {
    ord(b"\n"): "cr",
    curses.KEY_LEFT: "kcub1",
    curses.KEY_DOWN: "kcud1",
    curses.KEY_RIGHT: "kcuf1",
    curses.KEY_UP: "kcuu1",
    curses.KEY_HOME: "khome",
    curses.KEY_IC: "kich1",
    curses.KEY_BACKSPACE: "kbs",
    curses.KEY_PPAGE: "kpp",
    curses.KEY_NPAGE: "knp",
    curses.KEY_F1: "kf1",
    curses.KEY_F2: "kf2",
    curses.KEY_F3: "kf3",
    curses.KEY_F4: "kf4",
    curses.KEY_F5: "kf5",
    curses.KEY_F6: "kf6",
    curses.KEY_F7: "kf7",
    curses.KEY_F8: "kf8",
    curses.KEY_F9: "kf9",
}

def compute_keymap(symbolic_map):
    oldterm = os.environ["TERM"]
    curses.setupterm("ansi")
    keymap = {}
    for key, value in symbolic_map.items():
        keymap[key] = (curses.tigetstr(value) or b"").replace(b"\\E", b"\x1b")
    acsc = curses.tigetstr("acsc")
    acsc = bytearray(acsc)
    acsc = dict(zip(acsc[1::2], acsc[::2]))
    curses.setupterm(oldterm)
    return keymap, acsc

def set_cloexec(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFD, 0)
    flags |= fcntl.FD_CLOEXEC
    fcntl.fcntl(fd, fcntl.F_SETFD, flags)

def main():
    locale.setlocale(locale.LC_ALL, '')  # curses needs it for wide chars
    # Options
    parser = optparse.OptionParser()
    parser.disable_interspersed_args()
    parser.add_option("-c", "--columns", dest="columns", metavar="N",
                      type="int", default=2, help="Number of columns")
    parser.add_option("-r", "--reverse", action="store_true",
                      dest="reverse", default=False,
                      help="Order last column to the left")
    parser.add_option("-i", "--invert", action="store_true",
                      dest="invert", default=False,
                      help="Invert the foreground and background colors")
    options, args = parser.parse_args()

    # Environment
    keymapping, acsc = compute_keymap(SYMBOLIC_KEYMAPPING)
    t = Terminal(acsc, options.columns, reverse=options.reverse,
                 invert=options.invert)

    errpiper, errpipew = os.pipe()
    set_cloexec(errpipew)
    pid, masterfd = pty.fork()
    if pid == 0: # child
        os.close(errpiper)
        try:
            if len(args) < 1:
                os.execvp(os.environ["SHELL"], [os.environ["SHELL"]])
            else:
                os.execvp(args[0], args)
        except OSError as err:
            os.write(errpipew, "exec failed: %s" % (err,))
        sys.exit(1)

    os.close(errpipew)
    data = os.read(errpiper, 1024)
    os.close(errpiper)
    if data:
        print(data)
        sys.exit(1)

    # Self-pipe: SIGWINCH only wakes the select loop, the resize happens there.
    winchr, winchw = os.pipe()
    os.set_blocking(winchw, False)
    def on_winch(_signum, _frame):
        try:
            os.write(winchw, b'w')
        except BlockingIOError:
            pass  # a wake-up is already pending

    # Begin multicolumn layout
    try:
        t.masterfd = masterfd
        t.start()
        signal.signal(signal.SIGWINCH, on_winch)  # replaces the ncurses handler
        t.resizepty(masterfd)
        refreshpending = None
        while True:
            res, _, _ = select.select([0, masterfd, winchr], [], [],
                                      refreshpending and 0)
            if winchr in res:
                os.read(winchr, 1024)  # coalesce queued resizes
                size = os.get_terminal_size(0)
                curses.resize_term(size.lines, size.columns)
                t.resized()
                t.resizepty(masterfd)
            elif 0 in res:
                while True:
                    key = t.realscreen.getch()
                    if key == -1:
                        break
                    if key == 0xb3:
                        t.switchmode()
                        t.resizepty(masterfd)
                    elif key in keymapping:
                        os.write(masterfd, keymapping[key])
                    elif key <= 0xff:
                        os.write(masterfd, struct.pack("B", key))
                    else:
                        if "TCVT_DEVEL" in os.environ:
                            raise ValueError("getch returned %d" % key)
            elif masterfd in res:
                try:
                    data = os.read(masterfd, 1024)
                except OSError:
                    break
                if not data:
                    break
                for char in bytearray(data):
                    if "TCVT_DEVEL" in os.environ:
                        t.feed(char)
                    else:
                        try:
                            t.feed(char)
                        except ValueError:
                            t.feed_reset()
                if refreshpending is None:
                    refreshpending = time.time() + 0.1
            elif refreshpending is not None:
                t.screen.refresh()
                refreshpending = None
            if refreshpending is not None and refreshpending < time.time():
                t.screen.refresh()
                refreshpending = None
    finally:
        t.stop()

if __name__ == '__main__':
    main()
