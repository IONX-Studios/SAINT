"""SAINTOS App Installer (core/saintos_apps.py): catalog safety and the commands a selection becomes.
Commands are never run here (mocked Popen)."""

import json

import pytest

from core import saintos_apps as sa


@pytest.fixture(scope="module")
def catalog():
    return sa.load_catalog()


def test_catalog_has_the_usual_apps(catalog):
    for i in ("steam", "claude-code", "discord", "spotify", "vscode", "chrome", "ollama"):
        assert i in catalog
    assert catalog["steam"].ref == "com.valvesoftware.Steam" and catalog["steam"].method == "flatpak"
    assert catalog["claude-code"].ref == "@anthropic-ai/claude-code" and catalog["claude-code"].needs == ["nodejs"]
    assert len({a.ref for a in catalog.values()}) == len(catalog)


def test_plan_groups_installs_and_asks_for_admin_once(catalog):
    steps = sa.plan(["steam", "claude-code", "discord", "dev-tools", "steam"], catalog, home="/home/me", manager="dnf")
    argvs = [s.argv for s in steps]
    dnf = [a for a in argvs if a[:2] == ["pkexec", "dnf"]]
    assert len(dnf) == 1 and "nodejs" in dnf[0] and "git" in dnf[0] and dnf[0].count("nodejs") == 1
    assert ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", sa.FLATHUB] in argvs
    flat = [a for a in argvs if a[:2] == ["flatpak", "install"]]          # one per app: progress per app
    assert all("--user" in a for a in flat)
    assert [a[-1] for a in flat] == ["com.valvesoftware.Steam", "com.discordapp.Discord"]
    assert ["npm", "install", "-g", "--prefix", "/home/me/.local", "@anthropic-ai/claude-code"] in argvs
    assert argvs.index(dnf[0]) < argvs.index(argvs[-1])                     # nodejs before npm
    assert sum(s.needs_admin for s in steps) == 1


def test_flatpak_only_selection_needs_no_admin(catalog):
    steps = sa.plan(["steam", "spotify"], catalog)
    assert not any(s.needs_admin for s in steps) and not any(a.argv[0] == "pkexec" for a in steps)


def test_unknown_and_unsafe_entries_are_refused(catalog, tmp_path):
    with pytest.raises(KeyError):
        sa.plan(["steam; rm -rf ~"], catalog)
    bad = {"apps": [{"id": "x", "name": "x", "category": "c", "method": "dnf", "ref": "git;reboot"}]}
    p = tmp_path / "c.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        sa.load_catalog(p)
    bad["apps"][0].update(method="flatpak", ref="--from=https://evil/app")
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        sa.load_catalog(p)


def test_run_plan_reports_per_app_and_keeps_going(catalog):
    class P:
        def __init__(self, argv, **kw):
            self.argv = argv
            self.stdout = iter(["working\n"])

        def wait(self):
            return 1 if self.argv[0] == "npm" else 0
    lines = []
    res = sa.run_plan(sa.plan(["steam", "claude-code"], catalog), on_line=lines.append, popen=P)
    assert res == {"steam": True, "claude-code": False}
    assert any("FAILED" in l for l in lines)


def test_missing_tools_counts_npm_as_coming_from_dnf(catalog):
    steps = sa.plan(["claude-code"], catalog, manager="dnf")
    assert sa.missing_tools(steps, which=lambda t: f"/usr/bin/{t}" if t in ("pkexec", "dnf") else None) == []
    assert sa.missing_tools(sa.plan(["steam"], catalog), which=lambda t: None) == ["flatpak"]


def test_halo_installs_distribution_packages_with_pacman(catalog):
    """Halo is Arch: one pkexec pacman for every distribution package, with the Arch names (npm is
    its own package there)."""
    steps = sa.plan(["claude-code", "dev-tools", "ollama", "steam"], catalog, home="/home/me", manager="pacman")
    argvs = [s.argv for s in steps]
    pac = [a for a in argvs if a[:2] == ["pkexec", "pacman"]]
    assert len(pac) == 1 and pac[0][:5] == ["pkexec", "pacman", "-Syu", "--needed", "--noconfirm"]
    for pkg in ("nodejs", "npm", "github-cli", "python-pip", "ollama"):
        assert pac[0].count(pkg) == 1, pkg
    assert "gh" not in pac[0] and "gcc-c++" not in pac[0]          # Fedora names never reach pacman
    assert not any(a[:2] == ["pkexec", "dnf"] for a in argvs)
    assert steps[0].label.startswith("Arch packages:") and sum(s.needs_admin for s in steps) == 1
    assert sa.missing_tools(steps, which=lambda t: f"/usr/bin/{t}" if t in ("pkexec", "pacman", "flatpak") else None) == []


