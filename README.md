# «Мобильный мир» — проектная работа 4-го спринта

**Для проверки используйте каталог [`sharding-repl-cache`](sharding-repl-cache/README.md).**
Он объединяет задания 2, 3 и 4: шардирование MongoDB, репликацию и Redis-кеширование.

Итоговая схема заданий 1, 5 и 6: [Task1/task1.drawio](Task1/task1.drawio),
страница **05 — CDN в нескольких регионах**. [Посмотреть SVG](Task1/05-cdn.svg).

## Требования

- Docker и Docker Compose с поддержкой `docker compose up --wait`.
- Минимум 2 CPU и 4 ГБ памяти для учебного стенда. При запуске девяти MongoDB
  остановите неиспользуемые стенды или увеличьте память Docker.
- Свободный порт `8080` либо другой порт через `APP_PORT`.
- Доступ к Docker Hub для загрузки образов.

Образ приложения — **`kazhem/pymongo_api:1.0.0`**, платформа **`linux/amd64`**.
На ARM64 требуется поддержка эмуляции amd64, например в Docker Desktop.
В образ подключён [доработанный app.py](sharding-repl-cache/api_app/app.py)
как файл `/app/app.py` только для чтения: он добавляет статистику шардов и реплик,
сброс кеша после записи и работу при недоступности Redis. Python и зависимости
предоставляет опубликованный образ; сборка приложения для запуска не требуется.

## Запуск для ревьюера

Команды выполняются из корня репозитория с изменениями пул-реквеста:

```bash
cd sharding-repl-cache
bash scripts/init-sharding.sh
docker compose ps
```

Скрипт сам запускает сервисы и выполняет всю настройку:

1. Создаёт `configRS`, `shard1RS` и `shard2RS`, по три участника в каждой группе.
2. Ждёт **1 PRIMARY + 2 SECONDARY** в каждой группе, запускает `mongos`, Redis и API.
3. Добавляет оба шарда через `mongos`; включает шардирование `somedb.helloDoc`
   по ключу `{name: "hashed"}` и загружает **1000 документов**.
4. Сбрасывает кеш начальной коллекции, проверяет распределение документов
   и наличие копий на каждом узле.

Повторный запуск скрипта не создаёт дубликаты: используются стабильные `_id` и `upsert`.
Необходимые сеть и тома создаются автоматически. Ожидаются **12 running / healthy сервисов**.

Если `8080` занят, до запуска задайте свободный порт:

```bash
export APP_PORT=8085
bash scripts/init-sharding.sh
```

Сохраняйте выбранный `APP_PORT` в окружении при следующих командах запуска.
При `APP_PORT=8085` приложение будет доступно на http://localhost:8085.

## Что проверить в приложении

По умолчанию откройте в браузере:

- [http://localhost:8080/](http://localhost:8080/) — JSON с топологией MongoDB,
  количеством документов, распределением по шардам, составом replica set и `cache_enabled`.
- [http://localhost:8080/helloDoc/count](http://localhost:8080/helloDoc/count) — счётчики данных и реплик.
- [http://localhost:8080/helloDoc/users](http://localhost:8080/helloDoc/users) — кешируемый список пользователей.
- [http://localhost:8080/docs](http://localhost:8080/docs) — Swagger API.

Из каталога `sharding-repl-cache`:

```bash
curl --fail --silent --show-error "http://localhost:${APP_PORT:-8080}/"
curl --fail --silent --show-error "http://localhost:${APP_PORT:-8080}/helloDoc/count"
docker compose exec -T pymongo-api python < scripts/check-api.py
docker compose exec -T pymongo-api python < scripts/check-cache.py
```

В корневом JSON ожидается, среди прочих полей:

```json
{
  "mongo_topology_type": "Sharded",
  "mongo_db": "somedb",
  "mongo_is_mongos": true,
  "collections": {
    "helloDoc": {
      "documents_count": 1000,
      "documents_per_shard": {"shard1RS": 492, "shard2RS": 508}
    }
  },
  "replicas_per_shard": {"shard1RS": 3, "shard2RS": 3},
  "data_replicas_count": 6,
  "cache_enabled": true,
  "status": "OK"
}
```

Значения 492 / 508 относятся к начальной загрузке без дополнительных записей.
Число реплик **включает primary**; три config servers в число шести узлов данных
не входят. API показывает настроенный состав групп; фактическую доступность
и синхронизацию проверяет скрипт инициализации.

### Скорость кеша

Выполните из `sharding-repl-cache`:

```bash
docker compose exec -T redis redis-cli DEL api:cache:somedb:users:v1:helloDoc
docker compose exec -T pymongo-api python < scripts/benchmark-cache.py
```

Первый запрос заполняет кеш, затем измеряются **20 повторных запросов**.
Скрипт проверяет одинаковое содержимое ответов и завершается с ошибкой,
если хотя бы один повторный запрос занимает **100 мс или больше**.
TTL — 60 секунд; после записи в коллекцию следующий запрос снова будет холодным.
Секундная задержка холодного запроса присутствует в исходном учебном приложении.

## Состав финального стенда

| Сервисы | Назначение |
| --- | --- |
| `pymongo-api` | Приложение на `kazhem/pymongo_api:1.0.0`, порт `8080` |
| `redis` | Общий кеш списков пользователей, порт `6379` |
| `mongos` | Маршрутизация запросов к MongoDB, порт `27017` |
| `configSrv-1`, `configSrv-2`, `configSrv-3` | `configRS`, метаданные кластера, порт `27019` |
| `shard1-1`, `shard1-2`, `shard1-3` | `shard1RS`, первый шард, порт `27018` |
| `shard2-1`, `shard2-2`, `shard2-3` | `shard2RS`, второй шард, порт `27018` |

MongoDB имеет постоянные тома. Redis хранит временный кеш; после его перезапуска
данные снова читаются из MongoDB. Переменная приложения: `REDIS_URL=redis://redis:6379`.

## Остановка и повторный запуск

Из `sharding-repl-cache`:

```bash
docker compose stop
docker compose up -d --wait --wait-timeout 180
```

Подробная пошаговая настройка, диагностика реплик и проверки отказов:
[sharding-repl-cache/README.md](sharding-repl-cache/README.md).

## Структура сдаваемой работы

| Каталог / файл | Содержание |
| --- | --- |
| [mongo-sharding](mongo-sharding/README.md) | Задание 2: два шарда |
| [mongo-sharding-repl](mongo-sharding-repl/README.md) | Задание 3: по три участника каждого шарда |
| [sharding-repl-cache](sharding-repl-cache/README.md) | Задание 4: финальный запускаемый стенд с Redis |
| [Task1/task1.drawio](Task1/task1.drawio) | Пять вариантов схем; итоговый — страница 05 |
| [Task1/README.md](Task1/README.md) | Обоснование архитектуры заданий 1, 5 и 6 |

По условиям заданий 5 и 6 горизонтальное масштабирование, Gateway, Consul и CDN
представлены на схеме. Запускаемый стенд реализует задания 2–4.
Корневые `compose.yaml`, `api_app/` и `scripts/mongo-init.sh` — исходный PoC;
инструкция для сдачи выше использует отдельный проект `sharding-repl-cache`.
