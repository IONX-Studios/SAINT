# Halo OS v0.2 — "One SAINT" plan (formerly SAINTOS)

Status: **draft, waiting for approval.** Nothing below has been built yet.

Decisions made (2026-10-30):
- **Name: Halo** (the OS). SAINT stays the assistant's name.
- **Base: rebase on Arch Linux** (real AUR via `paru`), replacing Fedora/KIWI — new Phase 0 below.
- **Phones: iPhone and Android.** **Remote: TeamViewer integration plus Plasma's built-in RDP (KRdp).**
- **Settings: SAINT Settings as the front door, KDE modules embedded for the rest.**

Remaining open questions are in §14 (marked ❓).

Base today: SAINTOS 0.1.0 = Fedora 44 KDE Plasma 6 (Wayland), built with KIWI, SAINT in `/opt/saint`, plus
two separate helper apps (`saintos-control`, `saintos-apps`). Build 4 boots in a UEFI VM; see
`docs/SAINTOS_CURRENT_STATE.md`.

Working-tree note: `packaging/linux/image/build_iso.sh`, `validate_iso.sh` and the new
`grub-saintos.cfg.iso-template` have uncommitted changes from the last session. They get committed (or
reviewed with you) before any work in this plan starts.

---

## 0. Goals in one paragraph

One SAINT. One icon on the taskbar, one app that is assistant + app store + settings + phone hub + "evolve
the OS with AI". SAINT is visible everywhere it should be (start menu, wallpaper, widgets, boot, sounds,
animations) and invisible where it shouldn't be (no duplicate tray/taskbar logos). Installing apps actually
works, shows what it's doing, and tells you what landed. The Halo sits where it belongs and `Alt+\`` opens it.

---

## 1. Root causes already found in the code (the "why it's broken" list)

| Symptom you reported | Cause found | File |
|---|---|---|
| Too many SAINT logos on the taskbar/tray | The Plasma defaults script pins **three** launchers with SAINT/SAINTOS icons (`saint.desktop`, `saintos-control.desktop`, `saintos-apps.desktop`), the start button also uses the SAINTOS mark, and SAINT adds its own tray icon. Running SAINT adds a 4th/5th. | `rootfs/usr/share/plasma/shells/.../saintos-defaults.js`, `ui/main_window.py:_build_tray` |
| No default apps (Firefox etc.) pinned | Same script only *appends* SAINT launchers; it never writes a default set | `saintos-defaults.js` |
| `Alt+\`` does nothing | `GlobalHotkey.register()` returns immediately when `sys.platform != "win32"` — the hotkey is Windows-only (Win32 `RegisterHotKey`). On Plasma/Wayland apps can't grab keys at all; it has to go through KGlobalAccel or the XDG GlobalShortcuts portal | `ui/win.py:85` |
| Halo is in the middle of the screen | The Halo positions itself with `QWidget.move()` at the top-centre edge. **Wayland ignores client-side window positions**, so KWin centres it. Needs the layer-shell protocol (LayerShellQt) to anchor to an edge | `ui/halo.py:367`, `ui/placement.py` |
| BG is "just the circle" | Wallpaper generator draws only the Halo ring + a small "SAINTOS" caption at the bottom-left | `packaging/linux/branding/make_branding.py:wallpaper` |
| Installer doesn't say what it's doing | The log pane only fills from subprocess stdout; `pkexec`, `flatpak --noninteractive` and npm print little or buffer, so it looks frozen. No per-app progress, no notification | `ui/saintos_center.py:do_install`, `core/saintos_apps.py:run_plan` |
| Can't see apps after install | Likely (to verify in VM): the Plasma menu cache isn't rebuilt (`kbuildsycoca6`) after a `flatpak --user` install, and in the **live session** installs go to a RAM overlay that vanishes on reboot | same |
| Installer doesn't install / Control Center can't install | To reproduce in the VM first. Suspects: `pkexec` with no polkit agent visible, `dnf` lock held by PackageKit/Discover, live-session RAM overlay full, Flathub remote not reachable, catalog has no Roblox/Blender entries at all | same |
| Partitions are weird | Not yet diagnosed — Fedora 44's Anaconda (web UI) is used as-is. ❓ See §14 Q6 | `saintos.xml`, Anaconda |

---

## 2. Name and identity — **Halo**

