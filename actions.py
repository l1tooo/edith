"""Действия Джарвиса на ПК — только белый список безопасных действий.

Две точки входа:
- quick(text)  — мгновенный разбор частых команд регулярными выражениями (без токенов);
- run(name, arg) — выполнение действия по имени (так им пользуется Claude через строку «ACTION: имя | аргумент»).
Каждое действие возвращает короткую фразу для озвучки.
"""
import ctypes
import difflib
import json
import os
import re
import subprocess
import threading
import urllib.parse
import webbrowser
from pathlib import Path

HOME = Path.home()
CREATE_NO_WINDOW = 0x08000000

# Разговорные названия → что запускать. Остальное ищется по ярлыкам меню Пуск.
APP_ALIASES = {
    "стим": "steam", "steam": "steam", "дискорд": "discord", "discord": "discord",
    "слак": "slack", "слэк": "slack", "slack": "slack", "телеграм": "telegram", "телеграмм": "telegram",
    "хром": "google chrome", "хроме": "google chrome", "гугл хром": "google chrome", "chrome": "google chrome",
    "файрфокс": "firefox", "файрфоксе": "firefox", "фаерфокс": "firefox", "фаерфоксе": "firefox",
    "мозилла": "firefox", "браузер": "firefox", "браузере": "firefox", "эдже": "microsoft edge",
    "обсидиан": "obsidian", "клод": "claude", "клода": "claude", "спотифай": "spotify", "зум": "zoom",
    "вс код": "visual studio code", "вскод": "visual studio code", "код": "visual studio code",
    "эдж": "microsoft edge", "яндекс": "yandex",
}
SPECIAL = {  # системные вещи, которые не ярлыки
    "проводник": "explorer.exe", "калькулятор": "calc.exe", "блокнот": "notepad.exe",
    "настройки": "ms-settings:", "параметры": "ms-settings:", "диспетчер задач": "taskmgr.exe",
    "панель управления": "control.exe",
}
SITES = {"ютуб": "https://www.youtube.com", "youtube": "https://www.youtube.com", "гугл": "https://www.google.com",
         "почту": "https://mail.google.com", "почта": "https://mail.google.com", "гитхаб": "https://github.com",
         "твич": "https://www.twitch.tv", "кинопоиск": "https://www.kinopoisk.ru", "вк": "https://vk.com",
         "вконтакте": "https://vk.com", "чатгпт": "https://chatgpt.com", "википедию": "https://ru.wikipedia.org"}
