import argparse
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
import tkinter as tk
from tkinter import filedialog

# Регулярка для формата combined nginx
COMBINED_PATTERN = re.compile(
    r'^(?P<ip>\S+)\s+'                 # IP
    r'\S+\s+'                          # remote_user
    r'\S+\s+'                          # http_auth_password
    r'\[(?P<time_str>.*?)\]\s+'        # [$time_local]
    r'"(?P<method>\S+)\s+(?P<url>\S+)\s+[^"]*"\s+'  # "request"
    r'(?P<status>\d{3})\s+'            # status
    r'\S+\s+'                          # body_bytes_sent
    r'"[^"]*"\s+"[^"]*"'               # referer, user_agent
)

def parse_time_with_tz(time_str: str) -> datetime:
    """
    Парсит время из формата nginx [$time_local]:
    01/Oct/2025:12:34:56 +0300
    """
    time_part, offset_str = time_str.rsplit(' ', 1)
    dt_naive = datetime.strptime(time_part, '%d/%b/%Y:%H:%M:%S')

    sign = 1 if offset_str[0] == '+' else -1
    hours = int(offset_str[1:3])
    minutes = int(offset_str[3:5])
    total_minutes = sign * (hours * 60 + minutes)

    tz_offset = timedelta(minutes=total_minutes)
    return dt_naive.replace(tzinfo=timezone(tz_offset))

def ask_file_path() -> str | None:
    root = tk.Tk()
    root.withdraw()  # скрыть главное окно
    path = filedialog.askopenfilename(
        title="Выберите файл лога",
        filetypes=[("Log files", "*.log"), ("All files", "*.*")]
    )
    root.destroy()
    return path

def main():
    parser = argparse.ArgumentParser(description="Анализ access.log")
    parser.add_argument("log_path", nargs="?", help="Путь к access.log (откроется окно выбора)")

    args = parser.parse_args()

    log_path = args.log_path
    if not log_path:
        log_path = ask_file_path()
        if not log_path:
            print("Файл не выбран. Отмена.")
            return

    from_dt = None
    to_dt = None

    ip_counter = Counter()
    status_4xx = 0
    status_5xx = 0
    url_5xx_counter = Counter()

    total_lines = 0          # все строки в файле
    parsed_lines = 0         # валидные строки
    skipped_lines = 0        # невалидные строки (битые)

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            total_lines += 1
            line = line.strip()
            if not line:
                continue

            match = COMBINED_PATTERN.match(line)
            if not match:
                skipped_lines += 1
                continue
            parsed_lines += 1

            ip = match.group("ip")
            time_str = match.group("time_str")
            url = match.group("url")
            status = int(match.group("status"))

            try:
                req_time = parse_time_with_tz(time_str)
            except Exception:
                # Если время не распарсилось, считаем строку битой
                skipped_lines += 1
                continue

            ip_counter[ip] += 1

            if 400 <= status < 500:
                status_4xx += 1
            elif 500 <= status < 600:
                status_5xx += 1
                url_5xx_counter[url] += 1

    print("=== Статистика по строкам ===")
    print(f"Всего строк в файле: {total_lines}")
    print(f"Успешно распаршено: {parsed_lines}")
    print(f"Пропущено (неверный формат/ошибка времени): {skipped_lines}")

    print("\n=== Топ-10 IP по числу запросов ===")
    for ip, count in ip_counter.most_common(10):
        print(f"{ip}: {count}")

    print("\n=== Количество ошибок ===")
    print(f"4xx: {status_4xx}")
    print(f"5xx: {status_5xx}")

    print("\n=== Топ URL с 5xx ===")
    for url, count in url_5xx_counter.most_common(10):
        print(f"{url}: {count}")

if __name__ == "__main__":
    main()


print("\n=== Готово. Нажмите Enter, чтобы закрыть окно ===")
input()
