"""Emoji-, Kaomoji- und Symboldaten."""
import re
import unicodedata

import gi
gi.require_version('IBus', '1.0')
from gi.repository import IBus

DICTS = '/usr/share/ibus/dicts/emoji-{}.dict'
TONES = ['', '\U0001F3FB', '\U0001F3FC', '\U0001F3FD', '\U0001F3FE', '\U0001F3FF']
TONE_SET = set(TONES[1:])
TONE_NAMES = ['Standard', 'Hell', 'Mittelhell', 'Mittel', 'Mitteldunkel', 'Dunkel']

# Kategorien wie im Windows-Panel (Schlüssel, Titel, Symbol)
CATEGORIES = [
    ('smileys', 'Smileys & Tiere', 'emoji-people-symbolic'),
    ('people', 'Personen', 'emoji-body-symbolic'),
    ('celebr', 'Feiern & Objekte', 'emoji-activities-symbolic'),
    ('food', 'Essen & Pflanzen', 'emoji-food-symbolic'),
    ('travel', 'Verkehr & Orte', 'emoji-travel-symbolic'),
    ('symbols', 'Symbole', 'emoji-symbols-symbolic'),
]
_MAP = {
    'Smileys & Emotion': 'smileys', 'People & Body': 'people', 'Activities': 'celebr',
    'Objects': 'celebr', 'Food & Drink': 'food', 'Travel & Places': 'travel',
    'Symbols': 'symbols', 'Flags': 'symbols',
}
POPULAR = '😂 ❤️ 👍 😊 🙏 😍 😭 🎉 🔥 😁 🤔 👏 😅 🙂 😉 😢 😎 💪 ✅ 🚀'.split()


def norm(e):
    return e.replace('️', '')


def ss(text):
    """Schweizer Schreibweise: ß → ss (Anzeige und Suche)."""
    return text.replace('ß', 'ss').replace('ẞ', 'SS')


def fold(text):
    return ss(text.lower())


class Emoji:
    __slots__ = ('char', 'name', 'cat', 'kw')

    def __init__(self, char, name, cat, kw):
        self.char, self.name, self.cat, self.kw = char, name, cat, kw


class EmojiData:
    def __init__(self):
        self.items = []
        self.by_key = {}
        self.variants = {}  # norm(basis) -> {ton: emoji}
        try:
            de = {norm(x.get_emoji()): x for x in IBus.EmojiData.load(DICTS.format('de'))}
        except Exception:
            de = {}
        plants = False
        for x in IBus.EmojiData.load(DICTS.format('en')):
            e, cat = x.get_emoji(), x.get_category()
            if cat not in _MAP and cat != 'Animals & Nature':
                continue
            key = norm(e)
            mods = [c for c in e if c in TONE_SET]
            if mods:
                if len(set(mods)) == 1:
                    base = norm(''.join(c for c in e if c not in TONE_SET))
                    self.variants.setdefault(base, {}).setdefault(mods[0], e)
                continue
            if key in self.by_key:
                continue
            if cat == 'Animals & Nature':
                if e == '💐':
                    plants = True
                group = 'food' if plants else 'smileys'
            else:
                group = _MAP[cat]
            d = de.get(key)
            name = d.get_description() if d else x.get_description()
            name = ss(re.sub(r'^E\d+(\.\d+)? ', '', name))   # «E15.1 …»-Platzhalter kürzen
            kw = {fold(a) for a in x.get_annotations()}
            kw.add(fold(x.get_description()))
            if d:
                kw.update(fold(a) for a in d.get_annotations())
                kw.add(fold(d.get_description()))
            kw.add(fold(name))
            it = Emoji(e, name[:1].upper() + name[1:], group, tuple(kw))
            self.items.append(it)
            self.by_key[key] = it

    def toned(self, e, tone):
        if tone:
            v = self.variants.get(norm(e))
            if v and tone in v:
                return v[tone]
        return e

    def get(self, e):
        return self.by_key.get(norm(e))

    def search(self, q):
        q = fold(q).strip()
        terms = q.split()
        if not terms:
            return []
        res = []
        for i, it in enumerate(self.items):
            if not all(any(t in k for k in it.kw) for t in terms):
                continue
            n = fold(it.name)
            if n == q:
                s = 0
            elif q in n.split():          # «herz» → «Rotes Herz» vor «Hochzeit»
                s = 1
            elif n.startswith(q):
                s = 2
            elif q in it.kw:
                s = 3
            elif q in n:
                s = 4
            elif any(k.startswith(terms[0]) for k in it.kw):
                s = 5
            else:
                s = 6
            res.append((s, len(n), i, it))
        res.sort(key=lambda r: r[:3])
        return [r[3] for r in res]


