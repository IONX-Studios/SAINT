# Halo OS changelog

## 2026-11-02 — build 7 features (`halo/arch-rebase`)

- **SAINT Task Manager** (`saint-taskmgr`, Ctrl+Shift+Esc, menu: System): Processes (search, sort, end task /
  end now, warning before ending a process the desktop or system needs), Performance (CPU model, speed,
  temperature; memory and swap; each GPU through nvidia-smi or the kernel's DRM files; each disk's activity;
  each network adapter's throughput), App history (CPU time since each app started), Startup apps
  (enable/disable for your user), Users, Services (systemd; system services ask for the password), System
  information (OS, kernel, board, firmware, uptime, battery health, disk SMART health when readable).
  Runs as its own small process, refreshes only the visible page.
- **Halo widget** (`org.saintos.halo`): a movable, resizable animated ring for the desktop; opacity,
  thickness, speed, animation on/off (also still when Plasma's animations are off); follows SAINT's state
  when SAINT runs; click to talk.
- **SAINT Music widget** (`org.saintos.music`): any MPRIS player (Spotify, Firefox, VLC…) through playerctl:
  artwork, track, artist, album, progress with seeking, volume, play/pause/previous/next. On the default
  desktop under the SAINT widget.
- **Halo Dark (default) and Halo Light**: Plasma colour schemes generated from SAINT's own palette
  (`packaging/linux/branding/make_color_schemes.py`), a light/dark look-and-feel pair wired to Plasma's
  light/dark switch, and SAINT following the desktop theme on Linux.
- ISO validation checks the shortcut, launcher, widgets and themes.


## 2026-11-02 — audit and Priority 1 repairs (`halo/arch-rebase`)

Voice
- Kokoro-82M (onnxruntime) model files ship in the `saint` package with pinned SHA-256; the package build
  fails unless Kokoro synthesises speech from the packaged files. First boot speaks with Kokoro, not espeak-ng.
- New `core/voice_check.py`: the engine actually speaking, model path, runtime, audio output, microphone,
  Whisper model, last test results. Halo → Voice shows it and has **Test Kokoro voice** (synthesise + play)
  and **Test Whisper** (record 4 s + transcribe). A result is "passed" only when that happened.

Store and installs
- Live search as you type (debounced), clearing the box restores the picks at once, older results can't
  overwrite newer ones, filters only re-filter real search results.
- Every failed install step says why (out of space, network, unknown app, password prompt cancelled,
  pacman busy, permission, signature, conflict) in the log, the Store, Easy Install and the notification.
- Live ISO write overlay raised from archiso's 256 MB default to 50 % of RAM (`cow_spacesize`), which is
  what made Flatpak installs such as Heroic fail in the live session.

Desktop
- Halo on Wayland: one click-through window per screen paints only the edge bands, so it can't end up as a
  cross in the middle when KWin ignores client positions.
- SAINT's action notifications are placed through the `saint-shell` KWin script on Wayland (bottom centre),
  instead of `move()`, which Wayland ignores.
- Alt+` is released from KWin's "Walk Through Windows of Current Application" in the image
  (`/etc/xdg/kglobalshortcutsrc`), so it opens SAINT's overlay instead of switching windows.

SAINT UI
- Halo section: Back button at the top, SAINT's sidebar folds to icons so Halo's list is the only left
  navigation, Apps no longer jumps to the Store (Easy Install stays, with a link to the full Store).

Storage
- Linux: each mounted filesystem once (Btrfs subvolumes share a device), no ISO/EROFS/RAM filesystems, live
  session labelled "in memory", reserved space in the tooltip, unallocated disk space (lsblk) listed
  separately from free space.

Docs
- `HALOS_STATUS.md`, `HALOS_ROADMAP.md`, `HALOS_TEST_RESULTS.md`, `docs/REMOTE_DESKTOP.md` (Workstream B design).
