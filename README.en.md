# rusvoice: Russian voice-over in your own voice — stress, brands and abbreviations, fixed before synthesis

> A CLI for Russian text-to-speech with a cloned voice. Cloning itself is a solved, open
> problem; what is scarce is the **text layer that runs before synthesis** — stress marks
> (RUAccent), a brand dictionary (Knight Capital → «Найт Кэпитал»), hard «э» in loanwords,
> letter-by-letter abbreviations (ТЗ → «тэ-зэ»), numbers spelled out — and a way to see
> what the pipeline will do to a line **before** you hear it: `explain`, `lint`, `doctor`,
> and a check of what was actually spoken. The core has zero dependencies.

> [Русская версия](README.md) — the full documentation, this page is a summary.

[![PyPI](https://img.shields.io/pypi/v/rusvoice?label=pypi)](https://pypi.org/project/rusvoice/)
[![Python](https://img.shields.io/pypi/pyversions/rusvoice)](https://pypi.org/project/rusvoice/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![check](https://github.com/ilyautov/rusvoice/actions/workflows/ci.yml/badge.svg)](https://github.com/ilyautov/rusvoice/actions/workflows/ci.yml)

```bash
pip install rusvoice
rusvoice explain "Компания Knight Capital потеряла 440 миллионов — ТЗ было на MVP."
```

```
вход:  Компания Knight Capital потеряла 440 миллионов — ТЗ было на MVP.
выход: Компания Найт Кэпитал потеряла 440 миллионов — тэ-зэ было на эм-ви-пи.

⚠️  RUAccent недоступен в /usr/bin/python3 — ударения НЕ проставлены,
    текст уйдёт в синтез как есть
```

That warning is the whole point. Without it the environment stays silent, the voice-over
ships without stress marks, and the defect gets blamed on the model.

## Why this exists

Every voice-over defect sounds the same — «that's just how the model reads it» — while the
actual causes are different, and all of them are silent:

| What you hear | What actually happened |
|---|---|
| «Книгхт Капитал» instead of Knight Capital | not in the brand dictionary → letter-by-letter transliteration |
| the model «mumbles» | the reference transcript doesn't match the reference audio → hallucinated prefix, +13% WER |
| a dictionary fix «did nothing» | RUAccent isn't installed in *this* interpreter → text went through unmarked |
| one word sounds robotic | a `+` stress mark on a monosyllable → F5 renders it as a hard push |
| «too quiet» after normalisation | `loudnorm` returned −15.3 with exit code 0, and that was taken for success |

Every command here exists so that each row is caught **before** you listen.

## How it differs from the neighbours

[RUAccent](https://github.com/Den4ikAI/ruaccent) is not a competitor — it is a dependency,
called from inside. [RUNorm](https://github.com/Den4ikAI/runorm) and the others
([russian_tts_normalization](https://github.com/shigabeev/russian_tts_normalization),
[ru-tts-norm](https://github.com/dsnam/ru-tts-norm),
[saarus72/text_normalization](https://github.com/saarus72/text_normalization)) are
**normalisers**: text in, text out.

This is a different thing: the wiring around the whole voice-over — reference, synthesis,
loudness — and its distinguishing property is not the rule list (anyone can rewrite that in
a week) but **traceability**. See what the pipeline will do before synthesis (`explain`),
find lines in a script that will be read wrong (`lint`), verify what was actually spoken.

## Commands

| Command | What it does |
|---|---|
| `explain` | what the pipeline does to a line, word by word and layer by layer |
| `lint` | lines in a script that will be spoken wrong |
| `doctor` | whether this interpreter can be trusted: RUAccent, F5, ffmpeg, ASR |
| `dict` | pronunciation dictionaries: brands, hard «э», stress, homographs |
| `voice` | your voice as a named thing, with its own settings |
| `ref` | pull a usable voice reference out of any video or audio |
| `say` | the whole recipe in one command, with a check of what was spoken |
| `loud` | measure and fix loudness — success is judged by measuring the output |
| `ui` | the same thing in a browser |

Full documentation, examples and the reasoning behind each decision: [README.md](README.md)
(in Russian).

## Install

```bash
pip install rusvoice                    # core: explain / lint / dict / doctor
pip install "rusvoice[accent,ui]"       # + stress marks (RUAccent) and the browser UI
pip install "rusvoice[voice,accent]"    # + synthesis itself (F5)
```

The core runs on the standard library alone. That is not asceticism: a tool that explains
what will happen to your text must install *where the text pipeline already lives* — and
that place usually has its own version of torch.

## Licences

Package code — **[Apache-2.0](LICENSE)**. The weights it runs on carry their own terms:

- **F5-TTS** — MIT (code);
- ⚠️ **the Russian checkpoint `Misha24-10/F5-TTS_RUSSIAN` — CC BY-NC 4.0, non-commercial**;
- **RUAccent** — Apache-2.0.

The package neither ships nor redistributes the weights — it downloads them on demand, and
the terms stay with whoever downloaded them.

---

## Who built this

[Ilya Utov](https://github.com/ilyautov), the [AI Frontier](https://aifrontier.tech) lab. I write about how these tools work inside on [Telegram](https://t.me/gorilla_under_hood) and [LinkedIn](https://www.linkedin.com/in/ilyautov).

**Nearby:**

- [**humanizer-ru**](https://github.com/ilyautov/humanizer-ru): strips the AI fingerprint out of Russian text
- [**marketplaces-mcp-ru**](https://github.com/ilyautov/marketplaces-mcp-ru): Wildberries, Ozon, Yandex Market and Avito straight from the agent
- [**small-business-ru**](https://github.com/ilyautov/small-business-ru): 34 skills for Russian small business, the numbers computed in code
- [**consilium-principis**](https://github.com/ilyautov/consilium-principis): a board of thinkers where every quote is checked word for word
- [**hefest**](https://github.com/ilyautov/hefest): chemical safety for an industrial plant, kept inside the plant's own network

Everything else: [github.com/ilyautov](https://github.com/ilyautov). Useful? Star it, that is how other people find it.