# Kaomoji: (Kategorie, Suchbegriffe, Einträge)
KAOMOJI = [
    ('Klassische Emoticons', 'classic ascii smiley', [
        ':-)', ':-(', ';-)', ':-D', ':-P', ':-O', ':-|', ':-/', ":'-(", 'XD', '<3', '</3',
        '^_^', '^^', '-_-', 'o_O', 'O_o', '>_<', 'T_T', ':-*', 'B-)', '¯\\_(ツ)_/¯']),
    ('Begrüssung', 'hallo hi hello winken greeting bye tschüss', [
        '(*・ω・)ﾉ', '(￣▽￣)ノ', '(°▽°)/', '( ´ ∀ ` )ﾉ', '(^-^*)/', '(＠´ー`)ﾉﾞ', '(´• ω •`)ﾉ',
        '(o´▽`o)ﾉ', '(⌒▽⌒)ﾉ', 'ヾ(・ω・*)', 'ヾ(☆▽☆)', '(≧▽≦)/', '(*^▽^*)ゞ', 'ヽ(・∀・)ﾉ']),
    ('Fröhlich', 'freude happy glücklich lachen joy', [
        '(* ^ ω ^)', '(´ ∀ ` *)', '٩(◕‿◕｡)۶', '☆*:.｡.o(≧▽≦)o.｡.:*☆', '(o^▽^o)', '(⌒▽⌒)☆',
        '<(￣︶￣)>', 'ヽ(・∀・)ﾉ', '(´｡• ω •｡`)', '(￣ω￣)', '(o･ω･o)', '(＾▽＾)', '(✯◡✯)',
        '(◕‿◕)', '(*¯︶¯*)', 'ヽ(>∀<☆)ノ', '＼(≧▽≦)／', 'ヽ(o＾▽＾o)ノ', '(*≧ω≦*)', '(〃＾▽＾〃)']),
    ('Liebe', 'liebe love herz verliebt kuss', [
        '(ﾉ´ з `)ノ', '(♡μ_μ)', '(*^^*)♡', '☆⌒ヽ(*\'､^*)chu', '(♡-_-♡)', '(￣ε￣＠)',
        'ヽ(♡‿♡)ノ', '( ´ ∀ `)ノ～ ♡', '(─‿‿─)♡', '(´｡• ᵕ •｡`) ♡', '(*♡∀♡)', '(灬♥ω♥灬)',
        '(っ˘з(˘⌣˘ ) ♡', '(≧◡≦) ♡', '♡( ◡‿◡ )', '(´ ω `♡)']),
    ('Verlegen', 'verlegen schüchtern embarrassed rot', [
        '(⌒_⌒;)', '(o^ ^o)', '(*/ω＼)', '(*/。＼)', '(//ω//)', '(„ಡωಡ„)', '(*μ_μ)',
        '(*ﾉωﾉ)', '(〃▽〃)', '(⁄ ⁄•⁄ω⁄•⁄ ⁄)', '(*^.^*)']),
    ('Traurig', 'traurig sad weinen cry', [
        '(ノ_<。)', '(-_-)', '(´-ω-`)', '.･ﾟﾟ･(／ω＼)･ﾟﾟ･.', '(μ_μ)', '(ﾉД`)', '(-ω-、)',
        '。゜゜(´Ｏ`) ゜゜。', 'o(TヘTo)', '( ; ω ; )', '(｡╯︵╰｡)', '(っ˘̩╭╮˘̩)っ', '(╥_╥)',
        '(╥﹏╥)', '(ಥ﹏ಥ)', '(ಥ_ಥ)']),
    ('Wütend', 'wütend angry ärger böse', [
        '(＃`Д´)', '(`皿´＃)', '( ` ω ´ )', 'ヽ( `д´*)ノ', '(・`ω´・)', '(`ー´)', 'ヽ(`⌒´メ)ノ',
        '凸(`△´＃)', '(╬`益´)', '(ノಠ益ಠ)ノ', '(╯°□°)╯︵ ┻━┻', '┬─┬ノ( º _ ºノ)', '(ꐦ°᷄д°᷅)']),
    ('Überrascht', 'überrascht surprised schock staunen', [
        'w(°ｏ°)w', 'ヽ(°〇°)ﾉ', 'Σ(O_O)', 'Σ(°ロ°)', '(⊙_⊙)', '(o_O)', '(O.O)', '(°ロ°) !',
        '(□_□)', 'Σ(□_□)', '∑(O_O;)', '( : ౦ ‸ ౦ : )']),
    ('Verwirrt', 'verwirrt confused unsicher', [
        '(￣ω￣;)', 'σ(￣、￣〃)', '(￣～￣;)', '(-_-;)・・・', '┐(\'～`;)┌', '(・_・ヾ', '(〃￣ω￣〃ゞ',
        '┐(￣ヘ￣;)┌', '(・_・;)', '(￣_￣)・・・', '╮(￣ω￣;)╭', '(¯ . ¯;)', '(＠_＠)', '(•ิ_•ิ)?']),
    ('Achselzucken', 'egal shrug keine ahnung achselzucken', [
        '¯\\_(ツ)_/¯', '┐(￣ヘ￣)┌', 'ヽ(ー_ー )ノ', 'ヽ(´ー` )┌', '┐(︶▽︶)┌', '╮(︶︿︶)╭',
        '┐( ˘ ､ ˘ )┌', 'ᕕ( ᐛ )ᕗ', '┐(´～`)┌']),
    ('Tiere', 'tiere tier animal katze hund bär', [
        '(=^･ω･^=)', '(=^‥^=)', 'ฅ(•ㅅ•❀)ฅ', '∪＾ェ＾∪', 'U^ｪ^U', '(ᵔᴥᵔ)', 'ʕ •ᴥ• ʔ',
        'ʕ ᵔᴥᵔ ʔ', '(・⊝・)', '>゜))))彡', '<コ:彡', 'くコ:彡', '/(=✪ x ✪=)\\', 'ᘛ⁐̤ᕐᐷ']),
    ('Sonstiges', 'sonstige misc tanzen dance schlafen sleep', [
        '(｡•̀ᴗ-)✧', '(•̀ᴗ•́)و ̑̑', 'ᕦ(ò_óˇ)ᕤ', '( •_•)>⌐■-■', '(⌐■_■)', '(ง •̀_•́)ง',
        '(￣o￣) zzZZzzZZ', '(－_－) zzZ', '(∪｡∪)｡｡｡zzZ', '♪(┌・。・)┌', '└(￣-￣└))',
        '( ˘ ³˘)♥', '(っ´ω`)ﾉ(╥ω╥)', '( ͡° ͜ʖ ͡°)', 'ʕノ•ᴥ•ʔノ ︵ ┻━┻']),
]


