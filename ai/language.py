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

# The last line of a general answer, in each language (added when the model leaves it out).
DISCLAIMERS = {
    "en": "This is general information, not personal advice.",
    "ro": "Acestea sunt informații generale, nu sfaturi personalizate.",
    "es": "Esta es información general, no un consejo personal.",
    "fr": "Ceci est une information générale, pas un conseil personnalisé.",
    "it": "Queste sono informazioni generali, non consigli personali.",
    "de": "Dies sind allgemeine Informationen, keine persönliche Beratung.",
    "pt": "Esta é uma informação geral, não um conselho pessoal.",
    "pl": "To są informacje ogólne, a nie porada osobista.",
}
_HAS_DISCLAIMER = re.compile(
    r"general information|informa[țţt]i[ei]? general[ăe]|ghid general|"
    r"nu (?:este )?(?:un )?sfat(?:uri)? personal|información general|"
    r"information(s)? générale|informazioni generali|allgemeine Information|"
    r"informaç(ão|ões) gera|informacje ogólne",
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


# The English "general information" sentence, wherever the model puts it: on its own line, as
# a bullet, in brackets or right after the same sentence in another language.
_ENGLISH_LINE = re.compile(
    r"[ \t]*(?:[-*•][ \t]*)?\(?\bthis is (?:only |just )?general information,? (?:and )?"
    r"not (?:a )?(?:personal|personali[sz]ed|individual) advice\b\.?\)?",
    re.IGNORECASE,
)


def strip_english(text: str, code: str = "") -> str:
    """``text`` without the English "general information" sentence when the answer is in
    another language (``code``, else detected from the answer itself)."""
    if not _ENGLISH_LINE.search(text):
        return text
    rest = _ENGLISH_LINE.sub("", text)
    code = code or detect(rest)
    if not code or code == "en":
        return text
    rest = re.sub(r"[ \t]+\n", "\n", rest)
    return re.sub(r"\n{3,}", "\n\n", rest).strip()


def with_disclaimer(text: str, code: str) -> str:
    """``text`` ending with the "general information" line in its language, and only in it
    (an English copy under an answer in another language is removed)."""
    text = strip_english(text, code)
    code = code or detect(text)
    if not code or code not in DISCLAIMERS or _HAS_DISCLAIMER.search(text):
        return text
    return f"{text.rstrip()}\n\n{DISCLAIMERS[code]}"


def refer(code: str) -> str:
    return REFER.get(code, REFER["en"])
