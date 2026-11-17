#!/usr/bin/env python3
"""Emoji-Panel für Cinnamon (X11) nach Vorbild des Windows-Panels (Win + .)."""
import json
import math
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango, PangoCairo

import daten
import updater
import zwischenablage
from system import CaretTracker, IBusCaret, XHelper
from version import VERSION

APP_ID = 'org.emojipanel.App'
DATA_DIR = Path(os.environ.get('EMOJIPANEL_DATA_DIR', Path.home() / '.local/share/emoji-panel'))
STATE = DATA_DIR / 'zustand.json'
W, H = 372, 470
MAX_RECENT = 40
MAX_CLIP = 25
DEBUG = bool(os.environ.get('EMOJIPANEL_DEBUG'))


def log(*a):
    if DEBUG:
        print(*a, flush=True)

TABS = [
    ('recent', 'document-open-recent-symbolic', 'Zuletzt verwendet'),
    ('emoji', 'face-smile-symbolic', 'Emoji'),
    ('kaomoji', ';-)', 'Kaomoji'),
    ('symbols', 'Ω', 'Symbole'),
    ('clip', 'edit-paste-symbolic', 'Zwischenablage-Verlauf'),
]
EMOJI_FONT = Pango.FontDescription.from_string('Noto Color Emoji 19')
TEXT_FONT = Pango.FontDescription.from_string('Sans 11')
SYMBOL_FONT = Pango.FontDescription.from_string('Sans 15')
HEAD_FONT = Pango.FontDescription.from_string('Sans Semibold 9.5')


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class Grid(Gtk.DrawingArea):
    """Gezeichnetes Raster mit Abschnitten; Einträge: (text, tooltip, (art, wert))."""
    __gsignals__ = {'item-activated': (GObject.SignalFlags.RUN_FIRST, None, (object,))}
    PAD, GAP, HEAD = 8, 2, 30

    def __init__(self, pal, cell=40, rows=False, empty=''):
        super().__init__()
        self.pal, self.cell, self.rows, self.empty = pal, cell, rows, empty
        self.sections, self.flat, self.rects, self.headers, self.sec_y = [], [], [], [], []
        self.hover = self.sel = -1
        self.show_sel = False
        self.vadj = None
        self.cache = {}
        self.width = 0
        self.press = -1
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.set_has_tooltip(True)
        self.connect('size-allocate', self._alloc)
        self.connect('motion-notify-event', self._motion)
        self.connect('leave-notify-event', self._leave)
        self.connect('button-press-event', self._press)
        self.connect('button-release-event', self._release)
        self.connect('query-tooltip', self._tooltip)

    # --- Daten / Layout -------------------------------------------------
    def set_sections(self, secs, show_sel=False, keep_scroll=False):
        self.sections = [(t, list(i)) for t, i in secs if i]
        self.flat = [it for _, items in self.sections for it in items]
        self.sel = 0 if self.flat else -1
        self.hover = -1
        self.show_sel = show_sel
        self._relayout()
        if self.vadj and not keep_scroll:
            self.vadj.set_value(0)
        self.queue_draw()

    def _font(self, kind):
        return EMOJI_FONT if kind == 'emoji' else SYMBOL_FONT if kind == 'symbol' else TEXT_FONT

    def _layout(self, it, width=0):
        key = (it[0], it[2][0], width)
        lay = self.cache.get(key)
        if lay is None:
            lay = self.create_pango_layout(it[0])
            lay.set_font_description(self._font(it[2][0]))
            if width:
                lay.set_width(width * Pango.SCALE)
                lay.set_height(-3)
                lay.set_ellipsize(Pango.EllipsizeMode.END)
            if len(self.cache) > 6000:
                self.cache.clear()
            self.cache[key] = lay
        return lay

    def _alloc(self, w, alloc):
        if alloc.width != self.width:
            self.width = alloc.width
            self._relayout()

    def _relayout(self):
        Wd = self.width or (W - 2)
        P, G = self.PAD, self.GAP
        rects, headers, sec_y = [], [], []
        y = 4
        for title, items in self.sections:
            sec_y.append(y)
            if title:
                headers.append((y, title))
                y += self.HEAD
            x, h = P, self.cell
            for it in items:
                if self.rows:
                    w, h = Wd - 2 * P, 64
                else:
                    w = self.cell
                    if it[2][0] != 'emoji' and it[2][0] != 'symbol':
                        lw = self._layout(it).get_pixel_size()[0]
                        w = min(max(self.cell, lw + 22), Wd - 2 * P)
                if x + w > Wd - P + 0.5 and x > P:
                    x = P
                    y += h + G + (4 if self.rows else 0)
                rects.append((x, y, w, h))
                x += w + G
            y += h + 10
        self.rects, self.headers, self.sec_y = rects, headers, sec_y
        self.set_size_request(-1, max(y + 4, 40))

    # --- Zeichnen -------------------------------------------------------
    def do_draw(self, cr):
        p = self.pal
        _, y0, _, y1 = cr.clip_extents()
        if not self.flat:
            if self.empty:
                lay = self.create_pango_layout(self.empty)
                lay.set_font_description(TEXT_FONT)
                lay.set_width((self.get_allocated_width() - 40) * Pango.SCALE)
                lay.set_alignment(Pango.Alignment.CENTER)
                cr.set_source_rgba(*p['dim'])
                cr.move_to(20, 40)
                PangoCairo.show_layout(cr, lay)
            return
        for y, title in self.headers:
            if y0 - self.HEAD <= y <= y1:
                lay = self.create_pango_layout(title)
                lay.set_font_description(HEAD_FONT)
                cr.set_source_rgba(*p['fg'])
                cr.move_to(self.PAD + 4, y + 8)
                PangoCairo.show_layout(cr, lay)
        for i, (x, y, w, h) in enumerate(self.rects):
            if y + h < y0 or y > y1:
                continue
            it = self.flat[i]
            if self.rows:
                rounded(cr, x, y, w, h, 6)
                cr.set_source_rgba(*(p['hover'] if i == self.hover else p['card']))
                cr.fill()
            elif i == self.hover:
                rounded(cr, x, y, w, h, 5)
                cr.set_source_rgba(*p['hover'])
                cr.fill()
            if self.show_sel and i == self.sel:
                rounded(cr, x + 1, y + 1, w - 2, h - 2, 5)
                cr.set_source_rgba(*p['accent'])
                cr.set_line_width(2)
                cr.stroke()
            lay = self._layout(it, w - 24 if self.rows else 0)
            lw, lh = lay.get_pixel_size()
            cr.set_source_rgba(*p['fg'])
            if self.rows:
                cr.move_to(x + 12, y + (h - lh) / 2)
            else:
                cr.move_to(x + (w - lw) / 2, y + (h - lh) / 2)
            PangoCairo.show_layout(cr, lay)

    # --- Maus / Tastatur ------------------------------------------------
    def hit(self, px, py):
        for i, (x, y, w, h) in enumerate(self.rects):
            if y <= py < y + h and x <= px < x + w:
                return i
        return -1

    def _motion(self, w, ev):
        i = self.hit(ev.x, ev.y)
        if i != self.hover:
            self.hover = i
            self.queue_draw()

    def _leave(self, *a):
        self.hover = -1
        self.queue_draw()

    def _press(self, w, ev):
        if ev.button == 1:
            self.press = self.hit(ev.x, ev.y)
        return True

    def _release(self, w, ev):
        i = self.hit(ev.x, ev.y)
        if ev.button == 1 and i >= 0 and i == self.press:
            self.sel = i
            self.emit('item-activated', self.flat[i])
        self.press = -1
        return True

    def _tooltip(self, w, x, y, kb, tip):
        i = self.sel if kb else self.hit(x, y)
        if i < 0 or not self.flat[i][1]:
            return False
        tip.set_text(self.flat[i][1])
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = [int(v) for v in self.rects[i]]
        tip.set_tip_area(r)
        return True

    def move_sel(self, dx, dy, page=False):
        if not self.flat:
            return
        if not self.show_sel:
            self.show_sel = True
        elif dx:
            self.sel = max(0, min(len(self.flat) - 1, self.sel + dx))
        elif dy:
            x, y, w, h = self.rects[self.sel]
            cx = x + w / 2
            steps = max(1, int(self.vadj.get_page_size() // (h + self.GAP)) - 1) if page and self.vadj else 1
            ty = y
            for _ in range(steps):
                ys = sorted({r[1] for r in self.rects})
                k = ys.index(ty) + dy
                if not 0 <= k < len(ys):
                    break
                ty = ys[k]
            if ty != y:
                cand = [(abs(r[0] + r[2] / 2 - cx), j) for j, r in enumerate(self.rects) if r[1] == ty]
                self.sel = min(cand)[1]
        self.scroll_to_sel()
        self.queue_draw()

    def scroll_to_sel(self):
        if not self.vadj or self.sel < 0:
            return
        x, y, w, h = self.rects[self.sel]
        v, pg = self.vadj.get_value(), self.vadj.get_page_size()
        if y - self.HEAD < v:
            self.vadj.set_value(max(0, y - self.HEAD))
        elif y + h > v + pg:
            self.vadj.set_value(y + h - pg + 8)

    def current(self):
        return self.flat[self.sel] if 0 <= self.sel < len(self.flat) else None


class Panel(Gtk.Window):
    def __init__(self, app):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.app = app
        self.pal = app.pal
        self.tab = 'emoji'
        self.grabbed = False
        self.grab_id = 0
        self.focus_ev = None
        self.target = 0
        self.drag = None
        self.set_app_paintable(True)
        vis = self.get_screen().get_rgba_visual()
        if vis:
            self.set_visual(vis)
        self.set_size_request(W, H)
        self.connect('draw', self._clear)
        self.connect('key-press-event', self._key)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_name('panel')
        self.add(outer)

        # Kopfzeile: Reiter + Schliessen (zugleich Griff zum Verschieben)
        ebox = Gtk.EventBox()
        ebox.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK)
        ebox.connect('button-press-event', self._drag_start)
        ebox.connect('motion-notify-event', self._drag_move)
        ebox.connect('button-release-event', lambda *a: setattr(self, 'drag', None))
        head = Gtk.Box(spacing=2)
        head.get_style_context().add_class('head')
        ebox.add(head)
        self.tab_buttons = {}
        for key, icon, tip in TABS:
            b = Gtk.Button()
            b.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON) if icon.endswith('-symbolic')
                  else Gtk.Label(label=icon))
            b.set_tooltip_text(tip)
            b.set_can_focus(False)
            b.get_style_context().add_class('tab')
            b.connect('clicked', lambda b, k=key: self.set_tab(k))
            head.pack_start(b, False, False, 0)
            self.tab_buttons[key] = b
        close = Gtk.Button.new_from_icon_name('window-close-symbolic', Gtk.IconSize.BUTTON)
        close.set_can_focus(False)
        close.set_tooltip_text('Schliessen (Esc)')
        close.get_style_context().add_class('tab')
        close.connect('clicked', lambda *a: self.hide_panel())
        head.pack_end(close, False, False, 0)
        gear = Gtk.Button.new_from_icon_name('emblem-system-symbolic', Gtk.IconSize.BUTTON)
        gear.set_can_focus(False)
        gear.set_tooltip_text('Einstellungen und Updates')
        gear.get_style_context().add_class('tab')
        gear.connect('clicked', lambda *a: (self.hide_panel(), self.app.show_settings()))
        head.pack_end(gear, False, False, 0)
        outer.pack_start(ebox, False, False, 0)

        self.entry = Gtk.SearchEntry()
        self.entry.set_placeholder_text('Suchen')
        # eigenes Suchfeld nicht über IBus: sonst überschreibt es die gemerkte Cursorposition
        self.entry.set_property('im-module', 'gtk-im-context-simple')
        self.entry.get_style_context().add_class('search')
        self.entry.connect('changed', lambda *a: self.refresh())
        outer.pack_start(self.entry, False, False, 0)

        self.stack = Gtk.Stack()
        outer.pack_start(self.stack, True, True, 0)
        self.grids = {}
        for key, cell, rows, empty in [
                ('recent', 40, False, ''),
                ('emoji', 40, False, 'Keine Emoji gefunden'),
                ('kaomoji', 40, False, 'Keine Kaomoji gefunden'),
                ('symbols', 40, False, 'Keine Symbole gefunden'),
                ('clip', 40, True, 'Kopierte Texte erscheinen hier, solange das Panel läuft.\n'
                                   'Ein Klick fügt sie wieder ein.')]:
            g = Grid(self.pal, cell, rows, empty)
            g.connect('item-activated', self._activated)
            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.add(g)
            g.vadj = sw.get_vadjustment()
            self.grids[key] = g
            if key == 'emoji':
                page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                page.pack_start(sw, True, True, 0)
                page.pack_start(self._build_catbar(g), False, False, 0)
                g.vadj.connect('value-changed', self._sync_catbar)
            else:
                page = sw
            self.stack.add_named(page, key)
        outer.show_all()

    # --- Kategorieleiste / Hautfarbe -----------------------------------
    def _build_catbar(self, grid):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.tone_rev = Gtk.Revealer()
        tones = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER)
        tones.get_style_context().add_class('tones')
        for i, t in enumerate(daten.TONES):
            b = Gtk.Button()
            lab = Gtk.Label()
            lab.set_markup(f'<span font="Noto Color Emoji 15">✋{t}</span>')
            b.add(lab)
            b.set_tooltip_text(f'Hautfarbe: {daten.TONE_NAMES[i]}')
            b.set_can_focus(False)
            b.get_style_context().add_class('cat')
            b.connect('clicked', lambda b, t=t: self._set_tone(t))
            tones.pack_start(b, False, False, 0)
        self.tone_rev.add(tones)
        box.pack_start(self.tone_rev, False, False, 0)

        self.catbar = Gtk.Box(spacing=0)
        self.catbar.get_style_context().add_class('catbar')
        self.cat_buttons = []
        for key, title, icon in [('recent', 'Zuletzt verwendet', 'emoji-recent-symbolic')] + daten.CATEGORIES:
            b = Gtk.Button()
            b.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))
            b.set_tooltip_text(title)
            b.set_can_focus(False)
            b.get_style_context().add_class('cat')
            b.connect('clicked', lambda b, t=title: self._jump(t))
            self.catbar.pack_start(b, True, True, 0)
            self.cat_buttons.append((title, b))
        self.tone_btn = Gtk.Button()
        self.tone_lab = Gtk.Label()
        self.tone_btn.add(self.tone_lab)
        self.tone_btn.set_tooltip_text('Hautfarbe wählen')
        self.tone_btn.set_can_focus(False)
        self.tone_btn.get_style_context().add_class('cat')
        self.tone_btn.connect('clicked', lambda *a: self.tone_rev.set_reveal_child(
            not self.tone_rev.get_reveal_child()))
        self.catbar.pack_start(self.tone_btn, True, True, 0)
        box.pack_start(self.catbar, False, False, 0)
        self._update_tone_btn()
        return box

    def _update_tone_btn(self):
        self.tone_lab.set_markup(f'<span font="Noto Color Emoji 13">✋{self.app.tone}</span>')

    def _set_tone(self, t):
        self.app.tone = t
        self.app.save()
        self._update_tone_btn()
        self.tone_rev.set_reveal_child(False)
        self.refresh(keep_scroll=True)

    def _jump(self, title):
        g = self.grids['emoji']
        for (t, _), y in zip(g.sections, g.sec_y):
            if t == title:
                g.vadj.set_value(min(y, g.vadj.get_upper() - g.vadj.get_page_size()))
                return

    def _sync_catbar(self, adj):
        g = self.grids['emoji']
        v = adj.get_value() + 12
        cur = None
        for (t, _), y in zip(g.sections, g.sec_y):
            if y <= v:
                cur = t
        if 0 < adj.get_value() >= adj.get_upper() - adj.get_page_size() - 1 and g.sections:
            cur = g.sections[-1][0]
        for t, b in self.cat_buttons:
            ctx = b.get_style_context()
            (ctx.add_class if t == cur else ctx.remove_class)('active')

    # --- Inhalte --------------------------------------------------------
    def set_tab(self, key):
        self.tab = key
        self.stack.set_visible_child_name(key)
        for k, b in self.tab_buttons.items():
            ctx = b.get_style_context()
            (ctx.add_class if k == key else ctx.remove_class)('active')
        self.refresh()

    def refresh(self, keep_scroll=False):
        q = self.entry.get_text().strip()
        if q and self.tab == 'recent':
            self.set_tab('emoji')
            return
        app, g = self.app, self.grids[self.tab]
        terms = q.lower().split()
        if self.tab == 'emoji':
            self.catbar.get_parent().set_visible(not q)
            if q:
                g.set_sections([('', [app.emoji_item(e) for e in app.emoji.search(q)])], True)
            else:
                g.set_sections(app.emoji_sections(), keep_scroll=keep_scroll)
                self._sync_catbar(g.vadj)
        elif self.tab == 'recent':
            g.set_sections(app.recent_sections())
        elif self.tab == 'kaomoji':
            secs = []
            for title, kw, items in daten.KAOMOJI:
                hay = (title + ' ' + kw).lower()
                if all(t in hay for t in terms):
                    secs.append((title, items))
                else:
                    secs.append((title, [k for k in items if all(t in k.lower() for t in terms)]))
            g.set_sections([(t, [(k, '', ('kaomoji', k)) for k in i]) for t, i in secs], bool(q))
        elif self.tab == 'symbols':
            fterms = daten.fold(q).split()
            secs = []
            for t, items in app.symbols:
                hits = [(c, n, h) for c, n, h in items if all(x in h or x == c.lower() for x in fterms)]
                if fterms:
                    hits.sort(key=lambda r: daten.symbol_rank(r[1], r[2], fterms))
                secs.append((t, [(c, n, ('symbol', c)) for c, n, _ in hits]))
            if fterms:   # bei Suche eine gemeinsame, sortierte Trefferliste
                allhits = [it for _, items in secs for it in items]
                order = {c: daten.symbol_rank(n, h, fterms) for _, items in app.symbols for c, n, h in items}
                secs = [('', sorted(allhits, key=lambda it: order[it[0]]))]
            g.set_sections(secs, bool(q))
        elif self.tab == 'clip':
            g.set_sections([('', [(c.strip()[:500], '', ('clip', c)) for c in app.clips
                                  if all(t in c.lower() for t in terms)])], bool(q))

    def _activated(self, grid, item):
        self.app.insert(item)

    # --- Anzeigen / Verstecken -----------------------------------------
    def show_at(self, target, caret, pointer):
        self.target = target
        self.entry.set_text('')
        self.tone_rev.set_reveal_child(False)
        self.set_tab(self.tab)
        disp = Gdk.Display.get_default()
        if caret:
            cx, cy, ch = caret
        else:
            cx, cy, ch = pointer[0] - 20, pointer[1] - 8, 16
        mon = disp.get_monitor_at_point(int(cx), int(cy)) or disp.get_primary_monitor()
        wa = mon.get_workarea()
        pw, ph = self.size()
        x = max(wa.x + 4, min(cx, wa.x + wa.width - pw - 4))
        y = cy + ch + 6
        if y + ph > wa.y + wa.height:
            y = max(wa.y + 4, cy - ph - 6)
        self.move(int(x), int(y))
        log('zeigen', int(x), int(y), 'caret' if caret else 'maus', f'ziel {target:#x}')
        self.show()
        self.grab()

    def grab(self, tries=0, force=False):
        self.grab_id = 0
        if not self.get_visible() or (self.app.pasting() and not force):
            return False
        seat = self.get_display().get_default_seat()
        st = seat.grab(self.get_window(), Gdk.SeatCapabilities.KEYBOARD, False, None, None, None, None)
        if st != Gdk.GrabStatus.SUCCESS:
            # z. B. solange Cinnamon die Tastenkombination noch hält
            # zuerst schnell, dann gemächlich – aufgeben erst, wenn das Panel zu ist
            self.grab_id = GLib.timeout_add(25 if tries < 80 else 250, self.grab, tries + 1)
            return False
        self.grabbed = True
        self.set_focus(self.entry)
        if self.focus_ev is None:
            ev = Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE)
            ev.focus_change.window = self.entry.get_window()
            ev.focus_change.in_ = 1
            ev.set_device(seat.get_keyboard())
            self.focus_ev = ev  # bleibt bestehen (Fensterverweis ohne eigene Referenz)
        self.entry.send_focus_change(self.focus_ev)
        return False

    def ungrab(self):
        if self.grab_id:
            GLib.source_remove(self.grab_id)
            self.grab_id = 0
        if self.grabbed:
            self.get_display().get_default_seat().ungrab()
            self.get_display().flush()
            self.grabbed = False

    def hide_panel(self, why=''):
        log('verstecken', why)
        self.ungrab()
        self.hide()

    def contains_pointer(self):
        if not self.get_visible():
            return False
        seat = self.get_display().get_default_seat()
        _, px, py = seat.get_pointer().get_position()
        x, y = self.get_position()
        pw, ph = self.size()
        return x <= px < x + pw and y <= py < y + ph

    def size(self):
        w, h = self.get_size()
        req = self.get_preferred_size()[1]
        return max(w, req.width, W), max(h, req.height, H)

    # --- Ereignisse -----------------------------------------------------
    def _clear(self, w, cr):
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(0)  # CLEAR
        cr.paint()
        cr.set_operator(2)  # OVER
        return False

    def _drag_start(self, w, ev):
        if ev.button == 1:
            self.drag = (ev.x_root, ev.y_root) + tuple(self.get_position())
        return False

    def _drag_move(self, w, ev):
        if self.drag:
            x0, y0, wx, wy = self.drag
            self.move(int(wx + ev.x_root - x0), int(wy + ev.y_root - y0))
        return False

    def _key(self, w, ev):
        k = ev.keyval
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        g = self.grids[self.tab]
        if k == Gdk.KEY_Escape:
            self.hide_panel()
            return True
        if ev.state & (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK) and \
                k in (Gdk.KEY_period, Gdk.KEY_semicolon, Gdk.KEY_colon, Gdk.KEY_comma):
            self.hide_panel('Tastenkürzel')   # wie unter Windows: dieselbe Taste schliesst wieder
            return True
        if k in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab) or (ctrl and k in (Gdk.KEY_Page_Up, Gdk.KEY_Page_Down)):
            keys = [t[0] for t in TABS]
            d = -1 if k in (Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Page_Up) else 1
            self.set_tab(keys[(keys.index(self.tab) + d) % len(keys)])
            return True
        nav = {Gdk.KEY_Left: (-1, 0), Gdk.KEY_Right: (1, 0), Gdk.KEY_Up: (0, -1), Gdk.KEY_Down: (0, 1),
               Gdk.KEY_Page_Up: (0, -1), Gdk.KEY_Page_Down: (0, 1)}
        if k in nav:
            g.move_sel(*nav[k], page=k in (Gdk.KEY_Page_Up, Gdk.KEY_Page_Down))
            return True
        if k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            it = g.current() if g.show_sel else None
            if it:
                self.app.insert(it)
            return True
        return False


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.panel = None

    # --- Zustand --------------------------------------------------------
    def load(self):
        try:
            s = json.loads(STATE.read_text())
        except Exception:
            s = {}
        if not isinstance(s, dict):
            s = {}
        self.state = s
        tone = s.get('tone')
        self.tone = tone if tone in daten.TONES else ''
        rec = s.get('recent') if isinstance(s.get('recent'), dict) else {}
        self.recent = {k: [v for v in rec.get(k) if isinstance(v, str)] if isinstance(rec.get(k), list) else []
                       for k in ('emoji', 'kaomoji', 'symbol')}
        self.restore_clipboard = s.get('restore_clipboard') is not False
        if not isinstance(s.get('update_check'), bool):
            s['update_check'] = True
        if not isinstance(s.get('update_last_check', 0), (int, float)):
            s['update_last_check'] = 0
        if not isinstance(s.get('update_token', ''), str):
            s['update_token'] = ''

    def save(self):
        self.state.update(tone=self.tone, recent=self.recent, restore_clipboard=self.restore_clipboard)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            os.chmod(DATA_DIR, 0o700)
            tmp = STATE.with_suffix('.tmp')
            # privat (0600): enthält ggf. das GitHub-Token
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(json.dumps(self.state, ensure_ascii=False, indent=1))
            os.chmod(tmp, 0o600)
            tmp.replace(STATE)
        except OSError as e:
            print('Zustand konnte nicht gespeichert werden:', e, file=sys.stderr)

    def record(self, kind, val):
        if kind == 'clip':
            return
        lst = self.recent[kind]
        if val in lst:
            lst.remove(val)
        lst.insert(0, val)
        del lst[MAX_RECENT:]
        self.save()

    # --- Inhalte --------------------------------------------------------
    def emoji_item(self, it):
        return (self.emoji.toned(it.char, self.tone), it.name, ('emoji', it.char))

    def emoji_sections(self):
        if self._emoji_secs is None or self._emoji_secs[0] != self.tone:
            secs = []
            for key, title, _ in daten.CATEGORIES:
                secs.append((title, [self.emoji_item(it) for it in self.emoji.items if it.cat == key]))
            self._emoji_secs = (self.tone, secs)
        recent = self._recent_emoji()[:18]
        return [('Zuletzt verwendet', recent)] + self._emoji_secs[1]

    def _recent_emoji(self):
        out = []
        for e in self.recent['emoji'] or daten.POPULAR:
            it = self.emoji.get(e)
            if it:
                out.append(self.emoji_item(it))
        return out

    def recent_sections(self):
        names = {c: n for _, items in self.symbols for c, n, _ in items}
        return [('Emoji', self._recent_emoji()),
                ('Kaomoji', [(k, '', ('kaomoji', k)) for k in self.recent['kaomoji']]),
                ('Symbole', [(c, names.get(c, ''), ('symbol', c)) for c in self.recent['symbol']])]

    # --- Einfügen -------------------------------------------------------
    def insert(self, item):
        """Einfügen über eine Warteschlange: schnelle Klicks werden nacheinander abgearbeitet,
        die alte Zwischenablage wird erst ganz am Schluss einmal wiederhergestellt."""
        text, _, (kind, val) = item
        if kind == 'clip':
            text = val
        self.record(kind, val)
        new_run = not (self._restore_id or self._busy or self._queue or self._saving)
        if self._restore_id:
            GLib.source_remove(self._restore_id)
            self._restore_id = 0
        # zuerst einreihen: Klicks, die während des Sicherns eintreffen, landen dahinter
        self._queue.append(text)
        if new_run:
            self._saved = None
            if self.restore_clipboard and kind != 'clip':
                self._saving = True   # wait_* dreht eine innere Hauptschleife
                try:
                    self._saved = zwischenablage.Snapshot.take(self.clipboard) or 'leer'
                finally:
                    self._saving = False
        if kind == 'clip':
            self._saved = None  # Windows-Verhalten: gewählter Verlaufseintrag bleibt in der Zwischenablage
        if not self._busy and not self._saving:
            self._next_paste()

    def pasting(self):
        return self._busy or self._saving or bool(self._queue)

    def _next_paste(self):
        p = self.panel
        if not self._queue:
            self._busy = False
            if p.get_visible():
                p.grab()
            if self._saved is not None:
                self._restore_id = GLib.timeout_add(700, self._restore)
            return False
        self._busy = True
        text = self._queue.pop(0)
        self._own(text)
        self.clipboard.set_text(text, -1)
        term = self.x.is_terminal(self.x.window_info(p.target)[0])

        def paste():
            log('einfügen', repr(text), 'terminal' if term else '')
            # Grab nur für Strg+V freigeben, sonst landen Tasten des Nutzers (z. B. gehaltenes
            # Enter) im Zielprogramm; send_paste wartet, bis der X-Server die Tasten verteilt hat
            p.ungrab()
            try:
                self.x.send_paste(term)
            except Exception as e:
                print('Einfügen fehlgeschlagen:', e, file=sys.stderr)
            if p.get_visible():
                p.grab(force=True)
            # dem Zielprogramm Zeit lassen, die Zwischenablage zu lesen
            GLib.timeout_add(120, self._next_paste)
            return False
        GLib.timeout_add(40, paste)
        return False

    def _restore(self):
        self._restore_id = 0
        if self.pasting():
            return False
        prev, self._saved = self._saved, None
        log('wiederherstellen', 'leer' if prev == 'leer' else [a.name() for a, *_ in prev.items] if prev else None)
        if prev == 'leer':
            self.clipboard.clear()          # war vorher leer → wieder leer
        elif prev is not None:
            if prev.text is not None:
                self._own(prev.text)
            self.clip_owner.restore(prev)
        return False

    def _own(self, text):
        """Eigene Zwischenablage-Inhalte nicht in den Verlauf aufnehmen."""
        now = GLib.get_monotonic_time()
        self._own_texts = [(t, ts) for t, ts in self._own_texts if now - ts < 3_000_000]
        self._own_texts.append((text, now))

    def _clip_changed(self, cb, ev):
        def got(cb, text):
            now = GLib.get_monotonic_time()
            if any(t == text and now - ts < 3_000_000 for t, ts in self._own_texts):
                return
            if text and text.strip():
                if text in self.clips:
                    self.clips.remove(text)
                self.clips.insert(0, text)
                del self.clips[MAX_CLIP:]

        def targets(cb, atoms, *rest):
            # Passwortmanager (KeePassXC u. a.) markieren Geheimnisse – nicht merken
            if not any(a.name() == 'x-kde-passwordManagerHint' for a in (atoms or [])):
                cb.request_text(got)
        cb.request_targets(targets)

    # --- Anzeige --------------------------------------------------------
    def toggle(self, *a):
        p = self.panel
        if p.get_visible():
            p.hide_panel()
            return
        target = self.x.active_window()
        _, pid = self.x.window_info(target)
        # 1. IBus (auch Chromium/Edge/Electron), 2. AT-SPI (GTK-Programme), sonst Mauszeiger
        caret = self.ibus.caret(self.x.geometry(target))
        src = 'ibus'
        if not caret:
            caret, src = self.caret.caret(pid), 'at-spi'
        log('textcursor', src if caret else 'keiner', caret)
        _, px, py = Gdk.Display.get_default().get_default_seat().get_pointer().get_position()
        p.show_at(target, caret, (px, py))

    def _outside_click(self):
        p = self.panel
        if p and p.get_visible() and not p.contains_pointer():
            p.hide_panel('Klick ausserhalb')

    def _active_changed(self, wid):
        p = self.panel
        if p and p.get_visible() and wid != p.target:
            p.hide_panel(f'aktives Fenster {wid:#x}')

    # --- Gtk.Application -----------------------------------------------
    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.load()
        self.emoji = daten.EmojiData()
        self.symbols = daten.symbols()
        self._emoji_secs = None
        self.clips = []
        self._own_texts = []
        self._queue, self._busy, self._saved, self._restore_id, self._saving = [], False, None, 0, False
        self.pal = palette()
        install_css(self.pal)
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self.clipboard.set_can_store(None)   # beim Beenden an den Zwischenablage-Manager übergeben
        self.clip_owner = zwischenablage.Owner()
        self.clipboard.connect('owner-change', self._clip_changed)
        self.x = XHelper(self._outside_click, self._active_changed)
        self.caret = CaretTracker()
        self.ibus = IBusCaret()
        self.panel = Panel(self)
        self.settings_win = None
        self._updating = False
        for name, cb in (('toggle', self.toggle), ('quit', lambda *a: self.quit()),
                         ('settings', lambda *a: self.show_settings()),
                         ('update', lambda *a: self.check_updates(manual=True))):
            act = Gio.SimpleAction.new(name, None)
            act.connect('activate', cb)
            self.add_action(act)
        self.hold()
        if self.state.get('last_version') not in (None, VERSION):
            self.notify(f'Emoji-Panel {VERSION} installiert', 'Das Update wurde eingespielt.')
        if self.state.get('last_version') != VERSION:
            self.state['last_version'] = VERSION
            self.save()
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: self.quit() or False)
        GLib.timeout_add_seconds(90, self.check_updates)
        GLib.timeout_add_seconds(3600, lambda: self.check_updates() or True)

    def do_shutdown(self):
        # ausstehende Wiederherstellung sofort erledigen und Inhalt an den Manager übergeben
        if self._restore_id:
            GLib.source_remove(self._restore_id)
            self._restore_id = 0
            self._busy, self._queue = False, []
            self._restore()
        try:
            self.clipboard.store()
        except Exception:
            pass
        Gtk.Application.do_shutdown(self)

    def do_command_line(self, cl):
        args = cl.get_arguments()[1:]
        if '--quit' in args:
            self.quit()
        elif '--settings' in args:
            self.show_settings()
        elif '--update' in args:
            self.check_updates(manual=True)
        elif '--hidden' not in args:
            self.toggle()
        return 0

    # --- Einstellungen / Updates ---------------------------------------
    def notify(self, title, body, action=None):
        n = Gio.Notification.new(title)
        n.set_body(body)
        if action:
            n.set_default_action(action)
        self.send_notification(None, n)

    def show_settings(self):
        if not self.settings_win:
            self.settings_win = SettingsWindow(self)
            self.settings_win.connect('destroy', lambda *a: setattr(self, 'settings_win', None))
        self.settings_win.show_all()
        self.settings_win.present()

    def check_updates(self, manual=False, on_done=None):
        """Im Hintergrund nach neuer Version fragen; automatisch höchstens einmal am Tag."""
        import threading
        import time
        s = self.state
        kit = updater.kit_verwaltet()
        if kit:                       # Updates kommen über das Style Kit
            if manual and on_done:
                on_done(None, None)
            elif manual:
                self.kit_message(kit)
            return False
        if not manual and (not s.get('update_check') or time.time() - s.get('update_last_check', 0) < 86400):
            return False

        def work():
            try:
                info, err = updater.check(token=s.get('update_token') or None), None
            except Exception as e:
                info, err = None, e
            GLib.idle_add(done, info, err)

        def done(info, err):
            if not err:
                s['update_last_check'] = int(time.time())
                self.save()
            if on_done:
                on_done(info, err)
            if info and not manual:
                self.notify(f'Emoji-Panel {info["version"]} ist verfügbar', 'Klicken, um zu aktualisieren.',
                            'app.update')
            elif info:
                self.update_dialog(info)
            elif manual and not on_done:
                self.message('Update-Prüfung nicht möglich' if err else 'Emoji-Panel ist aktuell',
                             str(err) if err else f'Installiert ist die neueste Version {VERSION}.')
            return False
        threading.Thread(target=work, daemon=True).start()
        return False

    def message(self, title, text):
        # nicht modal: ein offener Dialog darf die Tastatur im Panel nicht blockieren
        dlg = Gtk.MessageDialog(transient_for=self.settings_win, text=title, title='Emoji-Panel',
                                message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK)
        dlg.format_secondary_text(text)
        dlg.set_keep_above(True)
        dlg.connect('response', lambda d, r: d.destroy())
        dlg.show()

    def kit_message(self, kit):
        dlg = Gtk.MessageDialog(transient_for=self.settings_win, text=updater.kit_meldung(kit),
                                title='Emoji-Panel', message_type=Gtk.MessageType.INFO,
                                buttons=Gtk.ButtonsType.NONE)
        dlg.format_secondary_text(f'Installiert ist Version {VERSION}.')
        if (updater.kit_info() or {}).get('verwalten'):
            dlg.add_button(f'{kit} öffnen', 2)
        dlg.add_button('Schliessen', 1)
        dlg.set_default_response(1)
        dlg.set_keep_above(True)
        dlg.connect('response', lambda d, r: (d.destroy(), r == 2 and updater.kit_oeffnen()))
        dlg.show()
        return dlg

    def update_dialog(self, info):
        kit = updater.kit_verwaltet()
        if kit:
            self.kit_message(kit)
            return
        if self._updating:          # Dialog offen oder Installation läuft schon
            return
        self._updating = True
        dlg = Gtk.MessageDialog(transient_for=self.settings_win, message_type=Gtk.MessageType.INFO,
                                buttons=Gtk.ButtonsType.NONE, title='Emoji-Panel – Update',
                                text=f'Emoji-Panel {info["version"]} ist verfügbar')
        notes = info['notes'].strip()
        dlg.format_secondary_text(f'Installiert ist Version {VERSION}.' + ('\n\n' + notes[:1500] if notes else ''))
        dlg.add_buttons('Später', 1, 'Jetzt aktualisieren', 2)
        dlg.set_default_response(1)   # Enter darf nie versehentlich ein Update starten
        dlg.set_keep_above(True)
        dlg.connect('response', self._update_response, info)
        dlg.show()

    def _update_response(self, dlg, res, info):
        dlg.destroy()
        if res != 2:
            self._updating = False
            return
        self.notify('Emoji-Panel wird aktualisiert …', f'Version {info["version"]} wird eingespielt.')
        import threading

        def work():
            try:
                updater.install(info)
                err = None
            except Exception as e:
                err = e
            GLib.idle_add(fin, err)

        def fin(err):
            if err:
                self._updating = False
                self.message('Update fehlgeschlagen', str(getattr(err, 'stderr', '') or err))
            else:
                self.save()
                updater.run_installer()   # beendet diese Instanz und startet die neue
            return False
        threading.Thread(target=work, daemon=True).start()


class SettingsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title='Emoji-Panel – Einstellungen')
        self.app = app
        self.set_default_size(460, -1)
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name('face-smile')
        grid = Gtk.Grid(row_spacing=12, column_spacing=16, margin=20)
        self.add(grid)
        row = 0

        def label(text, dim=False):
            lab = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=48)
            if dim:
                lab.get_style_context().add_class('dim-label')
            return lab

        def switch(text, get, set_):
            nonlocal row
            sw = Gtk.Switch(active=get(), halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
            sw.connect('notify::active', lambda w, p: (set_(w.get_active()), app.save()))
            grid.attach(label(text), 0, row, 1, 1)
            grid.attach(sw, 1, row, 1, 1)
            row += 1

        title = Gtk.Label(xalign=0)
        title.set_markup(f'<b>Emoji-Panel {VERSION}</b>')
        grid.attach(title, 0, row, 2, 1)
        row += 1
        grid.attach(label('Öffnen mit Super + . oder Super + ;', True), 0, row, 2, 1)
        row += 1
        switch('Zwischenablage nach dem Einfügen wiederherstellen',
               lambda: app.restore_clipboard, lambda v: setattr(app, 'restore_clipboard', v))
        kit = updater.kit_info()
        if kit:                       # Updates kommen über das Style Kit
            box = Gtk.Box(spacing=12)
            self.status = label(updater.kit_meldung(kit['kit']), True)
            box.pack_start(self.status, True, True, 0)
            if kit['verwalten']:
                self.btn = Gtk.Button(label=f'{kit["kit"]} öffnen')
                self.btn.connect('clicked', lambda *a: updater.kit_oeffnen())
                box.pack_end(self.btn, False, False, 0)
            grid.attach(box, 0, row, 2, 1)
            return
        switch('Täglich nach Updates suchen',
               lambda: app.state.get('update_check', True), lambda v: app.state.__setitem__('update_check', v))

        grid.attach(label('GitHub-Token (nur nötig ohne angemeldete «gh»-CLI)'), 0, row, 2, 1)
        row += 1
        tok = Gtk.Entry(text=app.state.get('update_token', ''), visibility=False, hexpand=True)
        tok.set_placeholder_text('leer = Token von «gh auth» verwenden')
        tok.connect('changed', lambda w: (app.state.__setitem__('update_token', w.get_text().strip()), app.save()))
        grid.attach(tok, 0, row, 2, 1)
        row += 1

        box = Gtk.Box(spacing=12)
        self.btn = Gtk.Button(label='Jetzt nach Updates suchen')
        self.btn.connect('clicked', self._check)
        self.status = label('', True)
        box.pack_start(self.btn, False, False, 0)
        box.pack_start(self.status, True, True, 0)
        grid.attach(box, 0, row, 2, 1)

    def _check(self, *a):
        self.btn.set_sensitive(False)
        self.status.set_text('Suche …')

        def done(info, err):
            if self.app.settings_win is not self:   # Fenster inzwischen geschlossen
                return
            self.btn.set_sensitive(True)
            if err:
                self.status.set_text(str(err))
            elif info:
                self.status.set_text(f'Version {info["version"]} verfügbar')
            else:
                self.status.set_text(f'Aktuell ({VERSION})')
        self.app.check_updates(manual=True, on_done=done)


def palette():
    s = Gtk.Settings.get_default()
    theme = (s.get_property('gtk-theme-name') or '').lower()
    dark = 'dark' in theme or s.get_property('gtk-application-prefer-dark-theme')
    probe = Gtk.Window()
    ok, acc = probe.get_style_context().lookup_color('theme_selected_bg_color')
    accent = (acc.red, acc.green, acc.blue, 1) if ok else ((0.3, 0.76, 1, 1) if dark else (0, 0.37, 0.72, 1))
    probe.destroy()
    if dark:
        return dict(dark=True, bg='#2c2c2c', border='rgba(255,255,255,0.09)', fgcss='#ffffff',
                    entry='rgba(255,255,255,0.06)', fg=(1, 1, 1, 1), dim=(1, 1, 1, 0.6),
                    hover=(1, 1, 1, 0.08), card=(1, 1, 1, 0.05), accent=accent,
                    hovercss='rgba(255,255,255,0.08)')
    return dict(dark=False, bg='#f9f9f9', border='rgba(0,0,0,0.12)', fgcss='#1b1b1b',
                entry='#ffffff', fg=(0.1, 0.1, 0.1, 1), dim=(0, 0, 0, 0.55),
                hover=(0, 0, 0, 0.055), card=(0, 0, 0, 0.035), accent=accent,
                hovercss='rgba(0,0,0,0.055)')


def install_css(p):
    a = 'rgb({},{},{})'.format(*[int(v * 255) for v in p['accent'][:3]])
    css = f"""
    #panel {{ background-color: {p['bg']}; border: 1px solid {p['border']}; border-radius: 8px;
              color: {p['fgcss']}; }}
    #panel .head {{ padding: 6px 6px 2px 6px; }}
    #panel button.tab, #panel button.cat {{ background: none; border: none; box-shadow: none;
              border-radius: 5px; min-height: 26px; min-width: 30px; padding: 3px 8px;
              color: {p['fgcss']}; }}
    #panel button.tab:hover, #panel button.cat:hover {{ background-color: {p['hovercss']}; }}
    #panel button.tab.active {{ background-color: {p['hovercss']}; box-shadow: inset 0 -3px {a}; }}
    #panel button.cat.active {{ box-shadow: inset 0 -3px {a}; }}
    #panel button.tab label {{ font-weight: bold; }}
    #panel entry.search {{ margin: 6px 10px 6px 10px; min-height: 32px; border-radius: 5px;
              background-color: {p['entry']}; color: {p['fgcss']}; border: 1px solid {p['border']};
              border-bottom: 2px solid {a}; box-shadow: none; }}
    #panel .catbar {{ border-top: 1px solid {p['border']}; padding: 3px 4px 4px 4px; }}
    #panel .tones {{ padding: 2px; border-top: 1px solid {p['border']}; }}
    #panel scrollbar {{ background: transparent; border: none; }}
    """
    prov = Gtk.CssProvider()
    prov.load_from_data(css.encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


if __name__ == '__main__':
    sys.exit(App().run(sys.argv))
