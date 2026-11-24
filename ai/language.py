"""The language of a short question, so the AI answers in it (and only in it).

Small models answer short questions about English facts in English (or another language
entirely). Telling them the language explicitly fixes that. Detection is a light word and
letter count over the languages our users write in; when unsure it returns "" and the model
follows the question on its own.
"""

from __future__ import annotations

import re

# code: (name for the prompt, markers)
LANGUAGES: dict[str, tuple[str, re.Pattern]] = {
    "ro": (
        "Romanian",
        re.compile(
            r"[ăâîșşțţ]|\b(este|sunt|și|si|sau|pentru|care|ce|câte|câtă|cât|cum|pot|"
            r"poate|mai|despre|bun|bune|buni|zahăr|zahar|grăsimi|sare|mănânc|mananc|conține|"
            r"contine|produs|produse|alimente|la|proteine|ingrediente|lapte|sănătos|sanatos|dacă|daca)\b",
            re.IGNORECASE,
        ),
    ),
    "es": (
        "Spanish",
        re.compile(
            r"[ñ¿¡]|\b(qué|cuánto|cuánta|cuántas|tiene|es|el|los|las|para|azúcar|mucha|mucho|"
            r"puedo|comer)\b",
            re.IGNORECASE,
        ),
    ),
    "fr": (
        "French",
        re.compile(
            r"[çœ]|\b(est-ce|c'est|qu'est|combien|les|des|pour|avec|très|sucre|sel|que|une)\b",
            re.IGNORECASE,
        ),
    ),
    "it": (
        "Italian",
        re.compile(
            r"\b(è|quanto|quanti|zucchero|sale|gli|della|degli|sono|posso|mangiare|questo)\b",
            re.IGNORECASE,
        ),
    ),
    "de": (
        "German",
        re.compile(r"[äöüß]|\b(ist|das|der|die|und|wie|viel|zucker|nicht|kann|ich)\b", re.I),
    ),
    "pt": (
        "Portuguese",
        re.compile(r"[ãõ]|\b(quanto|açúcar|tem|isso|posso|comer|não|são)\b", re.IGNORECASE),
    ),
    "pl": (
        "Polish",
        re.compile(r"[ąęłńśźż]|\b(czy|jest|ile|ma|mogę|cukru|zawiera)\b", re.IGNORECASE),
    ),
    "en": (
        "British English",
        re.compile(
            r"\b(the|is|it|are|what|how|much|many|does|do|can|my|this|should|good|for|of|in|"
            r"and|eat|any)\b",
            re.IGNORECASE,
        ),
    ),
}

# "This is general information, not personal advice" in the app's languages: never shown under
# an answer (no reminder each time; see strip_disclaimer).
_DISCLAIMER = (
    r"general information|not (?:a )?(?:personal|personali[sz]ed|individual) advice|"
    r"informa[țţt]i[ei]? general[ăe]|ghid general|"
    r"nu (?:este |sunt )?(?:un |o )?sfat(?:uri)? personal|información general|"
    r"information(?:s)? générale|informazioni generali|allgemeine Information|"
    r"informaç(?:ão|ões) gera|informacje ogólne"
)
_HAS_DISCLAIMER = re.compile(_DISCLAIMER, re.IGNORECASE)
# The whole sentence (or bullet line) holding it, with an optional "Note:" in front.
_DISCLAIMER_SENTENCE = re.compile(
    r"[ \t]*(?:[-*•][ \t]*)?\(?(?:\*\*)?(?:note|notă|nota|remarque|hinweis|uwaga)?:?(?:\*\*)?"
    r"[^.!?\n]*?(?:" + _DISCLAIMER + r")[^.!?\n]*[.!?]?\)?",
    re.IGNORECASE,
)

# Shown instead of a general answer the safety check stopped (amounts, doses, targets).
REFER = {
    "en": (
        "I can't give amounts, doses or targets for you. A GP, pharmacist or registered "
        "dietitian can help, and the NHS website has general guidance."
    ),
    "ro": (
        "Nu pot da cantități, doze sau ținte pentru tine. Un medic de familie, un farmacist sau "
        "un dietetician te pot ajuta, iar site-ul NHS are informații generale."
    ),
}


