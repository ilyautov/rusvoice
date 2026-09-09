# -*- coding: utf-8 -*-
"""Голоса: «мой голос» как названная вещь, а не как путь к файлу в каждой команде.

⚠️ **Это дефолт, а не удобство.** До этого модуля `say` без `--ref` брала зашитый
`CLONE_REF` — путь к эталону Ильи ВНУТРИ репозитория. Рядом с движком это работало и
выглядело нормально; в самостоятельной установке файла нет, и команда отказывала фразой
«эталона нет», не сказав, где его взять. При этом взять его пакет умеет давно —
`ref grab` достаёт годный эталон из любого видео. Дыра была не в возможностях, а ровно
в дефолте.

⭐ Настройки хранятся **у голоса**, а не глобально, потому что они и есть свойства голоса:
темп у одного человека 1.12, у другого 1.0. Глобальный `CLONE_ATEMPO` пришлось бы
переставлять при каждой смене голоса — то есть ровно тот молчаливый разъезд, где ускоряют
чужую речь чужим коэффициентом и слышат это как «модель торопится».

Раскладка: `~/.config/rusvoice/voices.json` (реестр) + `voices/<имя>.wav` рядом с
`<имя>.txt`. Копируем к себе, а не ссылаемся на исходник: эталон, который однажды
переименуют или сотрут, — это молчаливо сломанный голос.
"""
from __future__ import annotations

import json
import os
import re
import shutil

HOME = os.path.join(os.path.expanduser("~"), ".config", "rusvoice")
STORE = os.path.join(HOME, "voices.json")
VOICES_DIR = os.path.join(HOME, "voices")

# Настройки голоса: ключ → (разбор, что значит, границы). Список закрытый нарочно —
# «настройка» без проверки диапазона это способ узнать о битом значении из звука.
SETTINGS = {
    "tempo": (float, "ускорение после синтеза (1.0 = как есть)", 0.5, 2.0),
    "outro": (float, "пауза после последнего слова, с", 0.0, 3.0),
}

# ⚠️ `nfe` (шагов диффузии F5) сюда НЕ попал, хотя просился. Единственный канал до него —
# `_clone_param("F5_NFE", …)`, то есть env или config, а подпись провайдера
# `(ref, text, out)` параметра не пропускает. Пришлось бы либо ставить переменную окружения
# вокруг вызова, либо писать настройку голоса в конфиг клона — а конфиг рядом с движком
# берётся ЕГО, и настройка молча не доехала бы ровно в репозитории. Механизм, который
# работает не везде и не говорит об этом, хуже отсутствующего: `nfe` остаётся ручкой
# env/config, и это написано в README.

# Эталон, который УЖЕ годен: есть транскрипт рядом и разумная длина. Такой берём как есть,
# без ASR. ⚠️ Границы не с потолка: F5 клонирует по короткому куску, на длинном качество
# падает, а на слишком коротком не хватает просодии — рабочий диапазон и есть 4–25 с.
READY_MIN, READY_MAX = 4.0, 25.0


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", str(name).strip(), flags=re.UNICODE).strip("-").lower()
    return s or "voice"


def _load() -> dict:
    try:
        d = json.loads(open(STORE, encoding="utf-8").read())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    os.makedirs(HOME, exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)          # запись атомарна: полреестра хуже, чем его отсутствие


def all_voices() -> tuple:
    """(имя текущего | None, {имя: запись}). Записи — то, что реально лежит на диске."""
    d = _load()
    voices = {k: v for k, v in (d.get("voices") or {}).items()
              if isinstance(v, dict) and os.path.isfile(v.get("ref") or "")}
    cur = d.get("current")
    return (cur if cur in voices else None), voices


def get(name: str | None = None) -> dict | None:
    """Запись голоса: по имени или текущего. Нет такого / файл пропал → None.

    Имя кладём В запись: без него вызывающий держал бы имя и запись двумя переменными,
    а они однажды разъедутся."""
    cur, voices = all_voices()
    key = name or cur or ""
    v = voices.get(key)
    return {**v, "name": key} if v else None


def current_ref() -> str | None:
    v = get()
    return v.get("ref") if v else None


def settings(name: str | None = None) -> dict:
    """Настройки голоса. Пусто — значит везде дефолты команды, и это нормальный ответ."""
    v = get(name)
    return {k: v[k] for k in SETTINGS if v and k in v} if v else {}


