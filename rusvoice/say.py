# -*- coding: utf-8 -*-
"""Канонический рецепт озвучки одной командой — и отчёт о том, что на самом деле сделано.

Рецепт: F5-клон → темп → громкость. Он существовал как последовательность ручных шагов, и
каждый шаг имел свой способ тихо разъехаться. Здесь они собраны, но не спрятаны: команда
печатает и текст по слоям, и итог замера громкости.

⚠️ **Энхансера в рецепте больше нет.** `resemble-enhance` стоял в нём с 11.07.2026, когда
клон снимали с неидеального эталона и чистка помогала. На нынешнем выходе F5 вердикт Ильи
07.09.2026 — «какое-то гавно», и на мягком `lambd=0.1` тоже («хуета прям боль»). Дальше
подкручивать параметр не стали: два замера подряд в одну сторону — это не настройка, а
неподходящий инструмент. `pipeline/enhance.py` остаётся на месте для других входов, но
`say` его не зовёт: держать ручку, которая заведомо портит, — ровно та молчаливая ловушка,
против которой весь пакет.

Три места, где `say` делает не то, что вышло бы «в лоб»:

  · **Транскрипт эталона.** Пару «аудио ↔ текст» связывает рецепт
    (`rusvoice.clone._ref_text_source`), а команда её ПОКАЗЫВАЕТ: видно, откуда взялся
    ref_text, и слышно предупреждение, если сайдкара рядом нет и в F5 уйдёт глобальный
    дефолт.
  · **Двойные ударения.** `clone_synth` сам зовёт `accentize`, поэтому в него уходит текст
    ПОСЛЕ произношения, но ДО ударений. Показываем при этом полный след — та же функция
    даст тот же результат внутри.
  · **Двойной темп.** `clone_synth` применяет `CLONE_ATEMPO` внутри, а `--tempo` работает
    после синтеза. Если внешний ключ задан, `say` про это скажет, а не ускорит дважды.

⚠️ Нужен интерпретатор, в котором стоят F5 и RUAccent (в репозитории это brew-питон:
`.venv` их не видит). Команда не падает стектрейсом — она отдаёт диагноз доктора.

⭐ Движок для синтеза больше НЕ нужен: рецепт переехал в `rusvoice.clone`. Раньше `say` —
единственная команда, ради которой существует всё остальное, — требовала рядом весь
видеотракт, и самостоятельная установка умела всё, кроме собственно озвучки.
"""
from __future__ import annotations

import os
import subprocess

from rusvoice import clone as C  # noqa: E402
from rusvoice import layers as L  # noqa: E402
from rusvoice import loudness as LD  # noqa: E402
from rusvoice import refaudio as R  # noqa: E402
from rusvoice import verify as V  # noqa: E402
from rusvoice import voices as V0  # noqa: E402


def blockers() -> list:
    """Проверки доктора, из-за которых синтеза не будет. Диагноз вместо стектрейса."""
    from rusvoice import doctor as D
    return [c for c in (D._check_accent(), D._check_f5()) if c.status == D.FAIL]


def _tempo(src: str, out: str, tempo: float) -> bool:
    from rusvoice import engine as E
    ffmpeg, _ = E.ffmpeg_bins()
    tempo = max(0.5, min(2.0, tempo))          # atempo за один проход принимает 0.5..2.0
    r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", src,
                        "-af", f"atempo={tempo:.3f}", out], capture_output=True, text=True)
    return r.returncode == 0 and os.path.isfile(out)


# Воздух после последнего слова. `clone_synth` кладёт 0.18с — это паддинг КЛИПА, рассчитанный
# на стык со следующей сценой. Для отдельного файла это и есть конец всего текста, и 0.18с
# слышно как захлопнутую дверь. ⭐ Вердикт Ильи 07.09.2026: «хвост нужен именно на конце всего
# текста, а не между предложениями» — сказано после слепой сверки трёх обработок хвоста клипа,
# где разницы он не услышал (весь спор шёл на −50 dBFS, ниже порога слышимости). Значит дело
# не в форме затухания, а в ДЛИНЕ паузы после последнего слова.
OUTRO_TAIL = 0.5