def _shell_folder(name: str, fallback: Path) -> Path:
    """Настоящий путь системной папки (рабочий стол бывает в OneDrive) из реестра User Shell Folders."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return Path(os.path.expandvars(winreg.QueryValueEx(k, name)[0]))
    except OSError:
        return fallback


FOLDERS = {"загрузк": _shell_folder("{374DE290-123F-4565-9164-39C4925E467B}", HOME / "Downloads"),
           "документ": _shell_folder("Personal", HOME / "Documents"),
           "рабочий стол": _shell_folder("Desktop", HOME / "Desktop"),
           "картинк": _shell_folder("My Pictures", HOME / "Pictures"),
           "изображен": _shell_folder("My Pictures", HOME / "Pictures"),
           "музык": _shell_folder("My Music", HOME / "Music"), "видео": _shell_folder("My Video", HOME / "Videos")}

TRANSLIT = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
                          "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
                          "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
                          "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"})


# ---------------------------------------------------------------- поиск программ
_apps: "dict[str, str]" = {}  # имя (нижний регистр) → путь .lnk или 'shell:AppsFolder\\AppID'


def load_apps() -> None:
    """Ярлыки меню Пуск + приложения из магазина (Get-StartApps). Вызывается один раз в фоне."""
    found: "dict[str, str]" = {}
    for root in (Path(os.environ["ProgramData"]) / "Microsoft/Windows/Start Menu/Programs",
                 Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"):
        for lnk in root.rglob("*.lnk"):
            name = lnk.stem.lower()
            if not re.search(r"uninstall|удал|readme|help|справк|documentation|website", name):
                found.setdefault(name, str(lnk))
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                              "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=20, creationflags=CREATE_NO_WINDOW).stdout
        for line in out.splitlines():
            if "|" in line:
                name, appid = line.rsplit("|", 1)
                found.setdefault(name.strip().lower(), "shell:AppsFolder\\" + appid.strip())
    except Exception:  # noqa: BLE001
        pass
    _apps.clear()
    _apps.update(found)


threading.Thread(target=load_apps, daemon=True).start()


def find_app(spoken: str) -> "tuple[str, str] | None":
    s = spoken.lower().strip(" .,!?")
    s = APP_ALIASES.get(s, s)
    candidates = [s, s.translate(TRANSLIT)]
    best, best_score = None, 0.0
    for name, target in _apps.items():
        for c in candidates:
            score = 1.0 if name == c else (0.92 if name.startswith(c) or c in name.split() else
                                           difflib.SequenceMatcher(a=c, b=name).ratio())
            if score > best_score:
                best, best_score = (name, target), score
    return best if best_score >= 0.72 else None


# ---------------------------------------------------------------- действия
def open_app(arg: str) -> str:
    key = arg.lower().strip(" .,!?")
    if key in SPECIAL:
        os.startfile(SPECIAL[key])
        return "Открываю."
    if key in SITES:
        return open_site(key)
    hit = find_app(key)
    if not hit:
        return f"Не нашёл программу «{arg}»."
    name, target = hit
    if target.startswith("shell:AppsFolder"):
        subprocess.Popen(["explorer.exe", target])
    else:
        os.startfile(target)
    return f"Открываю {name}."


def close_app(arg: str) -> str:
    hit = find_app(arg)
    proc = (hit[0] if hit else arg).split()[0].lower()
    # Вежливо: CloseMainWindow, программа сама спросит про несохранённое
    ps = f"$p = Get-Process | Where-Object {{ $_.MainWindowHandle -ne 0 -and ($_.ProcessName -like '*{proc}*' -or $_.MainWindowTitle -like '*{proc}*') }}; " \
         "$p | ForEach-Object { [void]$_.CloseMainWindow() }; $p.Count"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         timeout=15, creationflags=CREATE_NO_WINDOW).stdout.strip()
    return "Закрываю." if out and out != "0" else f"Не нашёл открытое окно «{arg}»."


MEDIA_BROWSER = "chrome"  # видео и музыка — в Chrome (Firefox блокирует автозапуск со звуком); задаётся из config.json
BROWSER_HINT = re.compile(r"\s*\b(?:в|во|через)?\s*(хроме?|chrome|файрфоксе?|фаерфоксе?|firefox|мозилле?|эдже?|edge)\b\s*", re.I)
BROWSER_EXE = {"chrome": "chrome.exe", "firefox": "firefox.exe", "edge": "msedge.exe"}


def _split_browser(arg: str) -> "tuple[str, str | None]":
    """«расслабляющая музыка в хроме» → («расслабляющая музыка», "chrome")."""
    m = BROWSER_HINT.search(arg)
    if not m:
        return arg.strip(), None
    word = m.group(1).lower()
    name = "chrome" if word.startswith(("хром", "chrome")) else "edge" if word.startswith(("эдж", "edge")) else "firefox"
    return (arg[:m.start()] + " " + arg[m.end():]).strip(), name


def open_url(url: str, browser: "str | None" = None) -> None:
    """Открыть адрес в указанном браузере (путь из реестра App Paths) или в браузере по умолчанию."""
    if browser:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                exe = winreg.QueryValue(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{BROWSER_EXE[browser]}")
                subprocess.Popen([exe, url])
                return
            except OSError:
                continue
    webbrowser.open(url)


def open_site(arg: str) -> str:
    arg, browser = _split_browser(arg)
    key = arg.lower().strip(" .,!?")
    url = SITES.get(key) or (arg if re.match(r"https?://", arg) else "https://" + arg if "." in arg else None)
    if not url:
        return search_google(arg + (f" в {browser}" if browser else ""))
    if not browser and "youtube." in url:  # YouTube — всегда в браузере для видео (Chrome)
        browser = MEDIA_BROWSER
    open_url(url, browser)
    return "Открываю."


def search_google(arg: str) -> str:
    arg, browser = _split_browser(arg)
    open_url("https://www.google.com/search?q=" + urllib.parse.quote(arg), browser)
    return "Ищу."


def search_youtube(arg: str) -> str:
    arg, browser = _split_browser(arg)
    open_url("https://www.youtube.com/results?search_query=" + urllib.parse.quote(arg), browser or MEDIA_BROWSER)
    return "Ищу на YouTube."


DRIVE_NAMES = {"эф": "F", "ф": "F", "це": "C", "си": "C", "с": "C", "е": "E", "и": "E", "дэ": "D", "ди": "D", "д": "D"}
FOLDER_ALIASES = {"проджект": "projects", "проджекты": "projects", "проекты": "projects", "проект": "projects",
                  "игры": "games", "геймс": "games", "стим": "steam"}


def _drive(text: str) -> "str | None":
    m = re.search(r"\b(?:диск|раздел)\w*\s+([a-zа-я]{1,2})\b|^([a-z]):?\\?$|\b([a-z]):\\", text, re.I)
    if not m:
        return None
    letter = next(g for g in m.groups() if g).lower()
    letter = DRIVE_NAMES.get(letter, letter).upper()
    return f"{letter}:\\" if len(letter) == 1 and Path(f"{letter}:\\").exists() else None


def _find_dir(name: str, roots: "list[Path]") -> "Path | None":
    name = FOLDER_ALIASES.get(name, name)
    want = [name, name.translate(TRANSLIT)]
    best, best_score = None, 0.0
    for root in roots:
        try:
            level1 = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(("$", "."))]
        except OSError:
            continue
        candidates = list(level1)
        for d in level1[:60]:
            try:
                candidates += [x for x in d.iterdir() if x.is_dir() and not x.name.startswith(("$", "."))]
            except OSError:
                pass
        for d in candidates:
            n = d.name.lower()
            for w in want:
                score = 1.0 if n == w else 0.9 if n.startswith(w) else difflib.SequenceMatcher(a=w, b=n).ratio()
                score -= 0.02 * (len(d.parts) - len(root.parts))  # ближе к корню — лучше
                if score > best_score:
                    best, best_score = d, score
    return best if best_score >= 0.66 else None


def open_folder(arg: str) -> str:
    """Известные папки, диски («диск F», «F:\\») и любая папка по названию («папку projects на диске F»)."""
    text = arg.lower().strip(" .,!?")
    if re.match(r"^[a-z]:\\", arg, re.I) and Path(arg).exists():
        os.startfile(arg)
        return "Открываю."
    for key, path in FOLDERS.items():
        if key in text:
            os.startfile(path)
            return "Открываю."
    drive = _drive(text)
    name = re.sub(r"\b(?:папк\w*|на|в|во|диск\w*|раздел\w*|открой|покажи|перейди)\b|\b[a-zа-я]{1,2}\b$", " ", text)
    name = re.sub(r"[^\w\s-]", " ", name).strip()
    if drive and not name:
        os.startfile(drive)
        return f"Открываю диск {drive[0]}."
    if name:
        roots = [Path(drive)] if drive else [HOME, *[Path(f"{l}:\\") for l in "CEF" if Path(f"{l}:\\").exists()]]
        found = _find_dir(name, roots)
        if found:
            os.startfile(found)
            return f"Открываю {found.name}."
        return f"Не нашёл папку «{name}»."
    return "Не понял, какую папку открыть."


def youtube_play(arg: str) -> str:
    """Сразу запускает первое видео по запросу (а не просто страницу поиска)."""
    import requests
    arg, browser = _split_browser(arg)
    browser = browser or MEDIA_BROWSER
    try:
        html = requests.get("https://www.youtube.com/results", params={"search_query": arg}, timeout=8,
                            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ru"}).text
        vid = re.search(r'"videoId":"([\w-]{11})"', html)
    except Exception:  # noqa: BLE001
        vid = None
    if not vid:
        return search_youtube(f"{arg} в {browser}")
    open_url(f"https://www.youtube.com/watch?v={vid.group(1)}", browser)
    return "Включаю."


SELF_WINDOW = re.compile(r"(?:сво[иеё]\w*\s+)?(?:лог\w*|окно джарвиса|джарвис\w*|эдит\w*|консол\w*|себя)$", re.I)
SELF_TITLE = "джарвис"  # заголовок окна с журналом; задаётся из jarvis.py по имени помощника
TERMINALS = ("cmd", "powershell", "pwsh", "windowsterminal", "conhost", "wt", "bash", "wsl")


def _windows() -> "list[tuple[int, str, str]]":
    """Видимые окна верхнего уровня: (hwnd, заголовок, имя процесса без .exe)."""
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # 4 = GW_OWNER: пропустить диалоги
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        title = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, title, n + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        name = ""
        if h:
            buf, size = ctypes.create_unicode_buffer(260), wintypes.DWORD(260)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = Path(buf.value).stem.lower()
            kernel32.CloseHandle(h)
        found.append((hwnd, title.value, name))
        return True

    user32.EnumWindows(cb, 0)
    return found


def _find_window(arg: str) -> "int | None":
    """Окно по программе («хром», «slack») или заголовку; «свои логи / окно джарвиса» — окно самого Джарвиса."""
    text = arg.lower().strip(" .,!?")
    wins = _windows()
    if SELF_WINDOW.search(text):
        return next((h for h, t, _ in wins if t.strip().lower().startswith(SELF_TITLE)), None)
    hit = find_app(text)
    words = (hit[0] if hit else text).split()
    # имя процесса: «google chrome» → chrome, «microsoft edge» → edge, «telegram desktop» → telegram
    proc = words[-1] if words[0] in ("google", "microsoft") and len(words) > 1 else words[0]
    by_proc = [h for h, _, p in wins if proc in p]
    if by_proc:
        return by_proc[0]
    return next((h for h, t, _ in wins if text in t.lower()), None)


def _show(hwnd: int, cmd: int) -> None:
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, cmd)
    if cmd != 6:  # 6 = свернуть; иначе — вывести на передний план
        user32.keybd_event(0x12, 0, 0, 0)  # Alt: иначе Windows не даёт фоновому процессу забрать фокус
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(0x12, 0, 2, 0)


def focus_app(arg: str) -> str:
    """Выводит окно программы на передний план и разворачивает; если окна нет (свёрнута в трей) — запускает."""
    import time
    hwnd = _find_window(arg)
    if hwnd:
        _show(hwnd, 3)
        time.sleep(0.3)
        if ctypes.windll.user32.GetForegroundWindow() != hwnd:  # Windows не дала забрать фокус — ещё раз
            _show(hwnd, 9)
            time.sleep(0.3)
        if ctypes.windll.user32.GetForegroundWindow() != hwnd:
            return "Не получилось переключиться на это окно."
        return "Готово."
    return open_app(arg)


def minimize_app(arg: str) -> str:
    """Свернуть окно программы, своё окно («свои логи») или всё сразу («всё» → рабочий стол)."""
    if re.search(r"^(?:вс[её]|все окна|окна)$", arg.lower().strip(" .,!?")):
        user32 = ctypes.windll.user32
        user32.keybd_event(0x5B, 0, 0, 0)
        _key(0x44)  # Win+D
        user32.keybd_event(0x5B, 0, 2, 0)
        return ""
    hwnd = _find_window(arg)
    if not hwnd:
        return f"Не нашёл окно «{arg}»."
    _show(hwnd, 6)
    return ""


def _type_unicode(text: str) -> None:
    """Набрать текст «как с клавиатуры» (SendInput KEYEVENTF_UNICODE): кириллица без раскладки, и приложения вроде
    Slack сами ставят курсор в поле сообщения, когда начинаешь печатать (вставка Ctrl+V туда не доходила)."""
    import time
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    for ch in text:
        data = ch.encode("utf-16-le")  # эмодзи и редкие символы — две UTF-16 «половинки»
        for code in (int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)):
            down = INPUT(type=1, ki=KEYBDINPUT(0, code, 0x0004, 0, 0))          # KEYEVENTF_UNICODE
            up = INPUT(type=1, ki=KEYBDINPUT(0, code, 0x0004 | 0x0002, 0, 0))   # + KEYUP
            ctypes.windll.user32.SendInput(2, (INPUT * 2)(down, up), ctypes.sizeof(INPUT))
        time.sleep(0.01)


def type_text(arg: str) -> str:
    """Напечатать текст в окно на переднем плане. Не в терминалы."""
    import time
    if not arg.strip():
        return "Не понял, что напечатать."
    time.sleep(0.4)  # дать окну, показанному предыдущим действием, стать активным
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    proc = next((p for h, _, p in _windows() if h == hwnd), "")
    if any(t == proc for t in TERMINALS):
        return "Не печатаю в командную строку — это небезопасно."
    _type_unicode(arg)
    return ""


def _paste(text: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $args[0]", text],
                   timeout=10, creationflags=CREATE_NO_WINDOW)
    user32 = ctypes.windll.user32
    user32.keybd_event(0x11, 0, 0, 0)
    _key(0x56)  # Ctrl+V
    user32.keybd_event(0x11, 0, 2, 0)


def _chat_go(app: str, arg: str) -> str:
    """Перейти в чат через быстрый поиск Ctrl+K (так умеют Slack и приложение Claude) — как это делает человек."""
    import time
    target = re.sub(r"^(?:в\s+)?(?:чат|канал|переписк\w*|личк\w*|диалог|разговор)\s+(?:с\s+|про\s+)?", "",
                    arg.strip(" .,!?"), flags=re.I)
    if not target:
        return "Не понял, какой чат открыть."
    hwnd = _find_window(app)
    if not hwnd:
        open_app(app)
        time.sleep(4)
        hwnd = _find_window(app)
        if not hwnd:
            return f"Не нашёл окно {app}."
    _show(hwnd, 9)
    time.sleep(0.5)
    user32 = ctypes.windll.user32
    user32.keybd_event(0x11, 0, 0, 0)
    _key(0x4B)  # Ctrl+K — быстрый переход
    user32.keybd_event(0x11, 0, 2, 0)
    time.sleep(0.6)
    _paste(target)
    time.sleep(1.2)  # дать приложению найти совпадения
    _key(0x0D)
    return ""


def slack_go(arg: str) -> str:
    return _chat_go("slack", arg)


def claude_go(arg: str) -> str:
    return _chat_go("claude", arg)


# ---------------------------------------------------------------- клики
CLICKS_PS = Path(__file__).with_name("clickables.ps1")
# Кнопки, нажимать которые — только после «да»
RISKY_CLICK = re.compile(r"удал|delete|remove|купить|оплат|\bpay\b|buy|заказ|order|отправ|send|выйти|выход|log ?out|"
                         r"sign out|uninstall|сброс|reset|стереть|очистить|подтвердить|confirm|перевести|transfer", re.I)


def foreground() -> "tuple[int, str]":
    """Окно на переднем плане и имя его процесса."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return hwnd, next((p for h, _, p in _windows() if h == hwnd), "")


