"""Образцы женских голосов Microsoft Edge для Эдит."""
import asyncio
from pathlib import Path

import edge_tts

TEXT = ("Добрый вечер. Я Эдит. Все системы работают в штатном режиме, "
        "музыка на паузе, а за окном тридцать градусов. Чем могу помочь?")

SAMPLES = [
    ("1 - Светлана (русский)", "ru-RU-SvetlanaNeural"),
    ("2 - Seraphina (многоязычный)", "de-DE-SeraphinaMultilingualNeural"),
    ("3 - Vivienne (многоязычный)", "fr-FR-VivienneMultilingualNeural"),
    ("4 - Ava (многоязычный)", "en-US-AvaMultilingualNeural"),
    ("5 - Emma (многоязычный)", "en-US-EmmaMultilingualNeural"),
]


async def main() -> None:
    out = Path(__file__).parent / "voice-samples-edith"
    out.mkdir(exist_ok=True)
    for name, voice in SAMPLES:
        target = out / f"{name}.mp3"
        try:
            await edge_tts.Communicate(TEXT, voice).save(str(target))
            print("ok", name)
        except Exception as e:  # noqa: BLE001
            target.unlink(missing_ok=True)
            print("FAIL", name, type(e).__name__)


asyncio.run(main())
