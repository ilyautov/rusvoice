#!/usr/bin/env python3
"""
Препроцессор произношения для русского TTS (edge-tts, голос Дмитрий).

Почему текстовые замены, а не SSML: Microsoft режет SSML у edge-tts полностью
(остаётся только <voice>+<prosody>), поэтому произношение правится ТОЛЬКО
заменами в тексте до синтеза. Эмпирика на Дмитрии: латиница читается плохо
(«Claude MAX» → слышится «к кладу МХ»), кириллизация решает
(«Клод Макс» — чисто, проверено WhisperX-транскрипцией).

Слои обработки (по порядку):
  1. BRANDS — словарь брендов: регистронезависимый, по границам слов,
     многословные фразы матчатся первыми (сортировка по длине ключа).
     Расширяем без кода: pipeline/brands_ru.json мержится ПОВЕРХ встроенного.
  1b. STRESS — словарь ударений (pipeline/stress_ru.json): фонетическое
     переписывание кириллица→кириллица под верное ударение. edge-tts НЕ уважает
     U+0301 (доказано), но читает иначе-написанное слово с нужным ударением
     («рассеяние»→«рассейяние» = ударение на Е). Только ПРОВЕРЕННЫЕ пары
     (whisperx char-align + акустика). После брендов, до транслита. Пусто = no-op.
  2. Фолбэк-транслитерация оставшейся латиницы по правилам практической
     транскрипции (посимвольно-диграфная: sh→ш, ch→ч, oo→у, ck→к…).
     Лучше неидеальная кириллица, чем «мах» вместо «макс».
     Числа, единицы, кириллица и пунктуация не трогаются.
     Короткие ALL-CAPS аббревиатуры (2-4 буквы) вне словаря — по буквам
     английскими именами («TBC» → «ти-би-си»), как и словарные API/CLI/SDK.
  3. (opt-in) Ударения silero-stress: только при PRONOUNCE_STRESS=1 и
     установленном пакете. «+е» от silero → «е» + U+0301 (комбинируемый акут).
     Вердикт «уважает ли Дмитрий U+0301» ещё не вынесен — поэтому строго opt-in.
     Пакет НЕ устанавливаем — graceful import, нет пакета → молча пропускаем.

API: pronounce_ru(text) -> text. env PRONOUNCE=0 — сквозной байпас (текст как есть).
"""
import json
import os
import re

# ── 0. Единицы измерения: знак → слово с числом СЛОВАМИ ───────────────────────
# Фидбэк Ильи (cycle 4): клон-голос F5 читает голую цифру роботизированно («40»,
# «до 10x» → «до десять х»). Раскрываем И знак единицы, И само число — словами,
# с верным падежом (после до/от/с… — родительный: «до сорока процентов»).
# Хирургически: число трогаем ТОЛЬКО при знаке единицы (%/$/Nx). Голые числа
# («403», «2.0», «issue 34049») не трогаем. Не ломаем «100%-ный» (после % буква).
_PREP = r"до|от|со|с|около|более|менее|свыше|порядка|почти|примерно|где-то"
# предлог захватываем только как ОТДЕЛЬНОЕ слово (lookbehind на границу) — иначе
# «с» матчится внутри «плюс», «минус» и т.п.
_PFX = rf"(?:(?<![\w-])(?P<p>{_PREP})\s+)?"
_PCT_RE = re.compile(rf"{_PFX}(?P<n>\d+(?:[.,]\d+)?)\s*%(?![-\wА-Яа-яЁё])", re.IGNORECASE)
_USD_RE = re.compile(rf"{_PFX}\$\s*(?P<n>\d+(?:[.,]\d+)?)", re.IGNORECASE)
# Множитель «10x»/«5х» (лат./кир. икс) → «N раз». НЕ внутри слова, НЕ часть «Max 5x».
_MULT_RE = re.compile(rf"{_PFX}(?<![\w])(?P<n>\d+)\s*[xхXХ](?![\w])", re.IGNORECASE)

# Числительные 0..999 словами (самодостаточно, без зависимостей). Покрывает все
# реалистичные значения единиц (проценты/множители/цены) и типичные голые числа
# (HTTP-коды, цены, счётчики — opt-in PRONOUNCE_NUMBERS). Вне диапазона → num2words
# (если есть) либо цифра остаётся.
_ONES = {0: "ноль", 1: "один", 2: "два", 3: "три", 4: "четыре", 5: "пять",
         6: "шесть", 7: "семь", 8: "восемь", 9: "девять"}