def clickables(hwnd: int) -> "list[dict]":
    """Кнопки, ссылки, вкладки и поля окна — с названиями и экранными координатами (Windows UI Automation)."""
    out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(CLICKS_PS),
                          "-Hwnd", str(hwnd)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=25, creationflags=CREATE_NO_WINDOW).stdout
    items = []
    for line in out.splitlines():
        parts = line.split("|", 5)
        if len(parts) == 6 and all(p.lstrip("-").isdigit() for p in parts[1:5]):
            x, y, w, h = map(int, parts[1:5])
            items.append({"type": parts[0], "x": x, "y": y, "w": w, "h": h, "name": parts[5]})
    return items


def click_xy(arg: str) -> str:
    """Левый клик в экранных координатах «x,y»."""
    import time
    x, y = (int(v) for v in arg.split(","))
    user32 = ctypes.windll.user32
    user32.SetCursorPos(x, y)
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
    return ""


def press_enter(_: str = "") -> str:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    proc = next((p for h, _, p in _windows() if h == hwnd), "")
    if any(t == proc for t in TERMINALS):
        return "Не нажимаю Enter в командной строке — это небезопасно."
    _key(0x0D)
    return ""


def _endpoint():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


def set_volume(arg: str) -> str:
    m = re.search(r"\d+", arg)
    if not m:
        return "Скажите громкость в процентах."
    level = max(0, min(100, int(m.group())))
    ep = _endpoint()
    ep.SetMute(0, None)
    ep.SetMasterVolumeLevelScalar(level / 100, None)
    return f"Громкость {level}."