def test_every_distribution_package_has_an_arch_name(catalog, tmp_path):
    assert all(a.arch.split() for a in catalog.values() if a.method == "dnf")
    bad = {"apps": [{"id": "x", "name": "x", "category": "c", "method": "dnf", "ref": "git"}]}
    p = tmp_path / "c.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        sa.load_catalog(p)
    bad["apps"][0]["arch"] = "git --overwrite=*"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        sa.load_catalog(p)


def test_installed_asks_the_systems_own_package_manager(catalog):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return type("R", (), {"returncode": 0})()
    assert sa.installed(catalog["ollama"], run=run, manager="pacman")
    assert sa.installed(catalog["ollama"], run=run, manager="dnf")
    assert calls == [["pacman", "-Q", "ollama"], ["rpm", "-q", "ollama"]]
    assert sa.system_manager(which=lambda t: "/usr/bin/pacman" if t == "pacman" else None) == "pacman"
    assert sa.system_manager(which=lambda t: None) == "dnf"


def test_progress_is_reported_per_step_with_percentages(catalog):
    class P:
        def __init__(self, argv, **kw):
            self.argv = argv
            self.stdout = iter(["Downloading  10%\n", "Installing 55 %\n", "Installing 100%\n"])

        def wait(self):
            return 0
    events = []
    steps = sa.plan(["steam", "spotify"], catalog, manager="pacman")
    sa.run_plan(steps, on_line=lambda t: None, popen=P, on_step=lambda i, n, s, st, p: events.append((i, n, st, p)))
    assert len(steps) == 3                                    # remote-add + one install per app
    assert events[0] == (0, 3, "started", 0) and (2, 3, "done", 100) in events
    assert [e[3] for e in events if e[0] == 1 and e[2] == "progress"] == [10, 55, 100]


def test_install_blockers_are_said_before_starting():
    errors, warnings = sa.blockers(cmdline="BOOT_IMAGE=/vmlinuz archisobasedir=halo quiet",
                                   exists=lambda p: p == "/var/lib/pacman/db.lck")
    assert errors and "busy" in errors[0]
    assert warnings and "live session" in warnings[0]
    assert sa.blockers(cmdline="root=UUID=x rw", exists=lambda p: False) == ([], [])


def test_finish_refreshes_the_menu_and_lists_what_was_installed(catalog):
    calls = []
    title, body = sa.finish({"steam": True, "vscode": True, "discord": False}, catalog,
                            run=lambda argv, **kw: calls.append(argv), which=lambda t: f"/usr/bin/{t}")
    assert title == "Installed 2, 1 failed"
    assert body.splitlines() == ["✓ Steam", "✓ Visual Studio Code", "✗ Discord"]
    assert calls[0] == ["kbuildsycoca6"]                       # new apps show in the menu without logging out
    assert calls[-1][:3] == ["notify-send", "--app-name=SAINT", "--icon=saint"] and calls[-1][-1] == body


def test_store_favourites_roblox_studio_vscode_blender(catalog):
    assert catalog["roblox"].ref == "org.vinegarhq.Sober" and catalog["roblox"].method == "flatpak"
    assert catalog["roblox-studio"].ref == "org.vinegarhq.Vinegar"
    assert "not from Roblox" in catalog["roblox"].about and "not from Roblox" in catalog["roblox-studio"].about
    assert catalog["vscode"].method == "aur" and catalog["vscode"].ref == "visual-studio-code-bin"
    assert catalog["blender"].method == "flatpak"
    steps = sa.plan(["vscode", "teamviewer"], catalog, manager="pacman")
    aur = [s.argv for s in steps if s.argv[0] == "paru"]
    assert aur == [["paru", "-S", "--needed", "--noconfirm", "--skipreview", "--sudo", "pkexec", "visual-studio-code-bin"],
                   ["paru", "-S", "--needed", "--noconfirm", "--skipreview", "--sudo", "pkexec", "teamviewer"]]
    assert sa.missing_tools(steps, which=lambda t: None) == ["paru"]
