# MCBSAdmin (MineCraft Bedrock Server Administrator)

A portable, terminal-based **Minecraft Bedrock** server manager with a curses TUI.

> **Running Java Edition instead?** See **MCSAdmin**, the sibling project that
> manages a Minecraft **Java** server (`server.jar`, with a managed JRE):
> [Here!](<https://github.com/Polaricito/MCSAdmin>)

MCBSAdmin installs the official **Bedrock Dedicated Server** (BDS) Linux build,
boots and manages it, watches CPU/RAM usage, tracks who is connected, and lets
you run console commands — all from one adaptive TUI window. It has **zero
third-party Python dependencies** (only the standard library), so it runs on
essentially any Linux box and installs cleanly from a git checkout.

## Features

- **Install the latest Bedrock build** — resolves the newest stable Linux
  build from Mojang's official download API (falling back to the community
  Bedrock-OSS registry) and unpacks it in place. No version selector: Bedrock
  ships one current build, so `install` always fetches the latest.
- **In-place updates** — a new build is unpacked over the existing server
  directory without renaming anything: your world, `server.properties`,
  allowlist and permissions are preserved and only binaries/packs are replaced.
- **Native, no Java** — BDS is a native `bedrock_server` binary; no JVM, no
  jar, no RCON. `LD_LIBRARY_PATH` is set so its shared libraries resolve.
- **Adaptive TUI** — the layout rearranges itself to the terminal size
  (console + side-by-side panels on wide terminals, stacked on narrow ones).
- **Distinct color tones** — lighter shade for the server console, darker
  shade for the player list, accent colors for status and resources.
- **Live monitoring** — system and per-process CPU (from `/proc/stat`) and
  memory (`/proc/meminfo`, `VmRSS`) with zero external tools, drawn as fill
  bars. The server's RAM is shown against the system's total (e.g.
  `900 MiB / 15.3 GiB`).
- **Connected players** — join/leave lines from the console update the player
  list instantly; click a player to **kick** or **ban** them.
- **World options** — difficulty, gamemode, max players, view/tick distance,
  allow cheats, online mode, pvp and more, all editable from the TUI.
- **Allowlist editor** — add/remove names and flip `allow-list` on or off
  without losing the entries.
- **Console commands** — type straight into the server console, or use local
  commands such as `/stop`, `/restart`, `/players`, `/help`.

## Requirements

- Linux (reads `/proc`)
- Python 3.8+
- A terminal that supports 256 colors (`TERM=xterm-256color` etc.)

## Dependencies

- Python 3.8+ (only the standard library is used — no third-party packages)
- A terminal emulator that supports 256 colors and curses (GNOME Terminal,
  Konsole, kitty, Alacritty, tmux/screen, or a plain Linux TTY)
- No third-party Python packages; only the standard library (`curses`,
  `subprocess`, `threading`, `json`, …)

## Install

```sh
# run without installing, from a checkout:
python3 -m mcbsadmin --help

# install as a system command:
python3 -m pip install --user --break-system-packages .
mcbsadmin --help
```

`--break-system-packages` is required on distros that mark pip as externally
managed (Arch, recent Debian/Ubuntu) — it installs into your user profile
instead of the system Python. On those the `mcbsadmin` command lands in
`~/.local/bin`, so make sure it's on your `PATH`:

```sh
# bash / zsh
export PATH="$PATH:$HOME/.local/bin"

# fish
set -U fish_user_paths "$HOME/.local/bin"
```

Arch users: install straight from this repository —

```sh
python3 -m pip install --user --break-system-packages git+https://github.com/Polaricito/MCBSAdmin.git
mcbsadmin --help
```

## Quick start

```sh
mcbsadmin install   # fetch + unpack the latest Bedrock build
mcbsadmin           # launch the TUI
```

Inside the TUI, press `S` to start the server. That's it.

### TUI control

| Key             | Action                                  |
|-----------------|-----------------------------------------|
| `S`             | start the server                        |
| `X` / `R`       | stop / restart the server               |
| `I`             | install the latest Bedrock build        |
| `W`             | world options (difficulty, gamemode, pvp, …) — requires server stopped |
| `E`             | server settings (name / port) — requires server stopped |
| `H`             | show key bindings and local commands    |
| `Q` / `Ctrl-Q`  | quit (stops the server first)           |
| `PgUp` / `PgDn` | scroll the console                      |