def change_volume(arg: str) -> str:
    ep = _endpoint()
    step = 0.1 if arg == "up" else -0.1
    level = max(0.0, min(1.0, ep.GetMasterVolumeLevelScalar() + step))
    ep.SetMute(0, None)
    ep.SetMasterVolumeLevelScalar(level, None)
    return f"Громкость {round(level * 100)}."


def mute(arg: str) -> str:
    _endpoint().SetMute(0 if arg == "off" else 1, None)
    return "Звук включён." if arg == "off" else "Звук выключен."


def _key(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


def media(arg: str) -> str:
    vk = {"pause": 0xB3, "play": 0xB3, "next": 0xB0, "prev": 0xB1}.get(arg, 0xB3)
    _key(vk)
    return ""


BROWSER_KEYS = {  # только безопасные сочетания для вкладок и страниц
    "new_tab": [0x11, 0x54], "close_tab": [0x11, 0x57], "reopen_tab": [0x11, 0x10, 0x54],
    "next_tab": [0x11, 0x09], "prev_tab": [0x11, 0x10, 0x09], "back": [0x12, 0x25], "forward": [0x12, 0x27],
    "refresh": [0x74], "fullscreen": [0x7A],
}
BROWSERS = ("chrome", "firefox", "msedge", "browser", "opera")


def browser(arg: str) -> str:
    """«new_tab» или «new_tab chrome»: сначала (при необходимости) показать браузер, потом нажать сочетание клавиш."""
    parts = arg.lower().split()
    key = parts[0] if parts else ""
    if key not in BROWSER_KEYS:
        return "Не знаю такой команды для браузера."
    if len(parts) > 1:
        focus_app(" ".join(parts[1:]))
    else:
        # без указания браузера — работаем, только если на переднем плане уже браузер
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {pid.value}).ProcessName"],
                             capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW).stdout.strip().lower()
        if not any(b in out for b in BROWSERS):
            return "Не вижу браузер на экране — скажите, в каком: в хроме или в файрфоксе."
    import time
    time.sleep(0.4)  # дать окну стать активным
    combo = BROWSER_KEYS[key]
    for vk in combo:
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(combo):
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
    return ""


