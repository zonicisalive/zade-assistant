from zade.hinglish import to_latin


def test_hindi_reads_as_hinglish():
    assert to_latin("तुम स्क्रीन देखो और बताओ सबसे ज़्यादा वाउचेस किसके हैं") == \
        "tum skrin dekho aur batao sabse zyada vauches kiske hain"
    assert to_latin("स्पॉटिफाई बंद करो") == "spotifai band karo" and to_latin("हाँ भेज दो") == "han bhej do"
    assert to_latin("कमल करना, 10787।") == "kamal karna, 10787."
    assert to_latin("Discord खोलो") == "Discord kholo" and to_latin("open firefox") == "open firefox"
