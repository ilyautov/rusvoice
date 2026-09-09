# -*- coding: utf-8 -*-
"""Проверка того, что реально прозвучало, — последнее звено цепочки.

Все остальные проверки пакета стоят ДО синтеза: они ловят то, что мы предусмотрели.
Модель может уронить слово уже после них, и тогда не сработает ни одна.

⚠️ Это не гипотеза. В движке такое было и стоило дорого: edge отдал 2.9 с на текст в
53 слова — в треке оказалось ДВА слова, — а единственной проверкой синтеза был размер
файла («>2000 байт»), и обрезок весил 17 КБ. Рендер пошёл дальше, сайдкар записался,
дефект нашёлся только ушами.

**Судим по доле, а не по совпадению слов.** ASR ошибается на каждом десятом слове —
на этом уже обжигались, когда брали текст субтитров из распознавания («КЛУБНИКА» →
«ЯЗЫКОВ НЕ КОГО»). Требовать дословного совпадения значит получить ложную тревогу на
каждой реплике с брендом. Поэтому вердикт — доля покрытия, и порог не назначен, а
откалиброван по прогону движка: здоровые сцены 0.955–0.983, обрезанная 0.038.

⚠️ На коротком тексте доля неинформативна: одна склейка ASR роняет её на треть. Ниже
порога длины не судим вовсе — молчание честнее уверенного числа.
"""
from __future__ import annotations

import re

MIN_WORDS = 12          # тот же порог, что у `tts_engine.TTS_COVERAGE_MIN_WORDS`
MIN_COVERAGE = 0.7      # тот же порог, что у `tts_engine.TTS_COVERAGE_MIN`


def _norm(w: str) -> str:
    return re.sub(r"[^0-9а-яёa-z]", "", str(w).lower())


def available() -> bool:
    """ASR где угодно: движок (whisperx) или свой faster_whisper — нам нужны только слова."""
    from rusvoice import engine as E
    if E.get("clipper") is not None:
        return True
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def check(path: str, text: str, *, lang: str = "ru") -> dict:
    """Сверить записанный wav с текстом, который в него отправляли.

    `text` — то, что ушло В СИНТЕЗ (после слоя произношения, до ударений): ASR услышит
    «Джемини», а не `Gemini`, и сверка с исходным текстом врала бы на каждом бренде."""
    want = [_norm(w) for w in str(text or "").split()]
    want = [w for w in want if w]
    if len(want) < MIN_WORDS:
        return {"judged": False,
                "detail": f"текст короче {MIN_WORDS} слов — доля покрытия на нём неинформативна"}
    if not available():
        return {"judged": False, "detail": "ASR недоступен — что прозвучало, не проверено"}
    try:
        from rusvoice import refaudio as R
        heard = [_norm(w.get("w") or w.get("word") or "") for w in R.words(path, lang=lang)]
    except Exception as exc:  # noqa: BLE001 — проверка не повод потерять готовый синтез
        return {"judged": False, "detail": f"ASR не отработал ({exc}) — не проверено"}
    heard = [w for w in heard if w]
    cov = len(heard) / len(want)
    out = {"judged": True, "coverage": cov, "words_text": len(want), "words_heard": len(heard),
           "ok": cov >= MIN_COVERAGE}
    if out["ok"]:
        out["detail"] = f"прозвучало {len(heard)} слов из {len(want)} ({cov:.2f})"
        return out
    # Список слов показываем ТОЛЬКО при провале: на здоровой реплике это был бы шум от
    # ослышек ASR, а здесь он единственная подсказка, где именно оборвалось.
    missing = [w for w in want if w not in set(heard)]
    out["missing"] = missing
    out["detail"] = (f"прозвучало {len(heard)} слов из {len(want)} ({cov:.2f}, порог "
                     f"{MIN_COVERAGE}) — не хватает: " + " ".join(missing[:8]) +
                     ("…" if len(missing) > 8 else ""))
    return out