_SYMBOL_RANGES = [
    ('Allgemeine Interpunktion', [(0xA1, 0xBF), (0x2010, 0x205E), (0x2E2E, 0x2E2E)], 'satzzeichen'),
    ('Währungssymbole', [(0x24, 0x24), (0xA2, 0xA5), (0x20A0, 0x20C0)], 'währung geld'),
    ('Lateinische Symbole', [(0xC0, 0xFF), (0x100, 0x17F)], 'buchstabe akzent'),
    ('Geometrische Symbole', [(0x25A0, 0x25FF), (0x2190, 0x21FF)], 'form geometrie'),
    ('Mathematische Symbole', [(0xB1, 0xB1), (0xD7, 0xD7), (0xF7, 0xF7), (0x2070, 0x209C),
                               (0x2150, 0x215F), (0x2200, 0x22FF)], 'mathe mathematik'),
    ('Zusatzsymbole', [(0x2100, 0x214F), (0x2600, 0x26FF), (0x2700, 0x27BF)], 'symbol zeichen'),
    ('Sprachsymbole', [(0x391, 0x3A9), (0x3B1, 0x3C9), (0x410, 0x44F)], 'sprache griechisch kyrillisch'),
]
# Zeichen, die in mehreren Bereichen liegen, nur einmal zeigen – hier:
_PREFER = {'Währungssymbole': [(0xA2, 0xA5)],
           'Mathematische Symbole': [(0xB1, 0xB1), (0xD7, 0xD7), (0xF7, 0xF7), (0xB2, 0xB3), (0xB9, 0xB9),
                                     (0xBC, 0xBE)]}
