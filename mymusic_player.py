#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
myusic - Lightweight Terminal Music Player (CLI/TUI) for Linux.

Features:
- Automatically detects the ~/Myusic folder (creates it if missing)
- Plays .mp3, .flac, .ogg, .wav, .m4a files located in ~/Myusic
- Interactive curses-based TUI (similar to cmus)
- Vim-style key bindings and a ':' command mode
- Audio backend: external subprocess via mpv (preferred), ffplay, or vlc
- External player stdout/stderr/stdin are sent to /dev/null so they never
  corrupt the curses layout
- File validation is intentionally light: exists + supported extension +
  non-empty file. Actual decoding is left to the external player.
- Uses curses default colors with -1 background so a transparent/translucent
  terminal background/wallpaper remains visible.

Quick install (Arch Linux):
    sudo pacman -S mpv
    chmod +x mymusic_player.py
    sudo cp mymusic_player.py /usr/local/bin/myusic
"""

import os
import sys
import time
import math
import shutil
import signal
import curses
import subprocess
from pathlib import Path

MUSIC_DIR = Path.home() / "Myusic"
SUPPORTED = {".mp3", ".flac", ".ogg", ".wav", ".m4a"}

STOPPED = "STOPPED"
PLAYING = "PLAYING"
PAUSED = "PAUSED"

SPECTRUM_CHARS = ["▂", "▃", "▄", "▅", "▆", "▇", "█"]

# Backend priority: mpv is preferred, with ffplay/vlc as fallbacks.
BACKENDS = [
    ("mpv", ["--no-video", "--really-quiet", "--force-window=no", "--loop-file=no"]),
    ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
    ("vlc", ["--intf", "dummy", "--play-and-exit", "--no-video", "--quiet"]),
]
BACKEND_NAME, BACKEND_ARGS, BACKEND_PATH = None, None, None

def find_backend():
    for name, args in BACKENDS:
        path = shutil.which(name)
        if path:
            return name, args, path
    return None, None, None

BACKEND_NAME, BACKEND_ARGS, BACKEND_PATH = find_backend()
HAS_BACKEND = BACKEND_PATH is not None

def probe_audio_file(path):
    """
    Minimal validation only. Do NOT deep-decode here.

    A file is considered a playable candidate when:
      - its extension is supported,
      - it exists,
      - it is a regular file,
      - its size is greater than zero.

    The external player decides whether the file can actually be decoded.
    """
    try:
        p = Path(path)
        ext = p.suffix.lower()
        if ext not in SUPPORTED:
            return False, "unsupported extension"
        if not p.exists():
            return False, "file not found"
        if not p.is_file():
            return False, "not a file"
        if p.stat().st_size <= 0:
            return False, "empty file"
        return True, "ok"
    except PermissionError:
        return False, "permission denied"
    except OSError as e:
        return False, f"read error: {e}"
    except Exception as e:
        return False, f"validation error: {e}"

def get_duration_seconds(path):
    """Read duration via ffprobe when available. Return 0.0 if unavailable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        value = (proc.stdout or "").strip()
        return max(0.0, float(value)) if value else 0.0
    except Exception:
        return 0.0

