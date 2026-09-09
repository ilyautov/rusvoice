#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Публикация необратима: форк и индексация не отзываются.

Перед первым релизом ручной скан выхватил ровно три вещи — служебный кэш, уехавший
в коммит; ссылки в метаданных, ведущие на другой (приватный) репозиторий; и личный
транскрипт эталона, который пережил вычистку кода, потому что лежал в тестовой
фикстуре. Ни одну из них не видно ни глазами в диффе, ни тестами.

Поэтому скан здесь — проверка, а не привычка: привычку однажды не выполняют.

Ловит: личные пути и имена файлов · аудио в индексе · служебный кэш · ссылки в чужой
репозиторий · битые относительные ссылки в разметке.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF = "scripts/prepublish_check.py"

# Личное. Пакет не знает ничьего голоса и ничьих путей — это его свойство, а не
# гигиена: зашитый путь к эталону превращает «голос не выбран» в «эталона нет».
PERSONAL = [
    (r"/Users/[a-z]", "домашний путь конкретной машины"),
    (r"ilya_ref|pravki_ref", "имя личного файла-эталона"),
    (r"\bilyautov@", "личный адрес почты"),
]
# Служебное: в дистрибутив не входит, в репозитории быть не должно.
JUNK = re.compile(r"(^|/)(\.pytest_cache|__pycache__|\.DS_Store|\.ruff_cache)(/|$)")
AUDIO = re.compile(r"\.(wav|mp3|m4a|flac|ogg)$", re.I)

# Ссылки в метаданных должны вести в ЭТОТ репозиторий: путь в приватный отдаёт
# первому зашедшему 404 и выглядит как заброшенный проект.
FOREIGN_REPO = re.compile(r"github\.com/[\w.-]+/(?!rusvoice\b)[\w.-]+")
FOREIGN_OK = {"README.md", "README.en.md"}  # там нарочно ссылки на соседей


def tracked():
    """Отслеживаемое ПЛЮС ещё не добавленное.

    ⚠️ Одного `ls-files` мало: файл, который вот-вот закоммитят, ему не виден — и
    проверка пропускает ровно тот момент, ради которого написана. Игнорируемое
    (`--exclude-standard`) не берём: оно и так никуда не поедет.
    """
    def ls(*args):
        out = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", *args],
                             capture_output=True, text=True, check=True).stdout
        return [p for p in out.split("\0") if p]
    return sorted(set(ls()) | set(ls("--others", "--exclude-standard")))


def main():
    bad = []
    for path in tracked():
        if JUNK.search(path):
            bad.append(f"{path}: служебный кэш в индексе")
            continue
        if AUDIO.search(path):
            bad.append(f"{path}: аудиофайл — голос в пакет не едет")
            continue
        full = os.path.join(ROOT, path)
        try:
            text = open(full, encoding="utf-8").read()
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        # Файл правил содержит сами искомые строки и ловит себя. Исключение явное:
        # подгонять регулярки так, чтобы они себя не видели, — значит ослабить их.
        if path == SELF:
            continue
        for pattern, why in PERSONAL:
            for m in re.finditer(pattern, text):
                line = text[:m.start()].count("\n") + 1
                bad.append(f"{path}:{line}: {why} — «{m.group(0)}»")
        if path not in FOREIGN_OK:
            for m in FOREIGN_REPO.finditer(text):
                line = text[:m.start()].count("\n") + 1
                bad.append(f"{path}:{line}: ссылка на другой репозиторий — «{m.group(0)}»")

    # Относительные ссылки. README писался внутри движка, и `../README.md` там вёл
    # к живому файлу — в отдельном репозитории это 404, которого автор не увидит
    # никогда: у него на диске рядом лежит и то, и другое.
    link = re.compile(r"\[([^\]]+)\]\((?!https?://|mailto:|#)([^)]+)\)")
    for path in tracked():
        if not path.endswith(".md"):
            continue
        text = open(os.path.join(ROOT, path), encoding="utf-8").read()
        base = os.path.dirname(path)
        for m in link.finditer(text):
            target = m.group(2).split("#")[0].strip()
            if not target:
                continue
            if not os.path.exists(os.path.join(ROOT, base, target)):
                line = text[:m.start()].count("\n") + 1
                bad.append(f"{path}:{line}: битая ссылка «{target}»")

    if bad:
        print("Публиковать нельзя:", file=sys.stderr)
        for b in bad:
            print("  ·", b, file=sys.stderr)
        return 1
    print(f"чисто: {len(tracked())} файлов, личного и служебного нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
