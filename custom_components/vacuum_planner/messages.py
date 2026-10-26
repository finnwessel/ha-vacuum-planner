"""Push notification texts (German and English)."""

from __future__ import annotations

MESSAGES: dict[str, dict[str, str]] = {
    "de": {
        "prewarn_title": "🤖 Gleich wird geputzt",
        "prewarn_message": "In {minutes} Min: {rooms} · ca. {duration} Min, fertig gegen {finish}.{rest}",
        "prewarn_rest": " Danach noch: {rooms}.",
        "skip_today": "Heute nicht",
        "start_now": "Jetzt",
        "return_title": "🤖 Saugroboter läuft noch",
        "return_message": "Du bist gleich zuhause und er putzt noch ({rooms}). Soll er zur Station fahren? Ohne Antwort fährt er in 3 Minuten zurück.",
        "return": "Zur Station",
        "continue": "Weitermachen",
        "plan_b_title": "🤖 Heute noch putzen?",
        "plan_b_message": "Fällig: {rooms}. Jetzt starten? Sonst erledigt er es, wenn ihr das nächste Mal weg seid.",
        "plan_b_now": "Jetzt putzen",
        "plan_b_later": "Heute nicht",
        "error_title": "⚠️ Saugroboter braucht Hilfe",
        "error_message": "{error} – Akku {battery} %.{skipped}",
        "error_skipped": " {rooms} lasse ich heute aus.",
        "no_start_message": "Er ist nach zwei Versuchen nicht losgefahren ({rooms}).",
        "unknown_error": "Unbekannter Fehler",
    },
    "en": {
        "prewarn_title": "🤖 Cleaning starts soon",
        "prewarn_message": "In {minutes} min: {rooms} · about {duration} min, done around {finish}.{rest}",
        "prewarn_rest": " Then: {rooms}.",
        "skip_today": "Not today",
        "start_now": "Now",
        "return_title": "🤖 Vacuum is still cleaning",
        "return_message": "You are almost home and it is still cleaning ({rooms}). Send it back to the dock? Without an answer it returns in 3 minutes.",
        "return": "Back to dock",
        "continue": "Keep going",
        "plan_b_title": "🤖 Clean today?",
        "plan_b_message": "Due: {rooms}. Start now? Otherwise it runs the next time you are away.",
        "plan_b_now": "Clean now",
        "plan_b_later": "Not today",
        "error_title": "⚠️ Vacuum needs help",
        "error_message": "{error} – battery {battery} %.{skipped}",
        "error_skipped": " Skipping {rooms} for today.",
        "no_start_message": "It did not start after two attempts ({rooms}).",
        "unknown_error": "Unknown error",
    },
}


def text(language: str, key: str, **kwargs: object) -> str:
    table = MESSAGES.get((language or "en").split("-")[0], MESSAGES["en"])
    return table.get(key, MESSAGES["en"][key]).format(**kwargs)


def join_names(names: list[str], language: str) -> str:
    if len(names) <= 1:
        return "".join(names)
    word = " und " if (language or "").startswith("de") else " and "
    return ", ".join(names[:-1]) + word + names[-1]
