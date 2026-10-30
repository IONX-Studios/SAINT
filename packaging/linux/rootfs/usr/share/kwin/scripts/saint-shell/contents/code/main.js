// SAINT shell (KWin script). On Wayland an app can't place its own windows, so SAINT asks here:
//   a SAINT window whose title contains "SAINT:place:<x>,<y>,<w>,<h>" is put at that geometry (global
//   logical pixels), kept above, and left off the taskbar, pager and Alt+Tab. SAINT changes the title
//   to move it (ui/placement.py: wayland_place).
// The top screen edge opens SAINT's overlay, like hovering the edge on Windows (org.saintos.Saint).
const MARK = "SAINT:place:";

function place(w) {
    if (!w || w.resourceClass !== "saint") {
        return;
    }
    const caption = w.caption || "";
    const at = caption.indexOf(MARK);
    if (at < 0) {
        return;
    }
    const g = caption.slice(at + MARK.length).split(",").map(Number);
    if (g.length !== 4 || g.some(isNaN) || g[2] <= 0 || g[3] <= 0) {
        return;
    }
    w.skipTaskbar = true;
    w.skipPager = true;
    w.skipSwitcher = true;
    w.keepAbove = true;
    w.frameGeometry = { x: g[0], y: g[1], width: g[2], height: g[3] };
}

function watch(w) {
    place(w);
    w.captionChanged.connect(function () { place(w); });
}

workspace.windowList().forEach(watch);
workspace.windowAdded.connect(watch);

registerScreenEdge(KWin.ElectricTop, function () {
    callDBus("org.saintos.Saint", "/Saint", "org.saintos.Saint", "ToggleOverlay");
    return true;
});