def detect(text: str) -> str:
    """The language code of ``text``, or "" when unsure (no marker, or a tie)."""
    scores = {code: len(p.findall(text or "")) for code, (_, p) in LANGUAGES.items()}
    best = max(scores.values())
    if not best:
        return ""
    top = [code for code, n in scores.items() if n == best]
    if len(top) > 1:
        # English words also appear in other questions ("Is it vegan?"): a non-English
        # language that ties with English wins only through its own letters.
        top = [c for c in top if c != "en"] if len(top) == 2 and "en" in top else top
    return top[0] if len(top) == 1 else ""


def name(code: str) -> str:
    return LANGUAGES[code][0] if code in LANGUAGES else ""


def strip_disclaimer(text: str) -> str:
    """``text`` without any "this is general information, not personal advice" sentence, in
    any of the app's languages, wherever the model put it (own line, bullet, brackets,
    "Note: …")."""
    if not _HAS_DISCLAIMER.search(text):
        return text
    rest = _DISCLAIMER_SENTENCE.sub("", text)
    rest = re.sub(r"[ \t]+\n", "\n", rest)
    rest = re.sub(r"\n{3,}", "\n\n", rest).strip()
    return rest or text


def refer(code: str) -> str:
    return REFER.get(code, REFER["en"])


# Put above a rule answer that stays in English although the question was in another language.
# "busy": the AI is off, out of messages or failed (a passing problem); "consent": the answer
# names the user's diets or allergies, which go to the AI only with the switch "Use my diets
# and allergies in AI answers" on; "private": notes, memories, lists or scans, sent only with
# "Let Ask Foodo use my diary, lists, notes and memories" on.
ENGLISH_ONLY = {
    "busy": {
        "ro": "Momentan nu pot traduce acest răspuns, așa că este în engleză.",
        "es": "Ahora mismo no puedo traducir esta respuesta, así que está en inglés.",
        "fr": "Je ne peux pas traduire cette réponse pour le moment, elle est donc en anglais.",
        "it": "Al momento non posso tradurre questa risposta, quindi è in inglese.",
        "de": "Ich kann diese Antwort gerade nicht übersetzen, daher ist sie auf Englisch.",
        "pt": "De momento não consigo traduzir esta resposta, por isso está em inglês.",
        "pl": "Nie mogę teraz przetłumaczyć tej odpowiedzi, więc jest po angielsku.",
    },
    "consent": {
        "ro": (
            "Acest răspuns conține datele tale, așa că este în engleză. Pentru răspunsuri în "
            "limba ta, activează „Use my diets and allergies in AI answers” în Settings."
        ),
        "es": (
            "Esta respuesta contiene tus datos, así que está en inglés. Para recibirla en tu "
            "idioma, activa «Use my diets and allergies in AI answers» en Settings."
        ),
        "fr": (
            "Cette réponse contient vos données, elle est donc en anglais. Pour l'avoir dans "
            "votre langue, activez « Use my diets and allergies in AI answers » dans Settings."
        ),
        "it": (
            "Questa risposta contiene i tuoi dati, quindi è in inglese. Per averla nella tua "
            "lingua, attiva “Use my diets and allergies in AI answers” in Settings."
        ),
        "de": (
            "Diese Antwort enthält deine Daten, daher ist sie auf Englisch. Für Antworten in "
            "deiner Sprache aktiviere „Use my diets and allergies in AI answers“ in Settings."
        ),
        "pt": (
            "Esta resposta contém os teus dados, por isso está em inglês. Para a receberes na "
            "tua língua, ativa “Use my diets and allergies in AI answers” em Settings."
        ),
        "pl": (
            "Ta odpowiedź zawiera Twoje dane, więc jest po angielsku. Aby dostawać odpowiedzi "
            "w swoim języku, włącz „Use my diets and allergies in AI answers” w Settings."
        ),
    },
}


