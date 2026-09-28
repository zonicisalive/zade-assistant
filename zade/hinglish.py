"""Hindi in Devanagari -> Hinglish in English letters, the way people type it: "तुम स्क्रीन देखो" -> "tum
skrin dekho". Speech recognition writes Hindi in Devanagari; Zade's phrases ("band karo"), the overlay and
yes/no answers work in Latin letters.

Each consonant carries an "a" unless a vowel sign or virama follows; Hindi drops that "a" at the end of a word
and between a vowel-consonant and a consonant-vowel ("sabse", not "sabase"), which is applied right to left.
"""

import re

CONSONANTS = dict(zip("कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसह",
                      "k kh g gh n ch chh j jh n t th d dh n t th d dh n p f b bh m y r l v sh sh s h".split()))
NUKTA = {"क": "q", "ख": "kh", "ग": "gh", "ज": "z", "फ": "f", "ड": "r", "ढ": "rh"}  # क़ ज़ फ़ ड़ ...
VOWELS = dict(zip("अआइईउऊऋएऐओऔऑ", "a aa i i u u ri e ai o au o".split()))
SIGNS = dict(zip("ािीुूृेैोौॉॅ", "a i i u u ri e ai o au o e".split()))
DIGITS = dict(zip("०१२३४५६७८९", "0123456789"))
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _word(w):
    units = []  # [consonant sound, vowel sound or None for none, is inherent "a"]
    i = 0
    while i < len(w):
        c = w[i]
        if c in CONSONANTS:
            sound = CONSONANTS[c]
            if i + 1 < len(w) and w[i + 1] == "़":  # nukta
                sound, i = NUKTA.get(c, sound), i + 1
            units.append([sound, "a", True])
        elif c in SIGNS and units:
            units[-1][1:] = [SIGNS[c], False]
        elif c == "्" and units:  # virama: no vowel
            units[-1][1:] = [None, False]
        elif c in VOWELS:
            units.append(["", VOWELS[c], False])
        elif c in "ँं" and units:  # candrabindu, anusvara: nasal
            units[-1][1] = (units[-1][1] or "") + "n"
            units[-1][2] = False
        elif c == "ः" and units:  # visarga
            units[-1][1] = (units[-1][1] or "") + "h"
        else:
            units.append([DIGITS.get(c, c), None, False])
        i += 1
    if len(units) > 1 and units[-1][2]:
        units[-1][1:] = [None, False]
    for j in range(len(units) - 2, 0, -1):
        if units[j][2] and units[j - 1][1] and units[j + 1][0] and units[j + 1][1]:
            units[j][1:] = [None, False]
    return "".join(c + (v or "") for c, v, _ in units)


def to_latin(text):
    """Text with every Devanagari word in English letters; anything else (English, emoji) as it was."""
    if not DEVANAGARI.search(text or ""):
        return text
    text = text.replace("।", ".").replace("॥", ".")  # danda
    return re.sub(r"[ऀ-ॿ]+", lambda m: _word(m[0]), text)