class MyusicApp:
    def __init__(self, stdscr):
        self.stdscr = stdscr

        self.songs = []
        self.file_ok = {}
        self.file_reason = {}

        self.selected = 0
        self.current = -1
        self.status = STOPPED

        self.proc = None
        self.pos = 0.0
        self.duration = 0.0
        self.start_time = 0.0
        self.pause_time = 0.0
        self.paused_total = 0.0

        self.scroll_offset = 0
        self.cmd = ""
        self.cmd_mode = False
        self.msg = ""
        self.msg_time = 0.0
        self.show_help_screen = False
        self.running = True
        self.spectrum = []

        self.ensure_dir()
        self.scan()

        if not HAS_BACKEND:
            self.notify("No audio backend found. Install one with: sudo pacman -S mpv")

    def ensure_dir(self):
        try:
            if not MUSIC_DIR.exists():
                MUSIC_DIR.mkdir(parents=True, exist_ok=True)
                self.notify(f"Directory created: {MUSIC_DIR}")
        except Exception as e:
            self.notify(f"Failed to create directory: {e}")

    def probe_cached(self, path):
        key = str(path)
        if key not in self.file_ok:
            ok, reason = probe_audio_file(path)
            self.file_ok[key] = ok
            self.file_reason[key] = reason
        return self.file_ok[key], self.file_reason[key]

    def scan(self):
        try:
            self.songs = sorted(
                [p for p in MUSIC_DIR.iterdir()
                 if p.is_file() and p.suffix.lower() in SUPPORTED],
                key=lambda p: p.name.lower(),
            )
        except Exception as e:
            self.songs = []
            self.notify(f"Failed to scan folder: {e}")

        self.file_ok = {}
        self.file_reason = {}
        for song in self.songs:
            self.probe_cached(song)

        if self.selected >= len(self.songs):
            self.selected = max(0, len(self.songs) - 1)
        if self.current >= len(self.songs):
            self.stop()
            self.current = -1

    def notify(self, text):
        self.msg = text
        self.msg_time = time.time()

    def _spawn_player(self, path):
        if not HAS_BACKEND:
            raise RuntimeError("mpv/ffplay/vlc not found")

        args = [BACKEND_PATH, *(BACKEND_ARGS or []), str(path)]

        # Send stdout/stderr/stdin to DEVNULL so the player cannot draw over curses.
        return subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _terminate_proc(self):
        proc = self.proc
        self.proc = None
        if proc is None:
            return

        try:
            if proc.poll() is not None:
                return

            # Ask the player to stop first.
            try:
                os.kill(proc.pid, signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass

            proc.wait(timeout=0.6)
        except Exception:
            # If still alive (e.g. SIGSTOP-paused), wake it and force kill.
            try:
                os.kill(proc.pid, signal.SIGCONT)
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                try:
                    os.kill(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
            try:
                proc.wait(timeout=0.6)
            except Exception:
                pass

    def _load_and_play(self, index):
        if not self.songs:
            self.notify("No songs found in ~/Myusic")
            return False

        index = max(0, min(index, len(self.songs) - 1))
        path = self.songs[index]

        ok, reason = self.probe_cached(path)
        if not ok:
            self.notify(f"Skipped ({path.name}): {reason}")
            return False

        if not HAS_BACKEND:
            self.notify("No audio backend found. Install one with: sudo pacman -S mpv")
            return False

        self._terminate_proc()

        try:
            self.proc = self._spawn_player(path)
        except Exception as e:
            self.proc = None
            self.status = STOPPED
            self.notify(f"Failed to load ({path.name}): {e}")
            return False

        self.current = index
        self.selected = index
        self.status = PLAYING
        self.start_time = time.time()
        self.pause_time = 0.0
        self.paused_total = 0.0
        self.pos = 0.0
        self.duration = get_duration_seconds(path)
        self.notify(f"Playing ({BACKEND_NAME}): {path.name}")
        return True

    def play_index(self, index):
        return self._load_and_play(index)

    def _valid_indices(self):
        return [i for i, p in enumerate(self.songs) if self.probe_cached(p)[0]]

    def _advance(self, direction):
        valid = self._valid_indices()
        if not valid:
            self.notify("No valid audio files to play.")
            return

        n = len(self.songs)
        start = self.current if 0 <= self.current < n else self.selected
        if start < 0:
            start = 0

        for step in range(1, n + 1):
            idx = (start + direction * step) % n
            if idx in valid:
                self.play_index(idx)
                return

        self.notify("No valid audio files to play.")

    def next_song(self):
        self._advance(+1)

    def prev_song(self):
        self._advance(-1)

    def toggle_pause(self):
        if not HAS_BACKEND:
            self.notify("No audio backend found. Install one with: sudo pacman -S mpv")
            return

        if self.proc is None:
            if self.current >= 0 and self.songs:
                self.play_index(self.current)
            elif self.songs:
                self.play_index(self.selected if self.selected >= 0 else 0)
            return

        try:
            if self.status == PLAYING:
                # POSIX pause/resume: SIGSTOP stops the player process,
                # SIGCONT resumes audio playback.
                os.kill(self.proc.pid, signal.SIGSTOP)
                self.status = PAUSED
                self.pause_time = time.time()
            elif self.status == PAUSED:
                os.kill(self.proc.pid, signal.SIGCONT)
                self.status = PLAYING
                now = time.time()
                self.paused_total += now - self.pause_time
                self.pause_time = 0.0
            else:
                self.play_index(self.selected if self.selected >= 0 else 0)
        except Exception as e:
            self.notify(f"Pause/Resume failed: {e}")

    def stop(self):
        self._terminate_proc()
        self.status = STOPPED
        self.pos = 0.0
        self.start_time = 0.0
        self.pause_time = 0.0
        self.paused_total = 0.0

    def add_file(self, src_str):
        src = Path(src_str).expanduser()
        if not src.exists():
            self.notify(f"File not found: {src}")
            return
        if not src.is_file():
            self.notify("Path is not a file.")
            return
        if src.suffix.lower() not in SUPPORTED:
            self.notify("Unsupported file format.")
            return
        if src.stat().st_size == 0:
            self.notify("Empty file, not added.")
            return

        dest = MUSIC_DIR / src.name
        if dest.exists():
            self.notify(f"Already exists: {src.name}")
            return

        try:
            shutil.copy2(src, dest)
            self.scan()
            self.notify(f"Added: {src.name}")
        except Exception as e:
            self.notify(f"Failed to copy file: {e}")

    def update(self):
        if self.status == PLAYING:
            # Detect when the player exits naturally at the end of the track.
            if self.proc is not None and self.proc.poll() is not None:
                self.proc = None
                self.next_song()
                return
            if self.start_time > 0:
                self.pos = time.time() - self.start_time - self.paused_total
        else:
            # PAUSED/STOPPED: keep the visualizer flat.
            if self.spectrum:
                self.spectrum = [0.0] * len(self.spectrum)

    def _ensure_spectrum(self, width):
        width = max(0, width)
        if len(self.spectrum) != width:
            if width > len(self.spectrum):
                self.spectrum.extend([0.0] * (width - len(self.spectrum)))
            else:
                self.spectrum = self.spectrum[:width]

    def _spectrum_target(self, width):
        target = []
        t = self.pos if self.pos > 0 else time.time()
        for x in range(width):
            wave = 0.36
            wave += 0.28 * math.sin(x * 0.32 + t * 2.2)
            wave += 0.22 * math.sin(x * 0.11 - t * 1.35)
            wave += 0.14 * math.sin(x * 0.73 + t * 3.4)
            wave += 0.08 * math.sin(x * 1.9 - t * 5.1)
            wave *= 0.65 + 0.35 * math.sin(t * 0.7 + 0.5)
            target.append(max(0.0, min(1.0, pow(wave, 1.55))))
        return target

    def _update_spectrum(self, width):
        self._ensure_spectrum(width)
        if self.status != PLAYING:
            self.spectrum = [0.0] * width
            return

        target = self._spectrum_target(width)
        self.spectrum = [old + (new - old) * 0.48 for old, new in zip(self.spectrum, target)]

    @staticmethod
    def fmt_time(seconds):
        try:
            seconds = max(0, int(seconds))
        except Exception:
            seconds = 0
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"

    def _safe_addstr(self, y, x, text, width, attr=0):
        try:
            if attr:
                self.stdscr.attron(attr)
            self.stdscr.addnstr(y, x, text[: max(0, width - 1)], max(0, width - 1))
            if attr:
                self.stdscr.attroff(attr)
        except curses.error:
            pass

    def run_command(self, text):
        text = text.strip()
        if not text:
            return

        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("help", "h", "?"):
            self.show_help_screen = True
        elif cmd in ("quit", "q", "exit"):
            self.running = False
        elif cmd in ("rescan", "r"):
            self.scan()
            ok_count = sum(1 for p in self.songs if self.probe_cached(p)[0])
            self.notify(f"Rescanned. {len(self.songs)} files, {ok_count} valid.")
        elif cmd == "play":
            if not args:
                self.notify("Usage: :play <number>")
                return
            try:
                self.play_index(int(args[0]) - 1)
            except ValueError:
                self.notify("Invalid song number.")
        elif cmd == "add":
            if not args:
                self.notify("Usage: :add <path>")
                return
            self.add_file(args[0])
        else:
            self.notify(f"Unknown command: :{cmd}")

    def draw(self):
        try:
            self.stdscr.erase()
        except curses.error:
            return

        h, w = self.stdscr.getmaxyx()
        if w < 20 or h < 8:
            try:
                self.stdscr.refresh()
            except curses.error:
                pass
            return

        normal_attr = curses.color_pair(1) if curses.has_colors() else 0
        accent_attr = curses.color_pair(2) if curses.has_colors() else curses.A_BOLD
        selected_attr = curses.color_pair(3) | curses.A_BOLD if curses.has_colors() else curses.A_REVERSE

        header = " Myusic - Terminal Music Player "
        self._safe_addstr(0, 0, header.ljust(w), w, accent_attr | curses.A_BOLD)

        backend = BACKEND_NAME or "none"
        valid = sum(1 for p in self.songs if self.probe_cached(p)[0])
        info = f"Folder: {MUSIC_DIR}  |  Songs: {len(self.songs)}  |  Valid: {valid}  |  Backend: {backend}"
        self._safe_addstr(1, 0, info, w, normal_attr)

        if h >= 16:
            viz_h = 6
        elif h >= 12:
            viz_h = 4
        else:
            viz_h = 0
        viz_y = h - 3 - viz_h

        if self.show_help_screen:
            self.draw_help(h, w, normal_attr, accent_attr)
        else:
            self.draw_list(viz_y, w, start_y=3, normal_attr=normal_attr, selected_attr=selected_attr)
            if viz_h:
                self.draw_visualizer(viz_y, viz_h, w, normal_attr, accent_attr)

        status_y = h - 3
        status = f"[{self.status}] "
        if 0 <= self.current < len(self.songs):
            status += f"{self.current + 1}. {self.songs[self.current].name}  |  "
        status += f"{self.fmt_time(self.pos)} / {self.fmt_time(self.duration)}"
        self._safe_addstr(status_y, 0, status, w, accent_attr | curses.A_BOLD)

        # Hints or transient status message.
        if self.msg and time.time() - self.msg_time < 6:
            self._safe_addstr(h - 2, 0, self.msg, w, accent_attr | curses.A_BOLD)
        else:
            self._safe_addstr(
                h - 2,
                0,
                "j/k=Navigate | Enter=Play | Space/c=Pause | v=Stop | b=Next | z=Prev | :=Command | q=Quit",
                w,
                normal_attr,
            )

        try:
            if self.cmd_mode:
                self.stdscr.addnstr(h - 1, 0, ":" + self.cmd, w - 1, normal_attr)
                self.stdscr.clrtoeol()
                self.stdscr.move(h - 1, min(len(self.cmd) + 1, w - 1))
            else:
                self.stdscr.addnstr(h - 1, 0, ">", w - 1, normal_attr)
                self.stdscr.clrtoeol()
        except curses.error:
            pass

        try:
            self.stdscr.refresh()
            curses.doupdate()
        except curses.error:
            pass

    def draw_list(self, bottom_y, w, start_y=3, normal_attr=0, selected_attr=0):
        start_y = max(0, start_y)
        end_y = max(start_y, bottom_y)
        list_h = max(1, end_y - start_y)

        if self.songs:
            if self.selected < self.scroll_offset:
                self.scroll_offset = self.selected
            elif self.selected >= self.scroll_offset + list_h:
                self.scroll_offset = self.selected - list_h + 1
            self.scroll_offset = max(
                0, min(self.scroll_offset, max(0, len(self.songs) - list_h))
            )
        else:
            self.scroll_offset = 0

        if not self.songs:
            self._safe_addstr(
                start_y,
                0,
                "(Folder is empty. Use :add <path> to add music files)",
                w,
                normal_attr,
            )
            return

        for i in range(self.scroll_offset, min(self.scroll_offset + list_h, len(self.songs))):
            y = start_y + (i - self.scroll_offset)
            if y >= end_y:
                break

            song = self.songs[i]
            ok, _ = self.probe_cached(song)
            play_marker = "> " if i == self.current else "  "
            suffix = "" if ok else " [invalid]"
            line = f"{i + 1:>3}. {play_marker}{song.name}{suffix}"

            attr = selected_attr if i == self.selected else normal_attr
            self._safe_addstr(y, 0, line, w, attr)

    def draw_visualizer(self, y_start, height, w, normal_attr, accent_attr):
        if height <= 0 or w <= 0:
            return

        self._update_spectrum(w)
        bars_attr = curses.color_pair(4) if curses.has_colors() else accent_attr

        for row in range(height):
            y = y_start + row
            row_from_bottom = y_start + height - 1 - y
            line = []

            for x in range(w):
                level = self.spectrum[x]
                bar_height = int(level * height)
                if level > 0 and bar_height == 0:
                    bar_height = 1

                if row_from_bottom < bar_height:
                    ratio = (row_from_bottom + 1) / max(bar_height, 1)
                    char_index = min(len(SPECTRUM_CHARS) - 1, int(ratio * (len(SPECTRUM_CHARS) - 1)))
                    line.append(SPECTRUM_CHARS[char_index])
                else:
                    line.append(" ")

            self._safe_addstr(y, 0, "".join(line), w, bars_attr if any(c != " " for c in line) else normal_attr)

    def draw_help(self, h, w, normal_attr, accent_attr):
        help_text = [
            "",
            " COMMANDS (Command Mode, prefix with ':')",
            "  :add <path>    Copy a music file into ~/Myusic",
            "  :play <number> Play a song by its list number",
            "  :rescan / :r   Rescan the ~/Myusic folder",
            "  :help          Show this help screen",
            "  :quit / :q     Quit Myusic",
            "",
            " KEY BINDINGS (Normal Mode)",
            "  j / Down Arrow   Move cursor down",
            "  k / Up Arrow     Move cursor up",
            "  Enter            Play selected song",
            "  Space / c        Pause / resume (SIGSTOP/SIGCONT)",
            "  v                Stop (kill backend player)",
            "  b                Next song",
            "  z                Previous song",
            "  r                Quick rescan",
            "  q                Quit",
            "",
            f" Active backend: {BACKEND_NAME or 'none'}",
            " Press any key to return...",
        ]

        start_y = max(3, (h - len(help_text)) // 2)
        for i, line in enumerate(help_text):
            y = start_y + i
            if 0 <= y < h - 3:
                x = max(0, (w - len(line)) // 2)
                bold = curses.A_BOLD if line.startswith(" COMMANDS") or line.startswith(" KEY BINDINGS") else 0
                self._safe_addstr(y, x, line, w, accent_attr | bold if bold else normal_attr)

    def run(self):
        try:
            curses.curs_set(0)
        except Exception:
            pass

        self.stdscr.timeout(100)

        while self.running:
            self.update()
            self.draw()

            try:
                ch = self.stdscr.getch()
            except curses.error:
                continue

            if ch == -1 or ch == curses.KEY_RESIZE:
                continue

            if self.show_help_screen:
                self.show_help_screen = False
                continue

            if self.cmd_mode:
                if ch in (curses.KEY_ENTER, 10, 13):
                    command = self.cmd
                    self.cmd = ""
                    self.cmd_mode = False
                    self.run_command(command)
                elif ch == 27:  # ESC
                    self.cmd = ""
                    self.cmd_mode = False
                elif ch in (curses.KEY_BACKSPACE, 127, 8):
                    self.cmd = self.cmd[:-1]
                elif 32 <= ch <= 126:
                    self.cmd += chr(ch)
                continue

            if ch in (ord("j"), curses.KEY_DOWN):
                if self.songs:
                    self.selected = (self.selected + 1) % len(self.songs)
            elif ch in (ord("k"), curses.KEY_UP):
                if self.songs:
                    self.selected = (self.selected - 1) % len(self.songs)
            elif ch in (curses.KEY_ENTER, 10, 13):
                if self.songs:
                    self.play_index(self.selected)
            elif ch in (ord(" "), ord("c")):
                self.toggle_pause()
            elif ch == ord("v"):
                self.stop()
            elif ch == ord("b"):
                self.next_song()
            elif ch == ord("z"):
                self.prev_song()
            elif ch == ord(":"):
                self.cmd_mode = True
                self.cmd = ""
            elif ch == ord("q"):
                self.running = False
            elif ch == ord("r"):
                self.scan()
                ok_count = sum(1 for p in self.songs if self.probe_cached(p)[0])
                self.notify(f"Rescanned. {len(self.songs)} files, {ok_count} valid.")
            elif ch == ord("h"):
                self.show_help_screen = True

def setup_colors(stdscr):
    """Configure curses for a transparent/default terminal background."""
    if not curses.has_colors():
        return

    curses.start_color()
    try:
        curses.use_default_colors()
    except curses.error:
        # Some terminals do not support default colors; fall back gracefully.
        pass

    # -1 means "terminal default background" for every color pair.
    try:
        curses.init_pair(1, curses.COLOR_WHITE, -1)
        curses.init_pair(2, curses.COLOR_CYAN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_GREEN, -1)
    except curses.error:
        pass

    try:
        # Paint the window with the default background pair, not an opaque color.
        stdscr.bkgd(" ", curses.color_pair(1))
    except curses.error:
        pass

def run_tui(stdscr):
    """Internal curses entry point used by curses.wrapper()."""
    setup_colors(stdscr)
    app = MyusicApp(stdscr)
    try:
        app.run()
    finally:
        try:
            app.stop()
        except Exception:
            pass

def main():
    """Console-script entry point for `myusic`."""
    if not HAS_BACKEND:
        print("Warning: no mpv/ffplay/vlc backend found.")
        print("Install on Arch Linux: sudo pacman -S mpv")
        print("Ubuntu/Debian         : sudo apt install mpv")
        print("Fedora                : sudo dnf install mpv")
        time.sleep(1.2)

    try:
        curses.wrapper(run_tui)
    except KeyboardInterrupt:
        pass

    print("Thanks for using Myusic!")

if __name__ == "__main__":
    main()
