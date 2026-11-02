# Halo OS changelog

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
