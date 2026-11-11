# myusic

**myusic** is a lightweight, cross-distribution Linux terminal music player with a cmus-style TUI written in Python. It scans a single user folder, `~/Myusic`, and plays local audio files without requiring a heavyweight desktop application.

## Features

- Automatic `~/Myusic` folder management: created automatically on first run if missing
- Supports `.mp3`, `.flac`, `.ogg`, `.wav`, and `.m4a`
- cmus-like curses interface with header, song list, status bar, and `:` prompt
- Vim-style keybindings and command mode
- Multi-backend audio engine:
  - `mpv` preferred/recommended
  - `ffplay` fallback
  - `vlc` fallback
- External player logs are sent to `/dev/null` so the TUI is not overwritten
- Light file validation: supported extension + exists + regular non-empty file
- Transparent/translucent terminal background support using curses default colors (`-1` background)
- Works on Arch Linux, Ubuntu/Debian, Fedora, and other mainstream Linux distributions
- Installable with `pipx install .`, `pip install .`, or `./install.sh`

## Screenshot / UI Layout

The main screen contains:

1. Header banner: `Myusic - Terminal Music Player`
2. Folder/status line: folder path, song count, valid count, active backend
3. Indexed song list with a `>` playback marker
4. Status bar: `PLAYING` / `PAUSED` / `STOPPED`, current track, elapsed time
5. Transient notification line and hint line
6. `:` command prompt at the bottom

## Prerequisites

### Required

- `python3` (3.8+ recommended)
- Python standard library only; `pygame` is **not** required
- One external audio backend:
  - `mpv` **recommended**
  - or `ffplay` from `ffmpeg`
  - or `vlc`

### Recommended

- `ffmpeg` / `ffprobe` for track duration display

### Arch Linux

```bash
sudo pacman -S python mpv ffmpeg
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install python3 mpv ffmpeg
```

### Fedora

```bash
sudo dnf install python3 mpv ffmpeg
```

Depending on your Fedora setup, `ffmpeg`/`vlc` may require RPM Fusion. If in doubt, `mpv` is the recommended backend.

## Installation

### Option A: `pipx install .` (recommended)

Install `myusic` as an isolated CLI application:

```bash
cd /path/to/myusic
pipx install .
myusic
```

If pipx is not installed:

```bash
sudo pacman -S python-pipx        # Arch Linux
sudo apt install pipx             # Ubuntu/Debian
sudo dnf install pipx             # Fedora
```

Regular pip also works:

```bash
pip install .
myusic
```

The package exposes `myusic` via `project.scripts` and maps it to `mymusic_player:main`.

### Option B: `./install.sh`

Use the included installer script:

```bash
cd /path/to/myusic
chmod +x install.sh
./install.sh
```

The script:

- checks whether `~/.local/bin` exists and creates it if needed,
- copies `mymusic_player.py` to `~/.local/bin/myusic`,
- runs `chmod +x ~/.local/bin/myusic`,
- prints a success message telling you to run `myusic`,
- warns you if `~/.local/bin` is not in your `PATH`.

If `~/.local/bin` is not in your `PATH`, add this to your shell startup file (`~/.bashrc`, `~/.zshrc`, `~/.profile`, etc.):

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Then reload your shell:

```bash
source ~/.bashrc   # or ~/.zshrc / ~/.profile
```

### Option C: Manual fallback

```bash
cd /path/to/myusic
chmod +x mymusic_player.py
sudo cp mymusic_player.py /usr/local/bin/myusic
sudo chmod +x /usr/local/bin/myusic
myusic
```

Or run directly from the repository:

```bash
./mymusic_player.py
```

## `~/Myusic` Folder Behavior

`myusic` uses exactly one music folder: `~/Myusic`.

When the app starts:

- if `~/Myusic` exists, it is scanned for supported audio files;
- if it does not exist, it is created automatically and a notification is shown;
- only supported audio files are listed:
  - `.mp3`
  - `.flac`
  - `.ogg`
  - `.wav`
  - `.m4a`
- validation is intentionally minimal and never deep-decodes files during scanning;
- actual decoding is handled by the external backend at playback time;
- use `:add <path>` to copy external audio files into `~/Myusic`;
- use `:rescan` or `:r` after adding/removing files manually.

Example:

```bash
mkdir -p ~/Myusic
cp ~/Downloads/example.mp3 ~/Myusic/
myusic
```

Inside the app:

```text
:add ~/Downloads/example.mp3
:rescan
:play 1
```

## Keybindings

### Normal Mode

| Key | Action |
|---|---|
| `j` / `Down Arrow` | Move cursor down |
| `k` / `Up Arrow` | Move cursor up |
| `Enter` | Play selected song |
| `Space` / `c` | Pause / resume playback |
| `v` | Stop playback |
| `b` | Next song |
| `z` | Previous song |
| `r` | Quick rescan |
| `h` | Show help screen |
| `q` | Quit |

## Command Mode

Press `:` to enter command mode. Commands are entered at the bottom prompt.

| Command | Action |
|---|---|
| `:add <path>` | Copy a supported audio file into `~/Myusic` |
| `:play <number>` | Play the song with the given list number |
| `:rescan` | Rescan `~/Myusic` |
| `:r` | Shortcut for `:rescan` |
| `:help` | Show the help screen |
| `:quit` | Quit |
| `:q` | Shortcut for `:quit` |

Examples:

```text
:add /tmp/demo.mp3
:play 2
:rescan
:quit
```

## Transparent Background / Wallpaper

`myusic` initializes curses with default colors:

```python
curses.start_color()
curses.use_default_colors()
curses.init_pair(1, curses.COLOR_WHITE, -1)
```

The `-1` background argument tells curses to inherit the terminal's default background instead of painting an opaque block. On transparent/translucent terminals, your wallpaper stays visible behind the TUI. If the terminal does not support default colors, the app falls back gracefully.

## Audio Backend Notes

Playback is handled by an external player subprocess, not by Python-level decoders.

- stdout/stderr/stdin are redirected to `/dev/null`
- stopping uses `SIGTERM`, then `SIGKILL` as a fallback
- pause/resume uses POSIX `SIGSTOP` / `SIGCONT`
- when a track ends, `myusic` advances to the next valid track automatically

## Troubleshooting

### No audio backend found

Install `mpv` or `ffmpeg`:

```bash
sudo pacman -S mpv ffmpeg
```

### Song duration shows `00:00`

Install `ffprobe` via `ffmpeg`:

```bash
sudo pacman -S ffmpeg
```

### File shows `[invalid]`

The app only flags files when they are empty, missing, or have an unsupported extension. Actual decode errors are reported at playback time by the external backend instead of breaking the TUI.

### `myusic` command not found

Make sure `~/.local/bin` or `/usr/local/bin` is on your `PATH`:

```bash
echo "$PATH"
which myusic
```

## License

See [LICENSE](LICENSE).
