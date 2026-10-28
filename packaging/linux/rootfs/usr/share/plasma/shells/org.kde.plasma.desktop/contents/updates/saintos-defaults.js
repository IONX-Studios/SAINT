// SAINTOS defaults for each user's Plasma desktop (Plasma runs every update script once per user).
// Sets the SAINTOS wallpaper and pins SAINT and the Control Center to the task manager.
var wallpaper = "file:///usr/share/wallpapers/SAINTOS/";
var all = desktops();
for (var i = 0; i < all.length; i++) {
    var d = all[i];
    d.wallpaperPlugin = "org.kde.image";
    d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    d.writeConfig("Image", wallpaper);
}

var ps = panels();
for (var p = 0; p < ps.length; p++) {
    var ws = ps[p].widgets();
    for (var w = 0; w < ws.length; w++) {
        var t = ws[w].type;
        if (t === "org.kde.plasma.icontasks" || t === "org.kde.plasma.taskmanager") {
            ws[w].currentConfigGroup = ["General"];
            var launchers = String(ws[w].readConfig("launchers", ""));
            var add = ["applications:saint.desktop", "applications:saintos-control.desktop"];
            for (var a = 0; a < add.length; a++) {
                if (launchers.indexOf(add[a]) < 0) {
                    launchers = launchers ? launchers + "," + add[a] : add[a];
                }
            }
            ws[w].writeConfig("launchers", launchers);
        }
    }
}
