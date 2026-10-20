import csv
import time
import random
import os
from typing import List, Dict, Any
import requests

BASE_URL = "https://jsonplaceholder.typicode.com"

def fetch_with_retry(url: str, max_retries: int = 5, backoff_factor: float = 1.0) -> List[Dict[str, Any]]:
    """
    Получаем данные с повторными попытками при ошибках сети или недоступности API. Используем увеличивающуюся задержку между попытками.
    """
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"Не удалось получить данные после {max_retries} попыток: {e}")
            wait_time = backoff_factor * (2 ** attempt) + random.uniform(0, 0.5)
            time.sleep(wait_time)
    return []

def main():
    # Получаем абсолютный путь к директории, где находится скрипт
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(script_dir, "users_posts_stats.csv")

    # Получаем пользователей и посты с обработкой ошибок
    users = fetch_with_retry(f"{BASE_URL}/users")
    posts = fetch_with_retry(f"{BASE_URL}/posts")

    # Создаём словарь: ид пользователя-список постов
    user_posts_texts: Dict[int, List[str]] = {}
    for post in posts:
        user_id = post.get("userId")
        body = post.get("body", "")
        if user_id is not None:
            user_posts_texts.setdefault(user_id, []).append(body)

    # Формируем итоговые данные
    results = []
    for user in users:
        user_id = user.get("id")
        name = user.get("name", "Unknown")
        texts = user_posts_texts.get(user_id, [])
        count = len(texts)
        avg_length = sum(len(t) for t in texts) / count if count > 0 else 0.0

        results.append({
            "user_id": user_id,
            "name": name,
            "posts_count": count,
            "avg_text_length": avg_length
        })

    # Сортируем по числу постов по убыванию
    results.sort(key=lambda x: x["posts_count"], reverse=True)

    # Сохраняем в CSV в папке со скриптом
    with open(output_file, mode="w", newline="", encoding="utf-8") as f:
        fieldnames = ["user_id", "name", "posts_count", "avg_text_length"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Данные успешно сохранены в файл: {output_file}. Окно закроется автоматически через 10 секунд")
    time.sleep(10)

if __name__ == "__main__":
    main()
