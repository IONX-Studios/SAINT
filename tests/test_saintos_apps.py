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
    steps = sa.plan(["steam", "claude-code", "discord", "dev-tools", "steam"], catalog, home="/home/me")
    argvs = [s.argv for s in steps]
    dnf = [a for a in argvs if a[:2] == ["pkexec", "dnf"]]
    assert len(dnf) == 1 and "nodejs" in dnf[0] and "git" in dnf[0] and dnf[0].count("nodejs") == 1
    assert ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", sa.FLATHUB] in argvs
    flat = next(a for a in argvs if a[:2] == ["flatpak", "install"])
    assert "--user" in flat and flat[-2:] == ["com.valvesoftware.Steam", "com.discordapp.Discord"]
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
    steps = sa.plan(["claude-code"], catalog)
    assert sa.missing_tools(steps, which=lambda t: f"/usr/bin/{t}" if t in ("pkexec", "dnf") else None) == []
    assert sa.missing_tools(sa.plan(["steam"], catalog), which=lambda t: None) == ["flatpak"]
