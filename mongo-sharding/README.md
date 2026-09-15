# Задание 2. Шардирование MongoDB

Отдельный Compose-проект `mongo-sharding` реализует [первую схему задания 1](../Task1/01-sharding.svg).
База данных — **`somedb`**, шардированная коллекция — **`helloDoc`**.

## Состав стенда

| Сервис | Роль | Replica set | Внутренний порт |
| --- | --- | --- | --- |
| `pymongo-api` | Приложение FastAPI | — | `8080` |
| `mongos` | Маршрутизатор запросов MongoDB | — | `27017` |
| `configSrv-1` | Метаданные кластера, `mongod --configsvr` | `configRS` | `27019` |
| `shard1-1` | Первый шард, `mongod --shardsvr` | `shard1RS` | `27018` |
| `shard2-1` | Второй шард, `mongod --shardsvr` | `shard2RS` | `27018` |

В каждой группе пока один участник. Два шарда хранят разные части коллекции;
резервные узлы будут добавлены на следующем этапе. Приложение подключается
только к `mongos`, включая запрос статистики по шардам.
Порты соответствуют [документации MongoDB](https://www.mongodb.com/docs/manual/reference/default-mongodb-port/).

Используется официальный образ `mongo:7.0`. У каждого `mongod` отдельный постоянный том;
для локального стенда размер кеша WiredTiger ограничен 0,25 Гб на процесс.
Инфраструктура доступна во внутренней сети Compose, на хост опубликован порт API.

## Образ приложения

Используется опубликованный образ **`kazhem/pymongo_api:1.0.0`**.
Он доступен для `linux/amd64`, поэтому платформа задана явно; на ARM64
Docker должен поддерживать эмуляцию amd64 (например, Docker Desktop).

Файл [api_app/app.py](api_app/app.py) подключён в `/app/app.py` только для чтения.
Образ предоставляет Python и зависимости, а подключённый код — доработки проекта:
счётчики шардов. Compose запускает образ из Docker Hub с этим файлом.
Локальные Dockerfile и requirements.txt сохранены как исходники предыдущего этапа;
для запуска по этой инструкции сборка приложения не требуется.

## Быстрый запуск

Нужны запущенный Docker и Docker Compose с поддержкой `up --wait`.
Первый запуск скачивает образы и собирает приложение.

Из корня репозитория:

```bash
cd mongo-sharding
bash scripts/init-sharding.sh
```

Если порт `8080` занят, перед запуском задайте свободный порт:

```bash
export APP_PORT=8083
bash scripts/init-sharding.sh
```

Все дальнейшие команды выполняйте из `mongo-sharding/`. Сохраняйте выбранное
значение `APP_PORT` при последующих командах `docker compose up`; по умолчанию
оно равно `8080`.

Скрипт последовательно:

1. Запускает три процесса `mongod`, ожидает успешного `ping`.
2. Инициализирует одноузловые `configRS`, `shard1RS` и `shard2RS`, ожидает primary.
3. Запускает `mongos`, собирает и запускает API, проверяет готовность контейнеров.
4. Подключает шарды, включает шардирование `somedb.helloDoc` по `{name: "hashed"}`.
5. Создаёт четыре начальных диапазона до загрузки данных и добавляет 1000 документов.
6. Проверяет, что оба шарда содержат документы и их сумма равна общему количеству.

Повторный запуск разрешён: настройки проверяются, тестовые документы добавляются
через `upsert` со стабильными `_id` и `name`. Уже существующие записи не перезаписываются.
Ошибки команд MongoDB завершают скрипт с ненулевым кодом. Если до первого включения
шардирования коллекция уже непустая, скрипт сообщит об этом и сохранит данные.

## Пошаговая инициализация

Этот раздел выполняет те же действия, что и быстрый запуск.

### 1. Запустить config server и два шарда

```bash
docker compose up -d --wait --wait-timeout 120 configSrv-1 shard1-1 shard2-1
```

### 2. Инициализировать replica set

```bash
docker compose exec -T configSrv-1 mongosh --port 27019 --quiet --file /scripts/init-replica-set.js
docker compose exec -T shard1-1 mongosh --port 27018 --quiet --file /scripts/init-replica-set.js
docker compose exec -T shard2-1 mongosh --port 27018 --quiet --file /scripts/init-replica-set.js
```

[init-replica-set.js](scripts/init-replica-set.js) читает параметры из окружения
сервиса. Для нового `configRS` он выполняет эквивалент:

```javascript
rs.initiate({
  _id: "configRS",
  configsvr: true,
  members: [{_id: 0, host: "configSrv-1:27019"}]
})
```

Для шардов используются соответственно `shard1RS/shard1-1:27018` и
`shard2RS/shard2-1:27018`, без `configsvr: true`. Скрипт ждёт выборов primary.

### 3. Запустить маршрутизатор и приложение

```bash
docker compose up -d --wait --wait-timeout 180
```

`mongos` использует `--configdb configRS/configSrv-1:27019`.
API получает `MONGODB_URL=mongodb://mongos:27017` и `MONGODB_DATABASE_NAME=somedb`.

### 4. Настроить шардирование и загрузить документы

```bash
docker compose exec -T mongos mongosh --port 27017 --quiet --file /scripts/init-cluster.js
```

Основные команды из [init-cluster.js](scripts/init-cluster.js):

```javascript
db.adminCommand({addShard: "shard1RS/shard1-1:27018", name: "shard1RS"})
db.adminCommand({addShard: "shard2RS/shard2-1:27018", name: "shard2RS"})
db.adminCommand({enableSharding: "somedb", primaryShard: "shard1RS"})
db.getSiblingDB("somedb").helloDoc.createIndex({name: "hashed"})
db.adminCommand({
  shardCollection: "somedb.helloDoc",
  key: {name: "hashed"},
  numInitialChunks: 4
})
```

Скрипт проверяет существующие настройки перед выполнением этих команд и затем
загружает документы с `name` от `ly0` до `ly999` и `age` от 0 до 999.

Поле `name` выбрано потому, что API уже ищет пользователя по равенству этого поля.
Хеш распределяет последовательные имена между диапазонами. Параметр
`numInitialChunks: 4` распределяет пустые диапазоны между двумя шардами до загрузки:
не нужно ждать, пока 1000 небольших документов вызовут балансировку.
См. [`shardCollection`](https://www.mongodb.com/docs/v7.0/reference/command/shardCollection/).

### 5. Проверить кластер

```bash
docker compose exec -T mongos mongosh --port 27017 --quiet --file /scripts/check-sharding.js
docker compose ps
```

Проверка завершается успешно, если есть два нужных шарда, ключ `{name: "hashed"}`,
не меньше 1000 документов, непустой каждый шард и совпадение суммы с общим количеством.
Точное распределение не обязано быть 500/500.

## Проверка приложения

При стандартном порте:

- [Главная страница](http://localhost:8080/) — JSON со сведениями о кластере.
- [Количество документов](http://localhost:8080/helloDoc/count) — общий счётчик и разбивка по шардам.
- [Документация API](http://localhost:8080/docs).

Для выбранного `APP_PORT`:

```bash
curl --fail --silent --show-error "http://localhost:${APP_PORT:-8080}/"
curl --fail --silent --show-error "http://localhost:${APP_PORT:-8080}/helloDoc/count"
curl --fail --silent --show-error "http://localhost:${APP_PORT:-8080}/helloDoc/users/ly42"
```

На `/` проверьте:

- `mongo_topology_type`: `Sharded`;
- `mongo_is_mongos`: `true`;
- `collections.helloDoc.documents_count`: не меньше 1000;
- `collections.helloDoc.documents_per_shard`: оба шарда содержат документы;
- `shards`: адреса `shard1RS` и `shard2RS`.

Общий счётчик вычисляется через `count_documents({})`, разбивка — через
[`$collStats`](https://www.mongodb.com/docs/v7.0/reference/operator/aggregation/collstats/)
на `mongos`. Статистика шардов может включать ещё не удалённые копии документов
во время миграции чанков; общий счётчик и статистика читаются отдельными запросами.
Сравнивать их нужно после завершения перемещений и без параллельных записей.

Для независимой проверки количества непосредственно на шардах:

```bash
docker compose exec -T shard1-1 mongosh --port 27018 --quiet --file /dev/stdin <<'EOF'
print(db.getSiblingDB("somedb").helloDoc.countDocuments({}));
EOF

docker compose exec -T shard2-1 mongosh --port 27018 --quiet --file /dev/stdin <<'EOF'
print(db.getSiblingDB("somedb").helloDoc.countDocuments({}));
EOF
```

Интеграционная проверка API, включая создание пользователя через `mongos`:

```bash
docker compose exec -T pymongo-api python < scripts/check-api.py
```

Проверка удаляет только собственный временный документ после теста записи.

## Остановка и повторный запуск

```bash
docker compose stop
docker compose up -d --wait --wait-timeout 180
```

Тома сохраняют документы и конфигурацию кластера. Для проверки повторяемости
можно снова выполнить `bash scripts/init-sharding.sh`: без других записей
количество документов останется 1000.

## Изменения копии приложения

В `api_app/` скопирован исходный код и Dockerfile. Добавлена статистика по шардам
на `/` и `/{collection_name}/count`; получение сведений о топологии адаптировано
для подключения через `mongos`. Запускаемый образ `kazhem/pymongo_api:1.0.0`
содержит Motor 3.5.0 и PyMongo 4.8.0.
Также добавлен явный импорт `logging.config`, необходимый для самостоятельного
импорта модуля приложения.

## Результат проверки

Проверено 2026-09-15 на Docker Desktop (ARM64), MongoDB **7.0.43**:

- Запуск всех пяти сервисов и инициализация шардирования.
- Повторная инициализация: добавлено 0 документов, найдено 1000 существующих.
- Всего **1000 документов**: `shard1RS` — **492**, `shard2RS` — **508**.
- Счётчики API совпали с независимым `countDocuments()` на каждом шарде.
- Все **5 интеграционных проверок API** прошли, включая создание и чтение пользователя.
- HTTP-доступ с хоста проверен на `8083`, поскольку `8080` занят другим проектом.

Пример ответа `/helloDoc/count` после загрузки:

```json
{
  "status": "OK",
  "mongo_db": "somedb",
  "items_count": 1000,
  "documents_per_shard": {
    "shard1RS": 492,
    "shard2RS": 508
  }
}
```

Перед сдачей API повторно проверен на `kazhem/pymongo_api:1.0.0`: все 5 тестов прошли.