All shortcuts are **UPPERCASE** so any lowercase text you type goes straight
to the server console instead of triggering a hotkey.

Any other text + `Enter` is sent straight to the Minecraft server console.
Local control commands start with `/`: `/start`, `/stop`, `/restart`,
`/install`, `/players`, `/help`.

### Resource bars

The resources pane renders each measurable resource as a three-line block —
the fill bar on top, the value underneath, then the label, e.g. for the server
process using 900 MiB of a 4 GiB box:

```
[########--------------]
900 MiB / 4.0 GiB
Server RAM
```

CPU rows show the percentage and RAM rows show live usage as `used / total`
(`900 MiB / 15.3 GiB`), so the server's own RSS and the whole system's memory
are directly comparable. The server RAM bar fills according to the share of
the system's total RAM the server process is using. Uptime is pinned to the
bottom, on the right, with the public IP and game port on the left (see
below).

The player count lives under the `PLAYERS` header (as `PLAYERS [n/m]`), where
`m` comes from `max-players` in `server.properties` (Bedrock's default is
10). When a panel is too short for the full form it degrades to a compact
two-line / single-line layout.

When the server is up, the resources pane pins a network/uptime block to
the bottom, with the machine's **public IP** and the configured game **port**
on the left and the uptime on the right:

```
203.0.113.9                      14:32
19132                           Uptime
```

The IP is resolved once at startup (ipify, falling back to the outbound
interface address) and cached; the port mirrors the `gameport` you set in
server settings.

### Player actions: kick / ban

Clicking a player opens a menu with `Kick player` and `Ban player`. Each
action then asks for an optional reason in a small form with a visible
text field and `[send]` / `[cancel]` buttons — type the reason and hit
**Enter** or click `[send]`; leave it blank to kick/ban without a reason.
There is no IP ban: Bedrock Dedicated Server dropped the `ban-ip` command,
so MCBSAdmin only offers kick and name ban. **Esc** steps back (from the
reason form to the player menu).

### Worlds

