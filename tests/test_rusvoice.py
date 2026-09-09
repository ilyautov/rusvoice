# -*- coding: utf-8 -*-
"""Пакет `rusvoice`: прослеживаемый текстовый слой + доктор окружения.

Тесты гоняются в `.venv`, где НЕТ ни RUAccent, ни F5 — и это здесь не помеха, а условие:
ровно в таком окружении движок молча синтезировал без ударений, и пакет обязан про это
СКАЗАТЬ, а не промолчать. Поэтому проверяется и путь «зависимости на месте» (через подмену),
и путь «их нет».

Главный замок — `matches_engine`: объяснение обязано быть про тот же текст, который уйдёт
в синтез. Разъехавшееся объяснение хуже отсутствующего.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rusvoice import doctor as D  # noqa: E402
from rusvoice import layers as L  # noqa: E402

SAMPLE = "Claude Code на подписке MAX: лендинг за 5x дешевле."


def test_trace_matches_the_engine_path():
    """Инвариант: пословный разбор не имеет права менять итоговый текст."""
    for text in (SAMPLE, "Простая реплика без брендов.", "", "ТЗ на API готово."):
        assert L.matches_engine(text), text


def test_trace_matches_engine_without_accent():
    assert L.matches_engine(SAMPLE, accent=False)


def test_layers_are_attributed_not_just_diffed():
    """Ценность пакета — не «текст изменился», а КТО его изменил."""
    tr = L.apply(SAMPLE, accent=False)
    by = tr.by_layer()
    assert "бренды" in by, by
    assert any(c.before.startswith("Claude") for c in by["бренды"])
    assert "твёрдая э" in by and any("лендинг" in c.before for c in by["твёрдая э"])


def test_diff_narrows_to_the_part_that_actually_changed():
    """⭐ Слой умеет менять ЧИСЛО слов («5x» → «пять раз»), и пословная пара тогда врёт.
    Но подставлять вместо неё всю строку — врать иначе: на экране это выглядело так, будто
    слой переписал фразу целиком, хотя тронул один токен."""
    ch = L._diff("множители", "за 5x дешевле, чем тендер", "за пять раз дешевле, чем тендер")
    assert len(ch) == 1 and (ch[0].before, ch[0].after) == ("5x", "пять раз")


def test_diff_marks_a_pure_insertion_and_deletion():
    assert L._diff("x", "было слово", "было")[0].after == L._GONE
    assert L._diff("x", "было", "было слово")[0].before == L._GONE


def test_diff_is_silent_when_nothing_changed():
    assert L._diff("x", "текст", "текст") == []


def test_multipliers_are_attributed_to_one_token():
    """Тот же замок, но через настоящий слой: разбор обязан назвать токен, а не фразу."""
    tr = L.apply("лендинг за 5x дешевле", accent=False)
    mult = tr.by_layer().get("множители")
    assert mult and mult[0].before == "5x", mult


def test_layer_order_matches_pronounce():
    """Порядок слоёв продублирован; разъехавшись с `pronounce_ru`, он начнёт врать про
    авторство правок, оставаясь правдой про результат."""
    from rusvoice import pronounce as P
    src = open(P.__file__, encoding="utf-8").read()   # у самого модуля, а не по пути:
    #                                                   слой переехал в rusvoice, путь бы устарел
    body = src[src.index("def pronounce_ru"):src.index("def pronounce_ru") + 1800]
    seen = [fn for _, fn, _, _ in L._LAYERS if fn in body]
    positions = [body.index(fn) for fn in seen]
    assert positions == sorted(positions), "порядок слоёв разошёлся с pronounce_ru"
    assert len(seen) == len(L._LAYERS), "слой из _LAYERS не найден в pronounce_ru"


def test_missing_layer_is_reported_not_swallowed(monkeypatch):
    """Слой переименовали в pipeline — пакет обязан сказать, а не тихо его пропустить."""
    monkeypatch.setattr(L, "_LAYERS", L._LAYERS + [("выдуманный", "_нет_такого", None, None)])
    tr = L.apply("текст", accent=False)
    assert any("выдуманный" in w for w in tr.warnings)


def test_optional_layer_is_listed_as_skipped():
    tr = L.apply("текст", accent=False)
    assert any("PRONOUNCE_NUMBERS" in s for s in tr.skipped)


def test_bypass_env_is_reported(monkeypatch):
    monkeypatch.setenv("PRONOUNCE", "0")
    tr = L.apply(SAMPLE, accent=False)
    assert tr.text == SAMPLE
    assert any("PRONOUNCE=0" in s for s in tr.skipped)


def test_non_russian_is_left_alone():
    tr = L.apply(SAMPLE, lang="en", accent=False)
    assert tr.text == SAMPLE
    assert any("только для русского" in s for s in tr.skipped)


def test_missing_accentizer_warns_loudly(monkeypatch):
    """⭐⭐ Тот самый молчаливый провал: RUAccent нет → текст уходит в синтез без ударений,
    и правки словаря «не работают». Пакет обязан назвать интерпретатор."""
    from rusvoice import accentize as A
    monkeypatch.setattr(A, "_load_accentizer", lambda: None)
    tr = L.apply("реплика", accent=True)
    assert any("RUAccent недоступен" in w and sys.executable in w for w in tr.warnings)


def test_accent_layer_is_attributed_when_available(monkeypatch):
    from rusvoice import accentize as A
    monkeypatch.setattr(A, "_load_accentizer", lambda: (lambda s: s))
    monkeypatch.setattr(A, "accentize_ru", lambda s: s.replace("голос", "г+олос"))
    tr = L.apply("это не тот голос", accent=True)
    assert tr.text.endswith("г+олос")
    assert any(c.layer == "ударения" for c in tr.changes)


# ── линт ударений ────────────────────────────────────────────────────────────

def test_monosyllable_mark_is_flagged():
    """⭐⭐ F5 отыгрывает «+» как НАЖИМ. На слове с одной гласной ударение и так однозначно,
    поэтому метка там звучит как «нейрослоп»."""
    w = L.stress_warnings("это н+е тот голос")
    assert w and "односложном" in w[0]


def test_multisyllable_mark_is_fine():
    """«э-то» двусложное — метка там законна. Замок против слишком жадного правила."""
    assert L.stress_warnings("+это не тот г+олос") == []


def test_double_mark_in_one_word_is_flagged():
    assert any("две метки" in w for w in L.stress_warnings("пр+ив+ет"))


# ── доктор ───────────────────────────────────────────────────────────────────

def test_doctor_runs_and_names_the_interpreter():
    checks = D.run()
    names = {c.name: c for c in checks}
    assert names["интерпретатор"].detail == sys.executable
    assert D.worst(checks) in (D.OK, D.WARN, D.FAIL)


def test_doctor_flags_bad_hashseed(monkeypatch):
    """Пустой seed валит дочерний процесс F5. На одном батче звук возвращается, поэтому
    в логах это выглядит безобидно — и именно поэтому проверка нужна."""
    from rusvoice import clone as C
    monkeypatch.setattr(C, "clone_available", lambda: True)   # в `.venv` F5 нет
    monkeypatch.setenv("PYTHONHASHSEED", "")
    c = D._check_hashseed()
    assert c.status == D.WARN and "config_init_hash_seed" in c.detail
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    assert D._check_hashseed().status == D.OK


def test_doctor_reference_check_is_not_a_second_copy(tmp_path, monkeypatch):
    """⚠️ Своя копия проверки эталона в докторе пережила правку движка и сверяла сайдкар с
    переменной окружения — не с эффективным `ref_text`. Копия устаревает МОЛЧА и врёт ровно
    там, где на неё положились, поэтому проверка ровно одна."""
    ref = _tone(tmp_path / "r.wav", 10.0)
    (tmp_path / "r.txt").write_text("транскрипт", encoding="utf-8")
    assert D._check_reference(ref) == R.check(ref)


def test_doctor_flags_ref_text_divergence(tmp_path, monkeypatch):
    """F5_REF_TEXT обязан совпадать с транскриптом ДОСЛОВНО, иначе F5 галлюцинирует
    префикс (+13% WER) — и это слышно как «модель мямлит», а не как ошибка настройки."""
    ref = _tone(tmp_path / "ref.wav", 10.0)
    (tmp_path / "ref.txt").write_text("правильный транскрипт", encoding="utf-8")
    monkeypatch.setenv("F5_REF_TEXT", "совсем другой текст")
    got = {c.name: c for c in D._check_reference(ref)}
    assert got["дословность"].status == D.FAIL


def test_doctor_reports_missing_reference(tmp_path):
    assert D._check_reference(str(tmp_path / "нет.wav"))[0].status == D.FAIL


def test_doctor_counts_dictionaries():
    got = {c.name: c for c in D._check_dicts()}
    assert "словарь бренды" in got
    assert "записей" in got["словарь бренды"].detail


def test_worst_is_the_worst():
    mk = lambda s: D.Check("x", s, "")  # noqa: E731
    assert D.worst([mk(D.OK), mk(D.WARN)]) == D.WARN
    assert D.worst([mk(D.OK), mk(D.WARN), mk(D.FAIL)]) == D.FAIL
    assert D.worst([mk(D.OK)]) == D.OK


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli(*args):
    return subprocess.run([sys.executable, "-m", "rusvoice", *args],
                          capture_output=True, text=True, cwd=ROOT, timeout=120)


def test_cli_explain_json_shape():
    r = _cli("explain", "--json", "--no-accent", SAMPLE)
    assert r.returncode in (0, 1), r.stderr[-400:]
    d = json.loads(r.stdout)
    assert d["source"] == SAMPLE and d["text"] != SAMPLE
    assert {"layer", "before", "after"} <= set(d["changes"][0])


def test_cli_explain_reads_a_file(tmp_path):
    p = tmp_path / "line.txt"
    p.write_text(SAMPLE, encoding="utf-8")
    r = _cli("explain", "--json", "--no-accent", str(p))
    assert json.loads(r.stdout)["source"] == SAMPLE


def test_cli_explain_rejects_empty():
    r = _cli("explain", "")
    assert r.returncode == 2


def test_cli_doctor_json_shape():
    r = _cli("doctor", "--json")
    d = json.loads(r.stdout)
    assert d["status"] in (D.OK, D.WARN, D.FAIL)
    assert any(c["name"] == "интерпретатор" for c in d["checks"])


def test_cli_help_runs():
    r = _cli("--help")
    assert r.returncode == 0 and "explain" in r.stdout and "doctor" in r.stdout


# ── словари ──────────────────────────────────────────────────────────────────

from rusvoice import dicts as DI  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Копия словарей в tmp: тесты не имеют права трогать pipeline/*.json.

    Подменяются оба конца — путь у `rusvoice.dicts` и путь-константа у `pronounce`,
    иначе запись пойдёт в копию, а чтение останется на настоящем файле."""
    from rusvoice import pronounce as P
    kinds, attrs = {}, {"brands": "_JSON_PATH", "hard-e": "_HARD_E_PATH",
                        "stress": "_STRESS_PATH"}
    for name, k in DI.KINDS.items():
        dst = tmp_path / os.path.basename(k.path)
        dst.write_text(open(k.path, encoding="utf-8").read(), encoding="utf-8")
        kinds[name] = DI.Kind(k.name, str(dst), k.loader, k.applier, k.key_hint, k.builtin)
        monkeypatch.setattr(P, attrs[name], str(dst))
    monkeypatch.setattr(DI, "KINDS", kinds)
    return tmp_path