_TEENS = {10: "десять", 11: "одиннадцать", 12: "двенадцать", 13: "тринадцать",
          14: "четырнадцать", 15: "пятнадцать", 16: "шестнадцать", 17: "семнадцать",
          18: "восемнадцать", 19: "девятнадцать"}
_TENS = {20: "двадцать", 30: "тридцать", 40: "сорок", 50: "пятьдесят",
         60: "шестьдесят", 70: "семьдесят", 80: "восемьдесят", 90: "девяносто"}
_ONES_G = {0: "ноля", 1: "одного", 2: "двух", 3: "трёх", 4: "четырёх", 5: "пяти",
           6: "шести", 7: "семи", 8: "восьми", 9: "девяти"}
_TEENS_G = {10: "десяти", 11: "одиннадцати", 12: "двенадцати", 13: "тринадцати",
            14: "четырнадцати", 15: "пятнадцати", 16: "шестнадцати", 17: "семнадцати",
            18: "восемнадцати", 19: "девятнадцати"}
_TENS_G = {20: "двадцати", 30: "тридцати", 40: "сорока", 50: "пятидесяти",
           60: "шестидесяти", 70: "семидесяти", 80: "восьмидесяти", 90: "девяноста"}
_HUNDREDS = {100: "сто", 200: "двести", 300: "триста", 400: "четыреста",
             500: "пятьсот", 600: "шестьсот", 700: "семьсот",
             800: "восемьсот", 900: "девятьсот"}
_HUNDREDS_G = {100: "ста", 200: "двухсот", 300: "трёхсот", 400: "четырёхсот",
               500: "пятисот", 600: "шестисот", 700: "семисот",
               800: "восьмисот", 900: "девятисот"}


# Версия словарей произношения (brands_ru / stress_ru / hard_e_ru) в ключе аудио-кэша движка.
# Здесь же и по той же причине, что `accentize.RULES_VERSION` — см. комментарий там.
#
# ⚠️ Поднимать при КАЖДОЙ правке словаря: в хэш кэша идёт СЫРОЙ `vo`, а словари применяются
# уже внутри синтеза, поэтому сама правка озвучку не инвалидирует и «фикс не доезжает».
# v3 — твёрдая «э» в заимствованиях + буквенные аббревиатуры + бренды из корпуса.
RULES_VERSION = "3"


