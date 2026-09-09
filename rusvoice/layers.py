# -*- coding: utf-8 -*-
"""Текстовый слой русской озвучки — ПРОСЛЕЖИВАЕМЫЙ.

Зачем отдельный модуль, если `pipeline/pronounce.py` и `pipeline/accentize.py` уже есть.
Они отдают только итоговую строку, и это ровно та слепота, на которой мы теряли дни:
правка словаря «не доезжала» до звука, акцентизатор молча пропускал текст как есть в том
окружении, где его нет, а метка ударения на односложном слове давала нажим вместо ударения —
и всё это выяснялось СЛУШАНИЕМ выхода, то есть гаданием.

Здесь те же самые слои вызываются по одному и сравниваются до/после, поэтому видно не только
ЧТО получилось, но и КТО это сделал. Словари не форкаются: источник правды остаётся один —
`pipeline/*.json`.

⚠️ Инвариант: результат `apply()` обязан совпадать с `pronounce_ru` + `accentize_ru`,
вызванными обычным путём. Прослеживаемость не имеет права менять звук — иначе объяснение
будет про другой текст, чем синтез. На это стоит тест.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIPE = os.path.join(_ROOT, "pipeline")
if _PIPE not in sys.path:
    sys.path.insert(0, _PIPE)


@dataclass
class Change:
    """Одна правка: чей слой, что было, что стало."""
    layer: str
    before: str
    after: str

    def __str__(self) -> str:
        return f"{self.before} → {self.after}"


@dataclass
class Trace:
    source: str
    text: str
    changes: list[Change] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def by_layer(self) -> dict:
        out: dict = {}
        for c in self.changes:
            out.setdefault(c.layer, []).append(c)
        return out


# Порядок обязан повторять `pronounce_ru` — иначе объяснение врёт. Ключи `env` описывают
# байпас слоя, `opt` — что слой ВЫКЛЮЧЕН по умолчанию и включается ключом.
_LAYERS = [
    ("единицы", "_apply_units", None, None),
    ("бренды", "_apply_brands", None, None),
    ("множители", "_apply_multipliers", None, None),
    ("числа словами", "_apply_bare_numbers", "PRONOUNCE_NUMBERS", "1"),
    ("ударение-переписью", "_apply_stress", None, None),
    ("твёрдая э", "_apply_hard_e", None, None),
    ("аббревиатуры", "_spell_ru_abbr", None, None),
    ("транслит", "_translit_rest", None, None),
]

_WORD = re.compile(r"[^\s]+")
_VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"


def _words(s: str) -> list[str]:
    return _WORD.findall(s or "")


_GONE = "∅"


def _diff(layer: str, before: str, after: str) -> list[Change]:
    """Пословный разбор.

    Число слов слой менять умеет («5x» → «пять раз»), и тогда пословная пара врёт. Но
    показывать вместо неё ВСЮ строку — врать иначе: выглядит так, будто слой переписал
    фразу целиком, хотя тронул один токен. Поэтому совпадающие края отрезаем и оставляем
    ровно тот участок, который разошёлся.
    """
    wb, wa = _words(before), _words(after)
    if before == after:
        return []
    if len(wb) == len(wa):
        return [Change(layer, b, a) for b, a in zip(wb, wa) if b != a]
    i = 0
    while i < min(len(wb), len(wa)) and wb[i] == wa[i]:
        i += 1
    j = 0
    while j < min(len(wb), len(wa)) - i and wb[-1 - j] == wa[-1 - j]:
        j += 1
    b = " ".join(wb[i:len(wb) - j]) or _GONE      # пустая сторона = вставка или выпадение
    a = " ".join(wa[i:len(wa) - j]) or _GONE
    return [Change(layer, b, a)]


def _syllables(word: str) -> int:
    return sum(1 for ch in word if ch in _VOWELS)


def stress_warnings(text: str) -> list[str]:
    """⭐⭐ Метка на ОДНОСЛОЖНОМ слове. F5 отыгрывает «+» как НАЖИМ, а не как ударение:
    на слове с одной гласной ударение и так однозначно, и нажим слышен как «нейрослоп».
    Ловится только глазами по размеченному тексту — в звуке это списывают на модель."""
    out = []
    for w in _words(text):
        if "+" not in w:
            continue
        bare = w.replace("+", "")
        if _syllables(bare) <= 1:
            out.append(f"метка на односложном «{bare}» → F5 отыграет её как нажим")
        if w.count("+") > 1:
            out.append(f"две метки в одном слове «{bare}» — ударение может поехать")
    return out


def _pronounce_trace(text: str, lang: str = "ru") -> Trace:
    from rusvoice import pronounce as P

    tr = Trace(source=text, text=text)
    if not text or os.environ.get("PRONOUNCE", "1") == "0":
        tr.skipped.append("произношение целиком (PRONOUNCE=0)")
        return tr
    if lang is not None and not str(lang).strip().lower().startswith("ru"):
        tr.skipped.append(f"произношение: язык {lang!r}, слой только для русского")
        return tr
    cur = text
    for name, fn_name, env, need in _LAYERS:
        if env is not None and os.environ.get(env) != need:
            tr.skipped.append(f"{name} (нужен {env}={need})")
            continue
        fn = getattr(P, fn_name, None)
        if fn is None:                      # слой переименовали в pipeline — молчать нельзя
            tr.warnings.append(f"слой «{name}» не найден в pronounce.py ({fn_name})")
            continue
        nxt = fn(cur)
        tr.changes += _diff(name, cur, nxt)
        cur = nxt
    tr.text = cur
    return tr


def apply(text: str, *, lang: str = "ru", accent: bool = True) -> Trace:
    """Полный текстовый тракт клона: произношение по слоям, затем ударения.

    `accent=False` — путь edge-голоса: он «+» не уважает, и ставить метки бессмысленно.
    """
    tr = _pronounce_trace(text, lang=lang)
    if not accent:
        tr.skipped.append("ударения (голос не клон — «+» не уважается)")
        return tr
    if os.environ.get("ACCENTIZE", "1") == "0":
        tr.skipped.append("ударения (ACCENTIZE=0)")
        return tr

    from rusvoice import accentize as A

    if A._load_accentizer() is None:
        # ⚠️ Тот самый молчаливый провал: RUAccent живёт в brew-python, а в `.venv` его нет —
        # `accentize_ru` там просто возвращает текст, и правка словаря «не работает».
        tr.warnings.append(
            f"RUAccent недоступен в {sys.executable} — ударения НЕ проставлены, "
            "текст уйдёт в синтез как есть")
        return tr
    before = tr.text
    after = A.accentize_ru(before)
    tr.changes += _diff("ударения", before, after)
    tr.text = after
    tr.warnings += stress_warnings(after)
    return tr


def matches_engine(text: str, *, lang: str = "ru", accent: bool = True) -> bool:
    """Сверка с обычным путём движка: объяснение обязано быть про ТОТ ЖЕ текст, что уйдёт
    в синтез."""
    from rusvoice import accentize as A
    from rusvoice import pronounce as P

    ref = P.pronounce_ru(text, lang=lang)
    if accent and os.environ.get("ACCENTIZE", "1") != "0":
        ref = A.accentize_ru(ref)
    return apply(text, lang=lang, accent=accent).text == ref
