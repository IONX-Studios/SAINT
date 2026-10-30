"""App tools (Halo): "install Blender", "install Roblox Studio", "find me a video editor". Installs go through
the Store (core/store.py): the best installable match, the same install steps and the same notification
listing what was installed. High permission: SAINT says what it will install and from where, and waits."""

import threading

from modules.automation.tools import P, PermissionLevel, Tool, ToolError


def _available():
    import shutil
    return (True, "") if shutil.which("pacman") else (False, "The Store is part of Halo")


def _best(query: str):
    from core import store
    results = [r for r in store.search(query, sources=("featured", "repo", "flathub", "aur"))
               if not r.extra.get("blocked")]
    if not results:
        from core import winapps
        results = [r for r in winapps.search(query) if not r.extra.get("blocked")]
    if not results:
        raise ToolError(f"I couldn't find an app called “{query}”.")
    return results[0]


def install_app(name: str):
    from core import saintos_apps as sa
    from core import store
    r = _best(name)
    steps = store.plan(r)
    errors, _warnings = sa.blockers()
    if errors:
        raise ToolError(" ".join(errors))

    def work():
        try:
            catalog = sa.load_catalog()
        except Exception:
            catalog = {}
        result = sa.run_plan(steps, on_line=lambda _t: None)
        sa.finish(result or {r.id: False}, catalog, names={r.id: r.name})
    threading.Thread(target=work, daemon=True, name="app-install").start()
    return {"summary": f"Installing {r.name} ({r.label}). I'll let you know when it's done.",
            "app": r.name, "source": r.source}


def search_apps(query: str):
    from core import store
    results = store.search(query, sources=("featured", "repo", "flathub", "aur"))[:5]
    if not results:
        return {"summary": f"Nothing found for “{query}”."}
    return {"summary": "; ".join(f"{r.name} ({r.label})" for r in results),
            "results": [{"name": r.name, "source": r.source, "id": r.id, "about": r.summary} for r in results]}


def register_app_tools(registry):
    tools = [
        Tool("apps.install", "Install an app on Halo (Arch, Flathub, the AUR, or a Windows app with Wine)", {},
             PermissionLevel.HIGH, install_app, parameters={"name": P("string", "the app's name")},
             llm_exposed=True, category="apps"),
        Tool("apps.search", "Find apps in the Store", {}, PermissionLevel.LOW, search_apps,
             parameters={"query": P("string", "what to look for")}, llm_exposed=True, category="apps"),
    ]
    for t in tools:
        t.availability = _available
        registry.register(t)
