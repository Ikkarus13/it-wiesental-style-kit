// Tray einklappen – blendet Tray-Symbole hinter einem Pfeil aus (wie unter Windows).
//
// Funktioniert, indem es sich in die laufenden Applets "xapp-status@cinnamon.org"
// (StatusNotifier-/AppIndicator-Symbole) und "systray@cinnamon.org" (alte XEmbed-Symbole)
// einhängt. Die Applets selbst bleiben unverändert; beim Entfernen wird alles zurückgesetzt.
//
// Zwei Modi:
//  - "popup":  Pfeil öffnet ein Fenster über dem Panel, die ausgeblendeten Symbole werden
//              solange dorthin verschoben (wie das Windows-Überlaufmenü).
//  - "inline": Symbole klappen direkt im Panel auf und zu.

const Applet = imports.ui.applet;
const AppletManager = imports.ui.appletManager;
const Main = imports.ui.main;
const PopupMenu = imports.ui.popupMenu;
const Settings = imports.ui.settings;
const Tooltips = imports.ui.tooltips;
const Clutter = imports.gi.Clutter;
const St = imports.gi.St;
const GLib = imports.gi.GLib;
const Meta = imports.gi.Meta;

// Fenstertypen, als die Kontextmenüs der Apps erscheinen (GTK: OVERRIDE_OTHER).
const MENU_WINDOW_TYPES = [Meta.WindowType.MENU, Meta.WindowType.DROPDOWN_MENU,
                           Meta.WindowType.POPUP_MENU, Meta.WindowType.OVERRIDE_OTHER];

const XAPP_UUID = "xapp-status@cinnamon.org";
const SYSTRAY_UUID = "systray@cinnamon.org";
const RESCAN_SECONDS = 3;

function findApplets(uuid) {
    return AppletManager.definitions
        .filter(d => d && d.applet && d.applet._uuid === uuid)
        .map(d => d.applet);
}

function stripMarkup(text) {
    return (text || "").replace(/<[^>]*>/g, "").split("\n")[0].trim();
}

