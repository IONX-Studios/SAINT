// Halo defaults for each user's Plasma desktop (Plasma runs every update script once per user).
// The SAINT wallpaper; one SAINT icon on the taskbar, next to the apps people use every day; SAINT first
// in the start menu's favourites. (The SAINT Control Center and App Installer are pages of SAINT, not
// separate icons.)
var wallpaper = "file:///usr/share/wallpapers/SAINTOS/";
var pins = ["applications:firefox.desktop", "applications:org.kde.dolphin.desktop",
            "applications:org.kde.konsole.desktop", "applications:saint.desktop"];
var favorites = ["saint.desktop", "firefox.desktop", "org.kde.dolphin.desktop", "org.kde.konsole.desktop",
                 "org.kde.kate.desktop", "systemsettings.desktop"];

var all = desktops();
for (var i = 0; i < all.length; i++) {
    var d = all[i];
    // The SAINT wallpaper that reacts to SAINT's voice (still artwork when SAINT isn't running); the plain
    // image is set too, so choosing "Image" in Plasma's wallpaper settings shows the same artwork.
    d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    d.writeConfig("Image", wallpaper);
    d.wallpaperPlugin = "org.saintos.wallpaper";
    if (i === 0) {                  // the SAINT widget and what's playing, top right of the first screen
        var g = screenGeometry(d.screen);
        d.addWidget("org.saintos.saint", g.width - 400, 48, 360, 130);
        d.addWidget("org.saintos.music", g.width - 400, 196, 360, 140);
    }
}

var ps = panels();
for (var p = 0; p < ps.length; p++) {
    var ws = ps[p].widgets();
    for (var w = 0; w < ws.length; w++) {
        var t = ws[w].type;
        if (t === "org.kde.plasma.kickoff" || t === "org.kde.plasma.kicker") {
            ws[w].currentConfigGroup = ["General"];
            ws[w].writeConfig("icon", "saintos");                 // the SAINT mark on the start button
            ws[w].writeConfig("favorites", favorites.join(","));
            ws[w].writeConfig("favoritesPortedToKAstats", false);   // Kickoff imports the list on first load
        }
        if (t === "org.kde.plasma.icontasks" || t === "org.kde.plasma.taskmanager") {
            ws[w].currentConfigGroup = ["General"];
            ws[w].writeConfig("launchers", pins.join(","));        // exactly these, in this order
        }
    }
}