def test_counts_ignore_metadata_keys(sandbox):
    """⭐⭐ `len(json.load(...))` врал: `_comment`/`_limits`/`_verified` — тоже ключи.
    Словарь ударений с ОДНОЙ проверенной парой выглядел как четыре записи."""
    raw = json.load(open(DI.KINDS["stress"].path, encoding="utf-8"))
    assert len(raw) > DI.counts()["stress"]["из файла"], "в файле есть служебные ключи"
    assert DI.counts()["stress"]["из файла"] == len([k for k in raw if not k.startswith("_")])


def test_counts_separate_builtin_from_file(sandbox):
    """У брендов файл — лишь расширение поверх встроенного BRANDS, и «16 записей» было
    отчётом про расширение, а не про работающий словарь."""
    c = DI.counts()["brands"]
    assert c["итого"] > c["из файла"] > 0


def test_doctor_dict_numbers_match_the_loader(sandbox):
    got = {c.name: c for c in D._check_dicts()}
    n = DI.counts()["stress"]["итого"]
    assert f"{n} записей" in got["словарь ударения-переписью"].detail


def test_doctor_notices_broken_json(sandbox):
    """Загрузчик глотает битый JSON молча (чтобы не валить синтез) — значит сказать
    про это обязан кто-то другой."""
    open(DI.KINDS["hard-e"].path, "w", encoding="utf-8").write("{ это не json")
    got = {c.name: c for c in D._check_dicts()}
    assert got["словарь твёрдая э"].status == D.FAIL


def test_add_writes_and_layer_picks_it_up(sandbox):
    r = DI.add("brands", "Netlify", "Нетлифай")
    assert r.ok, r.detail
    from rusvoice import pronounce as P
    assert "Нетлифай" in P.pronounce_ru("деплой на Netlify")


def test_add_keeps_metadata_and_order(sandbox):
    """Порядок ключей в brands_ru.json смысловой (банки, площадки, сервисы) — алфавит
    стёр бы его и раздул дифф на одну строку до всего файла."""
    before = json.load(open(DI.KINDS["brands"].path, encoding="utf-8"))
    DI.add("brands", "Netlify", "Нетлифай")
    after = json.load(open(DI.KINDS["brands"].path, encoding="utf-8"))
    assert list(after)[:len(before)] == list(before)
    assert after["_comment"] == before["_comment"]
    assert list(after)[-1] == "Netlify"


def test_add_refuses_to_touch_soft_words(sandbox):
    """⭐ Сплошного правила «е→э» нет: словарь тем и оправдан. Негативный список надо
    спрашивать ДО записи, а не узнавать из покрасневшего теста."""
    r = DI.add("hard-e", "крем", "крэм")
    assert not r.ok and any("крем" in w for w in r.warnings)
    assert "крем" not in json.load(open(DI.KINDS["hard-e"].path, encoding="utf-8"))


def test_add_refuses_silent_overwrite(sandbox):
    r = DI.add("hard-e", "тренд", "трэнт")
    assert not r.ok and "уже есть" in r.detail
    assert DI.add("hard-e", "тренд", "трэнт", force=True).ok


def test_add_rejects_empty(sandbox):
    assert not DI.add("brands", "  ", "что-то").ok
    assert not DI.add("brands", "X", "").ok


def test_add_rejects_unknown_kind(sandbox):
    assert not DI.add("выдуманный", "a", "б").ok


def test_add_note_goes_into_verified(sandbox):
    """У словаря ударений своя дисциплина: «только ПРОВЕРЕННЫЕ пары». Чем проверено —
    в `_verified`, рядом с записью, а не в голове автора."""
    DI.add("stress", "карьер", "карьёр", note="edge-синтез 6/6, mid-sentence")
    data = json.load(open(DI.KINDS["stress"].path, encoding="utf-8"))
    assert data["_verified"]["карьер"].startswith("edge-синтез")
    assert data["_verified"]["рассеяние"], "прежние пометки не затёрты"


def test_add_reminds_about_the_audio_cache(sandbox):
    """⭐⭐ Правка словаря сама по себе кэш НЕ инвалидирует: в хэш идёт сырой vo.
    Без напоминания «фикс» не доезжает до перерендера и выглядит как несработавший."""
    r = DI.add("brands", "Netlify", "Нетлифай")
    assert "кэш" in r.hint and "bump" in r.hint


def test_list_check_marks_entries_live(sandbox):
    rows = DI.entries("hard-e", check=True)
    assert rows and all(e.live for e in rows)


def test_list_check_reports_a_dead_entry(sandbox, monkeypatch):
    """Ветка «запись есть, а слой её не применяет». Естественный такой словарь собрать
    трудно (побеждает самая длинная основа), поэтому слой подменяется — проверяется
    именно отчёт, а не устройство pronounce."""
    from rusvoice import pronounce as P
    monkeypatch.setattr(P, "_apply_hard_e", lambda t, table=None: t)
    dead = [e for e in DI.entries("hard-e", check=True) if not e.live]
    assert dead and "перекрыта" in dead[0].note


def test_add_refuses_an_entry_that_would_not_apply(sandbox, monkeypatch):
    from rusvoice import pronounce as P
    monkeypatch.setattr(P, "_apply_brands", lambda t, brands=None: t)
    r = DI.add("brands", "Netlify", "Нетлифай")
    assert not r.ok and "не доедет" in r.detail


def test_soft_words_are_actually_left_alone():
    """Список-сторож против того, чтобы новая основа в словаре твёрдой «э» задела чужое
    слово (кеш → кем? модел → модем?).

    ⚠️ Раньше здесь сверялась КОПИЯ этого списка в `tests/test_pronounce.py` — то есть
    охранялся сам факт существования копии, а после выноса пакета в отдельный репозиторий
    проверка просто не нашла бы файл. Теперь список один, а проверяется то, ради чего он
    заведён: слова обязаны проходить слой нетронутыми."""
    from rusvoice import pronounce as P
    src = ", ".join(DI.SOFT_WORDS)
    assert P.pronounce_ru(src) == src


# ── версия правил ────────────────────────────────────────────────────────────

@pytest.fixture
def version_sites(tmp_path, monkeypatch):
    """СИНТЕТИЧЕСКИЕ два места. Настоящее место сейчас одно — `rusvoice/pronounce.py`, —
    но механизм рассчитан на несколько, и именно это здесь и проверяется: если копии
    когда-нибудь заведутся снова, `bump` обязан двигать все, а разъезд — отказывать."""
    a, b = tmp_path / "html_reel.py", tmp_path / "footage.py"
    a.write_text('x = 1\nPRONOUNCE_RULES_VERSION = "3"\ny = 2\n', encoding="utf-8")
    b.write_text('_PRONOUNCE_RULES_VERSION = "3"\n', encoding="utf-8")
    monkeypatch.setattr(DI, "_VERSION_SITES", (
        (str(a), r'^PRONOUNCE_RULES_VERSION = "(\d+)"$'),
        (str(b), r'^_PRONOUNCE_RULES_VERSION = "(\d+)"$')))
    return a, b