def add(src: str, name: str | None = None, *, target: float = 10.0, lang: str = "ru",
        as_is: bool = False) -> dict:
    """Источник → сохранённый голос. Видео, длинное аудио, готовый эталон — всё сюда.

    Два пути, и команда говорит, какой выбрала. Готовый эталон (есть `.txt` рядом, длина
    в рабочем диапазоне) копируется как есть — гонять ASR по тому, что уже разобрано,
    значит тратить минуту и рисковать заменить выверенный транскрипт свежей ослышкой.
    Всё остальное проходит `ref grab`: дорожка → пословные тайминги → лучшее окно.
    """
    from rusvoice import refaudio as R

    if not os.path.isfile(src):
        return {"ok": False, "detail": f"файла нет: {src}"}
    name = _slug(name or os.path.splitext(os.path.basename(src))[0])
    os.makedirs(VOICES_DIR, exist_ok=True)
    dst = os.path.join(VOICES_DIR, name + ".wav")

    side = R.sidecar(src)
    dur = (R.props(src) or {}).get("seconds") or 0
    ready = as_is or (os.path.isfile(side) and READY_MIN <= dur <= READY_MAX)
    if ready:
        shutil.copyfile(src, dst)
        if os.path.isfile(side):
            shutil.copyfile(side, R.sidecar(dst))
        how = (f"взят как есть ({dur:.1f} с, транскрипт рядом)" if not as_is
               else f"взят как есть по --as-is ({dur:.1f} с)")
        grabbed = None
    else:
        grabbed = R.grab(src, dst, lang=lang, target=target)
        if not grabbed.get("ok"):
            return {"ok": False, "detail": grabbed.get("detail", "не собрал эталон")}
        how = grabbed["detail"]

    if not os.path.isfile(R.sidecar(dst)):
        return {"ok": False,
                "detail": "эталон без транскрипта: F5 будет галлюцинировать префиксы — "
                          "положите рядом .txt или дайте источник, из которого его снимут"}

    d = _load()
    d.setdefault("voices", {})[name] = {"ref": dst, "source": os.path.abspath(src)}
    d.setdefault("current", name)
    _save(d)
    text, _ = R.effective_ref_text(dst)
    return {"ok": True, "name": name, "ref": dst, "how": how, "text": text,
            "current": d["current"] == name, "grab": grabbed}


def use(name: str) -> dict:
    cur, voices = all_voices()
    name = _slug(name)
    if name not in voices:
        return {"ok": False, "detail": f"нет голоса «{name}»; есть: "
                                       + (", ".join(sorted(voices)) or "ни одного")}
    d = _load()
    d["current"] = name
    _save(d)
    return {"ok": True, "name": name, "ref": voices[name]["ref"]}


def set_param(key: str, value: str, name: str | None = None) -> dict:
    """Настройка голоса с проверкой диапазона. ⚠️ Битое значение отбиваем ЗДЕСЬ: дальше
    оно доехало бы до синтеза и стало бы слышимым дефектом без единого сообщения."""
    cur, voices = all_voices()
    name = _slug(name) if name else cur
    if not name or name not in voices:
        return {"ok": False, "detail": "нет текущего голоса — `rusvoice voice add <файл>`"}
    if key not in SETTINGS:
        return {"ok": False, "detail": f"неизвестная настройка «{key}»; есть: "
                                       + ", ".join(SETTINGS)}
    cast, what, lo, hi = SETTINGS[key]
    try:
        v = cast(value)
    except ValueError:
        return {"ok": False, "detail": f"«{value}» — не {cast.__name__} ({what})"}
    if not lo <= v <= hi:
        return {"ok": False, "detail": f"{key}={v} вне {lo}..{hi} ({what})"}
    d = _load()
    d["voices"][name][key] = v
    _save(d)
    return {"ok": True, "name": name, "key": key, "value": v, "what": what}


def remove(name: str) -> dict:
    """Убрать голос из реестра. ⚠️ Файлы НЕ трогаем: удаление записи — правка настройки,
    удаление записи ГОЛОСА — потеря материала, и путать эти две вещи нельзя."""
    cur, voices = all_voices()
    name = _slug(name)
    if name not in voices:
        return {"ok": False, "detail": f"нет голоса «{name}»"}
    d = _load()
    ref = d["voices"].pop(name)["ref"]
    if d.get("current") == name:
        d["current"] = next(iter(sorted(d["voices"])), None)
    _save(d)
    return {"ok": True, "name": name, "ref": ref, "current": d.get("current")}