The OS is **Halo**; the assistant is **SAINT**; the ring mark already in the branding is the Halo, so the
name and the logo now match. Rest of this document says "Halo OS" where "SaintOS/SAINTOS" was meant.

Note: "Halo" is a Microsoft trademark for its game franchise. For a personal/hobby OS that's fine; if you
ever distribute it publicly, a qualified name ("Halo OS", "SAINT Halo") lowers the risk. ❓ §14 Q1.

Rename scope (done as part of the Arch rebase, since the image is rebuilt anyway):
- `os-release`: `NAME="Halo"`, `PRETTY_NAME="Halo 0.2"`, `ID=halo`, `ID_LIKE=arch`.
- Boot menu, Plymouth, installer branding, ISO label `HALO_2026MM` (archiso label rules: ≤11 chars, no
  spaces), hostname default `halo`.
- Every user-visible "SAINTOS" in `ui/`, `.desktop` files, docs.
- Internal identifiers (`saintos_*` Python modules, `/usr/libexec/saintos`) are renamed to `halo` **only where
  the rebase already rewrites the file**; SAINT's Python modules keep their names to avoid churn in tests.

---

## 2b. Phase 0 — Rebase on Arch Linux (new, comes first)

Because you chose the real AUR, the base changes from Fedora 44 + KIWI to **Arch Linux + archiso**. Most
of SAINT is Python/Qt and moves over unchanged; the OS layer is rebuilt.

