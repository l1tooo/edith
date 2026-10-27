"""Скачивает запасной локальный голос Piper (ru_RU-ruslan, ~60 МБ) — он говорит, если нет интернета."""
from pathlib import Path

import requests

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/"
target = Path(__file__).parent / "voices"
target.mkdir(exist_ok=True)
for name in ("ru_RU-ruslan-medium.onnx", "ru_RU-ruslan-medium.onnx.json"):
    path = target / name
    if path.exists() and path.stat().st_size > 0:
        print("уже есть:", name)
        continue
    print("скачиваю:", name)
    with requests.get(BASE + name, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
print("готово")