def test_bump_moves_every_site_it_knows(version_sites):
    """Поднять одну копию из нескольких — значит получить не находящийся сайдкар вместо
    инвалидации кэша. Механизм обязан двигать все места сразу."""
    a, b = version_sites
    assert DI.rules_version() == "3"
    assert DI.bump_rules_version().ok
    assert DI.rules_version() == "4"
    assert 'PRONOUNCE_RULES_VERSION = "4"' in a.read_text(encoding="utf-8")
    assert '_PRONOUNCE_RULES_VERSION = "4"' in b.read_text(encoding="utf-8")


def test_diverged_sites_are_reported_not_bumped(version_sites):
    """Разные значения → отказ, а не тихий выбор одного из двух."""
    a, b = version_sites
    b.write_text('_PRONOUNCE_RULES_VERSION = "9"\n', encoding="utf-8")
    assert DI.rules_version() is None
    assert not DI.bump_rules_version().ok
    assert '"9"' in b.read_text(encoding="utf-8"), "разъезд не чиним вслепую"


def test_the_real_version_is_readable():
    """Тот же замок на настоящем файле. ⭐ Место одно и живёт В ПАКЕТЕ: раньше версия
    стояла двумя копиями в файлах ДВИЖКА, то есть команда пакета правила чужой
    репозиторий и после выноса `rusvoice` отвечала бы «поднять руками» навсегда."""
    from rusvoice import pronounce as P
    assert DI.rules_version() == P.RULES_VERSION


def test_add_with_bump_moves_the_version(sandbox, version_sites):
    r = DI.add("brands", "Netlify", "Нетлифай", bump=True)
    assert r.ok and "3 → 4" in r.detail


# ── CLI словарей ─────────────────────────────────────────────────────────────

def test_cli_dict_list_json():
    r = _cli("dict", "list", "hard-e", "--json")
    rows = json.loads(r.stdout)
    assert rows and {"key", "value", "source"} <= set(rows[0])


def test_cli_dict_list_check_is_green_on_the_real_dicts():
    for kind in ("brands", "hard-e", "stress"):
        r = _cli("dict", "list", kind, "--check")
        assert r.returncode == 0, f"{kind}: мёртвые записи\n{r.stdout[-400:]}"


# ── эталон голоса ────────────────────────────────────────────────────────────

from rusvoice import refaudio as R  # noqa: E402

WORDS = [{"w": "Правки", "start": 0.10, "end": 0.60},
         {"w": "это", "start": 0.70, "end": 0.90},
         {"w": "часть", "start": 1.00, "end": 1.50},
         {"w": "работы", "start": 1.60, "end": 2.30},
         {"w": "Не", "start": 3.90, "end": 4.10},
         {"w": "злись", "start": 4.20, "end": 4.90}]


def _tone(path, seconds=12.0):
    ff, _ = R._ffmpeg_bins()
    subprocess.run([ff, "-y", "-f", "lavfi", "-i", f"sine=f=220:d={seconds}",
                    "-ac", "1", "-ar", "24000", str(path)],
                   capture_output=True, timeout=120)
    return str(path)


def test_props_reads_the_stream(tmp_path):
    p = R.props(_tone(tmp_path / "t.wav", 3.0))
    assert 2.9 < p["seconds"] < 3.1 and p["channels"] == 1 and p["sample_rate"] == 24000


def test_effective_ref_text_is_not_a_second_copy(tmp_path, monkeypatch):
    """Порядок резолва не переписан здесь заново, а спрошен у рецепта: своя копия
    разъехалась бы ровно в ту сторону, ради которой проверка и написана.

    ⚠️ Раньше сверка шла с ДВИЖКОМ (`voiceclone._ref_text_source`). После переезда рецепта
    в пакет это стал один и тот же модуль, то есть тавтология, проложенная через чужой
    репозиторий. Замки на сам порядок — `tests/test_voiceclone.py`."""
    from rusvoice import clone as C
    monkeypatch.delenv("F5_REF_TEXT", raising=False)
    monkeypatch.setattr(C, "_clone_cfg", lambda: {})
    ref = tmp_path / "r.wav"
    ref.write_bytes(b"RIFF")
    (tmp_path / "r.txt").write_text("транскрипт этого эталона", encoding="utf-8")
    assert R.effective_ref_text(str(ref)) == C._ref_text_source(str(ref))
    assert R.effective_ref_text(str(ref))[0] == "транскрипт этого эталона"


def test_check_catches_a_reference_swapped_without_its_text(tmp_path, monkeypatch):
    """⭐⭐ Живая ловушка: сменили эталон, а `ref_text` остался прежним. F5 не падает —
    он галлюцинирует префиксы (+13% WER), и это слышно как «модель мямлит»."""
    ref = _tone(tmp_path / "meme.wav", 10.0)
    (tmp_path / "meme.txt").write_text("совсем другая начитка", encoding="utf-8")
    monkeypatch.setenv("F5_REF_TEXT", "Здравствуйте. Это запись голоса.")
    got = {c.name: c for c in R.check(ref)}
    assert got["дословность"].status == D.FAIL
    assert "РАСХОДИТСЯ" in got["дословность"].detail


def test_check_ignores_whitespace_but_not_words(tmp_path, monkeypatch):
    """Двойной пробел дословность не рвёт — а вот слово рвёт. Сравнение по словам."""
    ref = _tone(tmp_path / "r.wav", 10.0)
    (tmp_path / "r.txt").write_text("Здравствуйте.  Это  запись голоса.\n", encoding="utf-8")
    monkeypatch.setenv("F5_REF_TEXT", "Здравствуйте. Это запись голоса.")
    assert {c.name: c for c in R.check(ref)}["дословность"].status == D.OK
    monkeypatch.setenv("F5_REF_TEXT", "Здравствуйте. Это запись другого голоса.")
    assert {c.name: c for c in R.check(ref)}["дословность"].status == D.FAIL


def test_check_reports_missing_sidecar(tmp_path):
    got = {c.name: c for c in R.check(_tone(tmp_path / "r.wav", 10.0))}
    assert got["транскрипт"].status == D.FAIL and "cut" in got["транскрипт"].fix


def test_check_warns_on_a_window_outside_the_recipe(tmp_path, monkeypatch):
    ref = _tone(tmp_path / "long.wav", 40.0)
    (tmp_path / "long.txt").write_text("текст", encoding="utf-8")
    monkeypatch.setenv("F5_REF_TEXT", "текст")
    assert {c.name: c for c in R.check(ref)}["аудио"].status == D.WARN


def test_check_reports_a_missing_file(tmp_path):
    assert R.check(str(tmp_path / "нет.wav"))[0].status == D.FAIL


# ── нарезка по границам слов ─────────────────────────────────────────────────

def test_snap_keeps_only_whole_words():
    """Полслова в эталоне — это уже не дословный транскрипт, каким бы точным ни был .txt."""
    a, b, inside = R.snap(WORDS, 0.4, 1.55)
    assert [w["w"] for w in inside] == ["это", "часть"]
    assert (a, b) == (0.70, 1.50)


def test_snap_survives_an_empty_window():
    a, b, inside = R.snap(WORDS, 2.5, 3.0)
    assert inside == [] and (a, b) == (2.5, 3.0)


def test_cut_writes_a_transcript_that_matches_by_construction(tmp_path, monkeypatch):
    """Транскрипт собирается из ТЕХ ЖЕ слов, что попали в окно, — дословность обеспечена
    конструкцией, а не аккуратностью того, кто резал."""
    src = _tone(tmp_path / "src.wav", 12.0)
    monkeypatch.setattr(R, "words", lambda p, lang="ru": WORDS)
    out = str(tmp_path / "win.wav")
    r = R.cut(src, out, 0.0, 2.4)
    assert r["ok"], r["detail"]
    assert r["text"] == "Правки это часть работы"
    assert open(R.sidecar(out), encoding="utf-8").read().strip() == r["text"]
    monkeypatch.setenv("F5_REF_TEXT", r["text"])
    assert {c.name: c for c in R.check(out)}["дословность"].status == D.OK


def test_cut_window_lands_on_word_edges(tmp_path, monkeypatch):
    src = _tone(tmp_path / "src.wav", 12.0)
    monkeypatch.setattr(R, "words", lambda p, lang="ru": WORDS)
    r = R.cut(src, str(tmp_path / "w.wav"), 0.4, 1.55, pad=0.0)
    assert r["window"] == [0.7, 1.5] and r["words"] == 2


def test_cut_refuses_when_asr_returns_nothing(tmp_path, monkeypatch):
    src = _tone(tmp_path / "src.wav", 3.0)
    monkeypatch.setattr(R, "words", lambda p, lang="ru": [])
    r = R.cut(src, str(tmp_path / "w.wav"), 0.0, 2.0)
    assert not r["ok"] and not os.path.exists(tmp_path / "w.wav")


def test_cut_refuses_a_window_without_whole_words(tmp_path, monkeypatch):
    src = _tone(tmp_path / "src.wav", 12.0)
    monkeypatch.setattr(R, "words", lambda p, lang="ru": WORDS)
    assert not R.cut(src, str(tmp_path / "w.wav"), 2.5, 3.0)["ok"]


