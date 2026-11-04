"""Save a personal HTTPS proxy URL without putting credentials in argv or TOML."""
from getpass import getpass
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit


def main() -> None:
    url = getpass("Полный HTTPS URL прокси (ввод скрыт): ").strip()
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname and parsed.port != 0
                 and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
                 and not any(c.isspace() for c in url))
    except ValueError:
        valid = False
    if not valid:
        raise SystemExit("Нужен URL вида https://user:password@host:port")
    dest = Path.home() / ".config/antigravity-mcp/proxy-url"
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", dir=dest.parent, delete=False) as tmp:
        tmp.write(url + "\n")
    os.replace(tmp.name, dest)
    print(f"Адрес сохранён: {dest} (режим 0600).")


if __name__ == "__main__":
    main()