def say(text: str, out: str, *, ref: str | None = None, tempo: float | None = None,
        loud: bool = False, lang: str = "ru", outro: float | None = None,
        verify: bool = True) -> dict:
    """Текст → wav по канону. Возвращает отчёт по каждому шагу, включая пропущенные.

    ⭐ `tempo`/`outro` = None означает «взять у голоса, иначе канон» — и команда говорит,
    откуда взяла. Настройки живут у голоса, а не глобально, потому что они и есть его
    свойства: темп 1.12 у одного человека и 1.0 у другого, а один глобальный ключ пришлось
    бы переставлять при каждой смене голоса и однажды не переставить."""
    bad = blockers()
    if bad:
        return {"ok": False, "detail": "; ".join(f"{c.name}: {c.detail}" for c in bad)}

    steps, warnings = [], []

    # ① текст. В clone_synth уходит версия ДО ударений — он их ставит сам.
    spoken = L.apply(text, lang=lang, accent=False)
    trace = L.apply(text, lang=lang, accent=True)
    warnings += trace.warnings

    # ② эталон и его транскрипт — пара, которую движок сам не связывает.
    picked = None if ref else V0.get()
    ref = ref or C.default_ref()
    if picked and picked.get("ref") != ref:
        # Голос есть в реестре, но эталон пришёл другой (конфиг, зашитый путь). Тогда и
        # настройки голоса брать НЕЛЬЗЯ: они про другой эталон, а слышно это было бы как
        # «модель торопится», без единого сообщения о причине.
        picked = None
    if not ref:
        return {"ok": False,
                "detail": "голос не выбран. `rusvoice voice add <видео|аудио>` — достанет "
                          "эталон из чего угодно и запомнит; разовый файл — через --ref"}
    if not os.path.isfile(ref):
        return {"ok": False, "detail": f"эталона нет: {ref}"}
    eff, src = R.effective_ref_text(ref)
    named = f"голос «{picked_name}» · " if (picked_name := (picked or {}).get("name")) else ""
    steps.append(f"эталон: {named}{os.path.basename(ref)} · ref_text из «{src}» "
                 f"({len(eff.split())} слов)")

    # ②′ настройки голоса подставляются ТОЛЬКО туда, где вызывающий промолчал.
    vs = V0.settings() if picked is not None else {}
    if tempo is None:
        tempo = vs.get("tempo", 1.0)
        if "tempo" in vs:
            steps.append(f"темп {tempo} — из настроек голоса")
    if outro is None:
        outro = vs.get("outro", OUTRO_TAIL)
        if "outro" in vs:
            steps.append(f"хвост {outro} с — из настроек голоса")
    if not os.path.isfile(R.sidecar(ref)):
        warnings.append(f"рядом с эталоном нет {os.path.basename(R.sidecar(ref))} — в F5 "
                        f"уйдёт ref_text из «{src}»; если он не про это аудио, будут "
                        "галлюцинации префиксов")

    # ③ синтез
    base = os.path.splitext(out)[0]
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    ext_tempo = C._clone_param("CLONE_ATEMPO", "atempo", "1.0")
    try:
        if abs(float(ext_tempo or 1.0) - 1.0) > 1e-3 and abs(tempo - 1.0) > 1e-3:
            warnings.append(f"CLONE_ATEMPO={ext_tempo} применится ВНУТРИ синтеза, а --tempo "
                            f"{tempo} — после него: темп сложится дважды")
    except ValueError:
        pass
    try:
        cur = C.clone_synth(spoken.text, base + ".raw", ref=ref)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"синтез не отработал: {exc}"}
    steps.append(f"клон F5: {os.path.basename(cur)}")

    # ④ темп
    if abs(tempo - 1.0) > 1e-3:
        dst = base + ".spd.wav"
        if _tempo(cur, dst, tempo):
            cur = dst
            steps.append(f"темп: atempo={tempo:.3f}")
        else:
            warnings.append("atempo не отработал — темп не изменён")

    # ⑤ хвост: конец всего текста, а не стык между сценами (см. OUTRO_TAIL)
    if abs(outro - 0.18) > 1e-3:
        dst = base + ".tail.wav"
        try:
            C._pad_edges(cur, dst, head=0.18, tail=max(0.0, outro))
            cur = dst
            steps.append(f"хвост: {outro:.2f}с после последнего слова (у клипа было 0.18)")
        except Exception:  # noqa: BLE001 — хвост не повод терять готовый синтез
            warnings.append("хвост не переставлен — остался клиповый 0.18с")
    else:
        steps.append("хвост: клиповый 0.18с (--outro 0.18)")

    # ⑥ громкость
    loud_report = None
    if loud:
        loud_report = LD.normalize(cur, out)
        if loud_report["ok"]:
            steps.append(f"громкость: {loud_report['detail']}")
        else:
            warnings.append("громкость не доведена — " + loud_report["detail"])
            if not os.path.isfile(out):
                os.replace(cur, out)
    else:
        steps.append("громкость: пропущена (--loud)")
        if os.path.abspath(cur) != os.path.abspath(out):
            os.replace(cur, out)

    # ⑦ что РЕАЛЬНО прозвучало. Все проверки выше — до синтеза: они ловят предусмотренное.
    # Модель может уронить слово после них, и тогда не сработает ни одна (см. `verify`).
    ver = V.check(out, spoken.text, lang=lang) if verify else {"judged": False,
                                                              "detail": "пропущена (--no-verify)"}
    if not ver.get("judged"):
        steps.append("проверка речи: " + ver["detail"])
    elif ver["ok"]:
        steps.append("проверка речи: " + ver["detail"])
    else:
        warnings.append("речь не сошлась с текстом — " + ver["detail"])

    got = LD.measure(out)
    return {"ok": True, "out": out, "steps": steps, "warnings": warnings, "verify": ver,
            "text": trace.text, "changes": [(c.layer, c.before, c.after)
                                            for c in trace.changes],
            "loudness": {"I": got.integrated, "TP": got.true_peak},
            "loud_report": loud_report}