`V` (or the `[V] worlds` footer button) opens the **Worlds** selector — the
menu that owns *which world is loaded*, distinct from **World Options**
(`W`, which tunes one world's properties). Each `worlds/` folder is shown as
a card, with `*` marking the currently active one. Clicking a card selects
it; the bottom bar then offers `[switch]`, `[rename]`, `[del]` and
`[done]`, plus `[add]` to create a brand-new world (which becomes active).
The same flows work from the keyboard: **Enter** switches, `a` adds, `r`
renames, `d` deletes (with a confirm), **Esc** back. Renames use
`os.replace` and also rewrite the world's `levelname.txt` display name,
deletes ask for confirmation first, and switching only takes effect with
the server stopped.

### Allowlist

The allowlist editor lives in **World Options** (`W`), as the `allowlist`
row — press **Enter**/**Right arrow** (or click it) to open the current
list, with an `Add` button that prompts for a name and a
**Disable allowlist**/**Enable allowlist** button that flips `allow-list` in
`server.properties` without touching the entries. A click on any listed name
removes it. The row is marked with a trailing `>` while the allowlist is
active (`allow-list: true`) and shows plain `allowlist` without one when it
isn't. ESC steps back to the World Options list.

### Server settings and world options

`E` (or the `[E] settings` footer button) opens the server settings screen
(the server **name**, i.e. the MOTD, and the game **port**, default `19132`),
and `W` opens world options. Both refuse to open
while the server is running with a "Stop server first." notice, and the
`[W] world` button disappears from the bottom bar while it runs. The running
bar is `[X] stop [R] restart [H] help [Q] quit`; when stopped it is
`[S] start [I] install [E] settings [W] world [H] help [Q] quit`.

World options cover:

- difficulty (peaceful/easy/normal/hard), gamemode, max players, view
  distance, tick distance, allow cheats, online mode, pvp, force gamemode,
  default permission level (visitor/member/operator), and LAN visibility.
  Set `online mode` to `false` for an **offline** server — players join
  without an Xbox/Microsoft account — instead of the default `true`. Cycle
  choices with Enter, type numbers for the numeric fields, and reach `done`
  below the list to save. A `default` value leaves that property untouched;
  otherwise the chosen value overrides `server.properties` (on this start
  and every future write). The `allowlist` row opens the allowlist editor
  and carries a trailing `>` while `allow-list` is enabled.

## Command line

```
mcbsadmin                       launch the TUI (default)
mcbsadmin install               fetch + unpack the latest Bedrock build
mcbsadmin status                show install / config status
mcbsadmin --data-dir DIR ...    store config + server data in DIR
mcbsadmin --config FILE ...     use a specific config JSON
```

Bedrock ships one current build, so there is no version picker or
`versions` subcommand — `install` always gets the latest.

## Configuration

Everything lives in a single JSON file, so the whole manager is movable:
`~/.config/mcbsadmin/config.json` (or `$XDG_CONFIG_HOME/mcbsadmin/config.json`,
overridable with `MCBSADMIN_CONFIG`). MCBSAdmin never derives its data
location from the install prefix, so a system install under `/usr/bin` and
`/usr/share` still stores per-user state under the home directory.

To move everything to an explicit, writable location (e.g. when installed
system-wide or running in a restricted environment):

```sh
mcbsadmin --data-dir ~/.local/share/mcbsadmin status
mcbsadmin --data-dir ~/.local/share/mcbsadmin install
```

`MCBSADMIN_DATA_DIR` sets the same base for both the config file and the
default server directory.

```jsonc
{
  "server_dir": "~/.config/mcbsadmin/server",
  "version": "1.26.43.1",
  "world": { "difficulty": "hard", "pvp": "false" },
  "gameport": 19132,
  "gameportv6": 19133,
  "motd": "MCBSAdmin managed server"
}
```

Ports are Bedrock's defaults: `19132` (UDP) for the game port and `19133`
(UDP) for IPv6. `world` holds the World Options overrides from the TUI.

The `bedrock_server` binary, world save, `server.properties`, `allowlist.json`,
`permissions.json` and the install marker (`.mcbsadmin-version`) live in
`server_dir`. Copy/modify the config to move a whole installation between
machines.

## Portability notes

- Uses only the Python standard library; no `pip` deps to conflict.
- `curses` ships with CPython on all POSIX systems (Arch needs no extra
  packages).
- `/proc` parsing degrades gracefully on other OSes.
- Updates unpack in place and never rename folders, so a fresh build keeps
  your world, allowlist and configuration intact.

## Installing from GitHub

The repository is a standard `pyproject.toml` package, so you can install it
straight from a git checkout with no extra steps:

```sh
# latest commit on the default branch
python3 -m pip install --user --break-system-packages git+https://github.com/Polaricito/MCBSAdmin.git

# a specific tag
python3 -m pip install --user --break-system-packages git+https://github.com/Polaricito/MCBSAdmin.git@v1.0.0
```

The `--user --break-system-packages` combo targets distros with an externally
managed Python (Arch, recent Debian/Ubuntu); drop the flag if your system
still allows plain `pip install`. A `--user` install puts the `mcbsadmin`
command in `~/.local/bin` — add it to your `PATH` if the command isn't found:

```sh
# bash / zsh
export PATH="$PATH:$HOME/.local/bin"

# fish
set -U fish_user_paths "$HOME/.local/bin"
```

Or clone and install in editable mode for development:

```sh
git clone git@github.com:Polaricito/MCBSAdmin.git
cd MCBSAdmin
python3 -m pip install --user --break-system-packages -e .
```

## Updating

MCBSAdmin ships as one Python package, so updating is just re-installing the
latest commit from GitHub:

```sh
# re-run the same install command; pip pulls the newest code
python3 -m pip install --user --break-system-packages git+https://github.com/Polaricito/MCBSAdmin.git
```

The `--user` cache is keyed by version, and the git source changes on every
push, so force a fresh fetch + install to be sure you're on the newest code:

```sh
python3 -m pip install --user --break-system-packages \
  --upgrade --force-reinstall \
  git+https://github.com/Polaricito/MCBSAdmin.git
```

If you installed a specific tag, add `@vX.Y.Z` to the URL and drop
`--force-reinstall` for the same version.

Editable installs (development) update with a plain `git pull` inside the
checkout:

```sh
cd MCBSAdmin
git pull
```

Your server data and config (`~/.config/mcbsadmin/`) are untouched by
updates. Check your current version any time with `mcbsadmin --version`.

## Development

```sh
python3 -m unittest discover -s tests -v   # run unit tests
python3 -m py_compile mcbsadmin/*.py
```

## License

MIT — see `LICENSE`. MCBSAdmin is not affiliated with Mojang or Microsoft;
"Minecraft" is a trademark of Mojang Synergies AB.