TAB_WORDS = {"ютуб": "youtube", "ютуба": "youtube", "ютубом": "youtube", "гитлаб": "gitlab", "гитхаб": "github",
             "дженкинс": "jenkins", "тимсити": "teamcity", "таблиц": "таблицы", "таблицы": "таблицы", "вк": "vk",
             "почта": "mail", "почту": "mail", "твич": "twitch", "дискорд": "discord", "телеграм": "telegram"}
_config = Path(__file__).with_name("config.json")
if _config.exists():  # свои сокращения вкладок («почта» → gmail) — в config.json, ключ tab_words
    TAB_WORDS.update({k.lower(): v.lower() for k, v in
                      json.loads(_config.read_text(encoding="utf-8")).get("tab_words", {}).items()})
TABS_PS = Path(__file__).with_name("tabs.ps1")


def _tabs(*args: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(TABS_PS), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                          creationflags=CREATE_NO_WINDOW).stdout


def switch_tab(arg: str) -> str:
    """Переключиться на вкладку браузера по названию («почта» → Gmail, «ютуб» → YouTube) через UI Automation."""
    text = arg.lower().strip(" .,!?")
    want_browser = "chrome" if re.search(r"хром|chrome", text) else "firefox" if re.search(r"файрфокс|фаерфокс|firefox|мозилл", text) else None
    words = [w for w in re.findall(r"[\w-]+", text) if w not in ("в", "во", "на", "вкладку", "вкладка", "хроме", "хром",
                                                                  "chrome", "файрфоксе", "фаерфоксе", "firefox", "браузере")]
    if not words:
        return "Не понял, какую вкладку открыть."
    variants = {" ".join(words)} | {TAB_WORDS.get(w, w) for w in words} | {w.translate(TRANSLIT) for w in words}
    tabs = []
    for line in _tabs("-Mode", "list").splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            title = re.sub(r'^Вкладка "(.*)" закреплена$', r"\1", parts[3]).strip()
            tabs.append((parts[0], parts[1], int(parts[2]), title))
    if not tabs:
        return "Не вижу открытых вкладок."
    best, best_score = None, 0.0
    for tab in tabs:
        title = tab[3].lower()
        tokens = re.findall(r"\w+", title)
        for v in variants:
            if title == v or title.startswith(v + " ") or title == f"({v})":
                score = 1.3                    # название вкладки и есть то, что сказали («Gmail»)
            elif v in tokens:
                score = 1.1 - 0.005 * len(tokens)  # целое слово; короче заголовок — точнее попадание
            elif v in title:
                score = 1.0 - 0.005 * len(tokens)
            else:
                score = max([difflib.SequenceMatcher(a=v, b=t).ratio() for t in tokens] or [0])
            if want_browser and tab[0] == want_browser:
                score += 0.05
            if score > best_score:
                best, best_score = tab, score
    if not best or best_score < 0.7:
        return f"Не нашёл вкладку «{' '.join(words)}»."
    out = _tabs("-Mode", "select", "-Hwnd", best[1], "-Index", str(best[2])).strip()
    if out != "ok":
        return "Не получилось переключить вкладку."
    return f"Переключил на {best[3][:40]}."