def test_line_windows_stay_inside_the_recipe():
    ws = [{"w": f"с{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(40)]
    ws[10]["start"] += 1.5
    ws[10]["end"] += 1.5
    for a, b, _ in R.line_windows(ws, target=10.0):
        assert R.GOOD_SECONDS[0] <= b - a <= R.GOOD_SECONDS[1]


def test_line_windows_prefer_the_target_length():
    ws = [{"w": f"с{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(40)]
    got = R.line_windows(ws, target=8.0)
    assert got and abs((got[0][1] - got[0][0]) - 8.0) <= abs((got[-1][1] - got[-1][0]) - 8.0)


def test_cli_ref_check_json(tmp_path):
    """⚠️ Эталон задаём ЯВНО. Без `--ref` команда берёт выбранный голос или эталон движка,
    и тест молча зависел бы от того, где его запустили: в репозитории движка проходил, в
    самостоятельной установке — нет (поймано при сборке отдельного репозитория)."""
    ref = _tone(tmp_path / "r.wav", 10.0)
    (tmp_path / "r.txt").write_text("транскрипт этого эталона", encoding="utf-8")
    r = _cli("ref", "check", "--ref", str(ref), "--json")
    d = json.loads(r.stdout)
    assert any(c["name"] == "дословность" for c in d["checks"])


# ── громкость ────────────────────────────────────────────────────────────────

from rusvoice import loudness as LD  # noqa: E402
from rusvoice import say as SAY  # noqa: E402


def _tone_at(path, db, seconds=1.5):
    """Тон заданной громкости: детерминированный вход для замера."""
    ff, _ = R._ffmpeg_bins()
    subprocess.run([ff, "-y", "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
                    "-af", f"volume={db}dB", "-ac", "1", "-ar", "48000", str(path)],
                   capture_output=True, timeout=120)
    return str(path)


def test_measure_returns_numbers(tmp_path):
    m = LD.measure(_tone_at(tmp_path / "t.wav", -20))
    assert m.integrated is not None and m.true_peak is not None
    assert m.crest == pytest.approx(m.true_peak - m.integrated)


def test_measure_survives_a_file_it_cannot_read(tmp_path):
    bad = tmp_path / "не_аудио.wav"
    bad.write_text("не звук", encoding="utf-8")
    m = LD.measure(str(bad))
    assert m.integrated is None and not m.ok


def test_ok_needs_both_loudness_and_headroom():
    assert LD.Loudness("x", -14.0, -1.5, 5.0).ok
    assert not LD.Loudness("x", -14.0, -0.5, 5.0).ok, "пик у нуля — это клиппинг"
    assert not LD.Loudness("x", -16.0, -3.0, 5.0).ok
    assert not LD.Loudness("x", None, None, None).ok


def test_normalize_lands_on_target(tmp_path):
    """⭐⭐ Успех объявляется по ЗАМЕРУ выхода, а не по коду возврата ffmpeg: `loudnorm`
    отдавал −15.3 при rc=0, и это считалось нормализацией."""
    src = _tone_at(tmp_path / "quiet.wav", -30)
    r = LD.normalize(src, str(tmp_path / "out.wav"))
    assert r["ok"], r["detail"]
    assert abs(LD.measure(r["out"]).integrated - LD.TARGET) <= LD.TOLERANCE


def test_normalize_reports_the_input_it_could_not_read(tmp_path):
    bad = tmp_path / "нет.wav"
    bad.write_text("x", encoding="utf-8")
    r = LD.normalize(str(bad), str(tmp_path / "o.wav"))
    assert not r["ok"] and "не измерилась" in r["detail"]


def test_why_short_names_the_ceiling():
    """Недобор объясняется арифметикой, иначе он выглядит случайностью и лечится наугад."""
    before = LD.Loudness("s", -18.0, 0.0, 3.0)
    after = LD.Loudness("o", -15.3, -1.2, 3.0)
    msg = LD.why_short(before, after)
    assert "+4.0" in msg and "размах 18.0" in msg and "лимитер" in msg


def test_normalize_uses_the_limiter_with_level_disabled():
    """⚠️ У `alimiter` дефолт `level=enabled`: он сам подтягивает уровень обратно и
    возвращает клиппинг, молча обнуляя всё сделанное до него."""
    src = open(os.path.join(ROOT, "rusvoice", "loudness.py"), encoding="utf-8").read()
    assert "alimiter=limit={E.limiter_ceiling()}:level=disabled" in src


def test_cli_loud_measures_and_judges(tmp_path):
    r = _cli("loud", _tone_at(tmp_path / "q.wav", -30))
    assert r.returncode == 1 and "LUFS" in r.stdout
    out = tmp_path / "n.wav"
    r = _cli("loud", str(tmp_path / "q.wav"), "--out", str(out), "--json")
    assert r.returncode == 0 and json.loads(r.stdout)["ok"]


# ── say ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_synth(tmp_path, monkeypatch):
    """Синтез подменён: тесты гоняются в `.venv`, где F5 нет, а проверять надо ОБВЯЗКУ —
    какой текст ушёл в синтез, какой транскрипт эталона выставлен, что стало с громкостью."""
    from rusvoice import clone as C
    seen = {}

    def fake(text, out_base, ref=None):
        seen["text"] = text
        seen["ref"] = ref
        seen["F5_REF_TEXT"] = os.environ.get("F5_REF_TEXT")
        return _tone_at(out_base + ".wav", -25)

    monkeypatch.setattr(SAY, "blockers", lambda: [])
    monkeypatch.setattr(C, "clone_synth", fake)
    ref = _tone_at(tmp_path / "ref.wav", -20, seconds=8.0)
    return seen, ref


def test_say_blocks_with_a_diagnosis_not_a_stacktrace(tmp_path):
    """Чего-то для синтеза не хватает всегда: в `.venv` нет F5, в чистой установке — голоса.
    Инвариант не в том, ЧЕГО именно, а в том, что ответ — внятная фраза, а не стектрейс.

    ⚠️ Проверка была прибита к `.venv` («в detail есть F5») и в brew-питоне, где F5 стоит,
    падала на пустом месте. Тест, знающий про одно окружение, — это тест про окружение."""
    r = SAY.say("привет", str(tmp_path / "o.wav"))
    assert not r["ok"], "синтеза быть не могло: ни голоса, ни F5 в тестовом окружении"
    assert len(r["detail"]) > 20 and "Traceback" not in r["detail"], r["detail"]
    assert not os.path.exists(tmp_path / "o.wav")


def test_say_feeds_synth_the_text_before_accents(tmp_path, fake_synth, monkeypatch):
    """⭐ `clone_synth` сам зовёт `accentize` — отдать ему уже размеченный текст значило бы
    поставить ударения дважды. В синтез уходит версия ПОСЛЕ произношения, ДО ударений."""
    from rusvoice import accentize as A
    monkeypatch.setattr(A, "_load_accentizer", lambda: (lambda s: s))
    monkeypatch.setattr(A, "accentize_ru", lambda s: s.replace("Макс", "М+акс"))
    seen, ref = fake_synth
    r = SAY.say("подписка MAX", str(tmp_path / "o.wav"), ref=ref, loud=False)
    assert r["ok"]
    assert seen["text"] == "подписка Макс", "в синтез ушёл текст с ударениями"
    assert r["text"] == "подписка М+акс", "показан итог, каким его сделает сам синтез"


def test_say_reports_where_the_reference_text_came_from(tmp_path, fake_synth, monkeypatch):
    """Пару «аудио ↔ текст» связывает движок; задача команды — ПОКАЗАТЬ, что получилось.
    Окружение здесь чистим: иначе `F5_REF_TEXT` от соседнего теста перебил бы сайдкар и
    отчёт был бы правдой про другой запуск."""
    monkeypatch.delenv("F5_REF_TEXT", raising=False)
    seen, ref = fake_synth
    (tmp_path / "ref.txt").write_text("совсем другая начитка эталона", encoding="utf-8")
    r = SAY.say("привет", str(tmp_path / "o.wav"), ref=ref, loud=False)
    assert any("ref.txt" in s for s in r["steps"]), r["steps"]
    assert not any("нет ref.txt" in w for w in r["warnings"])


def test_say_warns_when_the_reference_has_no_transcript(tmp_path, fake_synth, monkeypatch):
    """Без сайдкара в F5 уйдёт глобальный дефолт — он может быть про другое аудио."""
    monkeypatch.delenv("F5_REF_TEXT", raising=False)
    seen, ref = fake_synth
    r = SAY.say("привет", str(tmp_path / "o.wav"), ref=ref, loud=False)
    assert any("нет ref.txt" in w for w in r["warnings"])


def test_say_refuses_a_missing_reference(tmp_path, fake_synth):
    r = SAY.say("привет", str(tmp_path / "o.wav"), ref=str(tmp_path / "нет.wav"))
    assert not r["ok"] and "эталона нет" in r["detail"]


def test_say_warns_about_tempo_applied_twice(tmp_path, fake_synth, monkeypatch):
    """`clone_synth` применяет CLONE_ATEMPO ВНУТРИ, а рецепт ставит темп ПОСЛЕ энхансера."""
    seen, ref = fake_synth
    monkeypatch.setenv("CLONE_ATEMPO", "1.12")
    r = SAY.say("привет", str(tmp_path / "o.wav"), ref=ref, tempo=1.15, loud=False)
    assert any("дважды" in w for w in r["warnings"])


def test_say_applies_tempo_after_synthesis(tmp_path, fake_synth):
    """Порядок рецепта: F5 → темп → громкость."""
    seen, ref = fake_synth
    r = SAY.say("привет", str(tmp_path / "o.wav"), ref=ref, tempo=1.5, loud=False)
    assert any("темп" in s for s in r["steps"])
    assert LD.measure(r["out"]).integrated is not None


def test_say_normalizes_loudness_when_asked(tmp_path, fake_synth):
    seen, ref = fake_synth
    r = SAY.say("привет", str(tmp_path / "o.wav"), ref=ref, loud=True)
    assert r["ok"] and abs(r["loudness"]["I"] - LD.TARGET) <= LD.TOLERANCE


def test_say_still_produces_a_file_when_loudness_fails(tmp_path, fake_synth, monkeypatch):
    """Недоведённая громкость — повод предупредить, а не потерять результат синтеза."""
    seen, ref = fake_synth
    monkeypatch.setattr(LD, "normalize",
                        lambda s, o, **kw: {"ok": False, "detail": "не вышло"})
    r = SAY.say("привет", str(tmp_path / "o.wav"), ref=ref, loud=True)
    assert r["ok"] and os.path.isfile(r["out"])
    assert any("громкость не доведена" in w for w in r["warnings"])


def _tail_seconds(path: str) -> float:
    """Сколько тишины после последнего слышимого звука."""
    import wave

    import numpy as np
    with wave.open(path) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    loud = np.nonzero(np.abs(a) > 32768 * 10 ** (-45 / 20))[0]
    return (len(a) - loud[-1]) / sr if len(loud) else 0.0


def test_say_puts_air_after_the_last_word(tmp_path, fake_synth):
    """⭐ Вердикт Ильи 07.09.2026: «хвост нужен именно на конце всего текста, а не между
    предложениями». 0.18с в `clone_synth` — паддинг КЛИПА, рассчитанный на стык со
    следующей сценой; для отдельного файла это конец всего текста и слышно как обрыв.
    ⚠️ Спорить на форме затухания бесполезно — три её варианта Илья не различил на слух
    (весь спор шёл на −50 dBFS). Слышна ДЛИНА паузы, её и ставим."""
    seen, ref = fake_synth
    r = SAY.say("привет", str(tmp_path / "o.wav"), ref=ref, loud=False, outro=0.6)
    assert r["ok"] and any("хвост: 0.60" in s for s in r["steps"])
    assert 0.5 < _tail_seconds(r["out"]) < 0.75


def test_say_can_keep_the_clip_tail(tmp_path, fake_synth):
    """0.18 — ровно то, что кладёт клип внутри ролика: шаг не должен работать вхолостую."""
    seen, ref = fake_synth
    r = SAY.say("привет", str(tmp_path / "o.wav"), ref=ref, loud=False, outro=0.18)
    assert any("клиповый 0.18" in s for s in r["steps"])


def test_say_keeps_the_synthesis_when_the_tail_step_fails(tmp_path, fake_synth, monkeypatch):
    """Хвост — не повод потерять готовый синтез: он дороже любой паузы."""
    from rusvoice import clone as C
    seen, ref = fake_synth
    monkeypatch.setattr(C, "_pad_edges",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("нет ffmpeg")))
    r = SAY.say("привет", str(tmp_path / "o.wav"), ref=ref, loud=False, outro=0.6)
    assert r["ok"] and os.path.isfile(r["out"])
    assert any("хвост не переставлен" in w for w in r["warnings"])


def test_say_no_longer_calls_the_enhancer():
    """⭐ Вердикт Ильи 07.09.2026: и lambd=0.5 («какое-то гавно»), и lambd=0.1 («хуета прям
    боль») хуже голого выхода F5. Два замера в одну сторону — это не настройка, а
    неподходящий инструмент; ручка, заведомо портящая звук, из рецепта убрана."""
    src = open(os.path.join(ROOT, "rusvoice", "say.py"), encoding="utf-8").read()
    assert "enhance_audio" not in src and "resemble" not in src.split('"""', 2)[2]


# ── линт озвучки ─────────────────────────────────────────────────────────────

from rusvoice import lint as LN  # noqa: E402


def test_lint_catches_the_transliteration_trap():
    """⭐⭐ «Knight Capital» прозвучало как «Книгхт Капитал»: виноват не акцентизатор, а
    последний слой — транслит берёт любую латиницу мимо словаря брендов. Показываем, во что
    именно она превратится: это убеждает быстрее, чем «слова нет в словаре»."""
    got = LN.scan("Vazgenoid Qwerzon потеряла всё", accent=False)
    tr = [i for i in got.issues if i.kind == "транслит"]
    assert [i.level for i in tr] == [LN.DEFECT, LN.DEFECT]
    assert "Вазгеноид" in tr[0].detail and "dict add brands" in tr[0].fix

    # ⚠️ Исходный кейс («Knight» → «Книгхт») в тест брать НЕЛЬЗЯ: 07.09.2026 слово
    # добавили в словарь, и тест, стоявший на дыре в ДАННЫХ, покраснел ровно от того,
    # что дыру закрыли. Здесь он остаётся замком на само закрытие.
    assert not LN.scan("Knight Capital потеряла всё", accent=False).issues


def test_lint_is_silent_on_words_the_dictionary_knows():
    assert not [i for i in LN.scan("Claude Code на подписке MAX", accent=False).issues
                if i.kind == "транслит"]


def test_lint_does_not_flag_deliberate_transliteration():
    """`API` читается по буквам, `x` — множитель: это правило, а не недосмотр."""
    got = LN.scan("API и CLI дают 5x прирост", accent=False)
    assert not [i for i in got.issues if i.kind == "транслит"]


def test_lint_ignores_latin_in_a_non_russian_line():
    """На нерусской реплике слой произношения выключен целиком — транслита не будет,
    и подсвечивать каждое английское слово значит утопить настоящие находки."""
    got = LN.scan("The quick brown fox jumps", lang="en", accent=False)
    assert not [i for i in got.issues if i.kind == "транслит"]


def test_lint_separates_defects_from_risks(monkeypatch):
    """⭐ Линт, где всё одинаково строго, пролистывают целиком — а с ним пролистают
    и «Вазгеноид». Риск сегодня один: омограф, где RUAccent прав чаще, чем нет.

    ⚠️ Цифры риском БЫЛИ — на том основании, что «модель часто читает верно». Замер
    09.09.2026 показал, что основания не было: гипотезу не проверял никто, и она неверна
    («440 миллионов» → «чуть-чуть сто миллионов»). Уровень поднят до дефекта."""
    monkeypatch.setattr(L, "apply", lambda text, **kw: L.Trace(
        text, "П+отом ст+анет легк+о", [], [], []))
    got = LN.scan("Потом станет легко")
    assert [i.level for i in got.issues] == [LN.RISK]
    assert LN.defects([got]) == []


def test_lint_treats_bare_digits_as_a_defect():
    """⭐⭐ Замер на F5 (09.09.2026): «440» прозвучало как «чуть-чуть сто», «45» как
    «сорт пять», «1340» как «34». Это не ослышка ASR — слова другие, повторяется на всех
    проходах. ⚠️ `PRONOUNCE_NUMBERS=1` число восстановит, но падеж не согласует
    («в две тысячи двадцать шесть году»), поэтому подсказка ведёт к «написать словом»."""
    got = LN.scan("было 440 миллионов", accent=False)
    digits = [i for i in got.issues if i.kind == "цифры"]
    assert digits and all(i.level == LN.DEFECT for i in digits)
    assert "словом" in digits[0].fix


def test_lint_flags_a_monosyllable_stress_mark(monkeypatch):
    from rusvoice import accentize as A
    monkeypatch.setattr(A, "_load_accentizer", lambda: (lambda s: s))
    monkeypatch.setattr(A, "accentize_ru", lambda s: s.replace("не", "н+е"))
    got = LN.scan("это н+е тот голос", accent=True)
    st = [i for i in got.issues if i.kind == "ударение"]
    assert st and st[0].level == LN.DEFECT


def test_lint_reports_the_environment_once_not_per_line(monkeypatch):
    """«RUAccent недоступен» — проблема ОКРУЖЕНИЯ. Повторённая у каждой реплики, она
    приучает пролистывать вывод."""
    from rusvoice import accentize as A
    monkeypatch.setattr(A, "_load_accentizer", lambda: None)
    got = LN.scan("обычная реплика", accent=True)
    assert not [i for i in got.issues if "RUAccent" in i.detail]
    assert "RUAccent" in LN.environment_note(True)
    assert LN.environment_note(False) == ""


def test_lint_reads_a_scenario(tmp_path):
    """Сценарий — главный вход: дефект ловится ДО рендера, а не просмотром готового ролика."""
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"lang": "ru", "scenes": [
        {"title": "первая\nстрока", "vo": "Vazgenoid Capital"},
        {"title": "без озвучки"},
        {"vo": "   "},
        {"vo": "The fox", "lang": "en"},
    ]}, ensure_ascii=False), encoding="utf-8")
    lines = LN.run(str(p), accent=False)
    assert [ln.idx for ln in lines] == [1, 4], "пустые сцены пропускаются"
    assert lines[0].label == "первая строка"
    assert LN.defects(lines) == [lines[0]], "английская сцена не дефект"


