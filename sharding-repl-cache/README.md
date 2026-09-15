# Задание 4. Кеширование запросов с Redis

Проект `sharding-repl-cache` — копия `mongo-sharding-repl` с Redis и включённым
кешированием `GET /{collection_name}/users`. Реализован
[третий вариант схемы](../Task1/03-caching.svg): репликация из второго варианта
плюс Redis. В пункте 3 задания упомянут второй вариант; добавление кеша
соответствует третьему этапу задания 1.

База — **`somedb`**, коллекция — **`helloDoc`**, ключ шардирования — `{name: "hashed"}`.

## Архитектура

| Группа / сервис | Инстансы | Назначение | Внутренний порт |
| --- | --- | --- | --- |
| `shard1RS` | `shard1-1`, `shard1-2`, `shard1-3` | Первый шард | `27018` |
| `shard2RS` | `shard2-1`, `shard2-2`, `shard2-3` | Второй шард | `27018` |
| `configRS` | `configSrv-1`, `configSrv-2`, `configSrv-3` | Метаданные кластера | `27019` |
| `mongos` | `mongos` | Маршрутизатор MongoDB | `27017` |
| `redis` | `redis` | Кеш ответов API | `6379` |
| `pymongo-api` | `pymongo-api` | Приложение | `8080` |

Всего **12 контейнеров**. В каждой группе MongoDB три участника: один primary
и два secondary, без арбитров. У девяти `mongod` отдельные постоянные тома.
Compose-проект `sharding-repl-cache` имеет собственные сеть и тома.
API обращается к MongoDB через `mongos`; порт Redis доступен внутри сети Compose.

В окружение API добавлено:

```yaml
REDIS_URL: "redis://redis:6379"
```

Redis использует образ `redis:7.4-alpine`, ограничение данных кеша 128 МБ
и вытеснение `allkeys-lru`. AOF и RDB отключены: после перезапуска Redis
кеш заполняется заново из MongoDB. API запускается после успешного `redis-cli ping`.
У каждого MongoDB ограничен кеш WiredTiger: 0,25 ГБ; это не лимит общей памяти процесса.

## Образ приложения

Используется опубликованный образ **`kazhem/pymongo_api:1.0.0`**.
Он доступен для `linux/amd64`, поэтому платформа задана явно; на ARM64
Docker должен поддерживать эмуляцию amd64 (например, Docker Desktop).

Файл [api_app/app.py](api_app/app.py) подключён в `/app/app.py` только для чтения.
Образ предоставляет Python и зависимости, а подключённый код — доработки проекта:
счётчики шардов, количество реплик и инвалидацию кеша с обходом недоступного Redis. Compose запускает образ из Docker Hub с этим файлом.
Локальные Dockerfile и requirements.txt сохранены как исходники предыдущего этапа;
для запуска по этой инструкции сборка приложения не требуется.

## Запуск и инициализация

Нужны Docker и Docker Compose с поддержкой `up --wait`.
Учитывайте память для девяти процессов MongoDB: при ограничении Docker Desktop
в 4 ГБ запускайте один учебный стенд за раз, особенно при наличии других проектов.

Из корня репозитория:

```bash
cd sharding-repl-cache
bash scripts/init-sharding.sh
```

Если `8080` занят, выберите свободный порт (при проверке использован `8085`):

```bash
export APP_PORT=8085
bash scripts/init-sharding.sh
```

