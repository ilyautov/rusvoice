# -*- coding: utf-8 -*-
"""Рецепт клона голоса: эталон + текст → wav. Каноническая реализация.

Здесь живёт то, ради чего пакет вообще нужен на выходе: последовательность шагов, каждый
из которых был выстрадан отдельным замером, и ни один из которых не очевиден. Порядок:

    эталон → его транскрипт → ударения → лид-токен → F5 → рез лида по сигналу → края

⚠️ **Почему модуль переехал сюда из `pipeline/voiceclone.py`.** Пока рецепт жил в движке,
`rusvoice say` — единственная команда, ради которой существует всё остальное, — требовала
рядом полный видеотракт. Остальные команды пакета (`explain`, `lint`, `dict`, `loud`,
разбор эталона) обходились стандартной библиотекой, и получалось, что самостоятельная
установка умеет всё, кроме собственно озвучки. Движок теперь импортирует рецепт отсюда,
а не наоборот: `voiceclone` осталcя реестром ВОСЬМИ провайдеров и бейкоффом, клон —
один из них, и его канон здесь.

⚠️ **Веса — не наши и не свободные.** Код F5-TTS под MIT, RUAccent под Apache-2.0, а
русский чекпойнт `Misha24-10/F5-TTS_RUSSIAN` — **CC BY-NC 4.0, non-commercial**. Модуль
их не везёт и не может: он их СКАЧИВАЕТ по требованию, и ответственность за условия
использования остаётся на том, кто скачал.

Расширение — через `PROVIDERS`/`AVAILABLE`: рецепт зовёт `PROVIDERS["f5"]`, а не функцию
напрямую, поэтому и движок (chatterbox/qwen3/voxcpm2), и приложение могут добавить свой
синтезатор, не трогая рецепт.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")

# провайдер: (ref_wav, text, out_wav) -> bool (успех)
PROVIDERS: dict = {}
# доступность: () -> bool (импорт/модель на месте)
AVAILABLE: dict = {}

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---- config-слой клона: env > config[clone] > зашитый дефолт ----
#
# 19.07.2026: путь был <корень>/config.json (не существует) — секция clone никогда
# не применялась (латентный баг из аудита). Канонический конфиг движка — pipeline/config.json.
#
# ⚠️ В самостоятельной установке никакого `pipeline/` нет, и молча остаться без настроек
# нельзя: «настройка голоса» — половина смысла продукта. Поэтому источников два, и
# `cfg_source()` честно говорит, какой сработал. Приоритет у конфига движка: рядом с
# движком поведение обязано остаться прежним до байта.
_ENGINE_CONFIG = os.path.join(_ROOT, "pipeline", "config.json")
_USER_CONFIG = os.path.join(os.path.expanduser("~"), ".config", "rusvoice", "clone.json")
_CONFIG_PATH = _ENGINE_CONFIG if os.path.isfile(_ENGINE_CONFIG) else _USER_CONFIG
_CLONE_CFG_CACHE: dict = {}


def cfg_source() -> str:
    """Откуда взялись настройки — для вывода пользователю, а не для логики."""
    if not os.path.isfile(_CONFIG_PATH):
        return f"настроек нет ({_CONFIG_PATH} отсутствует) — везде зашитые дефолты"
    return ("config.json движка" if _CONFIG_PATH == _ENGINE_CONFIG
            else "~/.config/rusvoice/clone.json") + f" ({_CONFIG_PATH})"


def _read_cfg(path: str) -> dict:
    """Весь json → dict. Нет файла / битый / не-dict → {}. Никогда не бросает.

    Движок читает свой конфиг через `cfgutil` (кэш по mtime, глубокая копия). Зовём его,
    когда он рядом, — иначе два ридера одного файла разъедутся в кэшировании; без него
    хватает голого json."""
    try:
        import cfgutil
        return cfgutil.load_cfg(path)
    except Exception:
        pass
    try:
        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
        return copy.deepcopy(parsed) if isinstance(parsed, dict) else {}
    except (OSError, ValueError):
        return {}


def _clone_cfg() -> dict:
    """Блок 'clone' из конфига (кэш на процесс). Нет файла/битый → {}."""
    if not _CLONE_CFG_CACHE:
        data = _read_cfg(_CONFIG_PATH)
        _CLONE_CFG_CACHE["v"] = (data.get("clone") or {}) if isinstance(data, dict) else {}
    return _CLONE_CFG_CACHE.get("v", {})


def _clone_param(env_key: str, cfg_key: str, default: str) -> str:
    """Приоритет: env > config[clone] > зашитый дефолт."""
    v = os.environ.get(env_key)
    if v is not None:
        return v
    cv = _clone_cfg().get(cfg_key)
    return str(cv) if cv is not None else default


# ---- провайдер F5-TTS (русский чекпойнт Misha24-10/F5-TTS_RUSSIAN) ----

F5_REPO = os.environ.get("F5_REPO", "Misha24-10/F5-TTS_RUSSIAN")
# accent-tune чекпойнт — объективно лучший (WER ~19% vs base ~65%, ASR-замер 12.06).
F5_CKPT = os.environ.get("F5_CKPT", "F5TTS_v1_Base_accent_tune/model_last_inference.safetensors")
F5_VOCAB = os.environ.get("F5_VOCAB", "F5TTS_v1_Base/vocab.txt")
# ⚠️ КРИТИЧНО для F5: ref_text обязан ТОЧНО соответствовать ref-аудио, иначе F5 галлюцинирует
# префиксы (утечка текста, +13% WER). Рецепт: короткий кусок ref (~10 с) + его whisper-транскрипт,
# и берётся он из САЙДКАРА рядом с эталоном — `voice add` кладёт его туда сам.
#
# ⚠️⚠️ Константа ниже — заведомо НЕ про ваше аудио. Это последний рубеж, а не дефолт: если до
# неё дошло, пара «аудио ↔ текст» уже разъехалась, и F5 начнёт галлюцинировать. Держать здесь
# чей-то настоящий транскрипт было бы вдвойне плохо — он не помог бы никому, кроме одного
# человека, и уехал бы вместе с пакетом в открытый доступ, где дословный транскрипт плюс
# эталон это готовый ключ клонирования голоса. Настоящий текст эталона движка живёт в его
# `pipeline/config.json[clone].ref_text`.
F5_REF_TEXT = os.environ.get(
    "F5_REF_TEXT",
    "Это короткая запись голоса для настройки синтеза речи.",
)


def _ref_text_source(ref: str, env_key: str = "F5_REF_TEXT") -> tuple:
    """Транскрипт ДЛЯ ЭТОГО эталона и откуда он взят: (текст, источник).

    ⭐⭐ Раньше транскрипт был глобальным, а эталон — аргументом, и пара расходилась молча.
    Соглашение «рядом с `ref.wav` лежит `ref.txt` с дословным транскриптом» знал только
    `talkingphoto.py`; `clone_synth(ref=…)` брал `F5_REF_TEXT`, то есть при смене эталона
    синтезировал чужое аудио с текстом Ильи. F5 на это не ругается — он галлюцинирует
    префиксы (+13% WER), и слышно это как «модель мямлит», а не как ошибка настройки.

    Порядок: env (осознанный разовый override) → **сайдкар рядом с эталоном** → config →
    зашитая константа. Сайдкар выше config и константы потому, что он про КОНКРЕТНОЕ
    аудио, а те двое — глобальные значения по умолчанию.

    ⚠️ Отличие только в пробелах сайдкар НЕ навязывает: у эталона движка в транскрипте
    двойной пробел, для голоса это ничто, а вот текст в F5 ушёл бы другой — и все прежние
    клон-рендеры перестали бы совпадать байт-в-байт.
    """
    v = os.environ.get(env_key)
    if v is not None:
        return v, f"env {env_key}"
    fallback = _clone_param(env_key, "ref_text", F5_REF_TEXT)
    src = "config.json[clone].ref_text" if _clone_cfg().get("ref_text") is not None \
        else "константа clone.F5_REF_TEXT"
    side = os.path.splitext(str(ref or ""))[0] + ".txt"
    try:
        disk = Path(side).read_text(encoding="utf-8").strip() if Path(side).is_file() else ""
    except Exception:
        # Сайдкар — удобство, а не обязательство: нечитаемый файл (права, не-UTF-8) не имеет
        # права валить синтез. Молча откатываемся на прежний источник.
        disk = ""
    if disk and " ".join(disk.split()) != " ".join((fallback or "").split()):
        return disk, f"транскрипт {Path(side).name}"
    return fallback, src


def _ref_text_for(ref: str, env_key: str = "F5_REF_TEXT") -> str:
    return _ref_text_source(ref, env_key)[0]


_F5 = {"model": None}


def _f5_paths():
    from huggingface_hub import hf_hub_download
    # env > config[clone] > зашитый дефолт (F5_CKPT/F5_VOCAB уже впитали env при импорте)
    ckpt = hf_hub_download(F5_REPO, _clone_param("F5_CKPT", "checkpoint", F5_CKPT))
    vocab = hf_hub_download(F5_REPO, _clone_param("F5_VOCAB", "vocab", F5_VOCAB))
    return ckpt, vocab


def _available_f5() -> bool:
    if not (F5_CKPT and F5_VOCAB):
        return False
    try:
        import f5_tts.api  # noqa: F401
        return True
    except Exception:
        return False


def _f5_synth(ref: str, text: str, out: str) -> bool:
    # F5/torch спавнит дочерние процессы, наследующие PYTHONHASHSEED из env; пустое/невалидное
    # значение → дочерний питон падает «config_init_hash_seed». Чинит клон из ЛЮБОГО вызывающего
    # (не только из скриптов с явным PYTHONHASHSEED=0). Уважаем валидное явное значение.
    # ⚠️ Гвард закрывает НЕ ВСЁ. Сообщение «config_init_hash_seed» продолжает мелькать в логах
    # и при выставленном значении: дочерний процесс падает, но при генерации В ОДИН БАТЧ звук
    # всё равно возвращается, и синтез считается успешным. Фатальным падение становится, когда
    # F5 режет текст на ДВА батча и более — тогда возврата нет и клип не собирается.
    # Замер 08.08.2026: 263 генерации подряд, многобатчевая ровно одна (строка на 31 слово при
    # медиане 7 по главе) — и упала только она. Практический вывод: держать реплику
    # КОРОТКОЙ. Это и так канон серии (одна строка = одна сцена ≈ 4.8с); длинная реплика
    # означает и кадр, висящий двадцать секунд, то есть дефект монтажа, а не только TTS.
    _phs = os.environ.get("PYTHONHASHSEED", "")
    if not (_phs == "random" or _phs.isdigit()):
        os.environ["PYTHONHASHSEED"] = "0"
    from f5_tts.api import F5TTS
    if _F5["model"] is None:
        ckpt, vocab = _f5_paths()
        _F5["model"] = F5TTS(ckpt_file=ckpt, vocab_file=vocab)
    ref_text = _ref_text_for(ref, "F5_REF_TEXT")
    # NFE шаги: больше = чище/плавнее текстура, медленнее. 64 утверждён Ильёй («теперь годно»), дефолт F5 = 32.
    nfe = int(_clone_param("F5_NFE", "nfe", "64"))
    _F5["model"].infer(ref_file=ref, ref_text=ref_text, gen_text=text,
                       nfe_step=nfe, file_wave=out)
    return Path(out).exists()


PROVIDERS["f5"] = _f5_synth
AVAILABLE["f5"] = _available_f5


# ---- эталон ----
#
# ⚠️ Пакет НЕ знает ничьего голоса. Раньше здесь стоял абсолютный путь к эталону Ильи внутри
# репозитория, и это было двумя проблемами сразу: в самостоятельной установке путь заведомо
# мёртв, а в открытом пакете имя личного файла — лишний след. Свой эталон каждый выбирает
# сам (`rusvoice voice add`), а движок подставляет свой при импорте `pipeline/voiceclone.py`.
# `CLONE_REF` остаётся точкой этой подстановки: пустая строка честно значит «не знаю».
CLONE_REF = os.environ.get("CLONE_REF", "")


def default_ref() -> str | None:
    """Эталон по умолчанию: **текущий голос** → config[clone].ref → CLONE_REF → None.

    ⚠️ None — нормальный ответ, а не поломка. `CLONE_REF` указывает на голос Ильи внутри
    репозитория; в самостоятельной установке этого файла нет и быть не должно — эталон
    приносит пользователь. Возвращать несуществующий путь как «дефолт» значит соврать.

    ⭐ Выбранный голос выше конфига: он назван человеком и только что, а конфиг —
    настройка окружения. ⚠️ `clone_synth` сюда НЕ ходит (у него свой прежний порядок
    `config → CLONE_REF`): движок обязан рендерить тем же эталоном независимо от того,
    что кто-то выбрал в CLI на этой машине."""
    try:
        from rusvoice import voices
        ref = voices.current_ref()
        if ref:
            return ref
    except Exception:          # реестр голосов — удобство, а не условие работы
        pass
    cfg = _clone_cfg().get("ref")
    if cfg:
        return str(cfg)
    if CLONE_REF and os.path.isfile(CLONE_REF):
        return CLONE_REF
    # Движок рядом — спрашиваем ЕГО. Свой эталон знает он, а не пакет (пакет уходит в
    # открытый доступ, и зашитый в нём чужой голос был бы не дефолтом, а ключом
    # клонирования). Тот же принцип, что с ffmpeg и ASR: движка нет — обходимся сами.
    from rusvoice import engine as E
    VC = E.get("voiceclone")
    ref = getattr(VC, "CLONE_REF", "") if VC else ""
    return ref if ref and os.path.isfile(ref) else None


# ---- рецепт ----

def accentize(text: str) -> str:
    """Авто-ударения через RUAccent. Graceful: нет модуля → текст как есть.
    Только clone-путь (F5 уважает '+', edge — нет)."""
    try:
        from rusvoice.accentize import accentize_ru
        return accentize_ru(text)
    except Exception:
        return text


def _pad_edges(src: str, dst: str, head: float = 0.18, tail: float = 0.18) -> None:
    """Привести тишину по краям РОВНО к head/tail — сначала снять, потом добавить.

    Раньше просто ДОБАВЛЯЛИ 0.18с, не глядя, сколько тишины уже есть. F5 отдаёт непредсказуемый
    пре-ролл, а ASR-трим лида на нём срывается молча (замер 02.08.2026: `.trim.wav` побайтово
    равен сырому — whisper отдал первое слово в ~0.05с, хотя реально речь начиналась в 0.87с).
    Итог в ролике: у части сцен 0.93–1.05с мёртвого воздуха ПОСЛЕ склейки — до 36% длительности
    кадра. В вертикали, где кадр живёт 2–3 секунды, это и слышно как «рваные склейки».
    Энергия — надёжнее ASR: `silenceremove` снимает тишину с обоих концов (второй проход через
    areverse), дальше добавляем ровно сколько нужно. Речь не трогаем: порог −45dB.
    """
    trim = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02:detection=peak,"
            "areverse,"
            "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02:detection=peak,"
            "areverse")
    af = f"{trim},adelay={int(head * 1000)}:all=1,apad=pad_dur={tail}"
    r = subprocess.run([FFMPEG, "-y", "-i", src, "-af", af, "-ar", "24000", "-ac", "1", dst],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode != 0 or not Path(dst).exists():   # graceful: старое поведение лучше, чем ничего
        subprocess.run([FFMPEG, "-y", "-i", src,
                        "-af", f"adelay={int(head * 1000)}:all=1,apad=pad_dur={tail}",
                        "-ar", "24000", "-ac", "1", dst],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


CLONE_LEAD = "Так,"   # лид-токен: F5 «жертвует» онсет на нём, а не на первом реальном слове
_WHISPER = {"m": None}


def _norm_word(s: str) -> str:
    return s.strip().strip(".,!?;:—-«»\"'").lower()


def _quietest_point(path: str, lo: float, hi: float, hop: float = 0.01):
    """Самая тихая точка сигнала в окне [lo, hi] — центр минимального по RMS кадра.

    ⚠️ Нужна потому, что границы слов у whisper — ОЦЕНКА, и стыкуются они встык:
    конец лида ровно равен началу первого слова, хотя между ними есть пауза. Где именно
    резать, знает только сам сигнал. Graceful: нет numpy / не читается wav → None,
    вызывающий откатится на оценку по таймстемпам."""
    try:
        import wave

        import numpy as np
        with wave.open(path) as w:
            sr, n = w.getframerate(), w.getnframes()
            a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
        if w.getnchannels() > 1:
            a = a.reshape(-1, w.getnchannels()).mean(axis=1)
        i0, i1 = max(0, int(lo * sr)), min(len(a), int(hi * sr))
        step = max(1, int(hop * sr))
        if i1 - i0 < step:
            return None
        best, best_t = None, None
        for i in range(i0, i1 - step + 1, step):
            seg = a[i:i + step]
            r = float((seg * seg).mean())
            if best is None or r < best:
                best, best_t = r, (i + step / 2.0) / sr
        return best_t
    except Exception:
        return None


def _lead_cut_point(words, lead: str):
    """Точка реза лида (сек) по whisper-словам. КЛЮЧЕВОЕ наблюдение: F5 ЧАСТО поглощает
    лид целиком (whisper его не слышит → первое слово = реальное, чистое в ~0.0). Тогда
    резать лид НЕЛЬЗЯ — иначе срежем первое реальное слово. Режем лид ТОЛЬКО если он
    реально произнесён (первое слово == лид). Иначе — лишь пре-ролл до первого слова.
    words = объекты с .word/.start(/.end). Пусто → None.

    ⚠️ Раньше произнесённый лид резался в `words[1].start - 0.05` — отступ назад от
    СЛЕДУЮЩЕГО слова. Это давало слышимый огрызок лида: на `/tmp/direct.f5raw.wav`
    whisper отдал «Так» 0.000–0.420 и «это» 0.420–0.640 (границы встык, паузы «нет»),
    рез приходился на 0.370 — а по сигналу там ещё ГРОМКАЯ часть лида (−16 dBFS),
    настоящая тишина живёт на 0.44–0.47. В выходе оставалось «…ак» перед первым словом,
    и слышно это было только ушами: ASR такой огрызок склеивал в правдоподобное слово.
    Поэтому здесь — лишь ОЦЕНКА (конец лида), уточняет её `_quietest_point` по сигналу."""
    if not words:
        return None
    lead_n = _norm_word(lead)
    if _norm_word(words[0].word) == lead_n and len(words) >= 2:
        return max(0.0, _lead_end(words))
    return max(0.0, words[0].start - 0.05)         # лид поглощён → режем только пре-ролл


def _lead_end(words) -> float:
    """Конец лида по whisper. ⚠️ `.end` может отсутствовать (старые faster_whisper) или
    быть вырожденным (== началу): и то и другое даёт рез в 0.0, то есть лид не срезан
    вовсе. В обоих случаях откатываемся на начало следующего слова."""
    end = float(getattr(words[0], "end", 0.0) or 0.0)
    return end if end > float(words[0].start) else float(words[1].start)


def _lead_search_window(words, lead: str):
    """Окно, в котором надо искать НАСТОЯЩУЮ границу лида (см. `_lead_cut_point`).

    Только для произнесённого лида: у поглощённого резать нечего. Границы окна взяты
    с запасом в обе стороны от стыка, который отдал whisper, — его оценка гуляет на
    десятки миллисекунд, а минимум RMS внутри окна всё равно приходится на паузу:
    любая пауза тише любой речи."""
    if not words or len(words) < 2:
        return None
    if _norm_word(words[0].word) != _norm_word(lead):
        return None
    return max(0.0, _lead_end(words) - 0.06), float(words[1].start) + 0.14


def _trim_lead(src: str, dst: str, lead: str) -> bool:
    """Обрезать лид-токен по whisper-таймстемпам (см. _lead_cut_point).
    Graceful: нет whisper / нет слов → False (caller откатится на паддинг сырого)."""
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return False
    try:
        if _WHISPER["m"] is None:
            _WHISPER["m"] = WhisperModel("small", device="cpu", compute_type="int8")
        segs, _ = _WHISPER["m"].transcribe(src, language="ru", beam_size=5, word_timestamps=True)
        words = [w for s in segs for w in (s.words or [])]
        cut = _lead_cut_point(words, lead)
        if cut is None:
            return False
        win = _lead_search_window(words, lead)
        if win is not None:
            exact = _quietest_point(src, win[0], win[1])
            if exact is not None:
                cut = exact
        subprocess.run([FFMPEG, "-y", "-ss", f"{cut:.3f}", "-i", src,
                        "-ar", "24000", "-ac", "1", dst],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return Path(dst).exists()
    except Exception:
        return False


def clone_synth(text: str, out_base: str, ref: str = None) -> str:
    """Engine-entry: синтез клонированным голосом через F5-провайдер НАПРЯМУЮ (без
    pronounce — `tts_engine.synth` уже кириллизовал) + авто-ударения + онсет-фикс +
    паддинг краёв. Возвращает путь к wav. Бросает, если F5 не отработал (решение,
    что делать дальше, — за вызывающим).
    Порядок: ref → accentize → лид-токен → F5 → трим лида → паддинг краёв."""
    ref = ref or (_clone_cfg().get("ref") or CLONE_REF)
    text = accentize(text)
    out = out_base + ".wav"
    raw = out_base + ".f5raw.wav"
    onset = os.environ.get("CLONE_ONSET_FIX", "1") != "0"
    gen = (CLONE_LEAD + " " + text) if onset else text
    if not PROVIDERS["f5"](ref, gen, raw):
        raise RuntimeError("F5 clone synth failed")
    src = raw
    if onset:
        trimmed = out_base + ".trim.wav"
        if _trim_lead(raw, trimmed, CLONE_LEAD):
            src = trimmed
    _pad_edges(src, out)
    # опц. ускорение клона БЕЗ сдвига высоты (env CLONE_ATEMPO / config clone.atempo, дефолт 1.0 →
    # байт-в-байт). Применяем ДО выравнивания караоке (tts_engine.synth зовёт whisperx на этом wav →
    # тайминги субтитров считаются по УЖЕ ускоренному аудио, синхрон цел). Рецепт голоса Ильи: ~1.12.
    try:
        tempo = float(_clone_param("CLONE_ATEMPO", "atempo", "1.0") or 1.0)
    except ValueError:
        tempo = 1.0
    if abs(tempo - 1.0) > 1e-3:
        tempo = max(0.5, min(2.0, tempo))            # atempo принимает 0.5..2.0 за один проход
        sped = out_base + ".spd.wav"
        r = subprocess.run([FFMPEG, "-y", "-i", out, "-af", f"atempo={tempo:.3f}", sped],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode == 0 and Path(sped).exists():
            os.replace(sped, out)
    return out


def clone_available() -> bool:
    """Доступен ли F5-клон в этом окружении (импорт + чекпойнт)."""
    fn = AVAILABLE.get("f5")
    try:
        return bool(fn and fn())
    except Exception:
        return False