def test_lint_reads_plain_lines(tmp_path):
    p = tmp_path / "lines.txt"
    p.write_text("Vazgenoid Capital\n\nобычная строка\n", encoding="utf-8")
    lines = LN.run(str(p), accent=False)
    assert len(lines) == 2 and lines[1].idx == 3


def test_lint_takes_bare_text():
    assert LN.run("Vazgenoid", accent=False)[0].text == "Vazgenoid"


def test_cli_lint_exit_code_is_about_defects(tmp_path):
    """Код выхода — на дефектах: линт, падающий на рисках, отключат в первый же день."""
    assert _cli("lint", "--no-accent", "Vazgenoid Capital").returncode == 1
    assert _cli("lint", "--no-accent", "было 440 миллионов").returncode == 1   # см. замер
    assert _cli("lint", "--no-accent", "обычная реплика").returncode == 0


def test_cli_lint_json_carries_levels():
    """Уровень обязан доезжать до машинного вывода — на нём стоит код выхода."""
    r = _cli("lint", "--no-accent", "--json", "Vazgenoid за 440")
    rows = json.loads(r.stdout)
    kinds = {i["kind"]: i["level"] for i in rows[0]["issues"]}
    assert kinds == {"транслит": LN.DEFECT, "цифры": LN.DEFECT}


