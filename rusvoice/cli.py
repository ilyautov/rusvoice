# -*- coding: utf-8 -*-
"""CLI русской озвучки: `explain` — что тракт сделает с текстом, `doctor` — можно ли ему верить.

Синтез клона сам по себе не дефицит: F5-TTS открыт, русский чекпойнт чужой. Дефицит — слой
поверх него и возможность УВИДЕТЬ его работу до синтеза. Обе команды здесь не трогают звук
вообще: они про текст и про окружение, то есть про те два места, где у нас ломалось молча.

    python3 -m rusvoice explain "Claude Code на подписке MAX"
    python3 -m rusvoice explain --json файл.txt
    python3 -m rusvoice doctor

⚠️ Запускать тем же интерпретатором, каким будет идти синтез. Именно в этом и смысл:
`doctor` в `.venv` честно скажет, что RUAccent там нет и ударений не будет.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rusvoice import dicts as DI  # noqa: E402
from rusvoice import doctor as D  # noqa: E402
from rusvoice import layers as L  # noqa: E402
from rusvoice import lint as LN  # noqa: E402
from rusvoice import loudness as LD  # noqa: E402
from rusvoice import refaudio as R  # noqa: E402
from rusvoice import say as SAY  # noqa: E402
from rusvoice import voices as V0  # noqa: E402

_MARK = {D.OK: "✓", D.WARN: "!", D.FAIL: "✗"}


def _read_input(value: str) -> str:
    """Аргумент — либо сам текст, либо путь к файлу. Файл важнее: реплики живут в сценарии,
    а не в командной строке. `-` читает stdin."""
    if value == "-":
        return sys.stdin.read().strip()
    if os.path.isfile(value):
        return open(value, encoding="utf-8").read().strip()
    return value


def cmd_explain(args) -> int:
    text = _read_input(args.text)
    if not text:
        print("пустой вход", file=sys.stderr)
        return 2
    tr = L.apply(text, lang=args.lang, accent=not args.no_accent)
    if args.json:
        print(json.dumps({
            "source": tr.source,
            "text": tr.text,
            "changes": [{"layer": c.layer, "before": c.before, "after": c.after}
                        for c in tr.changes],
            "warnings": tr.warnings,
            "skipped": tr.skipped,
        }, ensure_ascii=False, indent=1))
        return 1 if tr.warnings else 0

    print(f"вход:  {tr.source}")
    print(f"выход: {tr.text}")
    by = tr.by_layer()
    if by:
        print("\nчто сделал каждый слой:")
        for layer, chs in by.items():
            print(f"  {layer}:")
            for c in chs:
                print(f"    {c}")
    else:
        print("\nни один слой ничего не изменил")
    if tr.skipped:
        print("\nне применялось:")
        for s in tr.skipped:
            print(f"  · {s}")
    if tr.warnings:
        print("\n⚠️")
        for w in tr.warnings:
            print(f"  {w}")
    return 1 if tr.warnings else 0


def cmd_doctor(args) -> int:
    checks = D.run(args.ref)
    if args.json:
        print(json.dumps({"status": D.worst(checks),
                          "checks": [{"name": c.name, "status": c.status,
                                      "detail": c.detail, "fix": c.fix} for c in checks]},
                         ensure_ascii=False, indent=1))
    else:
        for c in checks:
            print(f"{_MARK.get(c.status, '?')} {c.name}: {c.detail}")
            if c.fix and c.status != D.OK:
                print(f"    → {c.fix}")
    return {D.OK: 0, D.WARN: 0, D.FAIL: 1}[D.worst(checks)]


def cmd_dict_list(args) -> int:
    rows = DI.entries(args.kind, check=args.check)
    if args.json:
        print(json.dumps([{"key": e.key, "value": e.value, "source": e.source,
                           "live": e.live, "note": e.note} for e in rows],
                         ensure_ascii=False, indent=1))
    else:
        for e in rows:
            if args.file_only and e.source != "файл":
                continue
            mark = "" if e.live is None else ("✓ " if e.live else "✗ ")
            tail = f"   ← {e.note}" if e.note else ""
            print(f"{mark}{e.key} → {e.value}  [{e.source}]{tail}")
        c = DI.counts()[args.kind]
        print(f"\nвсего {c['итого']}, из них {c['из файла']} из файла")
    dead = [e for e in rows if e.live is False]
    if dead and not args.json:
        print(f"⚠️ мёртвых записей: {len(dead)} — слой их не применяет")
    return 1 if dead else 0


def cmd_dict_add(args) -> int:
    r = DI.add(args.kind, args.key, args.value, note=args.note or "",
               bump=args.bump, force=args.force)
    if args.json:
        print(json.dumps({"ok": r.ok, "detail": r.detail, "warnings": r.warnings,
                          "hint": r.hint}, ensure_ascii=False, indent=1))
        return 0 if r.ok else 1
    print(("✓ " if r.ok else "✗ ") + r.detail)
    for w in r.warnings:
        print(f"  ⚠️ {w}")
    if r.hint:
        print(f"  → {r.hint}")
    return 0 if r.ok else 1


def cmd_dict_hear(args) -> int:
    """Услышать запись словаря, а не прочитать её: `add` сверяет строку, F5 — другое дело."""
    r = DI.hear(args.kind, args.key, args.out, ref=args.ref)
    if not r.get("ok"):
        print("✗ " + r.get("detail", "не вышло"))
        return 1
    print(f"✓ {r['out']}")
    print(f"  пробник: {r['probe']}")
    print(f"  ожидается: «{r['expected']}»")
    for st in r.get("steps", []):
        print(f"  · {st}")
    for w in r.get("warnings", []):
        print(f"  ⚠️ {w}")
    return 0


def cmd_dict_bump(args) -> int:
    r = DI.bump_rules_version()
    print(("✓ " if r.ok else "✗ ") + r.detail)
    return 0 if r.ok else 1


def cmd_ref_check(args) -> int:
    checks = R.check(args.ref)
    if args.json:
        print(json.dumps({"status": D.worst(checks),
                          "checks": [{"name": c.name, "status": c.status,
                                      "detail": c.detail, "fix": c.fix} for c in checks]},
                         ensure_ascii=False, indent=1))
    else:
        for c in checks:
            print(f"{_MARK.get(c.status, '?')} {c.name}: {c.detail}")
            if c.fix and c.status != D.OK:
                print(f"    → {c.fix}")
    return {D.OK: 0, D.WARN: 0, D.FAIL: 1}[D.worst(checks)]


def cmd_ref_cut(args) -> int:
    r = R.cut(args.src, args.out, args.start, args.end, lang=args.lang, rate=args.rate)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r.get("ok") else 1
    if not r.get("ok"):
        print("✗ " + r["detail"])
        return 1
    print(f"✓ {r['out']} — {r['detail']}")
    print(f"  окно по границам слов: {r['window'][0]}–{r['window'][1]} с")
    print(f"  транскрипт → {r['sidecar']}")
    print(f"  {r['text']}")
    print("\n⚠️ прочитать транскрипт глазами: у эталона исходника нет, "
          "текст пришёл из ASR и ошибка в нём станет ошибкой клона")
    return 0


def cmd_ref_grab(args) -> int:
    r = R.grab(args.src, args.out, lang=args.lang, target=args.target)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r.get("ok") else 1
    if not r.get("ok"):
        print("✗ " + r["detail"])
        return 1
    q = r["window_quality"]
    print(f"речь: {r['source_words']} слов в источнике")
    print(f"✓ {r['out']} — {r['detail']}")
    print(f"  окно {r['window'][0]}–{r['window'][1]} с · пауза {q['пауза']} с · "
          f"фон {q['фон']} dBFS · пик {q['пик']} dBFS")
    print(f"  транскрипт → {r['sidecar']}")
    print(f"  {r['text']}")
    if r["alternatives"]:
        print("\nчто ещё подошло бы:")
        for w in r["alternatives"]:
            print(f"  {w['start']:7.2f}–{w['end']:7.2f} ({w['seconds']:5.2f} с) "
                  f"пауза {w['пауза']:.2f} · фон {w['фон']:.0f} · пик {w['пик']:.0f}"
                  f"   {w['text'][:50]}")
    print("\n⚠️ транскрипт пришёл из ASR — прочитать глазами: его ошибка станет ошибкой клона")
    return 0


def cmd_ref_windows(args) -> int:
    ws = R.words(args.src, lang=args.lang)
    got = R.line_windows(ws, target=args.target)
    if args.json:
        print(json.dumps([{"start": a, "end": b, "words": n} for a, b, n in got],
                         ensure_ascii=False, indent=1))
        return 0
    if not got:
        print("подходящих окон не нашлось — задать --start/--end руками")
        return 1
    for a, b, n in got:
        text = " ".join(str(w.get("w", "")) for w in ws
                        if w.get("start") is not None and a <= w["start"] and w["end"] <= b)
        print(f"{a:7.2f}–{b:7.2f}  ({b - a:5.2f} с, {n} слов)  {text[:90]}")
    return 0


def cmd_loud(args) -> int:
    if not args.out:
        m = LD.measure(args.src)
        if args.json:
            print(json.dumps({"path": m.path, "I": m.integrated, "TP": m.true_peak,
                              "LRA": m.lra, "ok": m.ok}, ensure_ascii=False, indent=1))
            return 0 if m.ok else 1
        if m.integrated is None:
            why = LD.no_measure_reason()
            print("✗ " + (why or "не измерилось — ebur128 не отработал"))
            if why:
                print("    → поставить ffmpeg или указать FFMPEG_BIN; `rusvoice doctor` покажет")
            return 1
        print(f"{'✓' if m.ok else '!'} {m.integrated:.1f} LUFS · истинный пик "
              f"{m.true_peak:.1f} dBFS · LRA {m.lra}")
        if not m.ok:
            print(f"    → цель {LD.TARGET:g} ±{LD.TOLERANCE:g} LUFS, "
                  f"пик ниже {LD.TRUE_PEAK_MAX:g} dBFS; размах {m.crest:.1f} дБ")
        return 0 if m.ok else 1

    r = LD.normalize(args.src, args.out, target=args.target)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["ok"] else 1
    print(("✓ " if r["ok"] else "✗ ") + r["detail"])
    return 0 if r["ok"] else 1


def cmd_say(args) -> int:
    r = SAY.say(_read_input(args.text), args.out, ref=args.ref,
                tempo=args.tempo, loud=not args.no_loud, lang=args.lang,
                outro=args.outro, verify=not args.no_verify)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["ok"] else 1
    if not r["ok"]:
        print("✗ " + r["detail"])
        return 1
    print(f"✓ {r['out']}")
    print(f"\nсказано: {r['text']}")
    if r["changes"]:
        print("правки текста:")
        for layer, before, after in r["changes"]:
            print(f"  {layer}: {before} → {after}")
    print("\nшаги:")
    for s in r["steps"]:
        print(f"  · {s}")
    ld = r["loudness"]
    if ld["I"] is not None:
        print(f"\nна выходе: {ld['I']:.1f} LUFS · пик {ld['TP']:.1f} dBFS")
    if r["warnings"]:
        print("\n⚠️")
        for w in r["warnings"]:
            print(f"  {w}")
    return 1 if r["warnings"] else 0


def cmd_voice_add(args) -> int:
    r = V0.add(args.src, args.name, target=args.target, lang=args.lang, as_is=args.as_is)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["ok"] else 1
    if not r["ok"]:
        print("✗ " + r["detail"])
        return 1
    print(f"✓ голос «{r['name']}» — {r['how']}")
    print(f"  {r['ref']}")
    print(f"  транскрипт: {r['text']}")
    if r["current"]:
        print("\n  выбран текущим — `rusvoice say \"…\" --out o.wav` уже работает без --ref")
    else:
        print(f"\n  сделать текущим: rusvoice voice use {r['name']}")
    print("\n⚠️ транскрипт прочитать глазами: его ошибка станет ошибкой клона")
    return 0


def cmd_voice_list(args) -> int:
    cur, voices = V0.all_voices()
    if args.json:
        print(json.dumps({"current": cur, "voices": voices}, ensure_ascii=False, indent=1))
        return 0
    if not voices:
        print("голосов нет. `rusvoice voice add <видео|аудио>` — достанет эталон из чего угодно")
        return 0
    for name in sorted(voices):
        v = voices[name]
        opts = " · ".join(f"{k}={v[k]}" for k in V0.SETTINGS if k in v)
        print(f"{'→' if name == cur else ' '} {name:<16} {v['ref']}" + (f"   [{opts}]" if opts else ""))
    return 0


def cmd_voice_use(args) -> int:
    r = V0.use(args.name)
    print(("✓ текущий голос: " + r["name"]) if r["ok"] else "✗ " + r["detail"])
    return 0 if r["ok"] else 1


def cmd_voice_set(args) -> int:
    r = V0.set_param(args.key, args.value, args.name)
    if not r["ok"]:
        print("✗ " + r["detail"])
        return 1
    print(f"✓ {r['name']}: {r['key']} = {r['value']}  ({r['what']})")
    return 0


def cmd_voice_remove(args) -> int:
    r = V0.remove(args.name)
    if not r["ok"]:
        print("✗ " + r["detail"])
        return 1
    print(f"✓ убран из реестра: {r['name']}")
    print(f"  файл НЕ удалён: {r['ref']}")
    print(f"  текущий теперь: {r['current'] or 'нет'}")
    return 0


def cmd_ui(args) -> int:
    try:
        from rusvoice import webui
    except ImportError as exc:
        print(f"нужен fastapi/uvicorn в этом интерпретаторе: {exc}", file=sys.stderr)
        return 2
    return webui.serve(args.host, args.port)


def _plural(n, one, few, many):
    """1 реплика · 2 реплики · 5 реплик. Мелочь, но «1 реплик» в отчёте читается как баг."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def cmd_lint(args) -> int:
    lines = LN.run(args.src, lang=args.lang, accent=not args.no_accent)
    note = LN.environment_note(not args.no_accent)
    bad = [ln for ln in lines if ln.issues]
    if args.json:
        print(json.dumps([{"idx": ln.idx, "label": ln.label, "text": ln.text,
                           "spoken": ln.spoken,
                           "issues": [{"kind": i.kind, "level": i.level,
                                       "detail": i.detail, "fix": i.fix}
                                      for i in ln.issues]} for ln in lines],
                         ensure_ascii=False, indent=1))
        return 1 if LN.defects(lines) else 0
    if note:
        print(f"⚠️ {note}")
    for ln in bad:
        head = f"#{ln.idx}" + (f" · {ln.label}" if ln.label else "")
        print(f"\n{head}\n  {ln.text}")
        for i in sorted(ln.issues, key=lambda x: x.level != LN.DEFECT):
            mark = "✗" if i.level == LN.DEFECT else "·"
            print(f"  {mark} [{i.kind}] {i.detail}")
            if i.fix:
                print(f"      → {i.fix}")
    kinds = {}
    for ln in bad:
        for i in ln.issues:
            kinds[i.kind] = kinds.get(i.kind, 0) + 1
    tail = ", ".join(f"{k}: {v}" for k, v in sorted(kinds.items()))
    bug = LN.defects(lines)
    if not bad:
        print(f"чисто: {len(lines)} проверено")
        return 0
    word = _plural(len(bad), "реплика", "реплики", "реплик")
    print(f"\n{len(bad)} {word} из {len(lines)} с замечаниями" + (f" ({tail})" if tail else ""))
    if not bug:
        print("дефектов нет — только риски, код выхода 0")
    return 1 if bug else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rusvoice", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("explain", help="показать, что текстовый тракт сделает с репликой")
    e.add_argument("text", help="текст, путь к файлу или - для stdin")
    e.add_argument("--json", action="store_true")
    e.add_argument("--lang", default="ru")
    e.add_argument("--no-accent", action="store_true",
                   help="без ударений (путь edge-голоса: он «+» не уважает)")
    e.set_defaults(fn=cmd_explain)

    d = sub.add_parser("doctor", help="проверить окружение до синтеза")
    d.add_argument("--ref", default=None, help="путь к референсу голоса")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_doctor)

    dd = sub.add_parser("dict", help="словари произношения: посмотреть и дописать")
    ds = dd.add_subparsers(dest="dcmd", required=True)

    kinds = sorted(DI.KINDS)
    dl = ds.add_parser("list", help="показать записи словаря")
    dl.add_argument("kind", choices=kinds)
    dl.add_argument("--check", action="store_true",
                    help="проверить каждую запись слоем: живая или перекрыта")
    dl.add_argument("--file-only", action="store_true", help="без встроенных записей")
    dl.add_argument("--json", action="store_true")
    dl.set_defaults(fn=cmd_dict_list)

    da = ds.add_parser("add", help="дописать запись с проверкой, что она работает")
    da.add_argument("kind", choices=kinds)
    da.add_argument("key", help="ключ; смысл зависит от словаря (см. --help словаря)")
    da.add_argument("value", help="как читать")
    da.add_argument("--note", default="", help="чем проверено (пишется в _verified)")
    da.add_argument("--bump", action="store_true",
                    help="сразу поднять PRONOUNCE_RULES_VERSION (инвалидирует аудио-кэш)")
    da.add_argument("--force", action="store_true",
                    help="писать вопреки перезаписи/негативному списку/неживой записи")
    da.add_argument("--json", action="store_true")
    da.set_defaults(fn=cmd_dict_add)

    dh = ds.add_parser("hear", help="озвучить пробник записи — услышать, а не прочитать")
    dh.add_argument("kind", choices=kinds)
    dh.add_argument("key", help="ключ, уже лежащий в словаре")
    dh.add_argument("--out", default="dict_probe.wav")
    dh.add_argument("--ref", help="эталон голоса")
    dh.set_defaults(fn=cmd_dict_hear)

    db = ds.add_parser("bump", help="поднять версию правил в обоих зеркалах")
    db.set_defaults(fn=cmd_dict_bump)

    rf = sub.add_parser("ref", help="эталон голоса: сверка пары аудио↔транскрипт и нарезка")
    rs = rf.add_subparsers(dest="rcmd", required=True)

    rc = rs.add_parser("check", help="сверить эталон с тем ref_text, что реально уйдёт в F5")
    rc.add_argument("--ref", default=None)
    rc.add_argument("--json", action="store_true")
    rc.set_defaults(fn=cmd_ref_check)

    rw = rs.add_parser("windows", help="показать окна-кандидаты, разложенные по паузам")
    rw.add_argument("src")
    rw.add_argument("--target", type=float, default=10.0)
    rw.add_argument("--lang", default="ru")
    rw.add_argument("--json", action="store_true")
    rw.set_defaults(fn=cmd_ref_windows)

    rr = rs.add_parser("cut", help="вырезать окно по границам слов + дословный транскрипт")
    rr.add_argument("src")
    rr.add_argument("out")
    rr.add_argument("--start", type=float, required=True)
    rr.add_argument("--end", type=float, required=True)
    rr.add_argument("--rate", type=int, default=24000)
    rr.add_argument("--lang", default="ru")
    rr.add_argument("--json", action="store_true")
    rr.set_defaults(fn=cmd_ref_cut)

    rg = rs.add_parser("grab", help="видео/аудио → готовый эталон: лучшее окно + транскрипт")
    rg.add_argument("src", help="видео или аудио — дорожка достаётся сама")
    rg.add_argument("--out", required=True)
    rg.add_argument("--target", type=float, default=10.0, help="желаемая длина окна, с")
    rg.add_argument("--lang", default="ru")
    rg.add_argument("--json", action="store_true")
    rg.set_defaults(fn=cmd_ref_grab)

    vc = sub.add_parser("voice", help="голоса: добавить, выбрать, настроить")
    vs = vc.add_subparsers(dest="voice_cmd", required=True)

    va = vs.add_parser("add", help="видео/аудио/готовый эталон → сохранённый голос")
    va.add_argument("src", help="видео, аудио или готовый эталон")
    va.add_argument("--name", default=None, help="как назвать (по умолчанию — имя файла)")
    va.add_argument("--target", type=float, default=10.0, help="желаемая длина окна, с")
    va.add_argument("--as-is", action="store_true",
                    help="взять файл как есть, не искать окно (нужен .txt рядом)")
    va.add_argument("--lang", default="ru")
    va.add_argument("--json", action="store_true")
    va.set_defaults(fn=cmd_voice_add)

    vl = vs.add_parser("list", help="какие голоса есть и какой текущий")
    vl.add_argument("--json", action="store_true")
    vl.set_defaults(fn=cmd_voice_list)

    vu = vs.add_parser("use", help="сделать голос текущим")
    vu.add_argument("name")
    vu.set_defaults(fn=cmd_voice_use)

    vt = vs.add_parser("set", help="настройка голоса: " + ", ".join(V0.SETTINGS))
    vt.add_argument("key", choices=sorted(V0.SETTINGS))
    vt.add_argument("value")
    vt.add_argument("--name", default=None, help="по умолчанию — текущий голос")
    vt.set_defaults(fn=cmd_voice_set)

    vr = vs.add_parser("remove", help="убрать из реестра (файл остаётся)")
    vr.add_argument("name")
    vr.set_defaults(fn=cmd_voice_remove)

    lo = sub.add_parser("loud", help="замерить громкость; с --out — довести до −14 LUFS")
    lo.add_argument("src")
    lo.add_argument("--out", help="без него команда только меряет и судит")
    lo.add_argument("--target", type=float, default=LD.TARGET)
    lo.add_argument("--json", action="store_true")
    lo.set_defaults(fn=cmd_loud)

    sy = sub.add_parser("say", help="текст → wav по каноническому рецепту")
    sy.add_argument("text", help="текст, путь к файлу или - для stdin")
    sy.add_argument("--out", required=True)
    sy.add_argument("--ref", default=None, help="эталон голоса (транскрипт возьмётся из .txt)")
    sy.add_argument("--tempo", type=float, default=None,
                    help="atempo после синтеза; без ключа — настройка голоса, иначе 1.0")
    sy.add_argument("--no-loud", action="store_true", help="не доводить громкость")
    sy.add_argument("--no-verify", action="store_true",
                    help="не сверять ASR-ом, что прозвучало (сверка стоит ~секунды)")
    sy.add_argument("--outro", type=float, default=None,
                    help=f"воздух после последнего слова, сек (по умолчанию {SAY.OUTRO_TAIL}; "
                         "0.18 — как у клипа внутри ролика)")
    sy.add_argument("--lang", default="ru")
    sy.add_argument("--json", action="store_true")
    sy.set_defaults(fn=cmd_say)

    ui = sub.add_parser("ui", help="интерфейс в браузере поверх тех же команд")
    ui.add_argument("--host", default="127.0.0.1",
                    help="⚠️ дефолт не косметика: ручки пишут в словари и запускают синтез")
    ui.add_argument("--port", type=int, default=8765)
    ui.set_defaults(fn=cmd_ui)

    li = sub.add_parser("lint", help="найти реплики, которые прозвучат не так — до рендера")
    li.add_argument("src", help="scenario.json, текстовый файл, - для stdin или сам текст")
    li.add_argument("--lang", default="ru")
    li.add_argument("--no-accent", action="store_true")
    li.add_argument("--json", action="store_true")
    li.set_defaults(fn=cmd_lint)
    return p


def _utf8_output() -> None:
    """Заставить вывод быть UTF-8 независимо от кодировки консоли.

    ⚠️ Иначе на Windows пакет не работает вообще: консоль там по умолчанию не UTF-8, а
    весь вывод здесь русский — `rusvoice --help` падает с `UnicodeEncodeError` на первом
    же «↔» или «✓». Кириллицу cp1251 ещё вытянет, стрелки и галочки — нет, и поэтому
    ошибка выглядит случайной: одна команда работает, соседняя падает.

    `errors="replace"` вторым рубежом: сломанный символ должен портить один знак, а не
    ронять команду целиком.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # перенаправленный или подменённый поток — не наше дело


def main(argv=None) -> int:
    _utf8_output()
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
