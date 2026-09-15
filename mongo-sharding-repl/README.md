# Задание 3. Репликация MongoDB

Проект `mongo-sharding-repl` — копия `mongo-sharding`, доработанная по
[второму варианту схемы](../Task1/02-replication.svg).
База — **`somedb`**, коллекция — **`helloDoc`**, ключ шардирования — `{name: "hashed"}`.

## Архитектура

| Группа | Инстансы | Роль | Порт |
| --- | --- | --- | --- |
| `shard1RS` | `shard1-1`, `shard1-2`, `shard1-3` | Первый шард | `27018` |
| `shard2RS` | `shard2-1`, `shard2-2`, `shard2-3` | Второй шард | `27018` |
| `configRS` | `configSrv-1`, `configSrv-2`, `configSrv-3` | Метаданные кластера | `27019` |
| — | `mongos` | Маршрутизатор | `27017` |
| — | `pymongo-api` | API приложения | `8080` |

Всего **11 контейнеров**. В каждой группе **три участника всего: один primary
и два secondary**, без арбитров. Все участники голосуют и хранят данные своей
группы. Роли назначаются выборами; суффикс `-1` не закрепляет роль primary.
Это соответствует [модели репликации MongoDB](https://www.mongodb.com/docs/manual/replication/).
Метаданные также реплицируются в отдельной группе
[config servers](https://www.mongodb.com/docs/manual/core/sharded-cluster-config-servers/).

У каждого `mongod` отдельный постоянный том. Проект имеет собственные сеть и тома
с префиксом `mongo-sharding-repl`. Кеш WiredTiger ограничен 0,25 Гб на процесс.
Healthcheck MongoDB проверяет доступность TCP-порта; состояние группы и фактическое
копирование данных проверяют скрипты ниже. Приложение работает с БД через `mongos`.

## Образ приложения

Используется опубликованный образ **`kazhem/pymongo_api:1.0.0`**.
Он доступен для `linux/amd64`, поэтому платформа задана явно; на ARM64
Docker должен поддерживать эмуляцию amd64 (например, Docker Desktop).

Файл [api_app/app.py](api_app/app.py) подключён в `/app/app.py` только для чтения.
Образ предоставляет Python и зависимости, а подключённый код — доработки проекта:
счётчики шардов, количество реплик. Compose запускает образ из Docker Hub с этим файлом.
Локальные Dockerfile и requirements.txt сохранены как исходники предыдущего этапа;
для запуска по этой инструкции сборка приложения не требуется.

## Быстрый запуск

Нужны запущенный Docker и Docker Compose с поддержкой `up --wait`.
Учитывайте память для девяти процессов `mongod`; при нехватке ресурсов остановите
неиспользуемые учебные стенды или увеличьте память Docker Desktop.

Из корня репозитория:

```bash
cd mongo-sharding-repl
bash scripts/init-sharding.sh
```

Если `8080` занят, задайте свободный порт:

```bash
export APP_PORT=8084
bash scripts/init-sharding.sh
```

Далее команды выполняются из `mongo-sharding-repl/`. Сохраняйте выбранный
`APP_PORT` при следующих командах `docker compose up`.

Скрипт запускает группы по очереди, настраивает три replica set, ждёт состояния
**1 PRIMARY + 2 SECONDARY**, затем запускает `mongos` и API, включает шардирование,
загружает 1000 документов и проверяет их распределение и репликацию.

Инициализацию можно повторять: существующие настройки проверяются, документы
добавляются через `upsert` со стабильными `_id`. Дубликаты не создаются, имеющиеся
записи не перезаписываются. Несовместимая существующая конфигурация вызывает ошибку;
скрипт не заменяет её автоматически. Новому проекту нужны собственные тома,
создаваемые Compose, а не тома одноузлового стенда задания 2.

## Пошаговая настройка репликации

Эти шаги эквивалентны автоматическому запуску.

### 1. Настроить configRS

```bash
docker compose up -d --wait --wait-timeout 120 configSrv-1 configSrv-2 configSrv-3
docker compose exec -T configSrv-1 mongosh --port 27019 --quiet --file /scripts/init-replica-set.js
```

На новой группе [init-replica-set.js](scripts/init-replica-set.js) выполняет эквивалент:

```javascript
rs.initiate({
  _id: "configRS",
  configsvr: true,
  members: [
    {_id: 0, host: "configSrv-1:27019"},
    {_id: 1, host: "configSrv-2:27019"},
    {_id: 2, host: "configSrv-3:27019"}
  ]
})
```

### 2. Настроить первый шард

```bash
docker compose up -d --wait --wait-timeout 120 shard1-1 shard1-2 shard1-3
docker compose exec -T shard1-1 mongosh --port 27018 --quiet --file /scripts/init-replica-set.js
```

Конфигурация первого шарда:

```javascript
rs.initiate({
  _id: "shard1RS",
  members: [
    {_id: 0, host: "shard1-1:27018"},
    {_id: 1, host: "shard1-2:27018"},
    {_id: 2, host: "shard1-3:27018"}
  ]
})
```

### 3. Настроить второй шард

```bash
docker compose up -d --wait --wait-timeout 120 shard2-1 shard2-2 shard2-3
docker compose exec -T shard2-1 mongosh --port 27018 --quiet --file /scripts/init-replica-set.js
```

Используется такая же конфигурация с `_id: "shard2RS"` и узлами `shard2-1:27018`,
`shard2-2:27018`, `shard2-3:27018`.

Параметры всех групп заданы в окружении Compose: `REPLICA_SET`, `REPLICA_MEMBERS`,
`CONFIG_SERVER`. Скрипт ждёт готовности всех трёх участников и работает при
повторном запуске, даже если новый primary находится на узле `-2` или `-3`.

### 4. Запустить mongos, API и включить шардирование

```bash
docker compose up -d --wait --wait-timeout 180
docker compose exec -T mongos mongosh --port 27017 --quiet --file /scripts/init-cluster.js
```

В `mongos --configdb` указаны все три config servers:

```text
configRS/configSrv-1:27019,configSrv-2:27019,configSrv-3:27019
```

При добавлении шардов скрипт использует адреса всех участников:

```javascript
db.adminCommand({
  addShard: "shard1RS/shard1-1:27018,shard1-2:27018,shard1-3:27018",
  name: "shard1RS"
})
db.adminCommand({
  addShard: "shard2RS/shard2-1:27018,shard2-2:27018,shard2-3:27018",
  name: "shard2RS"
})
```

Далее создаются четыре начальных диапазона для пустой `somedb.helloDoc`,
и загружаются пользователи `ly0`–`ly999`. В пределах шарда документы копируются
на secondary; общее логическое количество документов остаётся **1000**.

Записи API и начальная загрузка используют `w=majority`: запись подтверждается
большинством участников соответствующей группы. Для API включены retryable writes.
При выборах нового primary возможна краткая пауза запросов.

## Проверка данных и копий

Распределение по шардам:

```bash
docker compose exec -T mongos mongosh --port 27017 --quiet --file /scripts/check-sharding.js
```

Фактическая репликация на все узлы:

```bash
docker compose exec -T configSrv-1 mongosh --port 27019 --quiet --file /scripts/check-replication.js
docker compose exec -T shard1-1 mongosh --port 27018 --quiet --file /scripts/check-replication.js
docker compose exec -T shard2-1 mongosh --port 27018 --quiet --file /scripts/check-replication.js
```

[check-replication.js](scripts/check-replication.js) проверяет три участника,
отсутствие арбитров, один primary, два secondary и одинаковое количество документов
на всех трёх узлах шарда. Для `configRS` проверяются записи о двух шардах и
шардированной коллекции. Скрипт ожидает синхронизации, а затем выводит адреса,
роли и счётчики узлов. Чтения на secondary разрешены явно для этой диагностики.

Для просмотра состояния вручную:

```bash
docker compose exec -T shard1-1 mongosh --port 27018 --quiet --file /dev/stdin <<'EOF'
const status = rs.status();
printjson(status.members.map(member => ({
  host: member.name,
  role: member.stateStr,
  health: member.health
})));
EOF
```

## Проверка приложения

По умолчанию приложение доступно на [localhost:8080](http://localhost:8080),
документация — [/docs](http://localhost:8080/docs).

```bash
curl --fail --silent --show-error "http://localhost:${APP_PORT:-8080}/"
curl --fail --silent --show-error "http://localhost:${APP_PORT:-8080}/helloDoc/count"
docker compose exec -T pymongo-api python < scripts/check-api.py
```

Оба эндпоинта показывают общее количество документов, количество по каждому
шарду и добавленные поля:

```json
{
  "replicas_per_shard": {"shard1RS": 3, "shard2RS": 3},
  "data_replicas_count": 6,
  "replica_sets": {
    "shard1RS": {
      "replica_set": "shard1RS",
      "members": ["shard1-1:27018", "shard1-2:27018", "shard1-3:27018"]
    },
    "shard2RS": {
      "replica_set": "shard2RS",
      "members": ["shard2-1:27018", "shard2-2:27018", "shard2-3:27018"]
    }
  }
}
```

**Количество реплик включает primary**. `data_replicas_count` считает шесть узлов
данных двух шардов; три config servers в это число не входят. Состав групп
извлекается из [`listShards`](https://www.mongodb.com/docs/manual/reference/command/listshards/),
а не задаётся константой в API. Это число настроенных участников, а не число
доступных в данный момент узлов. Доступность и роли проверяет `check-replication.js`.

Счётчики документов по шардам, как в задании 2, берутся из `$collStats`.
Сравнивайте их с общим счётчиком без параллельных записей и перемещений чанков.
Шесть проверок API включают счётчики документов, число реплик, чтение, документацию,
ответ для отсутствующей коллекции и создание временного пользователя с его удалением.

## Проверка отказа primary

Дополнительная проверка временно останавливает по одному текущему primary
в `shard1RS`, `shard2RS` и `configRS`:

```bash
bash scripts/check-failover.sh
```

Скрипт определяет primary, останавливает его контейнер, ждёт другого primary и
проверяет чтение и запись через API при двух доступных участниках. Затем он
запускает остановленный узел и ждёт восстановления трёх синхронизированных копий.
При ошибке скрипт также пытается вернуть остановленный контейнер в работу.

## Остановка и повторный запуск

```bash
docker compose stop
docker compose up -d --wait --wait-timeout 180
```

Данные и конфигурация сохраняются в томах. Для повторной проверки инициализации
можно выполнить `bash scripts/init-sharding.sh`.

Репликация в одном Docker-хосте позволяет проверить отказ отдельного процесса;
отказ самого хоста остаётся общей точкой отказа. API и `mongos` пока представлены
одним инстансом — это состав второго варианта схемы.

## Результат проверки

Проверено 2026-09-15 на Docker Desktop (ARM64), MongoDB 7.0.43:

- Все 11 сервисов запустились; каждая группа имеет 1 primary и 2 secondary.
- Всего 1000 документов: по **492** на каждом участнике `shard1RS` и по **508**
  на каждом участнике `shard2RS`.
- Все три config servers содержат сведения о двух шардах и `somedb.helloDoc`.
- Все **6 интеграционных проверок API** прошли.
- Проверены отказы primary обоих шардов и `configRS`: в каждом случае выбран
  новый primary, шесть проверок API прошли при остановленном узле, затем узел
  восстановлен и синхронизирован.
- Повторная инициализация после выборов прошла: добавлено 0 документов,
  найдено 1000 существующих; первый узел группы может оставаться secondary.
- HTTP-доступ к API с хоста подтверждён на порту 8084.

Для локальной проверки использован `APP_PORT=8084`; порт по умолчанию — 8080.

Перед сдачей API повторно проверен на `kazhem/pymongo_api:1.0.0`: все 6 тестов прошли.