def test_cli_lint_plural_agreement():
    """«1 реплик» в отчёте читается как баг инструмента и роняет доверие к находкам."""
    out = _cli("lint", "--no-accent", "Vazgenoid").stdout
    assert "1 реплика" in out


# ── эталон из видео ──────────────────────────────────────────────────────────

def _speech(path, seconds=14.0):
    """Источник со «словами»: тональные всплески в тишине. Настоящий голос тут не нужен —
    проверяется отбор окна по измерениям, а не ASR."""
    ff, _ = R._ffmpeg_bins()
    subprocess.run([ff, "-y", "-f", "lavfi",
                    "-i", f"sine=f=200:d={seconds}",
                    "-af", "volume='if(lt(mod(t,1),0.6),1,0.002)':eval=frame",
                    "-ac", "1", "-ar", "24000", str(path)], capture_output=True, timeout=120)
    return str(path)


def _grid_words(n=14, step=1.0, dur=0.6, start=0.0):
    return [{"w": f"с{i}", "start": start + i * step, "end": start + i * step + dur}
            for i in range(n)]


def test_extract_audio_takes_the_track_out_of_a_video(tmp_path):
    ff, _ = R._ffmpeg_bins()
    src = tmp_path / "v.mp4"
    subprocess.run([ff, "-y", "-f", "lavfi", "-i", "testsrc=d=3:s=64x64",
                    "-f", "lavfi", "-i", "sine=f=300:d=3", "-shortest", str(src)],
                   capture_output=True, timeout=120)
    out = str(tmp_path / "a.wav")
    assert R.extract_audio(str(src), out)
    p = R.props(out)
    assert p["channels"] == 1 and p["sample_rate"] == 24000


def test_measure_window_reads_the_floor_between_words_not_the_speech(tmp_path):
    """⭐ Фон меряется в ПРОМЕЖУТКАХ: усреднив по всему окну, речь перевесит шум, и грязная
    запись получила бы хорошую оценку. Клон наследует шум эталона вместе с голосом."""
    sig, sr = R._samples(_speech(tmp_path / "s.wav"))
    gap, floor, peak = R.measure_window(sig, sr, _grid_words(), 0.0, 10.0)
    assert gap == pytest.approx(0.4, abs=0.01)
    assert floor < peak - 15, f"фон {floor} должен быть далеко ниже пика {peak}"
    assert peak > -30, "пик считается по всему окну, включая речь"


def test_window_score_punishes_a_long_pause_hardest():
    """Длинная пауза внутри эталона слышна как склейка — это хуже, чем секунда длины."""
    clean = R.Window(0, 10, 20, gap=0.2, floor=-60, peak=-3)
    gappy = R.Window(0, 10, 20, gap=1.2, floor=-60, peak=-3)
    short = R.Window(0, 9, 18, gap=0.2, floor=-60, peak=-3)
    assert clean.score(10) < short.score(10) < gappy.score(10)


def test_window_score_punishes_a_noisy_floor_and_clipping():
    clean = R.Window(0, 10, 20, gap=0.2, floor=-60, peak=-3)
    noisy = R.Window(0, 10, 20, gap=0.2, floor=-20, peak=-3)
    clipped = R.Window(0, 10, 20, gap=0.2, floor=-60, peak=-0.1)
    assert noisy.score(10) > clean.score(10)
    assert clipped.score(10) > clean.score(10)


def test_candidates_are_measured_and_ranked(tmp_path):
    sig_path = _speech(tmp_path / "s.wav")
    got = R.candidates(sig_path, _grid_words(), target=8.0)
    assert got and all(R.GOOD_SECONDS[0] <= w.seconds <= R.GOOD_SECONDS[1] for w in got)
    scores = [w.score(8.0) for w in got]
    assert scores == sorted(scores), "кандидаты обязаны идти от лучшего к худшему"
    assert got[0].text.startswith("с")


def test_grab_produces_a_reference_with_its_transcript(tmp_path, monkeypatch):
    """Одна команда от видео до годного эталона: дорожка → тайминги → измерение окон →
    рез по границам слов. Транскрипт складывается из тех же слов — дословность по
    построению."""
    src = _speech(tmp_path / "src.wav")
    monkeypatch.setattr(R, "words", lambda p, lang="ru": _grid_words())
    out = str(tmp_path / "ref.wav")
    r = R.grab(src, out, target=8.0, work=str(tmp_path / "w"))
    assert r["ok"], r["detail"]
    assert os.path.isfile(R.sidecar(out))
    assert open(R.sidecar(out), encoding="utf-8").read().strip() == r["text"]
    assert set(r["window_quality"]) == {"пауза", "фон", "пик"}
    assert r["source_words"] == 14


def test_grab_result_passes_its_own_check(tmp_path, monkeypatch):
    """Выход `grab` обязан быть годным эталоном по мерке самого пакета — иначе команда
    делает вид, что работа сделана."""
    monkeypatch.delenv("F5_REF_TEXT", raising=False)
    src = _speech(tmp_path / "src.wav")
    monkeypatch.setattr(R, "words", lambda p, lang="ru": _grid_words())
    out = str(tmp_path / "ref.wav")
    R.grab(src, out, target=8.0, work=str(tmp_path / "w"))
    got = {c.name: c for c in R.check(out)}
    assert got["аудио"].status == D.OK
    assert got["дословность"].status == D.OK


def test_grab_reports_when_there_is_no_speech(tmp_path, monkeypatch):
    src = _speech(tmp_path / "src.wav", 3.0)
    monkeypatch.setattr(R, "words", lambda p, lang="ru": [])
    r = R.grab(src, str(tmp_path / "r.wav"), work=str(tmp_path / "w"))
    assert not r["ok"] and "речи" in r["detail"]
    assert not os.path.exists(tmp_path / "r.wav")


def test_grab_reports_a_source_it_cannot_read(tmp_path):
    bad = tmp_path / "не_медиа.txt"
    bad.write_text("не звук", encoding="utf-8")
    r = R.grab(str(bad), str(tmp_path / "r.wav"), work=str(tmp_path / "w"))
    assert not r["ok"] and "дорожку" in r["detail"]


def test_grab_says_no_when_every_window_is_gappy(tmp_path, monkeypatch):
    """Молча выданный эталон с восьмисекундной дырой хуже отказа: проверить его можно
    только ушами, а выглядит он как сделанная работа."""
    src = _speech(tmp_path / "src.wav")
    monkeypatch.setattr(R, "words", lambda p, lang="ru": _grid_words(n=2, step=9.0))
    r = R.grab(src, str(tmp_path / "r.wav"), work=str(tmp_path / "w"))
    assert not r["ok"]


# ── движок как опциональный бэкенд ───────────────────────────────────────────

def _no_engine(monkeypatch):
    """Окружение самостоятельной установки: модулей движка нет."""
    from rusvoice import engine as E
    monkeypatch.setattr(E, "get", lambda name: None)


def test_engine_get_never_raises():
    """Вызывающий обязан уметь сказать «нет» — значит `get` не имеет права бросать."""
    from rusvoice import engine as E
    assert E.get("такого_модуля_нет_и_не_будет") is None


def test_doctor_diagnoses_a_missing_engine_instead_of_a_stacktrace(monkeypatch):
    """⭐ Первая же чистая установка встретила пользователя `ModuleNotFoundError:
    No module named 'voiceclone'`. Отсутствие движка — не поломка, а другой сценарий:
    пакет, который делает молчаливые провалы произносимыми, не может падать сам.

    ⭐⭐ Сужение дошло до конца: движок пакету не нужен НИ ДЛЯ ЧЕГО, включая синтез
    (рецепт живёт в `rusvoice.clone`). Поэтому здесь уже не WARN, а OK — предупреждение
    без потери функциональности приучает пролистывать вывод целиком."""
    _no_engine(monkeypatch)
    checks = D.run()
    assert any(c.name == "движок" and c.status == D.OK for c in checks)
    # ⚠️ Формулировка сузилась вместе с зависимостью. Раньше проверялось «F5 и ffmpeg не
    # падают» — тогда их провал был бы СЛЕДСТВИЕМ отсутствия движка. Теперь провал F5
    # означает ровно то, что написано (в `.venv` его и правда нет), и запрещать его
    # значило бы требовать от доктора молчать о настоящей проблеме. Остаётся то, что
    # действительно должно быть неправдой: провал, ссылающийся на движок.
    assert not any("движк" in (c.detail or "") for c in checks if c.status == D.FAIL), \
        "движка нет рядом — но он больше ни на что не влияет, ссылаться на него нельзя"