| Fedora piece today | Arch replacement |
|---|---|
| KIWI + `saintos.xml` + `build_iso.sh` | **archiso** profile `packaging/arch/archiso/` (`profiledef.sh`, `packages.x86_64`, `airootfs/`, `pacman.conf`) built with `mkarchiso` |
| dnf / rpm | pacman + **paru** (AUR helper) preinstalled |
| Our own built packages | A **Halo pacman repo** (`[halo]`, signed with our key) holding `saint`, `halo-branding`, `halo-settings`, `halo-kwin-effects`, `halo-wallpaper`, and prebuilt AUR packages we depend on |
| SAINT venv built offline at image time | `saint` PKGBUILD: Python 3 venv in `/opt/saint` from the existing `saint-runtime.txt` lock (Arch's Python version differs — lock re-resolved and tested) |
| Anaconda installer | **Calamares** (KDE-native, used by EndeavourOS/Manjaro) with Halo branding, Btrfs + snapper preset, "install alongside Windows" — this also replaces §12 |
| Fedora shim + signed GRUB (Secure Boot worked out of the box) | **Arch has no Microsoft-signed bootloader.** Options: (a) `shim-signed` (AUR) + MOK enrolment on first boot, (b) `sbctl` with your own keys, (c) turn Secure Boot off. This laptop has Secure Boot **on** and BitLocker waiting for activation → (a) is the safest; see §14 Q11 |
| Plymouth `saintos` theme | Same theme, Arch `plymouth` package + mkinitcpio hook |
| systemd units, boot-report service, VM harness (`boot_test.ps1`, serial markers) | Kept; paths adjusted |
| GRUB | `systemd-boot` for UEFI ISO + GRUB (for BIOS and for "boot into another OS" + grub-btrfs snapshots) on installed systems |
| `validate_iso.sh` (Fedora checks) | Rewritten for archiso layout (52 checks → equivalent set) |
| Build VM `SAINTOS-build` (Fedora live) | Arch build VM (or an `archlinux` container in the same VM via podman) |
| CI workflow | `archlinux:latest` container running `mkarchiso` |

Rolling-release reality: Arch updates continuously. Halo pins a **dated snapshot** of the Arch repos
(Arch Linux Archive) per release so a given ISO is reproducible, and Halo updates move the snapshot date
forward after the VM tests pass.

**Phase 0 done when:** Halo ISO built with `mkarchiso`, validates, boots UEFI + BIOS in VirtualBox to Plasma 6
Wayland, `SAINT-OK` boot marker passes, `paru -S <aur pkg>` works, Calamares installs to a virtual disk and the
installed system reboots into Halo. The Fedora pipeline stays in git (tagged `saintos-fedora-0.1.0`) until then.

---

## 3. Phase 1 — Fix what's broken (P0, small, ships first)

### 3.1 One SAINT icon
- Pin set in `saintos-defaults.js` becomes an explicit, ordered list (not append):
  **Firefox · Dolphin · Konsole · SAINT · (System Settings or SAINT Settings)**. ❓ final list §14 Q5.
- `saintos-control.desktop` and `saintos-apps.desktop` become `NoDisplay=true` aliases that open SAINT on
  the right page (`saint --page store`), so old shortcuts keep working but never show as separate apps.
- SAINT's `StartupWMClass=SAINT` matched by every SAINT window (main, overlay, Halo, settings) so the
  running app groups under the single pinned icon instead of adding new ones.
- Tray icon: on Halo, hidden by default (SAINT already lives on the taskbar pin + Halo); setting to turn
  it back on. Windows build keeps its tray icon.
- Start button keeps the Halo mark (that's the start menu, not a SAINT duplicate) — ❓ confirm.

### 3.2 `Alt+\`` hotkey on Linux
- New `ui/hotkey_linux.py`: register through **KGlobalAccel over D-Bus** (`org.kde.kglobalaccel`) on
  Plasma; fall back to the **XDG GlobalShortcuts portal** on other Wayland desktops; X11 fallback via
  `XGrabKey` only if running under X.
- Also ship the shortcut in the image (`kglobalshortcutsrc` default + `X-KDE-Shortcuts` in `saint.desktop`)
  calling `saint --toggle-overlay`, which talks to the running instance over SAINT's existing single-instance
  channel. This works even if SAINT's own registration fails.
- Settings field keeps working; changing it re-registers through the same backend.
- Tests: parse/translate `alt+\`` → KDE key string; backend selection per platform.

### 3.3 Halo placement on Wayland
- Use **LayerShellQt** (Arch: `layer-shell-qt`) for the Halo, the overlay and the edge tab:
  anchor top-centre, exclusive zone 0, keyboard interactivity on-demand.
- Fallback when layer-shell isn't available: a **KWin window rule** shipped in the image
  (`kwinrulesrc`: wmclass `SAINT`, title `SAINT Halo` → position/above/skip taskbar/skip switcher).
- Windows path untouched.

### 3.4 Installer that tells you what it's doing
- `run_plan` emits structured events (`step_started`, `app_progress`, `app_done`, `app_failed`, `line`)
  instead of only raw lines. Flatpak runs **one app per command** so progress is per app; parse
  flatpak's and pacman's percentage output; AUR builds report build/install phases.
- UI: per-app row with status icon (queued / downloading x% / installing / ✓ / ✗ + reason) and an
  expandable log.
- Before running: detect and explain the real blockers instead of silently failing — pacman lock (`/var/lib/pacman/db.lck`),
  no network, live session ("installs here disappear on reboot — install Halo first"), disk space.
- After running: `kbuildsycoca6` + `update-desktop-database ~/.local/share/applications`, then a desktop
  notification **listing what was installed** (and what failed, with "Show details"), each item clickable to
  launch it. New apps get an "New" badge in the start menu (Plasma does this natively once the cache is
  rebuilt).
- Reproduce the "doesn't install" bug in the VM first (both live session and installed system), fix the
  actual cause, add a regression test.

### 3.5 Ollama installed by default
- Add `ollama` to the image package list, enable `ollama.service` (system) on the installed system.
- First-run: SAINT offers a model (❓ which default — §14 Q7) with size shown; downloads in the background
  with a progress notification. No silent multi-GB download in the live session.
- SAINT's AI provider defaults to Ollama when it answers on `127.0.0.1:11434`.

### 3.6 Wallpaper = SAINT
- New wallpaper set: large **SAINT** wordmark composed with the Halo (not a lone ring), light and dark
  variants, all common resolutions incl. ultrawide, generated by `make_branding.py`.
- (Phase 3 makes it reactive.)

**Phase 1 done when:** fresh VM install shows exactly one SAINT icon on the taskbar, Firefox pinned,
`Alt+\`` toggles the overlay, Halo is at the top edge, installing VS Code + Blender from SAINT shows per-app
progress, both appear in the start menu without logging out, a notification lists them, Ollama answers.

---

## 4. Phase 2 — One SAINT app (merge Control Center + App Installer into SAINT)

SAINT's main window (`ui/main_window.py` + `ui/pages/`) becomes the only app. New/merged pages:

| Page | From | Notes |
|---|---|---|
| Home | SAINT Overview | unchanged |
| **Store** | `saintos-apps` + new | §5 |
| **Evolve** | new | §8 — change the OS with Claude/AI agents |
| **Phone** | SAINT Devices + new | §9 |
| **Settings** | SAINT settings + Control Center pages (Service, AI, Voice, Permissions, Capabilities, Data, Diagnostics, Updates) | §6 |
| **Recovery** | new | §7 |
| Activity, Tasks, Memory, Music, History, Storage, Automations | existing | unchanged |

- `ui/saintos_center.py` page builders move into `ui/pages/` as real page classes (reusing the existing
  sidebar/design system, no second visual style). The standalone `saintos-control` command stays as a thin
  alias: `saint --page settings`.
- `saint --page <name>` and `saint --toggle-overlay` CLI flags, routed to the running instance.
- Start menu: one **SAINT** entry at the top of Favorites, plus a "Halo" category containing *Store*,
  *Evolve*, *Settings*, *Recovery* as `NoDisplay=false` actions of the same app (Plasma shows them as jump-list
  actions on right-click of the single taskbar icon too).
- First-run (currently opens the Control Center) opens SAINT on a Welcome page instead.

---

## 5. Phase 2 — SAINT Store (replaces Discover), backed by the AUR

One search bar over, in this order of preference:
1. **Halo repo** (`[halo]`) — our own signed packages, including prebuilt AUR packages we vetted (fast, no
   compiling).
2. **Arch official repos** (pacman).
3. **AUR** via `paru` — builds from PKGBUILD. Store shows AUR votes, popularity, last-updated, maintainer,
   "out of date" flag, and **always shows the PKGBUILD diff** on first install/update (AUR packages are
   user-submitted; this is where malware has appeared before).
4. **Flathub** — for apps that are better sandboxed or missing elsewhere.

Implementation:
- `core/halo_store.py` replaces `core/saintos_apps.py`'s dnf/flatpak/npm planner: search (AUR RPC
  `aur.archlinux.org/rpc/v5/search`, pacman `-Ss` via `pyalpm`, Flathub appstream), plan, run.
- Privileged pacman work goes through the polkit-guarded `halo-helper` (§7); AUR builds run **as your user**
  in a clean chroot-less build dir (`paru` never runs as root), only the final `pacman -U` is privileged.
- Catalog of "featured" apps (today's `catalog.json`) becomes a curated list pointing at the right source.

Store features:
- Search bar (instant, local index refreshed daily: Halo repo + pacman + AUR + Flathub), categories,
  app pages with screenshots/ratings from AppStream, Install / Open / Update / Remove.
- Queue + per-app progress (shared with §3.4), updates tab (pacman + AUR + Flatpak), "installed" tab.
- SAINT voice: "install Blender" → Store search → confirmation → install (deterministic, no LLM needed for
  exact matches).
- Discover not included in the archiso package list; `appstream://` URL handlers point to SAINT Store.

Required apps and how each installs:

| App | Source | Notes |
|---|---|---|
| Firefox | preinstalled (Arch `firefox`) | pinned |
| VS Code | AUR `visual-studio-code-bin` (Microsoft's build) — default; Arch `code` (open-source build) as alternative | the `-bin` one has the Microsoft marketplace and Copilot/Claude extensions |
| Blender | Arch `blender` (official repo) | |
| Roblox (player) | **Sober** (Flathub `org.vinegarhq.Sober`) | Third-party, runs Roblox's Android client; not endorsed by Roblox; can break when Roblox updates; Hyperion anti-cheat blocks the Windows client under Wine |
| Roblox Studio | **Vinegar** (Flathub `org.vinegarhq.Vinegar`) — Studio under Wine | Works for most people; GPU/driver dependent; also can break on Studio updates. Labelled "community" in the Store |
| Ollama | preinstalled: Arch `ollama` / `ollama-cuda` / `ollama-rocm` by GPU (§3.5) | |
| TeamViewer | AUR `teamviewer` | §10 |
| Everything already in `catalog.json` | migrated into the new sources | |

---

## 6. Phase 2 — SAINT Settings (decided: front door + embedded KDE modules)

Replacing KDE System Settings entirely means re-implementing ~80 control modules (displays, Bluetooth,
printers, users, …). Plan instead:

- **SAINT Settings is the front door**: its own designed pages for what people actually change — Wi-Fi,
  Bluetooth, Display (resolution/scale/night light), Sound, Wallpaper & Theme, Notifications, Power,
  Users, Default apps, Keyboard shortcuts, Updates, SAINT/AI, Privacy, Phone, Recovery.
- Simple pages use the same D-Bus/CLI backends Plasma uses (NetworkManager, BlueZ, PipeWire/wpctl,
  `kscreen-doctor`, `plasma-apply-*`).
- Everything else opens the matching KDE module **inside** SAINT Settings (`kcmshell6`/QML KCM embedding),
  so nothing is lost.
- System Settings is hidden from menus (still runnable), and its launcher/`systemsettings://` links route to
  SAINT Settings. Search covers both.

---

## 7. Phase 2 — Recovery (UEFI and dual boot)

In SAINT → Recovery:
- **Restart into UEFI firmware setup**: `systemctl reboot --firmware-setup` (checks `OsIndicationsSupported`
  first; button disabled on BIOS machines with the reason).
- **Restart into another OS once** (dual boot): list GRUB entries (`grub.cfg` menuentries, `os-prober`
  enabled) and EFI boot entries (`efibootmgr`); "Restart into Windows" uses `grub-reboot <entry>`
  (or `efibootmgr --bootnext`). Warns when **BitLocker** is on (this laptop: waiting
  for activation) that firmware-level changes may ask for the recovery key.
- **Snapshots / roll back**: Calamares preset installs on Btrfs; `snapper` + `snap-pac` (snapshot around every
  pacman transaction) + `grub-btrfs` so each Store install
  and each Evolve change makes a snapshot, bootable from the GRUB menu ("Halo — before 'Add star
  animations', 30 Oct 14:02").
- Reset SAINT settings, reset desktop layout, reinstall SAINT, safe graphics mode toggle, run diagnostics.
- All privileged actions go through one polkit-protected helper (`saintos-helper`, D-Bus system service,
  fixed action list) — no free-form root commands.

---

## 8. Phase 4 — Evolve: change Halo with Claude or other AI agents

What it is: a page in SAINT where you describe a change ("make window close animations slower", "add a
widget that shows my GPU temp", "when I plug in my headphones, pause Spotify") and an agent implements it.

Design:
- **Agents**: Claude Code (preferred; installed from the Store/npm), plus pluggable others (Codex CLI, Gemini
  CLI, local via Ollama + an open agent). Agent runs as **your user**, in a workspace
  `~/.local/share/saintos/evolve/<change-id>/`, never as root.
- **What it may change** — "extension points" with a clear apply method each:

  | Extension point | Lives in | Applies |
  |---|---|---|
  | SAINT skills/automations/modules | `~/.local/share/saint/extensions/` | live (hot reload) |
  | Plasma widgets (QML) | `~/.local/share/plasma/plasmoids/` | live (`plasmashell` reload of the widget) |
  | KWin scripts/effects (animations) | `~/.local/share/kwin/` | live (`qdbus org.kde.KWin /KWin reconfigure`) |
  | Themes, colors, sounds, wallpapers | `~/.local/share/…` | live |
  | Settings (`kwriteconfig6`) | `~/.config/` | live or re-login |
  | System-level (packages, services, `/etc`) | via `saintos-helper` only | after restart, with snapshot |

- **Flow**: describe → agent proposes a plan → SAINT shows the **diff and a risk label** → you approve →
  snapshot (Btrfs for system-level, git for user-level) → apply live or schedule for next restart →
  "Keep / Undo" for 2 minutes (auto-undo if the desktop crashes or you don't confirm) → change appears in a
  history list with one-click revert.
- Each change is a git commit in `~/.local/share/saintos/evolve/` so you can share or re-apply it, and the
  good ones can become recipes (§5).
- Hard limits: no network calls from generated code without a declared permission; no edits outside the
  extension points; system-level changes always need the polkit prompt.
- Voice: "SAINT, evolve: …" opens the same flow.

---

## 9. Phase 3 — Phone link (Android + iPhone), like Phone Link on Windows (decided: both)

| Feature | Android | iPhone |
|---|---|---|
| Notifications on the PC | **KDE Connect** (mature, Arch `kdeconnect`) | Bluetooth **ANCS** (what Windows Phone Link uses) via BlueZ — experimental on Linux; plus SAINT Mobile app (exists in `mobile/ios`) for SAINT's own notifications |
| Read & send SMS | KDE Connect (full) | Bluetooth **MAP** via BlueZ `obexd` — send/receive SMS/iMessage-as-SMS limits apply; iMessage threads are not available to any non-Apple PC software |
| Calls | KDE Connect (notify/mute) | HFP (call audio through PC) — later |
| Files, clipboard, media control | KDE Connect | SAINT Link (exists in `modules/link`) |
| "SAINT, text Mom I'm running late" | KDE Connect D-Bus `sendSms` | MAP `PushMessage` |

- SAINT → Phone page: pair (QR / Bluetooth), notification mirror, conversations, send box, "allow SAINT to
  send texts" policy.
- **Automatic texting safety**: by default SAINT reads back the message and recipient and waits for
  "send"; you can whitelist contacts/phrases for no-confirm sending. Every sent text is logged in History.
- Incoming notifications become SAINT notifications and can be read out ("you got a text from …").

---

## 10. Phase 3 — Remote desktop from your phone (decided: TeamViewer + built-in RDP)

- **TeamViewer**: preinstallable from AUR `teamviewer` (official Linux build repackaged) (TeamViewer supports Plasma Wayland via the
  portal/PipeWire screencast in recent versions; unattended access needs their "Easy Access" + an account).
  SAINT integration: "SAINT, let me in from my phone" → start TeamViewer service, show the ID, optionally
  notify the phone through SAINT Link.
- Built-in alternative with no account: **KRdp** (Plasma 6's own RDP server) — SAINT toggles it and shows
  the address; works with any RDP app on iPhone/Android (e.g. Microsoft Remote Desktop / Windows App) on the
  local network or over Tailscale.
- Remote session indicator: the Halo turns a distinct color while someone is viewing the desktop.

---

## 11. Phase 3 — Look & feel

### 11.1 Apple emojis  ❓ (§14 Q8)
Apple Color Emoji is Apple's proprietary font; it **can't be shipped in the ISO**. Options:
- Ship **Noto Color Emoji** or **Twemoji** (free) as default — Halo stays redistributable.
- Optional Store item "Use Apple-style emoji" that installs a community Apple-style font
  (e.g. `apple-emoji-linux` builds) to your user only, with a licence note. You decide.
- Fontconfig rule makes the chosen emoji font win everywhere (Qt, GTK, Firefox, Electron).

### 11.2 SAINT widgets (Plasma 6 plasmoids, QML)
- **SAINT** — Halo state, listen button, quick ask box, last answer.
- **Now playing** — Spotify/MPRIS with SAINT controls.
- **Phone** — battery, last notifications, quick reply.
- **System** — CPU/GPU/RAM/temps (game-mode aware).
- **Tasks & reminders** — from SAINT memory.
- All talk to SAINT over one local D-Bus interface `org.saintos.Saint` (new; also used by the wallpaper,
  hotkey, and Evolve). Default desktop gets SAINT + Now playing.

### 11.3 Reactive wallpaper
- Custom Plasma wallpaper plugin `org.saintos.wallpaper` (QML + shader): the SAINT wordmark + Halo.
- Subscribes to `org.saintos.Saint` signals: **idle** (slow breathing glow), **wake/listening** (Halo brightens,
  ring ripples with mic level), **thinking** (orbiting particles), **speaking** (pulses with TTS amplitude).
- Low-power: static when on battery saver, fullscreen game, or "reduce motion".

### 11.4 Window & virtual-desktop animations (KWin effects)
- **"Star" open/close**: windows open by expanding from a 4/8-point star burst and close by contracting into
  a star that flares and fades (KWin JS/QML effect + small GLSL shader).
- Minimize into the taskbar icon with a star trail; maximize with a short spring.
- Virtual desktops: a "warp" slide with Halo-coloured edge glow; overview with SAINT accent.
- All toggleable in SAINT Settings → Animations, speed slider, honours reduce-motion.

### 11.5 SAINT's own animations and sounds
- SAINT window/overlay/Halo **expand like a star on open and contract into a star on close** (Qt
  `QPropertyAnimation` + painter path, same motion language as the KWin effect, `ui/motion.py`).
- Sound theme `saintos` (freedesktop sound theme spec): login, logout, notification, SAINT wake, SAINT done,
  error, plug/unplug, screenshot, star-open/close. Original sounds (synthesised, so we own them).

---

## 12. Phase 0 — OS installer & disk partitioning (Calamares, comes with the Arch rebase)
- Anaconda goes away with Fedora; **Calamares** replaces it (Halo branding, slideshow showing SAINT features).
- Partition preset: GPT, EFI 1 GB (room for several kernels + Windows' loader), Btrfs `@`, `@home`,
  `@snapshots`, `@var_log`, zram swap. Snapshots ready for §7.
- "Install alongside Windows" with a size slider; refuses to shrink a BitLocker-encrypted partition and tells
  you to suspend BitLocker first; warns about Fast Startup. (This laptop: C: has 579 GB free, no unallocated
  space, BitLocker waiting for activation.)
- ❓ §14 Q6: what looked weird in the current partition screen, so the preset avoids it.

---

## 13. Delivery order, testing, and how I'll verify each phase

| Phase | Content | Size |
|---|---|---|
| **0** | §2, §2b, §12: Arch rebase, archiso, Halo repo + `saint` PKGBUILD, paru, Calamares, Secure Boot path, rename to Halo, VM boot + install test | largest single phase (several sessions) |
| **1** | §3: one icon, Firefox pins, hotkey, Halo position, installer progress + notification + visibility, Ollama, SAINT wallpaper, rename | ~1–2 sessions |
| **2** | §4–7, §12: one SAINT app, Store (+ recipes, Roblox/Studio/VS Code/Blender), Settings, Recovery, partition preset | largest |
| **3** | §9–11: phone link, remote desktop, emoji, widgets, reactive wallpaper, animations, sounds | large |
| **4** | §8: Evolve (AI agents changing the OS) — after snapshots (§7) exist, since it depends on them | medium |

Per phase:
- Unit tests in `tests/` for every new deterministic piece (Store planner, recipe verification, hotkey
  translation, boot-entry parsing, D-Bus interface). Full suite on Windows (no regressions) and Linux (build VM).
- Build an ISO in the `SAINTOS-build` VM → `validate_iso.sh` (new checks: Discover absent, Ollama present,
  one pinned SAINT, layer-shell installed) → `boot_test.ps1` UEFI + BIOS → `gui_check.sh` screenshots of
  taskbar, Halo, overlay after `Alt+\``, Store install of Blender.
- Install-to-disk test (`install_test.sh`) for Recovery, snapshots, Ollama service.
- Things that need real hardware and you: Bluetooth phone pairing, iPhone ANCS/MAP, TeamViewer account,
  restart-into-Windows on this laptop.
- Commits on a new branch `saintos/v0.2-one-saint`, one commit per item; nothing pushed without asking.

---

## 14. Open questions

Answered 2026-10-30: name = Halo · base = Arch (real AUR) · phones = iPhone + Android · remote = TeamViewer +
built-in RDP · settings = front door + embedded KDE.

1. **Name form**: plain "Halo", or "Halo OS" / "SAINT Halo" (lower trademark risk if ever shared publicly)?
5. **Taskbar pins**: Firefox · Dolphin · Konsole · SAINT — anything else (Steam, VS Code, Spotify)? Keep the
   Halo mark on the start button? Tray icon hidden by default?
6. **Partitions**: what exactly looked weird (installer screen, resulting layout, dual-boot with Windows)?
   A screenshot or description helps. Keep Anaconda or switch to Calamares?
7. **Ollama default model**: none (you pick), small (e.g. a ~2–4 GB model), or a bigger one? This laptop has
   an i7-13700HX and 32 GB RAM — which GPU?
8. **Apple emoji**: ship free Noto/Twemoji by default with an optional Apple-style download, OK?
9. **Evolve agents**: Claude Code via your own Anthropic account/API key — fine? Allow system-level changes
   (with snapshot + password prompt), or user-level only?
10. **Order**: Phase 0 (Arch rebase) first, or Phase 1 fixes in SAINT itself first (hotkey, Halo position,
    single icon, installer progress — most of that is base-independent and quick) while the Arch image is
    built after?
11. **Secure Boot on Arch**: shim + one-time MOK enrolment screen on first boot (recommended), your own
    keys via `sbctl`, or turn Secure Boot off?
12. **Fedora pipeline**: keep it in git tagged `saintos-fedora-0.1.0` and delete from the main branch once
    Halo boots (recommended), or keep both builds maintained?
