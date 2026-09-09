# -*- coding: utf-8 -*-
"""Эталон голоса: сверить пару «аудио ↔ транскрипт» и нарезать окно по границам слов.

F5 требует, чтобы `ref_text` соответствовал ref-аудио ДОСЛОВНО. Рассинхрон не падает и не
логируется — он звучит: модель галлюцинирует префиксы, +13% WER. Отсюда рецепт («короткий
кусок ~10 с + его whisper-транскрипт, окно по границам слов») и два места, где он рвётся:

  · **Транскрипт может быть не про это аудио.** Соглашение «рядом с `ref.wav` лежит
    `ref.txt`» знал только `pipeline/talkingphoto.py`; `voiceclone.clone_synth(ref=…)` брал
    глобальный `F5_REF_TEXT`, и смена эталона без смены текста давала мемное аудио с
    транскриптом Ильи — молча. Движок это теперь связывает сам (`_ref_text_source`), но
    перебить его по-прежнему можно — `F5_REF_TEXT` в окружении бьёт всё. `check` сверяет
    сайдкар именно с ЭФФЕКТИВНЫМ значением и говорит, откуда оно взялось.
  · **Окно, нарезанное по времени, рвёт слово.** Тогда транскрипт уже не дословен по
    построению. `cut` снимает границы с ASR-таймингов и пишет сайдкар из тех же слов, что
    попали в окно, — дословность обеспечивается конструкцией, а не аккуратностью.

⚠️ ASR здесь — измеритель ВРЕМЕНИ и источник текста ОДНОВРЕМЕННО, и это исключение из
правила «субтитры из исходника, не из ASR»: у эталона исходника нет, есть только запись.
Поэтому транскрипт после нарезки надо прочитать глазами — команда его для того и печатает.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIPE = os.path.join(_ROOT, "pipeline")
if _PIPE not in sys.path:
    sys.path.insert(0, _PIPE)

from rusvoice import clone as C  # noqa: E402
from rusvoice.doctor import FAIL, OK, WARN, Check  # noqa: E402

# Рецепт эталона (`assets/voice/PRAVKI_REF.md`, комментарии `voiceclone._f5_synth`):
# короткое окно, моно, СЫРОЕ — шумодав ухудшал и транскрипт, и сам клон.
GOOD_SECONDS = (6.0, 15.0)
MAX_INNER_GAP = 1.5      # пауза длиннее слышна в клоне как склейка


def _ffmpeg_bins():
    from rusvoice import engine as E
    return E.ffmpeg_bins()


def props(path: str) -> dict:
    """Длительность, частота, каналы. ffprobe — единственный, кто отвечает честно:
    имя файла и заголовок врут (у нас уже был `-shortest`, молча срезавший речь)."""
    _, ffprobe = _ffmpeg_bins()
    r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0",
                        "-show_entries", "stream=sample_rate,channels:format=duration",
                        "-of", "json", path], capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    d = json.loads(r.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    return {"seconds": float((d.get("format") or {}).get("duration") or 0),
            "sample_rate": int(st.get("sample_rate") or 0),
            "channels": int(st.get("channels") or 0)}


def effective_ref_text(ref: str | None = None) -> tuple[str, str]:
    """Текст, который РЕАЛЬНО уйдёт в F5 для этого эталона, и его источник.

    Не своя копия порядка, а вызов `voiceclone._ref_text_source`: разъехавшись с движком,
    проверка стала бы врать ровно в ту сторону, ради которой написана.

    ⭐ Порядок разрешения живёт в одном месте — `rusvoice.clone._ref_text_source`. Своя
    копия здесь устарела бы молча: это ровно тот разъезд, из-за которого транскрипт и
    эталон однажды разошлись и F5 галлюцинировал префиксы."""
    ref = ref or C.default_ref()
    if not ref:
        return "", "эталон не задан — укажите файл через --ref"
    return C._ref_text_source(ref, "F5_REF_TEXT")


def _norm(s: str) -> str:
    """Сравниваем по словам: лишние пробелы и переносы для F5 значения не имеют,
    а расхождение в СЛОВАХ — имеет."""
    return " ".join((s or "").split())


def sidecar(ref: str) -> str:
    return os.path.splitext(ref)[0] + ".txt"


def check(ref: str | None = None) -> list[Check]:
    ref = ref or C.default_ref()
    if not ref:
        return [Check("эталон", WARN, "голос не выбран",
                      "`rusvoice voice add <видео|аудио>` — достанет эталон из чего "
                      "угодно и запомнит; разовый файл — через --ref")]
    out = [Check("эталон", OK, ref)]
    if not os.path.isfile(ref):
        return [Check("эталон", FAIL, f"файла нет: {ref}")]

    p = props(ref)
    if not p:
        out.append(Check("аудио", FAIL, "ffprobe не прочитал поток"))
    else:
        st = OK if GOOD_SECONDS[0] <= p["seconds"] <= GOOD_SECONDS[1] else WARN
        out.append(Check("аудио", st,
                         f"{p['seconds']:.2f} с · {p['sample_rate']} Гц · "
                         f"{p['channels']} кан.",
                         f"рецепт: окно {GOOD_SECONDS[0]:.0f}–{GOOD_SECONDS[1]:.0f} с"
                         if st != OK else ""))
        if p["channels"] > 1:
            out.append(Check("каналы", WARN, f"{p['channels']} — эталон канонично моно"))

    eff, src = effective_ref_text(ref)
    out.append(Check("ref_text источник", OK, src))

    side = sidecar(ref)
    if not os.path.isfile(side):
        out.append(Check("транскрипт", FAIL,
                         f"нет {os.path.basename(side)} — дословность сверить нечем",
                         "нарезать окно через `rusvoice ref cut`, он пишет транскрипт сам"))
        return out

    disk = _norm(open(side, encoding="utf-8").read())
    if not disk:
        out.append(Check("транскрипт", FAIL, f"{os.path.basename(side)} пуст"))
        return out
    out.append(Check("транскрипт", OK, f"{len(disk.split())} слов из "
                                       f"{os.path.basename(side)}"))
    if _norm(eff) == disk:
        out.append(Check("дословность", OK, "эффективный ref_text совпадает с транскриптом"))
    else:
        out.append(Check("дословность", FAIL,
                         f"эффективный ref_text ({src}) РАСХОДИТСЯ с транскриптом рядом с "
                         "аудио — F5 будет галлюцинировать префиксы (+13% WER)",
                         f"снять F5_REF_TEXT/clone.ref_text — тогда движок возьмёт "
                         f"{os.path.basename(side)} сам"))
    return out


# ── нарезка окна ─────────────────────────────────────────────────────────────

def words(path: str, *, lang: str = "ru") -> list[dict]:
    """Пословные тайминги. Движок — whisperx через `clipper`, без него — faster_whisper."""
    from rusvoice import engine as E
    return E.transcribe_words(path, lang=lang)


def snap(ws: list[dict], start: float, end: float) -> tuple[float, float, list[dict]]:
    """Сдвинуть окно на границы слов: берём слова, целиком лежащие внутри запроса, и
    режем по их краям. Полслова в эталоне — это уже не дословный транскрипт."""
    inside = [w for w in ws if w.get("start") is not None
              and w["start"] >= start - 1e-6 and w["end"] <= end + 1e-6]
    if not inside:
        return start, end, []
    return float(inside[0]["start"]), float(inside[-1]["end"]), inside


def cut(src: str, out: str, start: float, end: float, *, lang: str = "ru",
        rate: int = 24000, pad: float = 0.05) -> dict:
    """Вырезать окно по границам слов и положить рядом дословный транскрипт.

    Транскрипт собирается из ТЕХ ЖЕ слов, что попали в окно, — дословность по построению,
    а не по аккуратности. Шумодав не применяем сознательно: на голосе Ильи чистка ухудшала
    и транскрипт, и клон (`PRAVKI_REF.md`).
    """
    ws = words(src, lang=lang)
    if not ws:
        return {"ok": False, "detail": "ASR не вернул слов — окно резать не по чему"}
    a, b, inside = snap(ws, start, end)
    if not inside:
        return {"ok": False, "detail": f"в окне {start}–{end} нет целых слов"}
    a = max(0.0, a - pad)
    b = b + pad

    ffmpeg, _ = _ffmpeg_bins()
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    r = subprocess.run([ffmpeg, "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", src,
                        "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", out],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(out):
        return {"ok": False, "detail": f"ffmpeg: {r.stderr[-300:]}"}

    text = _norm(" ".join(str(w.get("w", "")) for w in inside))
    open(sidecar(out), "w", encoding="utf-8").write(text + "\n")
    got = props(out)
    return {"ok": True, "out": out, "sidecar": sidecar(out), "text": text,
            "window": [round(a, 3), round(b, 3)], "words": len(inside),
            "seconds": got.get("seconds"),
            "detail": f"{got.get('seconds', 0):.2f} с, {len(inside)} слов"}


def line_windows(ws: list[dict], target: float = 10.0) -> list[tuple[float, float, int]]:
    """Кандидаты-окна около `target` секунд, разложенные по паузам между словами.

    Выбирать окно на глаз по волне — та же ручная работа, что и всё остальное здесь;
    длинная пауза внутри эталона слышна как склейка, поэтому режем по самым большим."""
    if len(ws) < 2:
        return []
    gaps = [(float(b["start"]) - float(a["end"]), i + 1)
            for i, (a, b) in enumerate(zip(ws, ws[1:]))]
    cuts = [0] + [i for _, i in sorted(gaps, reverse=True)[:12]] + [len(ws)]
    cuts = sorted(set(cuts))
    out = []
    for i, s in enumerate(cuts[:-1]):
        for e in cuts[i + 1:]:
            dur = float(ws[e - 1]["end"]) - float(ws[s]["start"])
            if GOOD_SECONDS[0] <= dur <= GOOD_SECONDS[1]:
                out.append((float(ws[s]["start"]), float(ws[e - 1]["end"]), e - s))
    out.sort(key=lambda w: abs((w[1] - w[0]) - target))
    return out[:5]


# ── эталон из произвольного видео/аудио ──────────────────────────────────────
# «Взять видео и забрать из него голос» — задача целиком: достать дорожку, найти чистый
# кусок ОДНОГО голоса и вырезать его так, чтобы транскрипт остался дословным. Руками это
# делается на глаз по волне, и на глаз же не видно двух вещей, которые слышны в клоне:
# длинной паузы внутри окна (звучит как склейка) и высокого фона между словами (клон
# наследует шум эталона вместе с голосом). Поэтому окна не выбираются, а ИЗМЕРЯЮТСЯ.

@dataclass
class Window:
    start: float
    end: float
    words: int
    gap: float          # самая длинная пауза внутри — «склейка» на слух
    floor: float        # фон между словами, dBFS — клон унаследует его вместе с голосом
    peak: float         # пик, dBFS — клиппованный источник даёт плохой эталон
    text: str = ""

    @property
    def seconds(self) -> float:
        return self.end - self.start

    def score(self, target: float) -> float:
        """Меньше — лучше. Веса подобраны так, чтобы длинная пауза и грязный фон били
        сильнее, чем несовпадение длительности: секунду туда-сюда ухо не заметит, а склейку
        и шипение — сразу."""
        s = abs(self.seconds - target) * 1.0
        s += max(0.0, self.gap - 0.35) * 8.0
        s += max(0.0, self.floor + 50.0) * 0.6      # тише −50 dBFS считаем чистым
        s += max(0.0, self.peak + 1.0) * 2.0        # ближе −1 dBFS к нулю — уже риск клиппинга
        return s


def extract_audio(src: str, out: str, *, rate: int = 24000) -> bool:
    """Видео или аудио → 24 кГц моно wav. Шумодав НЕ применяем: на голосе Ильи чистка
    ухудшала и транскрипт, и клон (`PRAVKI_REF.md`)."""
    ffmpeg, _ = _ffmpeg_bins()
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    r = subprocess.run([ffmpeg, "-y", "-i", src, "-vn", "-ac", "1", "-ar", str(rate),
                        "-c:a", "pcm_s16le", out], capture_output=True, text=True)
    return r.returncode == 0 and os.path.isfile(out)


def _samples(path: str):
    import wave

    import numpy as np
    with wave.open(path) as w:
        sr, n = w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    return np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0, sr


def _db(x) -> float:
    import numpy as np
    v = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    return -99.0 if v <= 1e-6 else round(20.0 * float(np.log10(v)), 1)


def measure_window(sig, sr: int, ws: list, a: float, b: float) -> tuple:
    """Пауза, фон между словами и пик окна.

    Фон меряется именно в ПРОМЕЖУТКАХ между словами: усреднять по всему окну бессмысленно —
    речь перевесит шум, и грязная запись получит хорошую оценку."""
    import numpy as np
    inside = [w for w in ws if w.get("start") is not None
              and w["start"] >= a - 1e-6 and w["end"] <= b + 1e-6]
    gap = max((float(y["start"]) - float(x["end"]) for x, y in zip(inside, inside[1:])),
              default=0.0)
    seg = sig[int(a * sr):int(b * sr)]
    peak = -99.0 if not len(seg) else round(20.0 * float(np.log10(
        max(float(np.max(np.abs(seg))), 1e-6))), 1)
    quiet = []
    for x, y in zip(inside, inside[1:]):
        s, e = int(float(x["end"]) * sr), int(float(y["start"]) * sr)
        if e - s > int(0.05 * sr):
            quiet.append(sig[s:e])
    floor = _db(np.concatenate(quiet)) if quiet else -99.0
    return gap, floor, peak


def candidates(path: str, ws: list, *, target: float = 10.0, limit: int = 5) -> list:
    """Окна-кандидаты по паузам, уже измеренные и отсортированные по качеству."""
    sig, sr = _samples(path)
    out = []
    for a, b, n in line_windows(ws, target=target):
        gap, floor, peak = measure_window(sig, sr, ws, a, b)
        text = _norm(" ".join(str(w.get("w", "")) for w in ws
                              if w.get("start") is not None
                              and w["start"] >= a - 1e-6 and w["end"] <= b + 1e-6))
        out.append(Window(a, b, n, gap, floor, peak, text))
    out.sort(key=lambda w: w.score(target))
    return out[:limit]


def grab(src: str, out: str, *, lang: str = "ru", target: float = 10.0,
         work: str | None = None) -> dict:
    """Видео/аудио → готовый эталон + дословный транскрипт рядом.

    Порядок: достать дорожку → пословные тайминги → измерить окна → вырезать лучшее по
    границам слов. Что выбрано и почему — в отчёте: молчаливый выбор здесь хуже плохого,
    потому что проверить его можно только ушами.
    """
    work = work or os.path.dirname(os.path.abspath(out)) or "."
    os.makedirs(work, exist_ok=True)
    full = os.path.join(work, "_grab_full.wav")
    if not extract_audio(src, full):
        return {"ok": False, "detail": f"не достал аудиодорожку из {src}"}
    ws = words(full, lang=lang)
    if not ws:
        return {"ok": False, "detail": "ASR не нашёл речи — резать нечего"}
    cands = candidates(full, ws, target=target)
    if not cands:
        return {"ok": False, "detail": f"нет окна длиной {GOOD_SECONDS[0]:.0f}–"
                                       f"{GOOD_SECONDS[1]:.0f} с без длинных пауз"}
    best = cands[0]
    # Пауза в полторы секунды внутри десятисекундного эталона — это уже не эталон: клон
    # наследует её как склейку, а речи остаётся меньше восьми секунд. Отказ с объяснением
    # полезнее молча выданного мусора.
    if best.gap > MAX_INNER_GAP:
        return {"ok": False,
                "detail": f"в лучшем окне пауза {best.gap:.1f} с (порог {MAX_INNER_GAP:.1f}) — "
                          "сплошной речи такой длины в источнике нет",
                "alternatives": [{"start": round(w.start, 2), "end": round(w.end, 2),
                                  "пауза": round(w.gap, 2)} for w in cands]}
    res = cut(full, out, best.start, best.end, lang=lang)
    if not res.get("ok"):
        return res
    res["window_quality"] = {"пауза": round(best.gap, 2), "фон": best.floor, "пик": best.peak}
    res["alternatives"] = [{"start": round(w.start, 2), "end": round(w.end, 2),
                            "seconds": round(w.seconds, 2), "пауза": round(w.gap, 2),
                            "фон": w.floor, "пик": w.peak, "text": w.text[:70]}
                           for w in cands[1:]]
    res["source_words"] = len(ws)
    return res