def lock(_: str = "") -> str:
    ctypes.windll.user32.LockWorkStation()
    return ""


def screenshot(_: str = "") -> str:
    # Win+PrintScreen — Windows сам сохранит в «Изображения\Снимки экрана»
    user32 = ctypes.windll.user32
    user32.keybd_event(0x5B, 0, 0, 0)
    _key(0x2C)
    user32.keybd_event(0x5B, 0, 2, 0)
    return "Снимок сохранён в картинки."


def sleep_pc(_: str = "") -> str:
    # настоящий сон (не гибернация): SetSuspendState(hibernate=False, force=True, disableWakeEvents=False)
    threading.Timer(2.5, lambda: ctypes.windll.powrprof.SetSuspendState(False, True, False)).start()
    return "Усыпляю компьютер."


def shutdown_pc(_: str = "") -> str:
    subprocess.Popen(["shutdown", "/s", "/t", "10"], creationflags=CREATE_NO_WINDOW)
    return "Выключаю компьютер через десять секунд."


def restart_pc(_: str = "") -> str:
    subprocess.Popen(["shutdown", "/r", "/t", "10"], creationflags=CREATE_NO_WINDOW)
    return "Перезагружаю через десять секунд."


TIMER_CALLBACK = None  # задаётся из jarvis.py: функция, которая скажет «время вышло»


def timer(arg: str) -> str:
    m = re.search(r"(\d+)\s*(сек|мин|час)?", arg)
    if not m:
        return "На сколько поставить таймер?"
    n, unit = int(m.group(1)), (m.group(2) or "мин")
    seconds = n * {"сек": 1, "мин": 60, "час": 3600}[unit]
    if TIMER_CALLBACK:
        threading.Timer(seconds, TIMER_CALLBACK, args=[f"Время вышло: {n} {unit}."]).start()
    return f"Таймер на {n} {unit}."


ACTIONS = {  # имя → (функция, нужно ли подтверждение)
    "open_app": (open_app, False), "close_app": (close_app, False), "open_site": (open_site, False),
    "search_google": (search_google, False), "search_youtube": (search_youtube, False),
    "open_folder": (open_folder, False), "youtube_play": (youtube_play, False), "focus_app": (focus_app, False),
    "minimize_app": (minimize_app, False), "type_text": (type_text, False), "press_enter": (press_enter, False),
    "slack_go": (slack_go, False), "claude_go": (claude_go, False), "click_xy": (click_xy, False),
    "click": (lambda _="": "", False),  # обрабатывается в jarvis.py (нужен выбор элемента Claude), здесь — пустышка
    "set_volume": (set_volume, False), "volume": (change_volume, False),
    "mute": (mute, False), "media": (media, False), "browser": (browser, False), "switch_tab": (switch_tab, False),
    "lock": (lock, False), "screenshot": (screenshot, False),
    "sleep_pc": (sleep_pc, False), "timer": (timer, False),
    "shutdown_pc": (shutdown_pc, True), "restart_pc": (restart_pc, True),
    "look_screen": (lambda _="": "", False),  # обрабатывается в jarvis.py (зрение), здесь — пустышка
}