# Deutsche Suchbegriffe für häufige Zeichen (erstes Wort erscheint im Namen)
_DE = {'€': 'euro', '£': 'pfund', '¥': 'yen', '¢': 'cent', '$': 'dollar', '°': 'grad', '©': 'copyright',
       '®': 'eingetragen marke', '™': 'marke trademark', '§': 'paragraf paragraph', '¶': 'absatz',
       '±': 'plusminus plus minus', '×': 'mal multiplikation', '÷': 'geteilt division',
       '…': 'auslassungspunkte punkte', '–': 'gedankenstrich', '—': 'geviertstrich',
       '«': 'anführungszeichen guillemet', '»': 'anführungszeichen guillemet', '„': 'anführungszeichen',
       '“': 'anführungszeichen', '”': 'anführungszeichen', '‰': 'promille', '√': 'wurzel',
       '∞': 'unendlich', '≈': 'ungefähr', '≠': 'ungleich', '≤': 'kleiner-gleich', '≥': 'grösser-gleich',
       '½': 'halb', '¼': 'viertel', '¾': 'dreiviertel', '✓': 'haken', '✔': 'haken', '✗': 'kreuz',
       '✘': 'kreuz', '★': 'stern', '☆': 'stern', '♥': 'herz', '☀': 'sonne', '☎': 'telefon',
       '✉': 'brief', '☺': 'smiley lächeln', '•': 'aufzählungspunkt punkt', '†': 'kreuz gestorben'}


def symbols():
    """[(Kategorie, [(zeichen, name, suchtext)])]"""
    home = {chr(cp): title for title, spans in _PREFER.items() for a, b in spans for cp in range(a, b + 1)}
    out = []
    for title, ranges, group_kw in _SYMBOL_RANGES:
        seen, items = set(), []
        for a, b in ranges:
            for cp in range(a, b + 1):
                c = chr(cp)
                name = unicodedata.name(c, None)
                if not name or c in seen or home.get(c, title) != title or \
                        unicodedata.category(c) in ('Mn', 'Me', 'Cf', 'Cc', 'Zs', 'Zl', 'Zp'):
                    continue
                seen.add(c)
                extra = _DE.get(c, '')
                if 'ARROW' in name:
                    extra += ' pfeil'
                label = name.capitalize() + (f' ({extra.split()[0]})' if c in _DE else '')
                items.append((c, label, fold(f'{name} {extra} {group_kw}')))
        out.append((title, items))
    return out


def symbol_rank(name, hay, terms):
    """Ganze Wörter vor Wortteilen, dann kürzere Namen («euro» → € vor ₠)."""
    words = set(hay.replace('-', ' ').replace('(', ' ').replace(')', ' ').split())
    return (0 if all(t in words for t in terms) else 1, len(name))
