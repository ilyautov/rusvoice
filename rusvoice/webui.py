# -*- coding: utf-8 -*-
"""Интерфейс поверх CLI — тонкая обёртка, вся логика в модулях пакета.

Зачем вообще экран, если есть терминал: у главной команды (`explain`) ценность в ПРОСМОТРЕ,
а просмотр итеративен. Правишь реплику, смотришь, что стало с ударениями, правишь снова —
в терминале это цикл из повторных запусков, на экране это одно поле с живым ответом.
Остальные вкладки здесь потому, что без них экран врал бы умолчанием: словарь, в который
нельзя дописать, и окружение, которого не видно, выглядят как «всё в порядке».

Своей логики тут нет ни строки — только маршалинг HTTP ↔ `layers`/`dicts`/`doctor`/`say`.
Разъехаться с CLI нечему: обе стороны зовут одни и те же функции.

⚠️ Ручки словаря ПИШУТ в `pipeline/*.json`, а `say` запускает синтез. Поэтому по умолчанию
слушаем только `127.0.0.1` и режем кросс-ориджин POST — тот же гвард, что в `pipeline/server.py`:
чужая страница в браузере не должна drive-by дописывать словарь.

⚠️ Синтез требует brew-питона (F5 + RUAccent). Запущенный из `.venv` сервер это не скрывает:
вкладка «озвучить» показывает диагноз доктора вместо кнопки.

    python3 -m rusvoice ui            # http://127.0.0.1:8765
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import Body, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402

from rusvoice import dicts as DI  # noqa: E402
from rusvoice import doctor as D  # noqa: E402
from rusvoice import layers as L  # noqa: E402
from rusvoice import refaudio as R  # noqa: E402
from rusvoice import say as SAY  # noqa: E402

app = FastAPI(title="rusvoice", version="0.1",
              description="Русская озвучка: текстовый слой, словари, окружение")

# Выходы синтеза замкнуты в один каталог: имя файла приходит от нас, клиент его не выбирает.
AUDIO_DIR = Path(os.environ.get("RUSVOICE_AUDIO_DIR")
                 or (Path(tempfile.gettempdir()) / "rusvoice_audio"))


@app.middleware("http")
async def _origin_guard(request, call_next):
    """Чужая страница не должна дописывать словарь и запускать синтез с localhost-порта."""
    origin = request.headers.get("origin")
    if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
        from urllib.parse import urlparse
        allowed = set(filter(None, os.environ.get("RUSVOICE_ALLOWED_ORIGINS", "").split(",")))
        if urlparse(origin).netloc != request.headers.get("host") and origin not in allowed:
            return JSONResponse({"detail": f"origin {origin} не разрешён"}, status_code=403)
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
def index():
    return (_HERE / "webui.html").read_text(encoding="utf-8")


@app.get("/api/doctor")
def api_doctor(ref: str | None = None):
    checks = D.run(ref)
    return {"status": D.worst(checks),
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail, "fix": c.fix}
                       for c in checks]}


@app.post("/api/explain")
def api_explain(text: str = Body(..., embed=True), lang: str = Body("ru", embed=True),
                accent: bool = Body(True, embed=True)):
    tr = L.apply(text, lang=lang, accent=accent)
    return {"source": tr.source, "text": tr.text, "warnings": tr.warnings,
            "skipped": tr.skipped,
            "changes": [{"layer": c.layer, "before": c.before, "after": c.after}
                        for c in tr.changes]}


@app.get("/api/dict/{kind}")
def api_dict_list(kind: str, check: bool = False):
    if kind not in DI.KINDS:
        raise HTTPException(status_code=404, detail=f"нет словаря {kind}")
    rows = DI.entries(kind, check=check)
    return {"kind": kind, "hint": DI.KINDS[kind].key_hint,
            "counts": DI.counts()[kind], "version": DI.rules_version(),
            "entries": [{"key": e.key, "value": e.value, "source": e.source,
                         "live": e.live, "note": e.note} for e in rows]}


@app.post("/api/dict/{kind}")
def api_dict_add(kind: str, key: str = Body(..., embed=True), value: str = Body(..., embed=True),
                 note: str = Body("", embed=True), force: bool = Body(False, embed=True),
                 bump: bool = Body(False, embed=True)):
    if kind not in DI.KINDS:
        raise HTTPException(status_code=404, detail=f"нет словаря {kind}")
    r = DI.add(kind, key, value, note=note, force=force, bump=bump)
    return {"ok": r.ok, "detail": r.detail, "warnings": r.warnings, "hint": r.hint,
            "version": DI.rules_version()}


@app.post("/api/dict-bump")
def api_dict_bump():
    r = DI.bump_rules_version()
    return {"ok": r.ok, "detail": r.detail, "version": DI.rules_version()}


@app.get("/api/ref")
def api_ref(ref: str | None = None):
    checks = R.check(ref)
    return {"status": D.worst(checks),
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail, "fix": c.fix}
                       for c in checks]}


@app.post("/api/say")
def api_say(text: str = Body(..., embed=True), ref: str | None = Body(None, embed=True),
            tempo: float = Body(1.0, embed=True), loud: bool = Body(True, embed=True)):
    """⚠️ Блокирует запрос на десятки секунд: F5 на M1 считает честно, очереди тут нет.
    Отдаём тот же отчёт, что и CLI, плюс ссылку на файл."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    fd, out = tempfile.mkstemp(prefix="say_", suffix=".wav", dir=str(AUDIO_DIR))
    os.close(fd)
    r = SAY.say(text, out, ref=ref, tempo=tempo, loud=loud)
    if r.get("ok"):
        r["url"] = "/audio/" + Path(r["out"]).name
    return r


@app.get("/audio/{name}")
def api_audio(name: str):
    """Отдаём только из своего каталога и только по имени — директории отбрасываются."""
    target = (AUDIO_DIR / Path(name).name).resolve()
    if not str(target).startswith(str(AUDIO_DIR.resolve()) + os.sep) or not target.is_file():
        raise HTTPException(status_code=404, detail="нет файла")
    return FileResponse(str(target), media_type="audio/wav")


def serve(host: str = "127.0.0.1", port: int = 8765) -> int:
    """⚠️ Дефолт `127.0.0.1` не косметика: ручки пишут в словари и запускают синтез."""
    import uvicorn
    print(f"rusvoice ui → http://{host}:{port}  (интерпретатор: {sys.executable})")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