# Описание для Claude — коротко, чтобы не тратить токены
FOR_CLAUDE = (
    "Ты можешь выполнять действия на ПК: добавь в конце ответа отдельную строку «ACTION: имя | аргумент». "
    "Действия: open_app|программа; close_app|программа; focus_app|программа (показать/развернуть окно, "
    "в т.ч. свёрнутое в трей); open_site|сайт; youtube_play|запрос (сразу включает первое видео — для "
    "«включи музыку/песню/видео»); search_youtube|запрос (только список результатов); search_google|запрос; "
    "open_folder|папка или путь (загрузки, документы, «диск F», «F:\\», «projects на диске F»); "
    "set_volume|0-100; volume|up/down; mute|on/off; media|pause/next/prev (пауза/продолжить/трек); "
    "browser|new_tab/close_tab/reopen_tab/next_tab/prev_tab/back/forward/refresh/fullscreen [браузер] "
    "(например «browser | new_tab chrome»); switch_tab|название вкладки [в хроме/в файрфоксе] (переключает "
    "на открытую вкладку по названию сайта или заголовку, ищет во всех браузерах); "
    "lock; screenshot; sleep_pc; timer|5 мин; shutdown_pc; restart_pc. "
    "minimize_app|программа (свернуть окно; «свои логи»/«себя» — твоё собственное окно с журналом; «всё» — свернуть все окна); "
    "type_text|текст (напечатать текст в окно на переднем плане — перед этим focus_app нужной программы); "
    "press_enter (отправить напечатанное — только если пользователь сказал «отправь»); "
    "slack_go|чат, канал или имя человека (перейти в Slack через быстрый поиск; после него можно type_text); "
    "чтобы напечатать в программе: сначала focus_app|программа, потом type_text; "
    "claude_go|название или тема чата (перейти в чат приложения Claude через поиск). "
    "«Нажми play/плей/паузу», «продолжи видео» — это media|pause (одна клавиша играть/пауза). "
    "look_screen — посмотреть на экран пользователя (два монитора): используй, если для ответа нужно видеть, "
    "что сейчас открыто или написано на экране; тогда больше ничего не пиши. "
    "До трёх действий подряд, каждое отдельной строкой ACTION, только по прямой просьбе. "
    "Браузер: добавь к аргументу open_site/search_google/search_youtube/youtube_play «в хроме» или «в файрфоксе», "
    "если пользователь назвал браузер; видео и музыка по умолчанию открываются в Chrome, сайты — в браузере по умолчанию. "
    "click|что нажать — клик мышью в окне на переднем плане по описанию: «первое видео», «второе видео в "
    "рекомендациях», «кнопку Принять», «поиск» (если нужно другое окно — сначала focus_app). "
    "Других действий НЕТ. "
    "Если часть просьбы невыполнима — выполни остальное и прямо скажи, какой шаг не умеешь. "
    "Описывай ровно то, что делает выбранное действие, не обещай большего; если такого действия нет — "
    "скажи, что пока не умеешь. Если фраза похожа на обрывок и непонятна — коротко переспроси."
)


def run(name: str, arg: str = "") -> "tuple[str, bool]":
    """Выполнить действие. Возвращает (фраза, нужно_подтверждение). Если нужно подтверждение — ничего не делает."""
    name = name.strip().lower()
    if name not in ACTIONS:
        return "Такого я пока не умею.", False
    func, confirm = ACTIONS[name]
    if confirm:
        return "", True
    return func(arg.strip()), False


