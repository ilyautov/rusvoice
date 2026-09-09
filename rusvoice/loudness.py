# -*- coding: utf-8 -*-
"""Громкость озвучки: довести до −14 LUFS и сказать правду, если не довелось.

Наивный путь — `loudnorm` — на выходе клона не работает и, что хуже, не жалуется:
у F5 пики стоят у 0 dBFS при среднем около −18, так что ограничитель истинного пика
упирается РАНЬШЕ, чем набирается громкость. `loudnorm` при этом возвращает rc=0 и −15.3
вместо −14. Рабочий рецепт (`pipeline/talkingphoto.normalize`) другой: усиление плюс
`alimiter`, и обязательно `level=disabled` — с дефолтным `level=enabled` лимитер сам
подтягивает уровень обратно и возвращает клиппинг, молча обнуляя всё сделанное до него.

Здесь этот рецепт не форкается, а вызывается. Своё тут одно, и ради него модуль и написан:
**замер ПОСЛЕ и честный вердикт**. Раньше «нормализовал до −14» писалось по факту запуска
команды, а не по факту результата — и это ровно тот класс вранья измерителя, из-за которого
пять волн подряд виноватым оказывался не движок.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIPE = os.path.join(_ROOT, "pipeline")
if _PIPE not in sys.path:
    sys.path.insert(0, _PIPE)

TARGET = -14.0
TOLERANCE = 0.6          # ±0.6 LUFS — допуск движка (`inspect_cli.loudness.ok_minus14`)
TRUE_PEAK_MAX = -1.0


@dataclass
class Loudness:
    path: str
    integrated: float | None
    true_peak: float | None
    lra: float | None

    @property
    def crest(self) -> float | None:
        """Расстояние от среднего до пика. Оно и есть потолок: чем оно больше, тем раньше
        лимитер начнёт срезать вместо того, чтобы прибавлять."""
        if self.integrated is None or self.true_peak is None:
            return None
        return self.true_peak - self.integrated

    @property
    def ok(self) -> bool:
        return (self.integrated is not None and abs(self.integrated - TARGET) <= TOLERANCE
                and self.true_peak is not None and self.true_peak < TRUE_PEAK_MAX)


def measure(path: str) -> Loudness:
    """Интегральная громкость, истинный пик и LRA через `ebur128`.

    ⚠️ `peak=true` обязателен: без него ffmpeg истинный пик просто не печатает, а молчание
    парсера выглядит как «измерили, и там ничего»."""
    from rusvoice import engine as E
    ffmpeg, _ = E.ffmpeg_bins()
    if not ffmpeg:
        return Loudness(None, None, None)      # нечем мерить — не выдаём тишину за замер
    r = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-i", str(path),
                        "-af", "ebur128=framelog=quiet:peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    txt = r.stderr or ""
    if "Summary:" not in txt:
        return Loudness(str(path), None, None, None)

    def pick(label):
        m = re.search(rf"^\s*{label}:\s*(-?\d+(?:\.\d+)?)", txt, re.M)
        return float(m.group(1)) if m else None

    return Loudness(str(path), pick("I"), pick("Peak"), pick("LRA"))


def why_short(before: Loudness, after: Loudness) -> str:
    """Объяснить недобор — не «не получилось», а СКОЛЬКО и ПОЧЕМУ.

    Нужного усиления столько-то, но пик уже здесь, значит лимитер обязан срезать столько-то:
    вот куда делась громкость. Без этой арифметики недобор выглядит как случайность и
    лечится подкручиванием наугад."""
    if after.integrated is None:
        return "выход не измерился — ebur128 не отработал"
    need = TARGET - (before.integrated if before.integrated is not None else TARGET)
    got = after.integrated - (before.integrated if before.integrated is not None else 0.0)
    tail = ""
    if before.crest is not None:
        tail = (f"; вход: среднее {before.integrated:.1f} при пике {before.true_peak:.1f} "
                f"(размах {before.crest:.1f} дБ) — лимитер упирается раньше громкости")
    return (f"нужно было +{need:.1f} дБ, дошло +{got:.1f}, "
            f"итог {after.integrated:.1f} LUFS{tail}")


def normalize(src: str, out: str, *, target: float = TARGET, passes: int = 3) -> dict:
    """Усиление + `alimiter` (рецепт `talkingphoto.normalize`), затем ЗАМЕР выхода.

    Лимитер съедает часть усиления, поэтому проходов несколько: каждый следующий добирает
    остаток. Успех объявляется по замеру, а не по коду возврата ffmpeg.
    """
    from rusvoice import engine as E
    ffmpeg, _ = E.ffmpeg_bins()
    if not ffmpeg:
        return {"ok": False, "detail": "ffmpeg не найден: ни FFMPEG_BIN, ни PATH"}

    before = measure(src)
    if before.integrated is None:
        return {"ok": False, "detail": f"не измерилась громкость входа: {src}"}

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    gain, after = target - before.integrated, None
    for _ in range(max(1, passes)):
        r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(src), "-af",
                            f"volume={gain:.2f}dB,alimiter=limit={E.limiter_ceiling()}:level=disabled",
                            "-ar", "48000", "-ac", "1", str(out)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(out):
            return {"ok": False, "detail": f"ffmpeg: {(r.stderr or '')[-300:]}"}
        after = measure(out)
        if after.integrated is None:
            break
        if abs(after.integrated - target) <= 0.3:
            break
        gain += target - after.integrated

    res = {"ok": bool(after and after.ok), "out": out,
           "before": {"I": before.integrated, "TP": before.true_peak, "LRA": before.lra},
           "after": {"I": after.integrated if after else None,
                     "TP": after.true_peak if after else None,
                     "LRA": after.lra if after else None}}
    if res["ok"]:
        res["detail"] = (f"{before.integrated:.1f} → {after.integrated:.1f} LUFS, "
                         f"истинный пик {after.true_peak:.1f} dBFS")
    else:
        res["detail"] = why_short(before, after or Loudness(out, None, None, None))
        if after and after.true_peak is not None and after.true_peak >= TRUE_PEAK_MAX:
            res["detail"] += (f"; истинный пик {after.true_peak:.1f} dBFS — это клиппинг, "
                              "проверить, что у alimiter стоит level=disabled")
    return res
