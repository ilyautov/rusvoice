# -*- coding: utf-8 -*-
"""Словари произношения: показать, что в них живого, и дописать запись с проверкой.

Три словаря `rusvoice/*.json` правились руками, и у ручной правки два молчаливых исхода,
одинаково незаметных до прослушивания выхода:

  · **запись не доезжает** — её перекрывает более длинная основа, или она вообще не
    матчится по границам слова. Слой отработал, текст не изменился, ошибки нет.
  · **правка не доезжает до РЕНДЕРА** — ключ аудио-кэша считается по сырому `vo`, а словарь
    применяется уже внутри синтеза. Старая озвучка остаётся лежать, «фикс» не слышен.
    Лечится только поднятием версии правил — `rusvoice dict bump` или `add --bump`.

Поэтому `add` не пишет вслепую: он применяет слой к пробнику и сверяет, что получилось
именно то, что заказано, а после успеха печатает напоминание про версию (или поднимает её
сам по `--bump`). `list --check` тем же способом проверяет уже лежащие записи — так видно
мёртвую строку, которая годами ничего не делает.

Источник правды один: пишем в те же `rusvoice/*.json`, что читает слой, служебные ключи на «_»
(`_comment`, `_limits`, `_verified`) сохраняются.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_PIPE = os.path.join(_ROOT, "pipeline")
if _PIPE not in sys.path:
    sys.path.insert(0, _PIPE)

# ⭐ Место ОДНО: версия живёт рядом с правилами, которые описывает, а движок её оттуда
# читает. Раньше здесь стоял список из двух файлов ДВИЖКА (`html_reel.py`, `footage.py`) —
# то есть команда пакета лезла править чужой репозиторий, и после выноса `rusvoice` в
# отдельный репозиторий отвечала бы «поднять руками» навсегда.
_VERSION_SITES = (
    (os.path.join(_HERE, "pronounce.py"), r'^RULES_VERSION = "(\d+)"$'),
)

# ⚠️ Зеркало списка из `tests/test_pronounce.py::test_hard_e_does_not_touch_soft_words`.
# Родные слова и мягкие заимствования: сюда «э» тащить нельзя. Сторож против того, чтобы
# новая основа задела чужое слово (кеш → кем? модел → модем?). Расхождение двух списков
# ловит тест в `tests/test_rusvoice.py`.
SOFT_WORDS = ("Текст", "тема", "лес", "дело", "крем", "музей", "эффект",
              "сессия", "декада", "дефис", "модем", "температура")


@dataclass
class Kind:
    """Один словарь: как читать, как применять, что значит ключ."""
    name: str
    path: str
    loader: str            # имя функции-загрузчика в pronounce
    applier: str           # имя функции-слоя в pronounce
    key_hint: str
    builtin: str = ""      # имя встроенного словаря, если слой начинается не с пустого


KINDS = {
    "brands": Kind(
        "brands", os.path.join(_HERE, "brands_ru.json"), "_load_brands", "_apply_brands",
        "точный токен (латиница или кириллица), регистронезависимо", builtin="BRANDS"),
    "hard-e": Kind(
        "hard-e", os.path.join(_HERE, "hard_e_ru.json"), "_load_hard_e", "_apply_hard_e",
        "ОСНОВА в начале слова, без окончания: «модел» покрывает модель/модели/моделирование"),
    "stress": Kind(
        "stress", os.path.join(_HERE, "stress_ru.json"), "_load_stress", "_apply_stress",
        "слово как ПИШЕТСЯ; значение — написание, которое edge читает с верным ударением"),
}


@dataclass
class Entry:
    key: str
    value: str
    source: str            # "файл" | "встроенный"
    live: bool | None = None
    note: str = ""


@dataclass
class Result:
    ok: bool
    detail: str
    warnings: list = field(default_factory=list)
    hint: str = ""


def _pronounce():
    from rusvoice import pronounce as P
    return P


def _table(kind: Kind) -> dict:
    return getattr(_pronounce(), kind.loader)()


def _file_table(kind: Kind) -> tuple[dict, dict]:
    """(служебные ключи, записи) из самого файла. Пустой/битый файл — не ошибка."""
    if not os.path.isfile(kind.path):
        return {}, {}
    try:
        data = json.load(open(kind.path, encoding="utf-8"))
    except Exception:
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    meta = {k: v for k, v in data.items() if k.startswith("_")}
    rows = {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, str)}
    return meta, rows


def _probe(kind: Kind, key: str) -> str:
    """Применить ТОЛЬКО этот слой к ключу как к отдельному слову."""
    return getattr(_pronounce(), kind.applier)(key)


def _is_live(kind: Kind, key: str, value: str) -> bool:
    """Живая ли запись: слой на своём же ключе обязан дать заявленное значение.

    Для основы (`hard-e`) сравнение по началу — ключ это не целое слово, а начало."""
    got = _probe(kind, key)
    return got.lower().startswith(value.lower()) if kind.name == "hard-e" \
        else got.lower() == value.lower()


def entries(kind_name: str, *, check: bool = False) -> list[Entry]:
    kind = KINDS[kind_name]
    _, rows = _file_table(kind)
    eff = _table(kind)
    out = []
    for k, v in sorted(eff.items(), key=lambda kv: kv[0].lower()):
        e = Entry(k, v, "файл" if k in rows else "встроенный")
        if check:
            e.live = _is_live(kind, k, v)
            if not e.live:
                e.note = f"перекрыта: слой даёт «{_probe(kind, k)}»"
        out.append(e)
    return out


def counts() -> dict:
    """Сколько записей РЕАЛЬНО работает в каждом словаре — по загрузчику, а не по файлу.

    Считать `len(json)` нельзя: `_comment`/`_limits`/`_verified` тоже ключи, и словарь
    ударений с одной живой парой выглядел бы как четыре."""
    out = {}
    for name, kind in KINDS.items():
        _, rows = _file_table(kind)
        out[name] = {"итого": len(_table(kind)), "из файла": len(rows)}
    return out


# ── версия правил кэша ───────────────────────────────────────────────────────

def engine_present() -> bool:
    """Есть ли рядом видеодвижок. Версия правил — это ключ ЕГО аудио-кэша; при установке
    пакета отдельно (`pip install rusvoice`) движка нет, и «версии не видно» тогда не
    поломка, а другой сценарий. Отличать одно от другого обязан вызывающий: иначе доктор
    у стороннего пользователя ругался бы на отсутствие того, что ему не нужно."""
    return any(os.path.isfile(path) for path, _ in _VERSION_SITES)


def rules_version() -> str | None:
    """Текущая версия правил произношения; None — если константу не удалось прочитать.

    ⚠️ Цикл по `_VERSION_SITES` остался, хотя место сейчас одно: он и есть сторож против
    того, чтобы копии завелись заново. Разные значения → None, то есть отказ, а не тихий
    выбор одного из двух."""
    seen = set()
    for path, pat in _VERSION_SITES:
        if not os.path.isfile(path):
            return None
        m = re.search(pat, open(path, encoding="utf-8").read(), re.M)
        if not m:
            return None
        seen.add(m.group(1))
    return seen.pop() if len(seen) == 1 else None


def bump_rules_version() -> Result:
    """Поднять версию правил. Без этого правка словаря не доедет до рендера: в хэш
    аудио-кэша идёт сырой `vo`, а словарь применяется уже внутри синтеза.

    ⭐ Место одно — `rusvoice/pronounce.py`, рядом с самими правилами. Раньше их было два, и
    оба в ДВИЖКЕ: команда пакета правила чужой репозиторий, а после выноса `rusvoice`
    отвечала бы «поднять руками» навсегда."""
    cur = rules_version()
    if cur is None:
        return Result(False, "константа RULES_VERSION не найдена в rusvoice/pronounce.py — "
                             "поднять руками")
    new = str(int(cur) + 1)
    for path, pat in _VERSION_SITES:
        src = open(path, encoding="utf-8").read()
        src = re.sub(pat, lambda m: m.group(0).replace(f'"{cur}"', f'"{new}"'), src, count=1,
                     flags=re.M)
        open(path, "w", encoding="utf-8").write(src)
    return Result(True, f"версия правил произношения: {cur} → {new}")


# ── запись ───────────────────────────────────────────────────────────────────

def _soft_word_damage(kind: Kind, key: str, value: str) -> list[str]:
    """Не заденет ли новая основа заведомо мягкое слово. Сплошного правила «е→э» нет,
    и словарь ровно этим и оправдан — значит негативный список надо спрашивать ДО записи,
    а не узнавать из покрасневшего теста."""
    if kind.name != "hard-e":
        return []
    P = _pronounce()
    table = dict(_table(kind))
    table[key.lower()] = value
    out = []
    for w in SOFT_WORDS:
        if P._apply_hard_e(w, table=table) != w:
            out.append(f"«{w}» — родное/мягкое слово, а основа его задевает")
    return out


def hear(kind_name: str, key: str, out: str, *, ref: str | None = None) -> dict:
    """Озвучить пробник записи — чтобы услышать её, а не прочитать.

    ⚠️ `add` проверяет СТРОКУ: что слой на «OpenAI» даёт «Оупен Эй Ай». Что из этой строки
    сделает F5, не знает никто, а разрыв тут ровно того же сорта, против которого весь
    пакет: «слой отработал» ≠ «прозвучало верно». Отдельной командой, а не частью `add`,
    потому что синтез на M1 стоит десятки секунд — а записей за один заход бывает тридцать.
    """
    from rusvoice import say as SAY

    if kind_name not in KINDS:
        return {"ok": False, "detail": f"неизвестный словарь {kind_name!r}"}
    val = _table(KINDS[kind_name]).get(key) or _table(KINDS[kind_name]).get(key.lower())
    if val is None:
        return {"ok": False, "detail": f"«{key}» в словаре {kind_name} нет"}
    # Пробник фразой, а не голым словом: F5 на одиночном слове ведёт себя иначе, чем в
    # потоке речи, и судить по нему значило бы проверять не тот случай.
    phrase = f"Вот как это читается: {key}."
    r = SAY.say(phrase, out, ref=ref, loud=False, verify=False)
    r["probe"], r["expected"] = phrase, val
    return r


def add(kind_name: str, key: str, value: str, *, note: str = "",
        bump: bool = False, force: bool = False) -> Result:
    """Дописать запись и ПРОВЕРИТЬ, что она работает.

    Проверка не формальность: запись легко перекрывается более длинной основой и тогда
    молча ничего не делает. Пишем во временный словарь, применяем слой, сверяем — и только
    после этого трогаем файл.

    ⚠️ Проверяется СТРОКА, не звук: услышать запись — `hear`.
    """
    if kind_name not in KINDS:
        return Result(False, f"неизвестный словарь {kind_name!r}: {', '.join(KINDS)}")
    kind = KINDS[kind_name]
    key, value = key.strip(), value.strip()
    if not key or not value:
        return Result(False, "пустой ключ или значение")

    meta, rows = _file_table(kind)
    eff = _table(kind)
    warnings = []

    prev = eff.get(key) or eff.get(key.lower())
    if prev is not None and prev != value and not force:
        return Result(False, f"«{key}» уже есть со значением «{prev}» — "
                             "перезапись только с --force")

    damage = _soft_word_damage(kind, key, value)
    if damage and not force:
        return Result(False, "основа задевает заведомо мягкие слова", warnings=damage,
                      hint="выбрать основу длиннее или отказаться от записи")
    warnings += damage

    # Проба на копии таблицы — файл ещё не тронут.
    probe_table = {k.lower(): v for k, v in eff.items()} if kind.name == "hard-e" \
        else dict(eff)
    probe_table[key.lower() if kind.name == "hard-e" else key] = value
    got = getattr(_pronounce(), kind.applier)(key, probe_table)
    hit = got.lower().startswith(value.lower()) if kind.name == "hard-e" \
        else got.lower() == value.lower()
    if not hit and not force:
        return Result(False, f"запись не доедет: слой на «{key}» даёт «{got}», "
                             f"а не «{value}»",
                      hint="скорее всего перекрывает более длинная основа в словаре")
    if not hit:
        warnings.append(f"записана НЕЖИВОЙ: слой даёт «{got}»")

    # Порядок ключей НЕ пересортировываем: в brands_ru.json он смысловой (банки, площадки,
    # сервисы), и алфавит его бы стёр, раздув дифф на одну строку до всего файла.
    # Новое уходит в конец, существующее правится на месте.
    rows[key] = value
    if note:
        ver = dict(meta.get("_verified") or {})
        ver[key] = note
        meta["_verified"] = ver
    body = dict(meta)
    body.update(rows)
    with open(kind.path, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)
        f.write("\n")

    detail = f"{kind.name}: «{key}» → «{value}» ({len(rows)} записей в файле)"
    if bump:
        r = bump_rules_version()
        detail += "; " + r.detail
        if not r.ok:
            warnings.append(r.detail)
        return Result(True, detail, warnings=warnings)
    return Result(True, detail, warnings=warnings,
                  hint="звук изменился, но аудио-кэш этого не знает: ключ считается по "
                       "сырому vo. Поднять версию — `rusvoice dict bump` "
                       f"(сейчас {rules_version()})")