class TrayCollapseApplet extends Applet.IconApplet {
    constructor(metadata, orientation, panelHeight, instanceId) {
        super(orientation, panelHeight, instanceId);
        this.setAllowedLayout(Applet.AllowedLayout.BOTH);
        this.orientation = orientation;

        this.settings = new Settings.AppletSettings(this, metadata.uuid, instanceId);
        this.settings.bind("mode", "mode", () => this._onModeChanged());
        this.settings.bind("columns", "columns", () => this._closePopup());
        this.settings.bind("popup-icon-size", "popupIconSize", () => this._closePopup());
        this.settings.bind("auto-collapse", "autoCollapse");
        this.settings.bind("expand-on-hover", "expandOnHover");
        this.collapsed = this.settings.getValue("collapsed");
        this.pinned = this.settings.getValue("pinned") || [];
        this.settings.bind("applets", "appletList", () => this._hookAll());

        this._xappHooks = new Set();
        this._systrayHooks = new Set();
        this._appletHooks = new Map();   // Cinnamon-Applet -> { wanted, signalId, shownForMenu }
        this._inPopup = [];              // [{ actor, parent, index, xapp }] solange das Popup offen ist
        this._enlargedIcons = [];        // im Popup vergrößerte Symbol-Icons { icon, size }
        this._autoCollapseId = 0;

        this._pinMenu = new PopupMenu.PopupSubMenuMenuItem("Immer sichtbar");
        this._applet_context_menu.addMenuItem(this._pinMenu);
        this._applet_context_menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._applet_context_menu.connect("open-state-changed", (menu, open) => {
            if (!open) return;
            // Popup und Kontextmenü teilen sich einen PopupMenuManager. Bliebe das Popup offen
            // (z. B. noch im "Maus freigegeben"-Zustand), könnte es später einen zweiten Grab
            // anfordern → Main.modalCount bleibt hängen → Tastatur und Klicks tot.
            if (this.menu && this.menu.isOpen) this.menu.close(false);
            this._buildPinMenu();
        });

        this._buildPopup(orientation);

        this.actor.connect("enter-event", () => {
            if (!this.expandOnHover) return;
            if (this.mode === "popup") {
                if (!this.menu.isOpen) this.menu.open(true);
            } else if (this.collapsed) {
                this._setCollapsed(false);
            }
        });

        this._editModeId = global.settings.connect("changed::panel-edit-mode", () => this._applyAll());

        this._updateArrow();
        this._hookAll();
        // Tray-Applets können nach uns laden oder neu geladen werden.
        this._rescanId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, RESCAN_SECONDS, () => {
            this._hookAll();
            return GLib.SOURCE_CONTINUE;
        });
    }

    on_applet_clicked() {
        if (this.mode === "popup")
            this.menu.toggle();
        else
            this._setCollapsed(!this.collapsed);
    }

    on_orientation_changed(orientation) {
        this.orientation = orientation;
        this._closePopup();
        this.menu.setOrientation(orientation);
        this._updateArrow();
    }

    on_applet_removed_from_panel() {
        if (this._rescanId) GLib.source_remove(this._rescanId);
        this._rescanId = 0;
        global.settings.disconnect(this._editModeId);
        this._clearAutoCollapse();
        this._closePopup();
        this._unhookAll();
        this.menu.destroy();
        this.settings.finalize();
    }

    // ---- Zustand ------------------------------------------------------------

    // Im Popup-Modus sind nicht angepinnte Symbole im Panel immer ausgeblendet.
    _panelCollapsed() {
        return this.mode === "popup" || this.collapsed;
    }

    _onModeChanged() {
        this._closePopup();
        this._clearAutoCollapse();
        this._updateArrow();
        this._applyAll();
    }

    _setCollapsed(collapsed) {
        this.collapsed = collapsed;
        this.settings.setValue("collapsed", collapsed);
        this._updateArrow();
        this._applyAll();

        this._clearAutoCollapse();
        if (!collapsed) this._startAutoCollapse(() => this._setCollapsed(true));
    }

    _startAutoCollapse(callback) {
        this._clearAutoCollapse();
        if (this.autoCollapse <= 0) return;
        this._autoCollapseId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, this.autoCollapse, () => {
            this._autoCollapseId = 0;
            callback();
            return GLib.SOURCE_REMOVE;
        });
    }

    _clearAutoCollapse() {
        if (this._autoCollapseId) GLib.source_remove(this._autoCollapseId);
        this._autoCollapseId = 0;
    }

    _updateArrow() {
        let name, tooltip;
        if (this.mode === "popup") {
            // Pfeil zeigt vom Panel weg, also dorthin, wo das Popup aufgeht (Windows: ^).
            let open = this.menu && this.menu.isOpen;
            let away = { [St.Side.BOTTOM]: "up", [St.Side.TOP]: "down",
                         [St.Side.LEFT]: "end", [St.Side.RIGHT]: "start" }[this.orientation] || "up";
            let back = { up: "down", down: "up", end: "start", start: "end" }[away];
            name = "pan-" + (open ? back : away) + "-symbolic";
            tooltip = open ? "" : "Ausgeblendete Symbole anzeigen";
        } else {
            // Der Pfeil zeigt in die Richtung, in die sich die Symbole ausklappen.
            let vertical = this.orientation === St.Side.LEFT || this.orientation === St.Side.RIGHT;
            if (vertical)
                name = this.collapsed ? "pan-up-symbolic" : "pan-down-symbolic";
            else
                name = this.collapsed ? "pan-start-symbolic" : "pan-end-symbolic";
            tooltip = this.collapsed ? "Ausgeblendete Symbole einblenden" : "Symbole ausblenden";
        }
        this.set_applet_icon_symbolic_name(name);
        this.set_applet_tooltip(tooltip);
    }

    _isPinned(key) {
        return this.pinned.indexOf(key) !== -1;
    }

    _setPinned(key, pinned) {
        let list = this.pinned.filter(k => k !== key);
        if (pinned) list.push(key);
        this.pinned = list;
        this.settings.setValue("pinned", list);
        this._applyAll();
    }

    // ---- Popup (Überlaufbereich) --------------------------------------------

    _buildPopup(orientation) {
        this._menuManager = this._menuManager || new PopupMenu.PopupMenuManager(this);
        this.menu = new Applet.AppletPopupMenu(this, orientation);
        this._menuManager.addMenu(this.menu);

        // Gleich große Zellen: Symbole sind je nach App unterschiedlich hoch (16/24 px),
        // sonst wirken die Zeilen ungleichmäßig eng.
        let layout = new Clutter.GridLayout({ column_spacing: 4, row_spacing: 14,
                                              row_homogeneous: true, column_homogeneous: true });
        this._grid = new St.Widget({ layout_manager: layout, style: "padding: 6px 4px;" });
        this._emptyLabel = new St.Label({ text: "Keine ausgeblendeten Symbole", style: "padding: 6px 10px;" });
        this.menu.box.add_child(this._grid);
        this.menu.box.add_child(this._emptyLabel);

        // Klick auf ein Symbol im Popup: Ereignis an das Symbol durchlassen, danach
        //  - Rechtsklick: Popup bleibt offen, gibt aber die Maus frei, damit das Menü der App
        //    Eingaben bekommt (siehe _releaseForAppMenu),
        //  - sonst: Popup schließen (die App öffnet meist ein Fenster).
        this._grid.reactive = true;
        this._grid.connect("captured-event", (actor, event) => {
            if (event.type() !== Clutter.EventType.BUTTON_RELEASE) return Clutter.EVENT_PROPAGATE;
            if (event.get_button() === Clutter.BUTTON_SECONDARY) {
                // Sofort, bevor das Symbol den Klick an die App meldet – die App öffnet ihr
                // Menü direkt und braucht dafür die Maus.
                this._releaseForAppMenu();
            } else {
                GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                    this.menu.close(false);
                    return GLib.SOURCE_REMOVE;
                });
            }
            return Clutter.EVENT_PROPAGATE;
        });

        this.menu.connect("open-state-changed", (menu, open) => {
            if (open) {
                this._fillPopup();
                this._startAutoCollapse(() => this.menu.close(true));
            } else {
                this._clearAutoCollapse();
                this._endRelease();
            }
            this._updateArrow();
        });
        // Symbole erst nach der Ausblend-Animation zurücklegen, sonst schrumpft das Popup sichtbar.
        this.menu.connect("menu-animated-closed", () => this._restorePopup());
    }

    // Cinnamon-Popups halten die Maus per Grab fest; solange das so ist, kann das Kontextmenü
    // der App nicht bedient werden. Deshalb: Grab lösen, das Popup aber als klickbare Fläche
    // anmelden (sonst gingen Klicks an das Fenster darunter) und warten, bis das Menü der App
    // wieder zu ist. Danach Grab zurück – oder Popup schließen, wenn ein neues Fenster den
    // Fokus bekommen hat (Menüpunkt hat etwas geöffnet).
    _releaseForAppMenu() {
        if (!this.menu.isOpen) return;
        this._endRelease();
        if (this._menuManager.grabbed) this._menuManager._ungrab();
        Main.layoutManager.trackChrome(this.menu.actor);
        this._released = { focus: global.display.focus_window, menuWindow: null, timeoutId: 0, signalId: 0 };

        let r = this._released;
        r.signalId = global.display.connect("window-created", (display, win) => {
            if (r.menuWindow || !MENU_WINDOW_TYPES.includes(win.get_window_type())) return;
            r.menuWindow = win;
            win.connect("unmanaged", () => {
                if (this._released !== r) return;
                // Kurz warten, ob ein Menüpunkt ein neues Fenster öffnet.
                r.timeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
                    r.timeoutId = 0;
                    this._regrab();
                    return GLib.SOURCE_REMOVE;
                });
            });
            if (r.timeoutId) GLib.source_remove(r.timeoutId);
            r.timeoutId = 0;
        });
        // App zeigt gar kein Menü → nach kurzer Zeit wieder normal weiter.
        r.timeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
            r.timeoutId = 0;
            if (!r.menuWindow) this._regrab();
            return GLib.SOURCE_REMOVE;
        });
    }

    _regrab() {
        let r = this._released;
        if (!r || !this.menu.isOpen) return;
        let focusChanged = global.display.focus_window !== r.focus;
        this._endRelease();
        // Nie doppelt greifen: PopupMenuManager._grab() prüft das selbst nicht, jedes
        // überzählige pushModal bliebe für immer bestehen (Sitzung friert ein).
        if (focusChanged || this._menuManager.grabbed || Main.modalCount > 0 ||
            this._menuManager._activeMenu !== this.menu) {
            this.menu.close(true);
            return;
        }
        this._menuManager._grab();
        if (this._menuManager.grabbed) this.menu.actor.grab_key_focus();
        else this.menu.close(true);
    }

    _endRelease() {
        let r = this._released;
        if (!r) return;
        this._released = null;
        if (r.timeoutId) GLib.source_remove(r.timeoutId);
        if (r.signalId) global.display.disconnect(r.signalId);
        Main.layoutManager.untrackChrome(this.menu.actor);
    }

    _closePopup() {
        if (this.menu && this.menu.isOpen) this.menu.close(false);
        this._restorePopup();
    }

    _hiddenActors() {
        let list = [];
        for (let applet of this._xappHooks) {
            for (let icon of this._sortedXappIcons(applet)) {
                if (icon._tcWanted && !this._isPinned(this._xappKey(icon)))
                    list.push({ actor: icon.actor, xapp: applet });
            }
        }
        for (let applet of this._systrayHooks) {
            if (!applet.button_box) continue;
            for (let button of applet.button_box.get_children().slice().reverse()) {
                if (!this._isPinned(this._systrayKey(button)))
                    list.push({ actor: button, xapp: null });
            }
        }
        return list;
    }

    _fillPopup() {
        this._restorePopup();
        let cols = Math.max(1, this.columns || 4);
        let layout = this._grid.layout_manager;
        let n = 0;
        for (let entry of this._hiddenActors()) {
            let parent = entry.actor.get_parent();
            if (!parent) continue;
            this._inPopup.push({ actor: entry.actor, parent: parent,
                                 index: parent.get_children().indexOf(entry.actor), xapp: entry.xapp });
            parent.remove_child(entry.actor);
            layout.attach(entry.actor, n % cols, Math.floor(n / cols), 1, 1);
            entry.actor.show();
            this._resizeIcons(entry.actor);
            n++;
        }
        for (let [applet, h] of this._appletHooks) {
            if (!h.wanted || this._isPinned(this._appletKey(applet))) continue;
            layout.attach(this._makeAppletProxy(applet), n % cols, Math.floor(n / cols), 1, 1);
            n++;
        }
        this._grid.visible = n > 0;
        this._emptyLabel.visible = n === 0;
    }

    // Im Panel zeichnet Cinnamon einfarbige (symbolische) Icons kleiner als farbige.
    // Im Popup stehen sie im Raster nebeneinander – dort alle gleich groß.
    _popupIconSize() {
        let small = this.getPanelIconSize(St.IconType.SYMBOLIC);
        let large = this.getPanelIconSize(St.IconType.FULLCOLOR);
        if (this.popupIconSize === "large") return large;
        if (this.popupIconSize === "medium") return Math.round((small + large) / 2);
        return small;
    }

    _resizeIcons(actor) {
        let size = this._popupIconSize();
        let walk = (a) => {
            if (a instanceof St.Icon) {
                if (a.icon_size !== size) {
                    this._enlargedIcons.push({ icon: a, size: a.icon_size });
                    a.icon_size = size;
                }
                return;
            }
            // Als Bild geladene Icons (Pfad statt Icon-Name) sind Actors mit fester Größe.
            if (a.content && a.get_n_children() === 0 && a.height > 0 && Math.round(a.height) !== size) {
                let [w, h] = [a.width, a.height];
                this._enlargedIcons.push({ icon: a, width: w, height: h });
                a.set_size(Math.round(w * size / h), size);
                return;
            }
            for (let c of a.get_children()) walk(c);
        };
        walk(actor);
    }

    _restoreIconSizes() {
        for (let e of this._enlargedIcons) {
            if (e.icon.is_finalized?.()) continue;
            if (e.size !== undefined) e.icon.icon_size = e.size;
            else e.icon.set_size(e.width, e.height);
        }
        this._enlargedIcons = [];
    }

    _restorePopup() {
        this._restoreIconSizes();
        // Ersatzknöpfe für Applets gehören uns – einfach wegwerfen.
        for (let child of this._grid.get_children()) {
            if (!this._inPopup.some(e => e.actor === child)) child.destroy();
        }
        if (this._inPopup.length === 0) return;
        let entries = this._inPopup;
        this._inPopup = [];
        let xapps = new Set();
        // In umgekehrter Reihenfolge zurück, damit die gemerkten Positionen stimmen.
        for (let e of entries.slice().reverse()) {
            if (e.actor.get_parent() === this._grid) this._grid.remove_child(e.actor);
            if (e.parent && !e.parent.is_finalized?.() && e.actor.get_parent() === null)
                e.parent.insert_child_at_index(e.actor, Math.max(0, e.index));
            if (e.xapp) xapps.add(e.xapp);
        }
        for (let applet of xapps) Object.getPrototypeOf(applet).sortIcons.call(applet);
        this._applyAll();
    }

    _sortedXappIcons(applet) {
        // Reihenfolge wie im Panel.
        let icons = Object.values(applet.statusIcons).filter(i => i._tcPatched);
        let order = applet.manager_container.get_children();
        icons.sort((a, b) => order.indexOf(a.actor) - order.indexOf(b.actor));
        return icons;
    }

    // ---- Symbole identifizieren ---------------------------------------------

    _xappLabel(icon) {
        let p = icon.proxy;
        return stripMarkup(p.tooltip_text) || stripMarkup(p.label) || p.name;
    }

    _xappKey(icon) {
        let name = icon.proxy.name || "";
        // Manche Apps haben pro Start wechselnde D-Bus-Namen – dann über den Tooltip merken.
        if (/^:\d|statusnotifieritem-\d+/i.test(name))
            return "tip:" + this._xappLabel(icon);
        return name;
    }

    _systrayKey(button) {
        return "systray:" + (button._tcRole || "").toLowerCase();
    }

    // ---- Einhängen ----------------------------------------------------------

    _hookAll() {
        let xapps = findApplets(XAPP_UUID);
        let systrays = findApplets(SYSTRAY_UUID);
        for (let applet of xapps) {
            if (!this._xappHooks.has(applet)) this._hookXapp(applet);
        }
        for (let applet of systrays) {
            if (!this._systrayHooks.has(applet)) this._hookSystray(applet);
        }
        let wantedUuids = (this.appletList || []).map(e => (e.uuid || "").trim()).filter(u => u);
        let others = [];
        for (let uuid of wantedUuids) others.push(...findApplets(uuid));
        for (let applet of others) {
            if (!this._appletHooks.has(applet)) this._hookApplet(applet);
        }
        for (let applet of [...this._appletHooks.keys()]) {
            if (!others.includes(applet)) this._unhookApplet(applet);
        }
        // Weggefallene Applets vergessen.
        for (let applet of [...this._xappHooks]) {
            if (!xapps.includes(applet)) this._xappHooks.delete(applet);
        }
        for (let applet of [...this._systrayHooks]) {
            if (!systrays.includes(applet)) this._systrayHooks.delete(applet);
        }
    }

    _hookXapp(applet) {
        this._xappHooks.add(applet);
        let proto = Object.getPrototypeOf(applet);
        // Während das Popup offen ist, liegen Symbole nicht in manager_container –
        // vor jeder Umsortierung durch das Applet erst alles zurücklegen.
        applet.addStatusIcon = (proxy) => {
            this._closePopup();
            proto.addStatusIcon.call(applet, proxy);
            this._patchXappIcons(applet);
        };
        applet.removeStatusIcon = (proxy) => {
            this._closePopup();
            proto.removeStatusIcon.call(applet, proxy);
        };
        applet.sortIcons = () => {
            this._closePopup();
            proto.sortIcons.call(applet);
        };
        this._patchXappIcons(applet);
    }

    _patchXappIcons(applet) {
        for (let key in applet.statusIcons) {
            let icon = applet.statusIcons[key];
            if (icon._tcPatched) continue;
            icon._tcPatched = true;
            icon._tcWanted = !!icon.proxy.visible;
            // Die App meldet "sichtbar/unsichtbar" – wir merken es uns und entscheiden selbst.
            icon.setVisible = (visible) => {
                icon._tcWanted = visible;
                this._applyXappIcon(icon);
            };
            this._applyXappIcon(icon);
        }
    }

    _applyXappIcon(icon) {
        let inPopup = icon.actor.get_parent() === this._grid;
        let show = icon._tcWanted &&
            (inPopup || !this._panelCollapsed() || this._isPinned(this._xappKey(icon)));
        if (show) icon.actor.show(); else icon.actor.hide();
    }

    _hookSystray(applet) {
        this._systrayHooks.add(applet);
        applet._onTrayIconAdded = (o, icon, role) => {
            this._closePopup();
            Object.getPrototypeOf(applet)._onTrayIconAdded.call(applet, o, icon, role);
            let button = applet.button_box.get_child_at_index(0);
            if (button && button.child === icon) {
                button._tcRole = role;
                this._applySystrayButton(button);
            }
        };
        // Signal ist schon mit der alten Funktion verbunden – neu verbinden.
        if (applet._signalManager) {
            applet._signalManager.disconnect("status-icon-added");
            applet._signalManager.connect(Main.statusIconDispatcher, "status-icon-added",
                applet._onTrayIconAdded, applet);
        }
        this._applySystray(applet);
    }

    // ---- Ganze Cinnamon-Applets (z. B. Wechseldatenträger) -------------------
    // Diese Applets blenden sich selbst ein und aus (actor.visible). Wir merken uns, was das
    // Applet will, und blenden es im eingeklappten Zustand aus. Im Popup erscheint statt des
    // Applets ein Ersatzknopf; ein Klick darauf zeigt das Applet kurz im Panel und öffnet
    // dort sein Menü, damit das Menü an der gewohnten Stelle aufgeht.

    _hookApplet(applet) {
        let h = { wanted: applet.actor.visible, signalId: 0, shownForMenu: false, ours: false };
        this._appletHooks.set(applet, h);
        h.signalId = applet.actor.connect("notify::visible", () => {
            if (h.ours) return;
            h.wanted = applet.actor.visible;
            this._applyApplet(applet);
        });
        this._applyApplet(applet);
    }

    _unhookApplet(applet) {
        let h = this._appletHooks.get(applet);
        if (!h) return;
        this._appletHooks.delete(applet);
        if (!applet.actor.is_finalized?.()) {
            applet.actor.disconnect(h.signalId);
            applet.actor.visible = h.wanted;
        }
    }

    _appletKey(applet) {
        return "applet:" + applet._uuid;
    }

    _appletLabel(applet) {
        let tip = stripMarkup(applet._applet_tooltip_text);
        if (tip) return tip;
        try {
            let [ok, data] = GLib.file_get_contents(applet._meta?.path + "/metadata.json");
            let name = JSON.parse(imports.byteArray.toString(data)).name;
            if (name) return imports.gettext.dgettext("cinnamon", name);
        } catch (e) { }
        return applet._uuid;
    }

    _applyApplet(applet) {
        let h = this._appletHooks.get(applet);
        if (!h) return;
        // Im Panel-Bearbeitungsmodus nichts verstecken, sonst lässt sich das Applet nicht verschieben.
        let show = h.wanted &&
            (h.shownForMenu || !this._panelCollapsed() || this._isPinned(this._appletKey(applet)) ||
             global.settings.get_boolean("panel-edit-mode"));
        if (applet.actor.visible === show) return;
        h.ours = true;
        applet.actor.visible = show;
        h.ours = false;
    }

    _makeAppletProxy(applet) {
        let src = applet._applet_icon;
        let icon;
        if (src) {
            icon = new St.Icon({ icon_name: src.icon_name, gicon: src.gicon, icon_type: src.icon_type,
                                 icon_size: this._popupIconSize(),
                                 style_class: src.get_style_class_name() });
        } else {
            icon = new St.Icon({ icon_name: "application-x-addon-symbolic", icon_type: St.IconType.SYMBOLIC,
                                 icon_size: this._popupIconSize() });
        }
        let button = new St.Button({ style_class: "applet-box", child: icon, reactive: true,
                                     track_hover: true, button_mask: St.ButtonMask.ONE | St.ButtonMask.THREE });
        button._tcTooltip = new Tooltips.Tooltip(button, this._appletLabel(applet));
        button.connect("clicked", (b, clickedButton) => this._openAppletFromPopup(applet, clickedButton));
        return button;
    }

    _openAppletFromPopup(applet, clickedButton) {
        let h = this._appletHooks.get(applet);
        if (!h) return;
        this.menu.close(false);
        h.shownForMenu = true;
        this._applyApplet(applet);
        // Kurz warten, bis das Applet im Panel Platz und Position hat, dann sein Menü öffnen.
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 60, () => {
            let menu = null;
            if (clickedButton === 3) {
                menu = applet._applet_context_menu;
                menu.toggle();
            } else {
                applet.on_applet_clicked(null);
                menu = [applet.menu, applet._applet_context_menu].find(m => m && m.isOpen) || null;
            }
            let done = () => {
                h.shownForMenu = false;
                this._applyApplet(applet);
            };
            if (menu && menu.isOpen) {
                let id = menu.connect("open-state-changed", (m, open) => {
                    if (open) return;
                    menu.disconnect(id);
                    done();
                });
            } else {
                done();
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    _applySystray(applet) {
        if (!applet.button_box) return;
        for (let button of applet.button_box.get_children()) this._applySystrayButton(button);
    }

    _applySystrayButton(button) {
        button.visible = !this._panelCollapsed() || this._isPinned(this._systrayKey(button));
    }

    _applyAll() {
        for (let applet of this._xappHooks) {
            for (let key in applet.statusIcons) {
                let icon = applet.statusIcons[key];
                if (icon._tcPatched) this._applyXappIcon(icon);
            }
        }
        for (let applet of this._systrayHooks) this._applySystray(applet);
        for (let applet of this._appletHooks.keys()) this._applyApplet(applet);
    }

    _unhookAll() {
        for (let applet of [...this._appletHooks.keys()]) this._unhookApplet(applet);
        for (let applet of this._xappHooks) {
            delete applet.addStatusIcon;
            delete applet.removeStatusIcon;
            delete applet.sortIcons;
            for (let key in applet.statusIcons) {
                let icon = applet.statusIcons[key];
                if (!icon._tcPatched) continue;
                delete icon.setVisible;
                delete icon._tcPatched;
                icon.setVisible(icon._tcWanted);
                delete icon._tcWanted;
            }
        }
        for (let applet of this._systrayHooks) {
            delete applet._onTrayIconAdded;
            if (applet._signalManager) {
                applet._signalManager.disconnect("status-icon-added");
                applet._signalManager.connect(Main.statusIconDispatcher, "status-icon-added",
                    applet._onTrayIconAdded, applet);
            }
            if (applet.button_box)
                for (let button of applet.button_box.get_children()) button.visible = true;
        }
        this._xappHooks.clear();
        this._systrayHooks.clear();
    }

    // ---- Kontextmenü --------------------------------------------------------

    _buildPinMenu() {
        this._pinMenu.menu.removeAll();
        let entries = [];

        for (let applet of this._xappHooks) {
            for (let k in applet.statusIcons) {
                let icon = applet.statusIcons[k];
                if (!icon._tcWanted) continue;  // gerade von der App selbst versteckt
                entries.push({ key: this._xappKey(icon), label: this._xappLabel(icon) });
            }
        }
        for (let applet of this._systrayHooks) {
            if (!applet.button_box) continue;
            for (let button of applet.button_box.get_children()) {
                if (!button._tcRole) continue;
                entries.push({ key: this._systrayKey(button), label: button._tcRole });
            }
        }

        // Applets immer anbieten, auch wenn sie gerade nichts anzeigen (z. B. kein USB-Stick).
        for (let applet of this._appletHooks.keys())
            entries.push({ key: this._appletKey(applet), label: this._appletLabel(applet) });

        entries.sort((a, b) => a.label.localeCompare(b.label));
        let seen = new Set();
        for (let e of entries) {
            if (seen.has(e.key)) continue;
            seen.add(e.key);
            let item = new PopupMenu.PopupSwitchMenuItem(e.label, this._isPinned(e.key));
            item.connect("toggled", (it, state) => this._setPinned(e.key, state));
            this._pinMenu.menu.addMenuItem(item);
        }
        if (seen.size === 0) {
            let item = new PopupMenu.PopupMenuItem("Keine Symbole gefunden", { reactive: false });
            this._pinMenu.menu.addMenuItem(item);
        }
    }
}

function main(metadata, orientation, panelHeight, instanceId) {
    return new TrayCollapseApplet(metadata, orientation, panelHeight, instanceId);
}