def test_doctor_keeps_the_text_layer_green_without_an_engine(monkeypatch):
    """Слой правки текста ни от чего из движка не зависит — это и есть продукт."""
    _no_engine(monkeypatch)
    names = {c.name: c for c in D.run()}
    assert names["словарь бренды"].status == D.OK
    assert names["версия правил"].status == D.OK   # это ключ кэша ДВИЖКА, не наш


def test_say_without_the_engine_reaches_synthesis(tmp_path, monkeypatch, fake_synth):
    """⭐⭐ Раньше здесь проверялось обратное: без движка `say` отказывала, назвав причину.
    Отказ был честным, но означал, что самостоятельная установка умеет всё, КРОМЕ
    озвучки — то есть кроме того, ради чего пакет и ставят. Рецепт переехал внутрь, и
    единственное, что теперь может помешать, — отсутствие самого F5."""
    _no_engine(monkeypatch)
    seen, ref = fake_synth
    r = SAY.say("привет как дела", str(tmp_path / "o.wav"), ref=str(ref), verify=False)
    assert r["ok"] is True and seen["text"]
    assert os.path.isfile(tmp_path / "o.wav")
    assert all("движк" not in (c.detail or "") for c in R.check(str(ref))), \
        "разбор эталона движком не интересуется"


def test_dictionaries_live_next_to_the_layer_that_reads_them():
    """⚠️ Словари переехали в пакет вместе со слоем: путь `pipeline/*.json` пережил бы
    установку без движка и молча читал бы пустоту."""
    from rusvoice import pronounce as P
    for path in (P._JSON_PATH, P._STRESS_PATH, P._HARD_E_PATH):
        assert os.path.dirname(path) == os.path.join(ROOT, "rusvoice"), path
        assert os.path.isfile(path)


# ── омографы: метка стоит, но не та ──────────────────────────────────────────

def test_homograph_fires_only_on_the_rare_reading():
    """⭐⭐ Замер по корпусу (6791 реплика): пропущенных меток у RUAccent практически нет
    (98.3% размечено), а ВЫБОР он путает — «Потом становится легко» уходило в синтез как
    «п+отом», то есть «облился по́том». Проверка «нет метки» этого не ловит в принципе."""
    from rusvoice import homographs as HG
    assert HG.scan("Потом станет легко", "П+отом ст+анет легк+о")
    assert not HG.scan("Потом станет легко", "Пот+ом ст+анет легк+о")


def test_homograph_is_silent_where_the_accentizer_is_right():
    """⚠️ Шум убивает линт быстрее, чем пропуск: «уже́» в корпусе 273 раза, «со́рок» — 133,
    и оба раза RUAccent прав. Симметричные пары (за́мок/замо́к) в таблицу не берём."""
    from rusvoice import homographs as HG
    assert not HG.scan("уже сорок", "уж+е с+орок")
    assert not HG.scan("старинный замок на горе", "стар+инный з+амок на гор+е")
    assert not HG.scan("этот замок щёлкнул", "этот зам+ок щёлкнул")


def test_homograph_respects_the_author_mark():
    """Ответ на предупреждение — метка в самом сценарии: она переживает любой акцентизатор.
    Ругаться на разрешённую автором неоднозначность значит приучить пролистывать вывод."""
    from rusvoice import homographs as HG
    assert not HG.scan("П+отом станет легко", "П+отом ст+анет легк+о")


def test_homograph_table_is_not_self_contradictory():
    """Редкая и частая разметки обязаны различаться и обе быть тем же словом."""
    from rusvoice import homographs as HG
    for word, (rare, gloss, common) in HG.RARE.items():
        assert rare != common, word
        assert rare.replace("+", "") == word and common.replace("+", "") == word, word
        assert gloss.strip(), word


def test_lint_reports_a_homograph_as_a_risk_not_a_defect(monkeypatch):
    """Код выхода на омографе обязан остаться нулевым: RUAccent прав чаще, чем нет, и
    линт, падающий на риске, отключают в первый же день."""
    monkeypatch.setattr(L, "apply", lambda text, **kw: L.Trace(
        text, "П+отом ст+анет легк+о", [], [], []))
    got = LN.scan("Потом станет легко")
    homo = [i for i in got.issues if i.kind == "омограф"]
    assert homo and all(i.level == LN.RISK for i in homo)
    assert not LN.defects([got])


# ── проверка того, что реально прозвучало ────────────────────────────────────

def test_verify_does_not_judge_a_short_text(tmp_path):
    """⚠️ На коротком тексте доля покрытия неинформативна: одна склейка ASR роняет её на
    треть. Молчание честнее уверенного числа."""
    from rusvoice import verify as V
    r = V.check(str(tmp_path / "нет.wav"), "три слова всего")
    assert r["judged"] is False and "короче" in r["detail"]


def test_verify_reports_when_asr_is_unavailable(tmp_path, monkeypatch):
    """Нет ASR — так и сказать. «Проверено» без проверки хуже, чем «не проверено»."""
    from rusvoice import engine as E
    from rusvoice import verify as V
    monkeypatch.setattr(E, "get", lambda name: None)
    r = V.check(str(tmp_path / "x.wav"), " ".join(["слово"] * 20))
    assert r["judged"] is False and "не проверено" in r["detail"]


def test_verify_catches_a_truncated_synthesis(tmp_path, monkeypatch):
    """⭐⭐ Ровно тот случай из движка: edge отдал 2.9 с на текст в 53 слова, в треке было
    ДВА слова, а проверкой был размер файла — обрезок весил 17 КБ и прошёл. Порог не
    назначен, а откалиброван по прогону: здоровые сцены 0.955–0.983, обрезанная 0.038."""
    from rusvoice import refaudio as R
    from rusvoice import verify as V
    text = " ".join(f"слово{i}" for i in range(20))
    monkeypatch.setattr(V, "available", lambda: True)
    monkeypatch.setattr(R, "words", lambda p, **kw: [{"w": "слово0"}, {"w": "слово1"}])
    r = V.check(str(tmp_path / "x.wav"), text)
    assert r["judged"] and not r["ok"] and r["coverage"] < 0.2
    assert "не хватает" in r["detail"]


def test_verify_accepts_a_healthy_synthesis_without_listing_words(tmp_path, monkeypatch):
    """⚠️ Список слов на здоровой реплике был бы шумом от ослышек ASR: он показывается
    ТОЛЬКО при провале, где он единственная подсказка, где оборвалось."""
    from rusvoice import refaudio as R
    from rusvoice import verify as V
    text = " ".join(f"слово{i}" for i in range(20))
    monkeypatch.setattr(V, "available", lambda: True)
    monkeypatch.setattr(R, "words", lambda p, **kw: [{"w": w} for w in text.split()])
    r = V.check(str(tmp_path / "x.wav"), text)
    assert r["judged"] and r["ok"] and "missing" not in r


def test_say_verifies_what_was_actually_said(tmp_path, fake_synth, monkeypatch):
    """Все прочие проверки пакета — ДО синтеза: они ловят предусмотренное. Эта ловит то,
    о чём не подумали."""
    from rusvoice import verify as V
    seen, ref = fake_synth
    monkeypatch.setattr(V, "check", lambda *a, **kw: {"judged": True, "ok": False,
                                                     "coverage": 0.1, "detail": "оборвалось"})
    r = SAY.say("привет", str(tmp_path / "o.wav"), ref=ref, loud=False)
    assert r["ok"] and any("речь не сошлась" in w for w in r["warnings"])
    assert r["verify"]["coverage"] == 0.1


def test_dict_hear_synthesizes_a_phrase_not_a_bare_word(tmp_path, monkeypatch):
    """⚠️ `add` проверяет СТРОКУ — что слой даёт «Оупен Эй Ай». Что из неё сделает F5, не
    знает никто. ⭐ Пробник фразой: на одиночном слове F5 ведёт себя иначе, чем в потоке,
    и судить по нему значило бы проверять не тот случай."""
    from rusvoice import say as SAY_MOD
    got = {}

    def fake_say(text, out, **kw):
        got["text"], got["verify"] = text, kw.get("verify")
        return {"ok": True, "out": out, "steps": [], "warnings": []}

    monkeypatch.setattr(SAY_MOD, "say", fake_say)
    r = DI.hear("brands", "OpenAI", str(tmp_path / "p.wav"))
    assert r["ok"] and r["expected"] == "Оупен Эй Ай"
    assert len(got["text"].split()) > 2 and "OpenAI" in got["text"]
    assert got["verify"] is False   # сверять ASR-ом пробник смысла нет


def test_dict_hear_refuses_a_key_that_is_not_in_the_dictionary(tmp_path):
    r = DI.hear("brands", "нетакогоключа", str(tmp_path / "p.wav"))
    assert not r["ok"] and "нет" in r["detail"]


# ── движок нужен только синтезу ──────────────────────────────────────────────

def test_limiter_ceiling_mirrors_the_engine():
    """⚠️ Число живёт в двух местах: у движка (`talkingphoto.LIMIT`) и своё, на случай
    установки без него. Разъезд — тихая смена громкости у половины путей."""
    from rusvoice import engine as E
    TP = E.get("talkingphoto")
    assert TP is None or float(TP.LIMIT) == E.TRUE_PEAK_LIMIT


def test_ffmpeg_is_found_without_the_engine(monkeypatch):
    """⭐ `loud` не обязан ждать видеотракт: libass пакету не нужен — субтитров он не
    рисует, ему хватает полей по краям и `atempo`."""
    import shutil

    from rusvoice import engine as E
    monkeypatch.setattr(E, "get", lambda name: None)
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
    ff, _ = E.ffmpeg_bins()
    assert (ff is None) == (shutil.which("ffmpeg") is None)


