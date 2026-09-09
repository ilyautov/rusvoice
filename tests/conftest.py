import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def pytest_sessionstart(session):
    """Без ffmpeg сказать это один раз словами, а не 48 трейсбеками.

    Треть тестов меряет настоящий звук: генерирует тон, считает громкость, режет края.
    Без ffmpeg они падают `TypeError: expected str … not NoneType` — сообщение, по
    которому человек ищет ошибку в тестах, а поставить надо ffmpeg. Проверяем той же
    функцией, какой пользуется код: вторая копия логики поиска однажды разъедется.
    """
    from rusvoice import engine as E
    if not E.ffmpeg_bins()[0]:
        raise pytest.UsageError(
            "ffmpeg не найден: ни FFMPEG_BIN, ни PATH.\n"
            "Треть тестов меряет настоящий звук — без него они падают, и это не про код.\n"
            "  macOS:  brew install ffmpeg\n"
            "  Ubuntu: sudo apt-get install -y ffmpeg\n"
            "  Windows: choco install ffmpeg\n"
            "Либо укажите путь: FFMPEG_BIN=/путь/к/ffmpeg python -m pytest tests/ -q"
        )