def _spell_ru(n: int, gen: bool = False) -> str | None:
    """Целое 0..999 словами (gen=True — родительный падеж). Вне диапазона → None."""
    ones, teens, tens = (_ONES_G, _TEENS_G, _TENS_G) if gen else (_ONES, _TEENS, _TENS)
    if n in teens:
        return teens[n]
    if 0 <= n < 10:
        return ones[n]
    if 20 <= n < 100:
        t, o = (n // 10) * 10, n % 10
        return tens[t] if o == 0 else f"{tens[t]} {ones[o]}"
    if 100 <= n < 1000:
        hundreds = _HUNDREDS_G if gen else _HUNDREDS
        h, rest = (n // 100) * 100, n % 100
        if rest == 0:
            return hundreds[h]
        return f"{hundreds[h]} {_spell_ru(rest, gen)}"
    return None


def _num_words(num: str, gen: bool) -> str:
    """Число-строка → слова (целое 0..999 через _spell_ru; дробное/вне диапазона —
    цифра как есть, опц. num2words для крупных целых)."""
    if "," in num or "." in num:
        return num
    n = int(num)
    w = _spell_ru(n, gen)
    if w is not None:
        return w
    try:
        import num2words
        return num2words.num2words(n, lang="ru")
    except Exception:
        return num


def _ru_plural(num: str, one: str, few: str, many: str) -> str:
    """Форма существительного по числу: 1 процент, 2-4 процента, 5+/11-14 процентов.
    Дробное (1,5) → форма «few» (1,5 процента)."""
    if "," in num or "." in num:
        return few
    n = int(num)
    if 11 <= n % 100 <= 14:
        return many
    d = n % 10
    if d == 1:
        return one
    if 2 <= d <= 4:
        return few
    return many


def _unit_sub(m, one: str, few: str, many: str) -> str:
    """Замена для единицы: [предлог] число-словами форма-существительного."""
    num = m.group("n")
    prep = m.group("p")
    words = _num_words(num, gen=bool(prep))
    unit = _ru_plural(num, one, few, many)
    return f"{prep} {words} {unit}" if prep else f"{words} {unit}"


def _apply_units(text: str) -> str:
    """«40%»→«сорок процентов», «до 40%»→«до сорока процентов», «$5»→«пять долларов»."""
    text = _PCT_RE.sub(lambda m: _unit_sub(m, "процент", "процента", "процентов"), text)
    text = _USD_RE.sub(lambda m: _unit_sub(m, "доллар", "доллара", "долларов"), text)
    return text


def _apply_multipliers(text: str) -> str:
    """Множитель «10x»→«десять раз», «до 10x»→«до десяти раз». Зовётся ПОСЛЕ брендов
    (чтобы «Max 5x» уже стал «Макс пять икс» и не попал сюда)."""
    def _sub(m):
        num, prep = m.group("n"), m.group("p")
        words = _num_words(num, gen=bool(prep))
        unit = _ru_plural(num, "раз", "раза", "раз")
        return f"{prep} {words} {unit}" if prep else f"{words} {unit}"
    return _MULT_RE.sub(_sub, text)


# ── 1. Словарь брендов ────────────────────────────────────────────────────────
# Лексика проекта (Дневник Claude: подписки/оплата/баны). Ключи — как пишутся
# в статьях; матчинг регистронезависимый. Многословные фразы держим выше для
# читаемости, но порядок матчинга гарантирует сортировка по длине в _brand_regex.
BRANDS = {
    # ── многословные фразы (матчатся первыми) ──
    "Claude Cowork": "Клод Коворк",
    "Claude Code": "Клод Код",
    "Claude Desktop": "Клод Десктоп",
    "claude.ai": "клод точка эй-ай",
    "console.anthropic.com": "консоль антропика",
    "App Store": "Эп Стор",
    "Google Play": "Гугл Плей",
    "Apple ID": "Эпл айди",
    "Agent SDK": "Эйджент эс-дэ-кей",
    "GitHub Actions": "Гитхаб Экшенс",
    "Fast Mode": "Фаст Мод",
    "Remote Control": "Римоут Контрол",
    "Restore purchases": "Рестор пёрчесиз",
    "Max 20x": "Макс двадцать икс",
    "Max 5x": "Макс пять икс",
    "claude -p": "клод минус пэ",
    # ── одиночные бренды/продукты ──
    "Anthropic": "Антропик",
    "Claude": "Клод",
    "Cowork": "Коворк",
    "OpenClaw": "ОупенКло",
    "GitHub": "Гитхаб",
    "Google": "Гугл",
    "Apple": "Эпл",
    "Opus": "Опус",
    "Sonnet": "Соннет",
    "Haiku": "Хайку",
    "Persona": "Персона",
    "Oplatym": "Оплатым",
    "Code": "Код",
    "MAX": "Макс",
    "Pro": "Про",
    "iOS": "айос",
    "macOS": "мак-ос",
    # ── термины из статей ──
    "Billing": "Биллинг",
    "overflow": "оверфлоу",
    "issue": "ишью",
    "null": "нал",
    "subscriptionType": "сабскрипшен тайп",
    # ── аббревиатуры: по буквам читаются Дмитрием чисто ──
    "API": "эй-пи-ай",
    "CLI": "си-эл-ай",
    "SDK": "эс-дэ-кей",
    "IDE": "ай-ди-и",
    "CI": "си-ай",
    "IP": "ай-пи",
    "AI": "эй-ай",
    "LLM": "эл-эл-эм",
    "URL": "ю-эр-эл",
    "EU": "и-ю",
    "VPN": "впн",   # кириллицей читается норм («вэ-пэ-эн»)
    "VAT": "ват",
    "BIN": "бин",
    "SIM": "сим",
}

# Расширение словаря без правки кода: длинный хвост (банки, площадки и т.п.)
_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brands_ru.json")


def _load_brands():
    """Встроенный словарь + опциональный brands_ru.json поверх (override-мерж)."""
    brands = dict(BRANDS)
    try:
        if os.path.exists(_JSON_PATH):
            with open(_JSON_PATH, encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                # ключи на «_» — служебные (комментарии), не бренды
                brands.update({k: v for k, v in user.items() if not k.startswith("_")})
    except Exception:
        pass  # битый JSON не должен валить синтез — работаем на встроенном
    return brands


def _brand_regex(brands=None):
    """Компилирует единый паттерн: альтернативы от длинных к коротким, границы
    слов — lookaround по латинице/цифрам (НЕ \\b: «Maximum» не должен дать
    «Максimum», а \\b не ловит стык с кириллицей). Возвращает (rx, таблица замен)."""
    brands = brands or _load_brands()
    keys = sorted(brands, key=len, reverse=True)
    parts = [r"\s+".join(re.escape(p) for p in k.split()) for k in keys]
    rx = re.compile(
        r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % "|".join(parts),
        re.IGNORECASE,
    )
    table = {" ".join(k.lower().split()): v for k, v in brands.items()}
    return rx, table


def _apply_brands(text, brands=None):
    rx, table = _brand_regex(brands)
    return rx.sub(lambda m: table[" ".join(m.group(0).lower().split())], text)


# ── 1b. Словарь ударений: фонетическое переписывание под ударение ─────────────
# edge-tts (Дмитрий) НЕ уважает U+0301 (комбинируемый акут) — доказано
# эмпирически. Но слово можно «переписать» кириллицей так, что Дмитрий читает
# его с верным ударением (тот же приём, что BRANDS): напр. «рассеяние» Дмитрий
# читает «рассея-нИ-е», а «рассейяние» — верно «рас-сЕ-я-ни-е» (проверено
# whisperx char-align + акустикой гласных). Только ПРОВЕРЕННЫЕ пары; словарь
# в pipeline/stress_ru.json (кириллица→кириллица). Применяется ПОСЛЕ брендов,
# ДО транслита. Регистронезависимо, по границам слова; начальный регистр токена
# восстанавливается на замене.
_STRESS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stress_ru.json")


def _load_stress():
    """Словарь ударений из stress_ru.json. Служебные ключи на «_» отбрасываем.
    Нет файла / битый JSON → пустой словарь (слой выключается, не валит синтез)."""
    try:
        if os.path.exists(_STRESS_PATH):
            with open(_STRESS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {k: v for k, v in data.items()
                        if not k.startswith("_") and isinstance(v, str)}
    except Exception:
        pass  # битый словарь не должен ломать TTS — работаем без слоя ударений
    return {}


def _match_case(src, repl):
    """Перенести «форму регистра» исходного токена на замену (для начала фразы/
    заголовков): Title→Title, ВЕРХНИЙ→ВЕРХНИЙ, иначе как в словаре (строчные)."""
    if src.isupper() and len(src) > 1:
        return repl.upper()
    if src[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def _stress_regex(table):
    """Единый паттерн ключей словаря: границы слова — НЕ \\b (не ловит стык с
    кириллицей корректно), а lookaround по буквам/цифрам кириллицы и латиницы.
    Длинные ключи раньше коротких."""
    if not table:
        return None
    keys = sorted(table, key=len, reverse=True)
    body = "|".join(re.escape(k) for k in keys)
    # граница: не буква (кириллица/латиница) и не цифра по обе стороны
    return re.compile(
        r"(?<![A-Za-zА-Яа-яЁё0-9])(?:%s)(?![A-Za-zА-Яа-яЁё0-9])" % body,
        re.IGNORECASE,
    )


def _apply_stress(text, table=None):
    """Переписать слова из словаря ударений (если он не пуст). Безопасно: пустой
    словарь → текст без изменений (байт-в-байт)."""
    if table is None:
        table = _load_stress()
    rx = _stress_regex(table)
    if rx is None:
        return text
    lower = {k.lower(): v for k, v in table.items()}

    def sub(m):
        tok = m.group(0)
        repl = lower.get(tok.lower())
        return _match_case(tok, repl) if repl is not None else tok

    return rx.sub(sub, text)


# ── 1c. Твёрдая «э» в заимствованиях ─────────────────────────────────────────
# Илья 26.08.2026: «не для лендинга, а для лэндинга — в большинстве слов пишется
# «е», а читается «э»». Верно это НЕ для всего русского (в родных словах «е»
# мягкая: лес, дело, текст), а для заимствований, где согласная перед «е»
# осталась твёрдой: лендинг, модель, контент, тренд. И edge, и клон читают их
# мягко — «лендинг», «модели»; переписываем графему, как в BRANDS/STRESS.
# Поэтому это СЛОВАРЬ (pipeline/hard_e_ru.json), а не правило: сплошная замена
# е→э изуродовала бы половину речи. Ключ — начало слова (основа без окончания),
# окончание не трогаем: «модел»→«модэл» покрывает все падежи и «моделирование».
_HARD_E_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hard_e_ru.json")


def _load_hard_e():
    """Словарь твёрдой «э» из hard_e_ru.json. Служебные ключи на «_» отбрасываем.
    Нет файла / битый JSON → пустой словарь (слой выключается, не валит синтез)."""
    try:
        if os.path.exists(_HARD_E_PATH):
            with open(_HARD_E_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {k.lower(): v for k, v in data.items()
                        if not k.startswith("_") and isinstance(v, str)}
    except Exception:
        pass  # битый словарь не должен ломать TTS — работаем без слоя
    return {}


def _hard_e_regex(table):
    """Основа в НАЧАЛЕ слова + любой кириллический хвост. Длинные основы раньше
    коротких, иначе «кеш» съел бы «кешир» до того, как тот сматчится."""
    if not table:
        return None
    body = "|".join(re.escape(k) for k in sorted(table, key=len, reverse=True))
    return re.compile(
        r"(?<![А-Яа-яЁёA-Za-z0-9])(?:%s)[А-Яа-яЁё]*" % body,
        re.IGNORECASE,
    )


def _apply_hard_e(text, table=None):
    """Переписать «е»→«э» в заимствованиях из словаря. Пустой словарь → текст
    байт-в-байт."""
    if table is None:
        table = _load_hard_e()
    rx = _hard_e_regex(table)
    if rx is None:
        return text

    def sub(m):
        tok = m.group(0)
        low = tok.lower()
        for stem in sorted(table, key=len, reverse=True):
            if low.startswith(stem):
                return _match_case(tok, table[stem] + low[len(stem):])
        return tok

    return rx.sub(sub, text)


# ── 2. Фолбэк-транслитерация остаточной латиницы ─────────────────────────────
# Практическая транскрипция, посимвольно-диграфная. Не идеал, но Дмитрий
# по кириллице читает предсказуемо, а по латинице — лотерея.
_DIGRAPHS = [
    ("tch", "ч"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"),
    ("ph", "ф"), ("th", "т"), ("ck", "к"), ("qu", "кв"),
    ("oo", "у"), ("ee", "и"), ("ea", "и"), ("ou", "ау"), ("ow", "оу"),
    ("ay", "ей"), ("ey", "ей"), ("oy", "ой"),
    ("ya", "я"), ("yu", "ю"), ("yo", "йо"),
]
_SINGLE = {
    "a": "а", "b": "б", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х",
    "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о",
    "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в",
    "w": "в", "x": "кс", "y": "и", "z": "з",
}
# Английские имена букв — для ALL-CAPS аббревиатур вне словаря («TBC»→«ти-би-си»)
_LETTER_NAMES = {
    "a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф",
    "g": "джи", "h": "эйч", "i": "ай", "j": "джей", "k": "кей", "l": "эл",
    "m": "эм", "n": "эн", "o": "оу", "p": "пи", "q": "кью", "r": "ар",
    "s": "эс", "t": "ти", "u": "ю", "v": "ви", "w": "дабл-ю", "x": "икс",
    "y": "вай", "z": "зед",
}


def _translit_word(w):
    """Один латинский токен → кириллица. Регистр первой буквы сохраняем."""
    lw = w.lower()
    if lw == "x":
        return "икс"  # «Max 5x» и прочие множители: «кс» нечитаемо
    # короткая ALL-CAPS аббревиатура — по буквам, в стиле словарных API/CLI
    if w.isupper() and 2 <= len(w) <= 4:
        return "-".join(_LETTER_NAMES[c] for c in lw)
    out, i = [], 0
    while i < len(lw):
        for dg, ru in _DIGRAPHS:
            if lw.startswith(dg, i):
                out.append(ru)
                i += len(dg)
                break
        else:
            c = lw[i]
            if c == "c":  # c перед e/i/y → «с», иначе «к»
                out.append("с" if lw[i + 1:i + 2] in ("e", "i", "y") else "к")
            else:
                out.append(_SINGLE.get(c, c))
            i += 1
    res = "".join(out)
    if w[:1].isupper():
        res = res[:1].upper() + res[1:]
    return res


# ── 2b. Кириллические буквенные аббревиатуры ─────────────────────────────────
# Правило ALL-CAPS «по буквам» из _translit_word работало ТОЛЬКО на латинице: `[A-Za-z]+`.
# Кириллическая аббревиатура проходила насквозь, и синтез импровизировал — Илья 27.08.2026 на
# ролике про промпты: «ТЗ — должно быть ТэЗэ». Это не редкий случай: наш домен полон ТЗ, РФ,
# СНГ, ИТ, ПО, ОС.
# ⚠️ Спеллинговать ВСЕ кириллические ALL-CAPS нельзя: часть читается словом (вуз, НАТО, СПИД,
# ГОСТ) и «гэ-о-эс-тэ» было бы хуже исходного. Разделитель надёжный и не вкусовой: аббревиатура
# БЕЗ ГЛАСНЫХ не может быть слогом, значит читается только по буквам. ТЗ/РФ/СНГ/ВТБ попадают,
# ГОСТ/НАТО/вуз — нет. Слово из одной буквы («В», «А» в начале фразы) не трогаем.
_RU_LETTER_NAMES = {
    "а": "а", "б": "бэ", "в": "вэ", "г": "гэ", "д": "дэ", "е": "е", "ж": "жэ",
    "з": "зэ", "и": "и", "й": "и краткое", "к": "ка", "л": "эль", "м": "эм",
    "н": "эн", "о": "о", "п": "пэ", "р": "эр", "с": "эс", "т": "тэ", "у": "у",
    "ф": "эф", "х": "ха", "ц": "цэ", "ч": "че", "ш": "ша", "щ": "ща",
    "ы": "ы", "э": "э", "ю": "ю", "я": "я",
}
_RU_VOWELS = frozenset("аеёиоуыэюя")
_RU_ABBR_RE = re.compile(r"(?<![А-Яа-яЁё])([А-ЯЁ]{2,4})(?![А-Яа-яЁё])")


def _spell_ru_abbr(text):
    """«ТЗ» → «тэ-зэ». Только ALL-CAPS кириллица 2-4 буквы БЕЗ гласных (см. комментарий выше)."""
    def repl(m):
        w = m.group(1)
        low = w.lower().replace("ё", "е")
        if any(c in _RU_VOWELS for c in low):
            return w                       # есть гласная → может читаться слогом, не трогаем
        if not all(c in _RU_LETTER_NAMES for c in low):
            return w                       # ь/ъ и прочее без имени буквы — оставляем как есть
        return "-".join(_RU_LETTER_NAMES[c] for c in low)
    return _RU_ABBR_RE.sub(repl, text)


def _translit_rest(text):
    """Транслит ТОЛЬКО латинских последовательностей: числа/единицы/кириллица/
    пунктуация остаются как есть (regex не задевает [^A-Za-z])."""
    return re.sub(r"[A-Za-z]+", lambda m: _translit_word(m.group(0)), text)


# ── 3. Ударения (opt-in, silero-stress) ──────────────────────────────────────
_STRESSOR = None
_STRESSOR_TRIED = False


def _load_stressor():
    """Graceful import silero-stress: пакет может отсутствовать — это норма.
    Сам НЕ устанавливаем. Возвращает callable(text)->text с «+» перед ударной
    гласной, либо None."""
    global _STRESSOR, _STRESSOR_TRIED
    if _STRESSOR_TRIED:
        return _STRESSOR
    _STRESSOR_TRIED = True
    try:
        import silero_stress  # type: ignore
    except Exception:
        return None
    # API пакета может отличаться по версиям — пробуем известные точки входа
    for name in ("stress", "put_stress", "accentify", "process"):
        fn = getattr(silero_stress, name, None)
        if callable(fn):
            _STRESSOR = fn
            return _STRESSOR
    for name in ("load_accentor", "load_model"):
        factory = getattr(silero_stress, name, None)
        if callable(factory):
            try:
                model = factory()
                _STRESSOR = model if callable(model) else getattr(model, "stress", None)
            except Exception:
                _STRESSOR = None
            return _STRESSOR
    return None


def _plus_to_acute(text):
    """Конвенция silero («+» ПЕРЕД ударной гласной) → гласная + U+0301 (акут)."""
    return re.sub(r"\+([аеёиоуыэюяАЕЁИОУЫЭЮЯ])", "\\1́", text)


def add_stress(text):
    """Расстановка ударений через silero-stress. Нет пакета / упал — текст как есть."""
    fn = _load_stressor()
    if fn is None:
        return text
    try:
        return _plus_to_acute(fn(text))
    except Exception:
        return text


# ── Публичная точка входа ────────────────────────────────────────────────────
# диапазон «2-4» / «10–20» (дефис/тире между числами) и голые целые. Голые числа читаются F5
# роботизированно (аудит 2026-07). Преобразование opt-in (PRONOUNCE_NUMBERS=1) — огульная замена
# рискует задеть версии/коды, поэтому дефолт off = байт-в-байт.
# диапазон: число–число, не десятичное (не предшествует/следует «.цифра» / «,цифра»)
_RANGE_RE = re.compile(r"(?<![\w.,])(\d{1,4})\s*[–—-]\s*(\d{1,4})(?![\w]|[.,]\d)")
# голое целое: не часть слова/кода (v2, COVID19), не десятичное (2.0, 1,403), но трейлинг-пунктуация ок
_BARE_INT_RE = re.compile(r"(?<![\w.,])(\d{1,6})(?![\w]|[.,]\d)")


def _apply_bare_numbers(text: str) -> str:
    """Диапазоны «2-4»→«от двух до четырёх» + голые целые словами. Opt-in (PRONOUNCE_NUMBERS=1).
    Пропускает дробные (есть .,), проценты/доллары/множители (обработаны раньше), коды с буквами."""
    text = _RANGE_RE.sub(lambda m: f"от {_num_words(m.group(1), gen=True)} "
                                   f"до {_num_words(m.group(2), gen=True)}", text)
    text = _BARE_INT_RE.sub(lambda m: _num_words(m.group(1), gen=False), text)
    return text


def pronounce_ru(text, lang="ru"):
    """Текст «как пишется» → текст «как читается» для русского TTS.
    env PRONOUNCE=0 — полный байпас; PRONOUNCE_STRESS=1 — ещё и ударения (opt-in).
    lang: гард языкового слоя — препроцессор ТОЛЬКО для русского, любой другой
    язык возвращается как есть (основной гейт в tts_engine.synth; здесь защита
    прямых вызовов). None = «не знаю» → считаем ru (backward-compat)."""
    if lang is not None and not str(lang).strip().lower().startswith("ru"):
        return text
    if not text or os.environ.get("PRONOUNCE", "1") == "0":
        return text
    text = _apply_units(text)   # %/$ + число → слова (до брендов/транслита)
    text = _apply_brands(text)
    text = _apply_multipliers(text)  # «Nx» → «N раз» (после брендов: Max 5x уже съеден)
    if os.environ.get("PRONOUNCE_NUMBERS") == "1":   # opt-in: голые числа/диапазоны словами
        text = _apply_bare_numbers(text)
    # Ударения через фонетическое переписывание (проверенные пары из
    # stress_ru.json) — ПОСЛЕ брендов, ДО транслита. Пустой словарь = no-op.
    text = _apply_stress(text)
    text = _apply_hard_e(text)  # заимствования: лендинг→лэндинг, модели→модэли
    text = _spell_ru_abbr(text)   # кириллические буквенные аббревиатуры (ТЗ→тэ-зэ)
    text = _translit_rest(text)
    if os.environ.get("PRONOUNCE_STRESS") == "1":
        text = add_stress(text)
    return text


if __name__ == "__main__":
    import sys
    src = " ".join(sys.argv[1:]) or "Claude Code на подписке MAX через App Store: issue 34049, subscriptionType: null."
    print(pronounce_ru(src))