def test_ffmpeg_env_wins_over_path(monkeypatch):
    from rusvoice import engine as E
    monkeypatch.setattr(E, "get", lambda name: None)
    monkeypatch.setenv("FFMPEG_BIN", "/такой/ffmpeg")
    assert E.ffmpeg_bins()[0] == "/такой/ffmpeg"


def test_transcribe_returns_empty_when_nothing_can_recognise(tmp_path, monkeypatch):
    """Пусто — это «распознавать нечем», и вызывающий обязан отличать это от «речи нет»."""
    from rusvoice import engine as E
    monkeypatch.setattr(E, "get", lambda name: None)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    assert E.transcribe_words(str(tmp_path / "x.wav")) == []


def test_ref_text_without_the_engine_names_its_source_honestly(tmp_path, monkeypatch):
    """Порядок резолва (env → сайдкар → config → константа) живёт в ОДНОМ месте —
    `rusvoice.clone._ref_text_source`, — поэтому без движка он тот же самый.

    ⚠️ Проверяем не только удачный случай: когда сайдкара нет, в F5 уходит ГЛОБАЛЬНЫЙ
    дефолт, и источник обязан называть себя константой. Спутать «транскрипт этого
    аудио» с «текстом вообще» — ровно тот дефект, из-за которого F5 галлюцинирует
    префиксы, а слышно это как «модель мямлит»."""
    from rusvoice import clone as C
    from rusvoice import engine as E
    monkeypatch.setattr(E, "get", lambda name: None)
    monkeypatch.delenv("F5_REF_TEXT", raising=False)
    monkeypatch.setattr(C, "_clone_cfg", lambda: {})
    ref = tmp_path / "voice.wav"
    ref.write_bytes(b"")
    (tmp_path / "voice.txt").write_text("это транскрипт", encoding="utf-8")
    text, src = R.effective_ref_text(str(ref))
    assert text == "это транскрипт" and "voice.txt" in src

    text, src = R.effective_ref_text(str(tmp_path / "нет.wav"))
    assert text == C.F5_REF_TEXT and "константа" in src


# ── голоса: «мой голос» как названная вещь ───────────────────────────────────

from pathlib import Path  # noqa: E402

from rusvoice import voices as V0  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Реестр голосов в песочнице. ⚠️ Без этого тесты писали бы в НАСТОЯЩИЙ
    `~/.config/rusvoice` и меняли рабочую настройку человека, который их запустил."""
    monkeypatch.setattr(V0, "HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(V0, "STORE", str(tmp_path / "cfg" / "voices.json"))
    monkeypatch.setattr(V0, "VOICES_DIR", str(tmp_path / "cfg" / "voices"))
    return tmp_path


def _ready_ref(tmp_path, name="src", seconds=10.0):
    """Готовый эталон: аудио нужной длины + транскрипт рядом."""
    p = _tone_at(tmp_path / f"{name}.wav", -20, seconds=seconds)
    (tmp_path / f"{name}.txt").write_text("дословный транскрипт эталона", encoding="utf-8")
    return p


def test_voice_add_takes_a_ready_reference_as_is(store):
    """⭐ Готовый эталон не гоняем через ASR: это минута работы ради того, чтобы заменить
    выверенный транскрипт свежей ослышкой распознавания."""
    r = V0.add(_ready_ref(store))
    assert r["ok"] and r["current"], r
    assert "как есть" in r["how"]
    assert os.path.isfile(r["ref"]) and os.path.isfile(R.sidecar(r["ref"]))
    assert r["text"] == "дословный транскрипт эталона"


def test_voice_add_copies_instead_of_linking(store):
    """⚠️ Ссылка на исходник — молчаливо сломанный голос: файл переименуют или сотрут,
    и узнаем мы об этом от F5, а не от нас самих."""
    src = _ready_ref(store)
    r = V0.add(src, "мой")
    os.remove(src)
    assert os.path.isfile(r["ref"]), "эталон обязан пережить исчезновение источника"
    assert V0.current_ref() == r["ref"]


def test_voice_add_refuses_a_reference_without_a_transcript(store, monkeypatch):
    """Эталон без транскрипта заставляет F5 галлюцинировать префиксы, и слышно это как
    «модель мямлит», а не как ошибка настройки. Отказ громче тихой порчи."""
    src = _tone_at(store / "no_text.wav", -20, seconds=10.0)
    monkeypatch.setattr(V0, "READY_MIN", 0.0)
    monkeypatch.setattr(V0, "READY_MAX", 999.0)
    r = V0.add(src, as_is=True)
    assert not r["ok"] and "транскрипт" in r["detail"]


def test_voice_list_hides_a_voice_whose_file_vanished(store):
    r = V0.add(_ready_ref(store))
    os.remove(r["ref"])
    cur, voices = V0.all_voices()
    assert voices == {} and cur is None, "запись без файла — не голос, а обломок реестра"


def test_voice_use_names_what_is_available(store):
    V0.add(_ready_ref(store, "первый"), "первый")
    bad = V0.use("второй")
    assert not bad["ok"] and "первый" in bad["detail"], "отказ обязан говорить, что ЕСТЬ"


def test_voice_set_rejects_a_value_outside_the_range(store):
    """⚠️ Битое значение отбиваем здесь: дальше оно доехало бы до синтеза и стало бы
    слышимым дефектом без единого сообщения."""
    V0.add(_ready_ref(store))
    assert not V0.set_param("tempo", "9")["ok"]
    assert not V0.set_param("tempo", "быстро")["ok"]
    assert not V0.set_param("громкость", "1")["ok"]
    assert V0.set_param("tempo", "1.12")["ok"]
    assert V0.settings()["tempo"] == 1.12


def test_voice_remove_keeps_the_file(store):
    """Убрать из реестра — правка настройки. Удалить файл — потеря материала. Разные вещи."""
    r = V0.add(_ready_ref(store))
    out = V0.remove(r["name"])
    assert out["ok"] and os.path.isfile(out["ref"])
    assert V0.current_ref() is None


def test_default_ref_prefers_the_chosen_voice_over_config(store, monkeypatch):
    """Выбранный голос назван человеком и только что; конфиг — настройка окружения."""
    from rusvoice import clone as C
    monkeypatch.setattr(C, "_clone_cfg", lambda: {"ref": "/из/конфига.wav"})
    assert C.default_ref() == "/из/конфига.wav"
    r = V0.add(_ready_ref(store))
    assert C.default_ref() == r["ref"]


def test_clone_synth_ignores_the_chosen_voice(store, monkeypatch, tmp_path):
    """⚠️⚠️ Инвариант движка. `clone_synth` — вход рендера, и он обязан брать эталон из
    конфига независимо от того, что кто-то выбрал в CLI на этой машине. Иначе `rusvoice
    voice use` молча переозвучил бы чужим голосом весь следующий ролик."""
    from rusvoice import clone as C
    V0.add(_ready_ref(store))
    monkeypatch.setattr(C, "_clone_cfg", lambda: {"ref": "/из/конфига.wav"})
    monkeypatch.setattr(C, "accentize", lambda t: t)
    monkeypatch.setattr(C, "_pad_edges", lambda src, dst, **k: Path(dst).write_bytes(b"o"))
    monkeypatch.setenv("CLONE_ONSET_FIX", "0")
    seen = {}

    def fake_f5(ref, text, out):
        seen["ref"] = ref
        Path(out).write_bytes(b"r")
        return True

    monkeypatch.setitem(C.PROVIDERS, "f5", fake_f5)
    C.clone_synth("текст", str(tmp_path / "a"))
    assert seen["ref"] == "/из/конфига.wav"


def test_say_takes_voice_settings_only_where_the_caller_stayed_silent(
        store, fake_synth, monkeypatch, tmp_path):
    """⭐ Настройка голоса — это дефолт, а не приказ: явный ключ обязан её перебивать."""
    seen, _ = fake_synth
    V0.add(_ready_ref(store))
    V0.set_param("outro", "0.9")
    monkeypatch.setattr(SAY, "_tempo", lambda src, dst, t: False)

    r = SAY.say("привет всем", str(tmp_path / "a.wav"), verify=False)
    assert any("0.9" in s and "настроек голоса" in s for s in r["steps"]), r["steps"]

    r = SAY.say("привет всем", str(tmp_path / "b.wav"), outro=0.18, verify=False)
    assert any("клиповый" in s for s in r["steps"]), r["steps"]


def test_say_without_a_voice_says_how_to_get_one(tmp_path, store, monkeypatch):
    """⚠️ Прежний отказ — «эталона нет: <путь>» — называл симптом и молчал о лекарстве,
    хотя достать эталон пакет умеет из любого видео (`ref grab`). Дыра была в дефолте."""
    from rusvoice import clone as C
    from rusvoice import engine as E
    monkeypatch.setattr(SAY, "blockers", lambda: [])
    monkeypatch.setattr(C, "_clone_cfg", lambda: {})
    monkeypatch.setattr(C, "CLONE_REF", str(tmp_path / "нет.wav"))
    monkeypatch.setattr(E, "get", lambda name: None)   # и движка рядом нет
    r = SAY.say("привет", str(tmp_path / "o.wav"))
    assert not r["ok"] and "voice add" in r["detail"]
