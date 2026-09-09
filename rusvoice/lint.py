# -*- coding: utf-8 -*-
"""Линт озвучки: найти реплики, которые прозвучат не так, — ДО рендера.

Сегодня такие места находят просмотром готового ролика: «Knight Capital» прозвучало как
**«Книгхт Капитал»**, и виноват был не акцентизатор, а последний слой — транслит. Он берёт
любую латиницу, не попавшую в словарь брендов, и читает её побуквенно-диграфно. Ошибки
нет, рендер прошёл, дефект слышен только ушами и только в конце.

Линт ловит три вещи, каждая — из уже случившегося:

  · **остаточная латиница** — то, что дойдёт до транслита. Показываем, во что именно она
    превратится: «Knight → Книгхт» убеждает быстрее, чем «слово не в словаре».
  · **метка ударения на односложном** — F5 отыгрывает «+» как нажим (см. `layers`).
  · **омограф с редким чтением** — метка СТОИТ, но не та: «Потом становится легко» уходило
    в синтез как «п+отом», то есть «облился по́том». Замер по корпусу: пропущенных меток
    у RUAccent практически нет (98.3% размечено), а вот ВЫБОР он путает — и «нет метки»
    этого не ловит. Лечится не словарём (он контекста не видит), а меткой в самом
    сценарии; см. `homographs`.
  · **голые цифры** — движок разворачивает единицы и множители, остальное уходит в синтез
    цифрами. ⭐⭐ Долго считалось риском («модель часто читает верно»). Замер 09.09.2026 на
    F5 закрыл вопрос: гипотезу никто не проверял, и она неверна. «440 миллионов» прозвучало
    как «чуть-чуть сто миллионов», «45 минут» — как «сорт пять минут», «1340 команд» — как
    «34 команды». Это не ослышка ASR: слова другие, и повторяется на всех проходах.
    ⚠️ `PRONOUNCE_NUMBERS=1` числа восстанавливает («четыреста сорок»), но НЕ склоняет:
    «в две тысячи двадцать шесть году», «к пять сентября», «на два месте». Поэтому в
    подсказке первым стоит «написать словом» — единственный вариант без изъяна.

Не ругается на то, что сделано НАРОЧНО: короткая ALL-CAPS аббревиатура (`API`, `CLI`) и
одиночный `x` в множителях транслитерируются по правилу, а не по недосмотру.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIPE = os.path.join(_ROOT, "pipeline")
if _PIPE not in sys.path:
    sys.path.insert(0, _PIPE)

from rusvoice import layers as L  # noqa: E402

_LATIN = re.compile(r"[A-Za-z]+")
_DIGITS = re.compile(r"\d+")


# Два уровня, и это не украшение. Транслит и метка на односложном — ДЕФЕКТЫ: известно, как
# именно прозвучит, и это неверно. Голые цифры — РИСК: модель их часто читает правильно,
# но не обещает. Уравнять их значит получить линт, который пролистывают целиком, а с ним
# пролистают и «Книгхт».
DEFECT, RISK = "дефект", "риск"


@dataclass
class Issue:
    kind: str
    detail: str
    fix: str = ""
    level: str = DEFECT


@dataclass
class Line:
    idx: int
    label: str
    text: str
    spoken: str
    issues: list


def _before_translit(text: str, lang: str = "ru") -> str:
    """Текст в том виде, в каком его получит последний слой.

    Считаем ровно теми же функциями и в том же порядке, что `pronounce_ru`, но
    останавливаемся перед транслитом: иначе латиница, которую надо показать, уже исчезнет."""
    from rusvoice import pronounce as P

    if not text or os.environ.get("PRONOUNCE", "1") == "0":
        return text
    if lang is not None and not str(lang).strip().lower().startswith("ru"):
        return text
    cur = text
    for name, fn_name, env, need in L._LAYERS:
        if fn_name == "_translit_rest":
            break
        if env is not None and os.environ.get(env) != need:
            continue
        fn = getattr(P, fn_name, None)
        if fn is not None:
            cur = fn(cur)
    return cur


def _latin_is_deliberate(tok: str) -> bool:
    """Транслит по правилу, а не по недосмотру: короткая ALL-CAPS читается по буквам
    (`API` → «эй-пи-ай»), одиночный `x` — множитель («5x» → «пять икс»)."""
    return tok.lower() == "x" or (tok.isupper() and 2 <= len(tok) <= 4)


def _is_ru(lang) -> bool:
    return lang is None or str(lang).strip().lower().startswith("ru")


def scan(text: str, *, lang: str = "ru", accent: bool = True) -> Line:
    from rusvoice import pronounce as P

    issues = []
    # ⚠️ На нерусской реплике транслита НЕ БУДЕТ (весь слой произношения выключен по `lang`),
    # и ругаться на латиницу значит подсветить каждое слово английской строки. Линт,
    # заваливший экран ложным, перестают читать вместе с настоящим.
    pre = _before_translit(text, lang) if _is_ru(lang) else ""
    for m in _LATIN.finditer(pre):
        tok = m.group(0)
        if _latin_is_deliberate(tok):
            continue
        issues.append(Issue(
            "транслит",
            f"«{tok}» → «{P._translit_word(tok)}» — слова нет в словаре брендов, "
            "последний слой прочтёт его побуквенно",
            f"rusvoice dict add brands {tok} <как читать>"))

    tr = L.apply(text, lang=lang, accent=accent)
    # Берём именно линт разметки, а не `tr.warnings`: там же лежит «RUAccent недоступен» —
    # проблема ОКРУЖЕНИЯ, и повторять её у каждой реплики значит приучить пролистывать вывод.
    for w in L.stress_warnings(tr.text):
        issues.append(Issue("ударение", w))

    # Омографы считаем по РАЗМЕЧЕННОМУ тексту: вопрос не «есть ли метка», а «та ли она».
    if accent and _is_ru(lang):
        from rusvoice import homographs as HG
        for word, chosen, why, fix in HG.scan(text, tr.text):
            issues.append(Issue("омограф", f"«{word}» → «{chosen}»: {why}", fix, RISK))

    for m in _DIGITS.finditer(tr.text):
        issues.append(Issue(
            "цифры",
            f"«{m.group(0)}» уйдёт в синтез цифрами — F5 их путает "
            "(замер: «440» → «чуть-чуть сто», «45» → «сорт пять»)",
            "написать словом; PRONOUNCE_NUMBERS=1 число восстановит, но падеж не согласует"))

    return Line(0, "", text, tr.text, issues)


# ── вход ─────────────────────────────────────────────────────────────────────

def _from_scenario(path: str) -> list:
    """Реплики сценария: `scenes[].vo`. Пустые сцены пропускаем — сцена без озвучки
    законна, и ругаться на неё значит приучить пролистывать вывод."""
    data = json.load(open(path, encoding="utf-8"))
    lang = data.get("lang") or "ru"
    out = []
    for i, sc in enumerate(data.get("scenes") or []):
        vo = (sc or {}).get("vo")
        if not vo or not str(vo).strip():
            continue
        label = str((sc or {}).get("title") or (sc or {}).get("kicker") or "").replace("\n", " ")
        out.append((i + 1, label[:40], str(vo), sc.get("lang") or lang))
    return out


def collect(src: str, *, lang: str = "ru") -> list:
    """Сценарий (.json) → реплики сцен; текстовый файл → строки; иначе сам аргумент."""
    if src == "-":
        return [(i + 1, "", ln, lang)
                for i, ln in enumerate(sys.stdin.read().splitlines()) if ln.strip()]
    if os.path.isfile(src):
        if src.lower().endswith(".json"):
            return _from_scenario(src)
        return [(i + 1, "", ln, lang)
                for i, ln in enumerate(open(src, encoding="utf-8").read().splitlines())
                if ln.strip()]
    return [(1, "", src, lang)]


def environment_note(accent: bool = True) -> str:
    """Замечание про окружение — одно на прогон. Без ударений линт всё ещё полезен
    (латиница и цифры считаются как обычно), но про метки он в этом запуске слеп."""
    if not accent:
        return ""
    from rusvoice import accentize as A
    if A._load_accentizer() is None:
        return (f"RUAccent недоступен в {sys.executable} — ударения не проставлены, "
                "и метки в этом прогоне не проверены")
    return ""


def defects(lines) -> list:
    """Только доказанные дефекты — на них и стоит завязывать код выхода."""
    return [ln for ln in lines if any(i.level == DEFECT for i in ln.issues)]


def run(src: str, *, lang: str = "ru", accent: bool = True) -> list:
    lines = []
    for idx, label, text, ln_lang in collect(src, lang=lang):
        got = scan(text, lang=ln_lang, accent=accent)
        got.idx, got.label = idx, label
        lines.append(got)
    return lines