def quick(text: str) -> "tuple[str, str] | None":
    """Частые команды без нейросети. Возвращает (действие, аргумент) или None."""
    t = text.lower().strip(" .,!?")
    t = re.sub(r"\b(?:мне|пожалуйста|плиз|быстро|давай|ка)\b\s*", "", t).strip(" ,")  # «покажи мне Slack» → «покажи slack»
    rules = [
        (r"^(?:сверни|спрячь|убери)\s+(.+)$", "minimize_app"),
        (r"^(?:открой|запусти|включи)\s+(?:мне\s+)?(?:сайт\s+)?(ютуб|youtube|гугл|почту|гитхаб|твич|кинопоиск|вк|вконтакте|чатгпт|википедию)$", "open_site"),
        (r"^(?:найди|поищи|включи)\s+(?:на|в)\s+ютубе?\s+(.+)$", "search_youtube"),
        (r"^(?:загугли|погугли|найди в гугле|найди в интернете)\s+(.+)$", "search_google"),
        (r"^(?:включи|поставь|запусти)\s+(.+?)\s+(?:на|в)\s+ютубе?$", "youtube_play"),
        (r"^(?:открой|покажи)\s+(?:папку\s+)?(загрузки|документы|рабочий стол|картинки|изображения|музыку|видео)$", "open_folder"),
        (r"^(?:открой|покажи|перейди\s+(?:в|на))\s+((?:папк\w*|диск\w*|раздел\w*)\b(?:\s+[\w:\\-]+){0,4})$", "open_folder"),
        (r"^(?:перейди|переключись|переключи|вернись)\s+(?:во?|на)\s+вкладку\s+(.+)$", "switch_tab"),
        (r"^(?:разверни|покажи|переключись на|переключи на)\s+(?!.*\b(?:папк|диск|раздел|вкладк)\w*)(\S+(?:\s+\S+)?)$", "focus_app"),
        (r"^(?:открой|запусти|включи)\s+(?!.*\b(?:музыку|звук|громкость|папк\w*|диск\w*|вкладк\w*|видео\w*|ролик\w*|"
         r"трек\w*|песн\w*|клип\w*|серию|фильм\w*|сериал\w*)\b)(\S+(?:\s+\S+){0,2})$", "open_app"),
        (r"^(?:закрой|выключи|заверши)\s+(?!.*\b(?:компьютер|комп|пк|звук|вкладк\w*)\b)(\S+(?:\s+\S+){0,2})$", "close_app"),
        (r"(?:громкость|звук)\s+(?:на\s+)?(\d+)", "set_volume"),
        (r"^(?:выключи звук|без звука|замьють?|мьют)$", "mute:on"),
        (r"^(?:включи звук|верни звук)$", "mute:off"),
        (r"^(?:сделай\s+)?(?:погромче|прибавь(?: громкость| звук)?)$", "volume:up"),
        (r"^(?:сделай\s+)?(?:потише|убавь(?: громкость| звук)?)$", "volume:down"),
        (r"^(?:пауза|поставь на паузу|стоп музыка|продолжи(?: видео| воспроизведение)?|играй|"
         r"(?:нажми\s+)?(?:плей|плэй|play|паузу|воспроизведение))$", "media:pause"),
        (r"^(?:следующ(?:ий|ая|ую)(?: трек| песн[яю])?|дальше|переключи трек)$", "media:next"),
        (r"^(?:предыдущ(?:ий|ая|ую)(?: трек| песн[яю])?|назад)$", "media:prev"),
        (r"^(?:открой\s+|сделай\s+)?нов(?:ую|ая)\s+вкладк[уа](?:\s+(?:в|на)\s+(\S+))?$", "browser_new_tab"),
        (r"^закрой\s+вкладку(?:\s+(?:в|на)\s+(\S+))?$", "browser_close_tab"),
        (r"^(?:верни|восстанови|открой обратно)\s+(?:закрытую\s+)?вкладку$", "browser:reopen_tab"),
        (r"^(?:перейди|переключись|переключи|вернись)\s+(?:во?|на)\s+вкладку\s+(.+)$", "switch_tab"),
        (r"^(?:открой|покажи)\s+вкладку\s+(?!.*\bнов)(.+)$", "switch_tab"),
        (r"^следующ(?:ая|ую)\s+вкладк[уа]$", "browser:next_tab"),
        (r"^предыдущ(?:ая|ую)\s+вкладк[уа]$", "browser:prev_tab"),
        (r"^(?:обнови|перезагрузи)\s+(?:страницу|вкладку)$", "browser:refresh"),
        (r"^(?:назад|вернись)\s+на\s+(?:страницу|прошлую страницу)|^страница назад$", "browser:back"),
        (r"^(?:на весь экран|полный экран|разверни видео)$", "browser:fullscreen"),
        (r"^(?:включи|запусти|открой|поставь)\s+((?:перв|втор|трет|четв|пят|последн)\w*\s+(?:видео|ролик\w*|клип\w*|"
         r"трек\w*|серию|результат\w*)(?:\s+.*)?)$", "click"),
        (r"^(?:нажми|кликни|щ[её]лкни|ткни)\s+(?:на\s+)?(?!(?:плей|плэй|play|паузу|воспроизведение|enter|энтер)$)(.+)$", "click"),
        (r"(?:заблокируй|блокируй)\s+(?:компьютер|комп|пк|экран)", "lock"),
        (r"(?:сделай\s+)?(?:скриншот|снимок экрана)", "screenshot"),
        (r"(?:таймер|засеки|напомни через)\s+(?:на\s+)?(\d+\s*\w*)", "timer"),
        (r"(?:выключи|отключи)\s+(?:компьютер|комп|пк)", "shutdown_pc"),
        (r"(?:перезагрузи|перезапусти)\s+(?:компьютер|комп|пк)", "restart_pc"),
        (r"(?:усыпи|в сон|спящий режим).*(?:компьютер|комп|пк)|(?:компьютер|комп|пк).*(?:усыпи|в сон|спать)", "sleep_pc"),
    ]
    for pattern, action in rules:
        m = re.search(pattern, t)
        if m:
            if action.startswith("browser_"):  # «новую вкладку в хроме» → browser | new_tab хроме
                where = m.group(1) if m.groups() and m.group(1) else ""
                return "browser", (action[len("browser_"):] + " " + where).strip()
            name, _, fixed_arg = action.partition(":")
            return name, fixed_arg or (m.group(1) if m.groups() else "")
    return None