ENGLISH_ONLY["private"] = {
    "ro": (
        "Acest răspuns conține notițele, listele sau istoricul tău, așa că este în engleză. "
        "Pentru răspunsuri în limba ta, activează „Let Ask Foodo use my diary, lists, notes and "
        "memories” în Settings."
    ),
    "es": (
        "Esta respuesta contiene tus notas, listas o historial, así que está en inglés. Para "
        "recibirla en tu idioma, activa «Let Ask Foodo use my diary, lists, notes and memories» "
        "en Settings."
    ),
    "fr": (
        "Cette réponse contient vos notes, listes ou historique, elle est donc en anglais. Pour "
        "l'avoir dans votre langue, activez « Let Ask Foodo use my diary, lists, notes and "
        "memories » dans Settings."
    ),
    "it": (
        "Questa risposta contiene le tue note, liste o cronologia, quindi è in inglese. Per "
        "averla nella tua lingua, attiva “Let Ask Foodo use my diary, lists, notes and memories” "
        "in Settings."
    ),
    "de": (
        "Diese Antwort enthält deine Notizen, Listen oder deinen Verlauf, daher ist sie auf "
        "Englisch. Für Antworten in deiner Sprache aktiviere „Let Ask Foodo use my diary, lists, "
        "notes and memories“ in Settings."
    ),
    "pt": (
        "Esta resposta contém as tuas notas, listas ou histórico, por isso está em inglês. Para "
        "a receberes na tua língua, ativa “Let Ask Foodo use my diary, lists, notes and "
        "memories” em Settings."
    ),
    "pl": (
        "Ta odpowiedź zawiera Twoje notatki, listy lub historię, więc jest po angielsku. Aby "
        "dostawać odpowiedzi w swoim języku, włącz „Let Ask Foodo use my diary, lists, notes "
        "and memories” w Settings."
    ),
}


def english_only(text: str, code: str, why: str = "busy") -> str:
    """``text`` with a one-line note in the question's language saying why it is in English."""
    note = ENGLISH_ONLY.get(why, {}).get(code)
    return f"{note}\n\n{text}" if note else text


# ---------- Which language an answer is written in (one rule for the whole chat) ----------
# Words naming a language, in the app's languages -> code ("in English", "în română"...).
_NAMES = {
    "en": r"english|engleză|engleza|anglais|inglés|ingles|englisch|inglese|inglês|angielsku",
    "ro": r"romanian|română|romana|românește|romaneste|roumain|rumano|rumänisch|rumeno|romeno|rumuńsku",  # noqa: E501
    "es": r"spanish|spaniolă|spaniola|espagnol|español|espanol|spanisch|spagnolo|espanhol|hiszpańsku",  # noqa: E501
    "fr": r"french|franceză|franceza|français|francais|francés|frances|französisch|francese|francês|francusku",  # noqa: E501
    "it": r"italian|italiană|italiana|italien|italiano|italienisch|włosku",
    "de": r"german|germană|germana|allemand|alemán|aleman|deutsch|tedesco|alemão|niemiecku",
    "pt": r"portuguese|portugheză|portugheza|portugais|portugués|portugiesisch|portoghese|português|portugalsku",  # noqa: E501
    "pl": r"polish|poloneză|poloneza|polonais|polaco|polnisch|polacco|polonês|polsku",
}
_EXPLICIT = re.compile(
    r"(?:\b(?:in|în|en|auf|em|po|na)\s+(?:limba\s+)?)(" + "|".join(_NAMES.values()) + r")\b",
    re.IGNORECASE,
)
_CODE_OF = {word: code for code, words in _NAMES.items() for word in words.split("|")}


def explicit(text: str) -> str:
    """The language the message asks for ("răspunde-mi în engleză", "in English please",
    "en français"), or ""."""
    m = _EXPLICIT.search(text or "")
    return _CODE_OF.get(m.group(1).lower(), "") if m else ""


def choose(text: str, *, previous: dict | None = None, preference: str = "",
           interface: str = "en") -> tuple[str, str]:  # fmt: skip
    """``(code, reason)``: the language of the answer, by one rule for every kind of answer.

    1. "explicit": the message asks for a language; that choice is kept for the rest of the
       conversation ("explicit-kept") until another explicit request;
    2. "question": the language of the current message, when it is clear;
    3. "conversation": a short or ambiguous message ("200 g?") continues in the language of
       the previous answer of the same chat (``previous``: its ``details``);
    4. "account": the language chosen in Settings (``Profile.answer_language``);
    5. "interface": the language of the interface.
    """
    asked = explicit(text)
    if asked:
        return asked, "explicit"
    previous = previous or {}
    if previous.get("lang") and previous.get("lang_reason") in ("explicit", "explicit-kept"):
        return previous["lang"], "explicit-kept"
    found = detect(text)
    if found:
        return found, "question"
    if previous.get("lang"):
        return previous["lang"], "conversation"
    if preference in LANGUAGES:
        return preference, "account"
    return (interface if interface in LANGUAGES else "en"), "interface"