Все следующие команды выполняются из `sharding-repl-cache/`.
Сохраняйте `APP_PORT` в окружении при следующих командах `docker compose up`.
По умолчанию API доступен на [localhost:8080](http://localhost:8080),
Swagger — [/docs](http://localhost:8080/docs).

[init-sharding.sh](scripts/init-sharding.sh) выполняет следующие действия:

1. Последовательно запускает `configRS`, `shard1RS`, `shard2RS`.
   Для каждой группы [init-replica-set.js](scripts/init-replica-set.js) выполняет
   `rs.initiate` с тремя участниками из `REPLICA_MEMBERS`, затем ждёт
   **1 PRIMARY + 2 SECONDARY**. У `configRS` включён `configsvr: true`.
2. Загружает образ API при отсутствии локальной копии и запускает остальные сервисы, включая Redis.
3. Через `mongos` выполняет [init-cluster.js](scripts/init-cluster.js):
   добавляет два шарда с адресами всех трёх участников, включает шардирование
   `somedb`, создаёт четыре начальных диапазона для пустой `helloDoc`,
   затем загружает 1000 документов (`ly0`–`ly999`).
4. Удаляет ключ кеша `helloDoc`, поскольку загрузка выполняется напрямую в БД.
5. Проверяет распределение и наличие копий на всех участниках трёх групп.

Повторный запуск безопасен для данных: скрипты проверяют конфигурацию,
добавляют документы через `upsert` со стабильными `_id` и не перезаписывают
существующие записи. Несовместимая конфигурация вызывает ошибку.
API и загрузка используют `w=majority`; для API включены retryable writes.

### Эквивалентные команды по шагам

```bash
docker compose up -d --wait --wait-timeout 120 configSrv-1 configSrv-2 configSrv-3
docker compose exec -T configSrv-1 mongosh --port 27019 --quiet --file /scripts/init-replica-set.js

docker compose up -d --wait --wait-timeout 120 shard1-1 shard1-2 shard1-3
docker compose exec -T shard1-1 mongosh --port 27018 --quiet --file /scripts/init-replica-set.js

docker compose up -d --wait --wait-timeout 120 shard2-1 shard2-2 shard2-3
docker compose exec -T shard2-1 mongosh --port 27018 --quiet --file /scripts/init-replica-set.js

docker compose up -d --wait --wait-timeout 180
docker compose exec -T mongos mongosh --port 27017 --quiet --file /scripts/init-cluster.js
docker compose exec -T redis redis-cli DEL api:cache:somedb:users:v1:helloDoc
```

## Как работает кеш

Реализована схема [cache-aside](https://redis.io/docs/latest/develop/use-cases/cache-aside/)
через существующую библиотеку [fastapi-cache2](https://github.com/long2ice/fastapi-cache):

1. `GET /helloDoc/users` ищет ответ в Redis.
2. При промахе читает до 1000 документов из MongoDB через `mongos` и сохраняет
   сериализованный ответ на **60 секунд**.
3. Повторные запросы в течение TTL получают ответ из Redis, без чтения MongoDB.
4. После успешной записи `POST /{collection_name}/users` API удаляет кеш списка
   только этой коллекции. Следующий GET заново читает MongoDB.

Ключ содержит базу, версию формата и имя коллекции:
`api:cache:somedb:users:v1:helloDoc`. Имена коллекций кодируются для однозначного
представления в ключах. Счётчики `/` и `/helloDoc/count`, чтение одного пользователя
и запись не кешируются.

В исходном задании перед чтением списка была искусственная задержка `time.sleep(1)`.
Она сохранена как `await asyncio.sleep(1)`, чтобы не блокировать обработку остальных
запросов. Поэтому холодный запрос занимает около секунды даже на маленькой БД;
прогретый запрос пропускает эту задержку. Это демонстрационный замер кеша,
а не оценка собственной скорости MongoDB.

При ошибке соединения или тайм-ауте Redis приложение продолжает читать и писать
в MongoDB. Тайм-аут соединения и операции Redis — по 200 мс. В этом режиме
порог 100 мс не применяется: запрос становится холодным. `cache_enabled` на `/`
показывает включённую настройку, а не текущую доступность Redis.

TTL ограничивает срок хранения устаревшего ответа при изменениях напрямую в БД,
недоступной инвалидации или одновременных чтении и записи. Строгая согласованность
кеша и защита от одновременного заполнения несколькими запросами здесь не реализованы.

## Проверка данных, реплик и API

```bash
curl --fail --silent --show-error "http://localhost:${APP_PORT:-8080}/"
curl --fail --silent --show-error "http://localhost:${APP_PORT:-8080}/helloDoc/count"
docker compose exec -T pymongo-api python < scripts/check-api.py

docker compose exec -T mongos mongosh --port 27017 --quiet --file /scripts/check-sharding.js
docker compose exec -T configSrv-1 mongosh --port 27019 --quiet --file /scripts/check-replication.js
docker compose exec -T shard1-1 mongosh --port 27018 --quiet --file /scripts/check-replication.js
docker compose exec -T shard2-1 mongosh --port 27018 --quiet --file /scripts/check-replication.js
```

В `/helloDoc/count` ожидаются:

```json
{
  "items_count": 1000,
  "documents_per_shard": {"shard1RS": 492, "shard2RS": 508},
  "replicas_per_shard": {"shard1RS": 3, "shard2RS": 3},
  "data_replicas_count": 6
}
```

Счётчики относятся к начальной загрузке без сторонних записей.
**Число реплик включает primary**, а три config servers не входят в число 6.
Состав групп берётся из `listShards`: это число настроенных, а не доступных узлов.
Состояние и фактическое копирование данных проверяет `check-replication.js`.
Все копии одного шарда содержат одни и те же документы; логически в БД 1000 записей.

## Проверка кеширования и скорости

Проверки включают сохранение ответа в Redis, попадание в кеш по счётчику Redis,
TTL и повторное заполнение после истечения, сброс после POST и изоляцию коллекций.
Временные коллекции удаляются после проверки:

```bash
docker compose exec -T pymongo-api python < scripts/check-cache.py
```

Удалите только нужный ключ, чтобы первый запрос гарантированно был холодным,
и измерьте **20 повторных запросов**:

```bash
docker compose exec -T redis redis-cli DEL api:cache:somedb:users:v1:helloDoc
docker compose exec -T pymongo-api python < scripts/benchmark-cache.py
```

[benchmark-cache.py](scripts/benchmark-cache.py) замеряет время от начала HTTP-запроса
до чтения всего тела ответа; запуск Docker CLI в это время не включён.
Скрипт сверяет содержимое ответов, выводит первый запрос, минимум, медиану и максимум
повторных запросов и завершает работу с ошибкой, если **хотя бы один из них ≥100 мс**.
Условие относится к прогретому кешу до истечения TTL и без записи, сбрасывающей ключ.

Для замера с хоста через опубликованный порт нужен только Python 3:

```bash
docker compose exec -T redis redis-cli DEL api:cache:somedb:users:v1:helloDoc
python3 scripts/benchmark-cache.py --url "http://localhost:${APP_PORT:-8080}/helloDoc/users"
```

Посмотреть ключ и оставшееся время хранения:

```bash
docker compose exec -T redis redis-cli TTL api:cache:somedb:users:v1:helloDoc
```

### Проверка недоступности Redis

```bash
bash scripts/check-cache-failover.sh
```

Скрипт останавливает Redis, проверяет чтение и запись через API, затем запускает
Redis обратно и повторяет проверки кеша и скорости. При ошибке также пытается
восстановить Redis через обработчик EXIT. MongoDB остаётся источником данных.
Скрипт проверки отказов primary из задания 3 сохранён как `scripts/check-failover.sh`.

## Остановка и повторный запуск

```bash
docker compose stop
docker compose up -d --wait --wait-timeout 180
```

Данные MongoDB сохраняются в томах, кеш Redis после перезапуска пуст.
Все контейнеры находятся на одном Docker-хосте; API и `mongos` пока одиночные.

## Результаты проверки для сдачи — 2026-09-15

Проверено на Docker Desktop ARM64 с запуском образа `kazhem/pymongo_api:1.0.0`
для amd64. Идентификатор образа работающего API совпал с загруженным из Docker Hub;
`app.py` подключён только для чтения. Все три учебных стенда оставались запущенными.

- Все 12 сервисов финального стенда — running / healthy.
- Инициализация прошла: 1000 документов, распределение 492 / 508;
  у каждого шарда три копии, `configRS` также содержит три участника.
- Все 6 проверок API и 3 проверки кеша прошли.
- При остановленном Redis чтение и запись работают; после восстановления
  повторные проверки кеша и скорости прошли.
- `/` возвращает JSON MongoDB и `cache_enabled: true`; `/docs` отвечает HTTP 200.

Каждая серия ниже содержит 20 повторных запросов; перед первой загрузкой ключ пуст.
Измеряется весь HTTP-ответ для 1000 пользователей без параллельной пользовательской нагрузки.

| Где выполнялся запрос | Первый, мс | Повторные: мин., мс | Медиана, мс | Макс., мс |
| --- | ---: | ---: | ---: | ---: |
| Внутри контейнера API | 1119,90 | 4,02 | 4,39 | 56,53 |
| После восстановления Redis, внутри API | 1132,97 | 4,02 | 4,57 | 6,97 |
| С хоста, порт 8085 | 1095,99 | 5,29 | 6,17 | 8,35 |

Во всех сериях каждый повторный запрос выполнился **быстрее 100 мс**.
На том же образе также прошли 5 API-проверок `mongo-sharding`
и 6 API-проверок `mongo-sharding-repl`.
