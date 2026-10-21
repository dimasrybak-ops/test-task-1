import json
import os
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Optional

from fabric import Connection, Config

# Путь к логу nginx на удалённых серверах
REMOTE_LOG_PATH = "/var/log/nginx/access.log"
# Регулярное выражение для извлечения IP-адреса из начала строки лога
IP_PATTERN = r'^(\d{1,3}(?:\.\d{1,3}){3})'


def get_script_dir() -> Path:
    """
    Берем путь к директории, откуда запускается скрипт
    """
    return Path(os.path.dirname(os.path.abspath(__file__)))


def load_config(config_filename: str = "Nginx_srv.cfg") -> dict:
    """
    Загружает конфигурацию из JSON-файла. В конфиге обязательно должно быть поле 'user'.
    """
    script_dir = get_script_dir()
    config_path = script_dir / config_filename

    if not config_path.exists():
        raise FileNotFoundError(f"Не найден файл конфигурации: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "user" not in cfg:
        raise ValueError("В конфиге должно быть поле 'user'.")

    # Значения по умолчанию, если полей нет в конфиге
    cfg.setdefault("hours", 48)
    cfg.setdefault("max_workers", 20)
    cfg.setdefault("key_path", "~/.ssh/id_rsa")

    return cfg


def read_hosts(hosts_filename: str = "hosts.txt") -> List[str]:
    """
    Файл hosts.txt должен лежать в папке со скриптом
    """
    script_dir = get_script_dir()
    hosts_path = script_dir / hosts_filename

    if not hosts_path.exists():
        raise FileNotFoundError(f"Не найден файл hosts: {hosts_path}")

    with open(hosts_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def get_awk_filter_command(hours: int) -> str:
    """
    Формируем awk-команду для фильтрации строк лога за последние N часов.
    Дата в логе имеет формат: [DD/Mon/YYYY:HH:MM:SS +ZZZZ].
    Awk передает текст скрипта на сервер и уже там выполняется парсинг дат.
    """
    import datetime
    from datetime import timezone

    now_ts = int(datetime.datetime.now(timezone.utc).timestamp())
    cutoff_ts = now_ts - (hours * 3600)

    awk_script = f"""
    BEGIN {{
        cutoff = {cutoff_ts}
        split("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec", month_map, " ")
        for (i=1; i<=12; i++) {{
            month_num[month_map[i]] = i
        }}
    }}
    {{
        # Ищем шаблон даты в квадратных скобках
        if (match($0, /\\[([0-9]{{2}})/([A-Za-z]{{3}})/([0-9]{{4}}):([0-9]{{2}}):([0-9]{{2}}):([0-9]{{2}}) [+-][0-9]{{4}}\\]/)) {{
            dd = substr($0, RSTART+1, 2)
            mon_name = substr($0, RSTART+4, 3)
            yyyy = substr($0, RSTART+8, 4)
            hh = substr($0, RSTART+13, 2)
            mm = substr($0, RSTART+16, 2)
            ss = substr($0, RSTART+19, 2)

            mon = month_num[mon_name]
            date_str = yyyy " " mon " " dd " " hh " " mm " " ss
            # mktime использует локальное время сервера, чтобы не было проблем с серверами в других часовых поясах
            ts = mktime(date_str)

            if (ts >= cutoff) {{
                print $0
            }}
        }}
    }}
    """
    # Возвращаем однострочную версию для передачи в SSH-команду
    return awk_script.replace("\n", " ")


def process_host(host: str, user: str, key_path: Optional[str], hours: int) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Обрабатывает один хост: подключается по SSH, забирает лог за последние N часов.
    Возвращает кортеж: (host, log_content_or_None, error_message_or_None).
    """
    config = Config(overrides={"user": user})
    connect_kwargs = {}
    if key_path:
        connect_kwargs["key_filename"] = [key_path]
    config.connect_kwargs = connect_kwargs

    try:
        with Connection(host, config=config, connect_timeout=15) as conn:
            # Простая проверка соединения
            conn.run("true", hide=True, warn=True)

            awk_cmd = get_awk_filter_command(hours)
            remote_cmd = (
                f"test -f {REMOTE_LOG_PATH} && awk '{awk_cmd}' {REMOTE_LOG_PATH} "
                f"|| echo '__FILE_NOT_FOUND__'"
            )

            result = conn.run(remote_cmd, hide=True, warn=True, pty=False)
            if result.failed:
                return host, None, "Ошибка выполнения команды на сервере"

            out = result.stdout.strip()
            if out == "__FILE_NOT_FOUND__":
                return host, None, "Файл лога не найден"
            return host, out, None
    except Exception as e:
        # В ошибке будет еще код от SSH/fabric
        return host, None, str(e)


def extract_ips(log_content: str) -> List[str]:
    """Извлекает IP-адреса из переданного фрагмента лога."""
    import re
    ips = []
    for line in log_content.splitlines():
        m = re.match(IP_PATTERN, line)
        if m:
            ips.append(m.group(1))
    return ips


def main():
    # Файлы Nginx_srv.cfg и hosts.txt ищем в папке со скриптом
    cfg = load_config("Nginx_srv.cfg")
    user = cfg["user"]
    key_path = cfg.get("key_path")
    hours = cfg["hours"]
    max_workers = cfg["max_workers"]

    hosts = read_hosts("hosts.txt")
    successful_logs: List[str] = []
    failed_report: List[Tuple[str, str]] = []

    print(f"=== Сбор логов за последние {hours} часов ({max_workers} потоков) ===")

    # Параллельный сбор: количество потоков берётся из конфига
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_host, h, user, key_path, hours): h for h in hosts}
        for future in as_completed(futures):
            host, log, error = future.result()
            if error:
                failed_report.append((host, error))
            elif log:
                successful_logs.append(log)

    # Подсчёт топ‑10 IP по всем собранным логам
    counter = Counter()
    for content in successful_logs:
        counter.update(extract_ips(content))

    print("\n=== Top 10 IP (за последние {} часов) ===".format(hours))
    top_ips = counter.most_common(10)
    if top_ips:
        for i, (ip, count) in enumerate(top_ips, 1):
            print(f"{i}. {ip} — {count}")
    else:
        print("IP не найдены")

    if failed_report:
        print("\n=== Недоступные серверы ===")
        for host, err in failed_report:
            print(f"- {host}: {err}")


if __name__ == "__main__":
    main()

print("\n=== Нажмите Enter, чтобы закрыть окно ===")
input()