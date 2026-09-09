# -*- coding: utf-8 -*-
"""Проверка окружения русской озвучки — до синтеза, а не после прослушивания.

Каждая проверка здесь стоит на КОНКРЕТНОМ провале, который у нас уже случался и каждый раз
выглядел не как поломка, а как «модель так читает»:

  · RUAccent живёт в brew-python, а в `.venv` его нет. `accentize_ru` там молча возвращает
    текст как есть — правки словаря «не работают», хотя словарь правильный.
  · `PYTHONHASHSEED` пустой или мусорный → дочерний процесс F5 падает на
    `config_init_hash_seed`. При генерации в один батч звук всё равно возвращается, поэтому
    ошибка выглядит безобидной; на двух батчах возврата нет и клип не собирается.
  · `F5_REF_TEXT` обязан совпадать с референсом ДОСЛОВНО. Не совпал — F5 галлюцинирует
    префикс, +13% WER, и это слышно как «модель мямлит», а не как ошибка настройки.
  · Словарь можно расширять, но пустой словарь — тоже валидное состояние, и тогда слой
    молча ничего не делает. Числа лучше видеть, чем предполагать.

Ни одна проверка ничего не чинит и не мутирует: доктор ставит диагноз.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIPE = os.path.join(_ROOT, "pipeline")
if _PIPE not in sys.path:
    sys.path.insert(0, _PIPE)

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def _check_interpreter() -> Check:
    return Check("интерпретатор", OK, sys.executable)


def _check_accent() -> Check:
    try:
        from rusvoice import accentize as A
    except Exception as exc:
        return Check("RUAccent", FAIL, f"модуль accentize не импортируется: {exc}")
    if os.environ.get("ACCENTIZE") == "0":
        return Check("RUAccent", WARN, "выключен через ACCENTIZE=0",
                     "снять ACCENTIZE=0, иначе ударения не проставляются")
    if A._load_accentizer() is None:
        return Check("RUAccent", FAIL,
                     f"недоступен в {sys.executable} — текст уйдёт в синтез БЕЗ ударений, "
                     "молча и без ошибки",
                     "запускать brew-питоном (/opt/homebrew/bin/python3.11) "
                     "или поставить ruaccent в текущее окружение")
    return Check("RUAccent", OK, f"модель {os.environ.get('RUACCENT_MODEL') or 'по умолчанию'}")


def _check_engine() -> Check:
    """Строка СПРАВОЧНАЯ, а не про поломку: движок пакету больше не нужен ни для чего.

    ⭐ История сужения в трёх шагах, и каждый следующий делал прошлую формулировку враньём.
    Сначала без движка не работали синтез, эталон и громкость. Потом константа лимитера,
    путь к ffmpeg и ASR нашлись у пакета — осталcя один СИНТЕЗ. Теперь и рецепт переехал
    в `rusvoice.clone`, и остаток — только предпочтения: рядом с движком берём его
    static-ffmpeg и его whisperx, потому что иначе поведение в репозитории поехало бы.
    ⚠️ Поэтому WARN здесь больше нет: предупреждать не о чем, а лишнее предупреждение
    приучает пролистывать вывод целиком."""
    from rusvoice import engine as E
    return Check("движок", OK, "рядом — ffmpeg и ASR берём у него") if E.present() \
        else Check("движок", OK, "рядом нет — работаем самостоятельно, это штатный режим")


def _check_f5() -> Check:
    from rusvoice import clone as C
    if not C.clone_available():
        return Check("F5", FAIL,
                     f"недоступен в {sys.executable} (нет f5_tts или чекпойнта)",
                     "`pip install \'rusvoice[voice]\'` в тот же интерпретатор + "
                     "`huggingface-cli download " + C.F5_REPO + "`")
    return Check("F5", OK, f"{C.F5_REPO} · {C.F5_CKPT}")


def _check_hashseed() -> Check:
    from rusvoice import clone as C
    if not C.clone_available():
        # Проверка про дочерний процесс F5. Нет F5 — нет и процесса, а предупреждение
        # стало бы шумом. ⚠️ Гейт был на наличии ДВИЖКА и молча промахнулся бы мимо
        # самостоятельной установки, где F5 есть, а движка нет.
        return Check("PYTHONHASHSEED", OK, "не относится — синтеза в этом окружении нет")
    v = os.environ.get("PYTHONHASHSEED", "")
    if v == "random" or v.isdigit():
        return Check("PYTHONHASHSEED", OK, v)
    return Check("PYTHONHASHSEED", WARN,
                 f"значение {v!r} невалидно — дочерний процесс F5 упадёт на "
                 "config_init_hash_seed; на одном батче звук всё равно вернётся, "
                 "на двух и более клип не соберётся",
                 "PYTHONHASHSEED=0 (voiceclone выставляет сам, но не во всех путях)")


def _check_reference(ref: str | None = None) -> list[Check]:
    """Делегируем `refaudio.check`.

    ⚠️ Здесь была своя копия проверки, и она пережила правку движка: сверяла сайдкар с
    переменной окружения, тогда как эффективный `ref_text` считается иначе. Копия проверки
    устаревает молча — и врёт ровно там, где на неё положились. Одна реализация, ленивый
    импорт (иначе цикл: `refaudio` берёт отсюда `Check`)."""
    from rusvoice import refaudio
    return refaudio.check(ref)


def _check_dicts() -> list[Check]:
    """Числа берём у ЗАГРУЗЧИКА, а не считаем ключи файла.

    ⚠️ `len(json.load(...))` здесь врёт дважды: служебные `_comment`/`_limits`/`_verified`
    тоже ключи (словарь ударений с одной живой парой выглядел бы как четыре), а у брендов
    файл — лишь расширение поверх встроенного `BRANDS`. Битый JSON загрузчик глотает
    молча, поэтому расхождение «в файле есть, в работе нет» тоже показываем."""
    from rusvoice import dicts as DI

    out = []
    for name, kind in (("бренды", "brands"), ("твёрдая э", "hard-e"),
                       ("ударения-переписью", "stress")):
        k = DI.KINDS[kind]
        if not os.path.isfile(k.path):
            out.append(Check(f"словарь {name}", WARN,
                             f"нет {os.path.basename(k.path)} — слой ничего не делает"))
            continue
        try:
            c = DI.counts()[kind]
        except Exception as exc:
            out.append(Check(f"словарь {name}", FAIL, f"не читается: {exc}"))
            continue
        raw = 0
        try:
            data = json.load(open(k.path, encoding="utf-8"))
            raw = len([x for x in data if not str(x).startswith("_")])
        except Exception:
            out.append(Check(f"словарь {name}", FAIL,
                             f"{os.path.basename(k.path)} не разбирается как JSON — "
                             "загрузчик глотает это молча и слой работает без файла",
                             "проверить файл: python3 -m json.tool " + k.path))
            continue
        if raw != c["из файла"]:
            out.append(Check(f"словарь {name}", WARN,
                             f"в файле {raw} строк, в работе {c['из файла']} — "
                             "часть не подхватилась (значение не строка?)"))
            continue
        detail = f"{c['итого']} записей" + (
            f" (из них {c['из файла']} из файла)" if k.builtin else "")
        out.append(Check(f"словарь {name}", OK if c["итого"] else WARN,
                         detail if c["итого"] else "пуст — слой молча ничего не делает"))
    return out


def _check_rules_version() -> Check:
    """Версия правил произношения — ключ аудио-кэша движка: не поднял её после правки
    словаря, и рендер молча отдаст прежнюю озвучку («фикс не доехал»).

    ⭐ Проверка больше НЕ про движок. Раньше версия жила двумя копиями в его файлах, и без
    движка рядом отвечать было нечего; теперь она рядом с самими правилами, то есть внутри
    пакета, и читается всегда."""
    from rusvoice import dicts as DI

    v = DI.rules_version()
    if v is None:
        return Check("версия правил", FAIL,
                     "константа RULES_VERSION не читается в rusvoice/pronounce.py",
                     "поднять руками или починить файл")
    return Check("версия правил", OK, f"правила произношения v{v}")


def _check_ffmpeg() -> Check:
    """Нужен для полей по краям и `atempo`. libass здесь ни при чём — субтитров нет."""
    from rusvoice import engine as E
    try:
        ff, _ = E.ffmpeg_bins()
    except Exception as exc:  # noqa: BLE001
        return Check("ffmpeg", FAIL, f"резолв не отдал бинарь: {exc}")
    if not ff:
        return Check("ffmpeg", FAIL, "не найден: ни FFMPEG_BIN, ни PATH",
                     "поставить ffmpeg или указать FFMPEG_BIN")
    return Check("ffmpeg", OK if os.path.exists(ff) else FAIL, ff)


def run(ref: str | None = None) -> list[Check]:
    checks = [_check_interpreter(), _check_engine(), _check_accent(), _check_f5(),
              _check_hashseed(), _check_ffmpeg()]
    checks += _check_reference(ref)
    checks += _check_dicts()
    checks.append(_check_rules_version())
    return checks


def worst(checks) -> str:
    if any(c.status == FAIL for c in checks):
        return FAIL
    return WARN if any(c.status == WARN for c in checks) else OK
