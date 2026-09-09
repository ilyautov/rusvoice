# -*- coding: utf-8 -*-
"""Движок как ПРЕДПОЧТЕНИЕ, а не как зависимость.

⭐⭐ Зависимость от видеодвижка сужалась трижды, и каждый шаг делал прошлое описание
враньём. Сначала без него не работали синтез, эталон и громкость. Потом константа
лимитера, путь к ffmpeg и ASR нашлись у пакета — остался один синтез. Теперь и рецепт
клона переехал в `rusvoice.clone`, и движок не нужен НИ ДЛЯ ЧЕГО.

Модуль остался, потому что «не нужен» и «не важен» — разные вещи. Когда движок рядом,
звать надо ЕГО: у него свой static-ffmpeg (с libass, который нам не нужен, но менять
бинарь на ходу значило бы менять поведение репозитория) и свой whisperx с кэшем. Отсюда
правило: движок есть → его реализация, движка нет → своя.

⚠️ Стектрейс вместо ответа остаётся запрещённым. `ModuleNotFoundError: No module named
'voiceclone'` — то, чем доктор встретил первого же установившего пакет, и ровно тот
молчаливый провал, ради которого пакет написан. Собственный провал не может быть
исключением, поэтому `get()` не бросает никогда.
"""
from __future__ import annotations

import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIPE = os.path.join(_ROOT, "pipeline")

MISSING = ("движка рядом нет (модули `pipeline/` не импортируются) — на возможности пакета "
           "это не влияет: ffmpeg и ASR берутся свои")

FIX = ("ничего чинить не нужно; движок подхватится сам, если запускать из его репозитория")


def _ensure_path() -> None:
    if os.path.isdir(_PIPE) and _PIPE not in sys.path:
        sys.path.insert(0, _PIPE)


def get(name: str):
    """Модуль движка или None. Никогда не бросает: вызывающий обязан уметь сказать «нет»."""
    _ensure_path()
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def present() -> bool:
    """Рядом ли движок. ⚠️ Ответ СПРАВОЧНЫЙ: ни одна возможность пакета от него не зависит.
    Проверяем по `voiceclone` — он тянет за собой остальной тракт."""
    return get("voiceclone") is not None


def need(*names):
    """Модули движка или (None, ...) — распаковывается на месте вызова.

    Возвращает кортеж всегда той же длины, чтобы вызывающий писал обычную распаковку,
    а не ветвление на каждый импорт."""
    got = tuple(get(n) for n in names)
    return got if len(names) > 1 else got[0]


# ── подмены для того, что движком быть не обязано ────────────────────────────
#
# Константа лимитера, путь к ffmpeg и ASR существуют и без видеотракта. Приоритет
# у движка: если он рядом, зовём ЕГО — иначе поведение в репозитории поехало бы.

TRUE_PEAK_LIMIT = 0.84          # ≈ −1.5 dBFS по сэмплам; зеркало `talkingphoto.LIMIT`
_WHISPER = {"m": None}          # модель ASR держим одну на процесс: загрузка дороже прогона


def limiter_ceiling() -> float:
    """Потолок лимитера. ⚠️ Зеркало ловится тестом: разъезд двух чисел = тихая смена
    громкости у половины путей."""
    TP = get("talkingphoto")
    return float(getattr(TP, "LIMIT", TRUE_PEAK_LIMIT)) if TP else TRUE_PEAK_LIMIT


def ffmpeg_bins() -> tuple:
    """(ffmpeg, ffprobe). Движок резолвит собственный static-ffmpeg — у него на то есть
    причина (libass для ASS-караоке). ⚠️ Пакету libass не нужен: субтитров он не рисует,
    ему хватает полей по краям и `atempo`. Поэтому без движка берём env и PATH."""
    import shutil

    pc = get("projconf")
    if pc is not None:
        try:
            ff, fp = pc.ffmpeg_bins()
            if ff:
                return ff, fp
        except Exception:
            pass
    return (os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg"),
            os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe"))


def transcribe_words(path: str, *, lang: str = "ru") -> list:
    """Пословные тайминги `[{w, start, end}]`.

    Движок отдаёт их через whisperx (`clipper`) — он же кэширует и умеет диаризацию.
    Без движка хватает `faster_whisper`: нам нужны только слова и их границы.
    Пусто — если распознавать нечем; вызывающий обязан это отличать от «речи нет»."""
    C = get("clipper")
    if C is not None:
        try:
            return list(C.transcribe(str(path), lang=lang).get("words") or [])
        except Exception:
            return []
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return []
    if _WHISPER["m"] is None:
        _WHISPER["m"] = WhisperModel(os.environ.get("RUSVOICE_ASR_MODEL", "small"),
                                     device="cpu", compute_type="int8")
    # ⚠️ `transcribe` отдаёт ГЕНЕРАТОР сегментов: вычерпать его дважды нельзя, второй
    # проход вернёт пустоту и это выглядит как «речи в файле нет» на нормальном аудио.
    segs, _ = _WHISPER["m"].transcribe(str(path), language=lang, word_timestamps=True)
    return [{"w": w.word.strip(), "start": w.start, "end": w.end}
            for s in list(segs) for w in (s.words or [])]
