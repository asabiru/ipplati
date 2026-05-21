# IP Automation

Каркас сервиса для учета сделок, сверки входящих платежей и определения возвратных сделок.

## Что уже есть

- FastAPI-приложение с SQLite по умолчанию
- модели сделок, банковских операций и чеков
- batch-import для `Wallet`, `Сбер`, `ОФД`
- базовый движок автосопоставления `deal -> bank transaction`
- отчет сверки по статусам

## Архитектура

Сервис разделен на 4 слоя:

1. `Wallet reader`
   Считывает сделки из интерфейса Telegram Wallet в режиме `read-only` и отправляет их в API `/wallet/deals/import`.
2. `Bank importer`
   Получает входящие платежи из `СберБизнес API` или из выгрузки и отправляет их в `/bank/transactions/import`.
3. `Receipt importer`
   Подтягивает чеки и чеки возврата из `Астрал.ОФД` и отправляет их в `/receipts/import`.
4. `Core reconciliation`
   Хранит единую БД, выполняет матчинг и отдает реестры/отчеты.

## Быстрый старт

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
uvicorn app.main:app --reload
```

После старта будут доступны:

- `GET /health`
- `POST /wallet/deals/import`
- `POST /bank/transactions/import`
- `POST /receipts/import`
- `POST /matching/run`
- `POST /classification/run`
- `GET /deals`
- `GET /reports/reconciliation`

## Reader CLI

`Wallet reader` сейчас готов в виде отдельного CLI-модуля. Он принимает сырой JSON из внешнего считывателя и приводит его к нашему API-формату.

Dry-run нормализации:

```bash
python -m app.reader.cli import-file --input docs/sample-wallet-export.json --dry-run
```

Сохранить нормализованный batch в файл:

```bash
python -m app.reader.cli normalize-file --input docs/sample-wallet-export.json --output out/wallet-batch.json
```

Отправить batch в работающий сервис:

```bash
python -m app.reader.cli import-file --input docs/sample-wallet-export.json --api-base-url http://127.0.0.1:8000
```

Импорт реального CSV из Wallet:

```bash
python -m app.reader.cli import-csv --input C:\Users\stasb\Downloads\p2p-order-history_2026-04-01_2026-04-30.csv --api-base-url http://127.0.0.1:8000
```

Самый простой рабочий сценарий для ручной выгрузки из `Telegram Wallet`: пользователь сам экспортирует `CSV`, а затем одной командой загружает сделки и сразу запускает сверку:

```bash
python -m app.reader.cli process-csv --input C:\Users\stasb\Downloads\p2p-order-history_2026-04-01_2026-04-30.csv --api-base-url http://127.0.0.1:8000
```

## Telegram Bot

В проект добавлен отдельный `Telegram`-бот для статистики. Он берет данные из API и умеет отвечать командами:

- `/report` - общий отчет
- `/deals` - последние сделки
- `/returns` - возвраты и риски
- `/help` - список команд

Нужные переменные в `.env`:

```env
TELEGRAM_BOT_BOT_TOKEN=ваш_токен_бота
TELEGRAM_BOT_API_BASE_URL=https://api.wabcrm.ru
TELEGRAM_BOT_RECENT_DEALS_LIMIT=5
```

Запуск бота в режиме long polling:

```bash
python -m app.telegram_bot.cli poll
```

Тестовая отправка текущего отчета в конкретный чат:

```bash
python -m app.telegram_bot.cli send-report --chat-id 123456789 --kind report
```

Для бота добавлен API endpoint:

```bash
GET /reports/dashboard
```

Наблюдать один CSV и переимпортировать его при каждом изменении:

```bash
python -m app.reader.cli watch-csv --input C:\Users\stasb\Downloads\p2p-order-history_2026-04-01_2026-04-30.csv --api-base-url http://127.0.0.1:8000
```

Наблюдать папку `Downloads` и автоматически забирать самую свежую выгрузку Wallet:

```bash
python -m app.reader.cli watch-downloads --directory C:\Users\stasb\Downloads --pattern p2p-order-history_*.csv --api-base-url http://127.0.0.1:8000
```

## Wallet Web Reader

Для `Wallet` добавлен отдельный `Playwright`-ридер в режиме `read-only`. Он не подтверждает сделки и не торгует, а только:

- открывает `wallet.tg`
- использует сохраненную сессию
- скачивает свежий CSV истории `P2P`
- отправляет его в `/wallet/deals/import`
- при необходимости сразу запускает сверку с банком и чеками

Сначала сохраните авторизованную сессию:

```bash
python -m app.wallet_web_reader.cli manual-login --storage-state artifacts/wallet/storage-state.json
```

После команды откроется `Chrome`. Войдите в `Wallet`, дойдите до `P2P`, и когда браузер вернется на URL с `/p2p`, состояние сохранится.

Скачать свежий CSV без импорта:

```bash
python -m app.wallet_web_reader.cli download-csv --storage-state artifacts/wallet/storage-state.json --downloads-dir artifacts/wallet/downloads
```

Скачать CSV, импортировать сделки и сразу запустить сверку:

```bash
python -m app.wallet_web_reader.cli import-csv --storage-state artifacts/wallet/storage-state.json --downloads-dir artifacts/wallet/downloads --api-base-url http://127.0.0.1:8000
```

Если верстка `Wallet` отличается, можно подправить env-настройки:

- `WALLET_WEB_READER_ORDERS_URL`
- `WALLET_WEB_READER_HISTORY_NAV_SELECTORS`
- `WALLET_WEB_READER_EXPORT_BUTTON_SELECTORS`
- `WALLET_WEB_READER_EXPORT_CSV_SELECTORS`

## Telegram Wallet Reader

Если `Wallet P2P` доступен только внутри `Telegram`, используйте Windows-ридер для `Telegram Desktop`. Он не читает сделки напрямую из UI, а помогает автоматизировать безопасный сценарий:

- найти и сфокусировать окно `Telegram`
- дождаться, пока вы вручную нажмете `Export CSV` в `Wallet -> P2P`
- автоматически забрать новый `p2p-order-history_*.csv`
- импортировать его в ядро и сразу запустить сверку

Посмотреть, видит ли система окно `Telegram`:

```bash
python -m app.telegram_wallet_reader.cli list-windows
```

Сфокусировать окно `Telegram`:

```bash
python -m app.telegram_wallet_reader.cli focus
```

Снять скрин окна `Telegram`:

```bash
python -m app.telegram_wallet_reader.cli capture-window
```

Сохранить дерево UI-контролов для дальнейшей автоматизации:

```bash
python -m app.telegram_wallet_reader.cli dump-controls
```

Дождаться нового CSV после ручного экспорта:

```bash
python -m app.telegram_wallet_reader.cli wait-export --downloads-dir C:\Users\stasb\Downloads
```

Полный сценарий: сфокусировать `Telegram`, дождаться вашего ручного экспорта и затем сразу импортировать сделки и запустить сверку:

```bash
python -m app.telegram_wallet_reader.cli manual-export-import --downloads-dir C:\Users\stasb\Downloads --api-base-url http://127.0.0.1:8000
```

## Automatic Sync

Есть отдельный фоновый синхронизатор, который сам:

- находит последнюю выгрузку `Wallet`
- находит последнюю выгрузку `Сбера`
- находит последнюю выгрузку `ОФД`
- импортирует все новое
- запускает матчинг, привязку чеков и классификацию

Запуск:

```bash
python -m app.sync.cli daemon --api-base-url http://127.0.0.1:8000
```

Загрузить выписку `Сбера` за период и сразу запустить сверку:

```bash
python -m app.sync.cli backfill-sber --api-base-url http://127.0.0.1:8000 --date-from 2026-04-01 --date-to 2026-04-30
```

Если нужно использовать только первую страницу ответа за каждый день:

```bash
python -m app.sync.cli backfill-sber --api-base-url http://127.0.0.1:8000 --date-from 2026-04-01 --date-to 2026-04-30 --first-page-only
```

По умолчанию он смотрит в `Downloads`, но это можно переопределить через `.env`:

```env
SYNC_WALLET_DIRECTORY=C:\Users\stasb\Downloads
SYNC_WALLET_PATTERN=p2p-order-history_*.csv
SYNC_SBER_DIRECTORY=C:\Users\stasb\Downloads
SYNC_SBER_PATTERN=sber-*.csv
SYNC_OFD_DIRECTORY=C:\Users\stasb\Downloads
SYNC_OFD_PATTERN=ofd-*.csv
```

Примеры файлов:

- `Wallet`: [docs/sample-wallet-export.json](C:/Users/stasb/Desktop/автоматика ип/docs/sample-wallet-export.json)
- `Сбер`: [docs/sample-sber-export.csv](C:/Users/stasb/Desktop/автоматика ип/docs/sample-sber-export.csv)
- `ОФД`: [docs/sample-ofd-export.csv](C:/Users/stasb/Desktop/автоматика ип/docs/sample-ofd-export.csv)

Сейчас для `Сбера` и `ОФД` сделан гибкий CSV-импорт по нескольким вариантам названий колонок. Если ваши реальные выгрузки отличаются, мы просто подправим alias-списки в адаптерах:

- [app/sync/adapters/sber_csv.py](C:/Users/stasb/Desktop/автоматика ип/app/sync/adapters/sber_csv.py:1)
- [app/sync/adapters/ofd_csv.py](C:/Users/stasb/Desktop/автоматика ип/app/sync/adapters/ofd_csv.py:1)

## Как определяется возвратная сделка

Сделка переводится в `return_detected`, если есть хотя бы один явный признак:

- из `Wallet` пришел статус `refund`, `refunded`, `returned`, `reversed` или `chargeback`
- из `Астрал.ОФД` пришел чек типа `refund`
- из банка пришла исходящая операция с `related_deal_external_id` в `raw_payload`

Сомнительные сделки по 3-м лицам остаются в `third_party_review`, пока не появится явный признак возврата.

## Следующий этап

- подключить реальный `Wallet reader` через RPA
- подключить `СберБизнес API`
- подключить прямой импорт из `Астрал`
- добавить больше правил определения возвратных сделок по третьим лицам
- вынести хранилище вложений и скриншотов

## Sber OAuth

- `GET /sber/status` показывает, настроен ли клиент и есть ли сохраненный токен
- `GET /sber/connect` начинает авторизацию в Сбере
- `GET /sber/callback` принимает callback Сбера
- `POST /sber/refresh` обновляет токен по `refresh_token`

Для автоматического обмена `code -> tokens` нужно заполнить `SBER_CLIENT_SECRET` и `SBER_STATE_SECRET` в `.env`.

## OFD Playwright Reader

Для `Астрал.ОФД` добавлен отдельный reader на `Playwright`, который:

- логинится в кабинет и сохраняет `storage state`
- открывает раздел `Чеки`
- считывает строки из видимой таблицы
- нормализует чеки в формат `/receipts/import`

Установить браузер:

```bash
pip install -e .
python -m playwright install chromium
```

Сохранить авторизованную сессию:

```bash
python -m app.ofd_reader.cli login --storage-state artifacts/ofd/storage-state.json
```

Снять сырые строки таблицы:

```bash
python -m app.ofd_reader.cli scrape --storage-state artifacts/ofd/storage-state.json --output artifacts/ofd/rows.json
```

Сразу импортировать чеки в API:

```bash
python -m app.ofd_reader.cli import --storage-state artifacts/ofd/storage-state.json --api-base-url http://127.0.0.1:8000 --dump-rows artifacts/ofd/rows.json
```

Более надежный режим после ручного входа: брать чеки не из DOM, а из внутреннего web API кабинета по сохраненной сессии.

Сохранить сырые документы из API:

```bash
python -m app.ofd_reader.cli fetch-api --storage-state artifacts/ofd/storage-state.json --date-from 2026-04-05 --date-to 2026-05-05 --output artifacts/ofd/documents.json
```

Сразу импортировать sale/refund чеки в ядро:

```bash
python -m app.ofd_reader.cli import-api --storage-state artifacts/ofd/storage-state.json --api-base-url http://127.0.0.1:8000 --date-from 2026-04-05 --date-to 2026-05-05 --dump-documents artifacts/ofd/documents.json
```

Если верстка кабинета отличается, подправляются только env-настройки:

- `OFD_READER_CHECKS_NAV_SELECTOR`
- `OFD_READER_TABLE_SELECTOR`
- `OFD_READER_NEXT_PAGE_SELECTOR`
- `OFD_READER_USERNAME_SELECTOR`
- `OFD_READER_PASSWORD_SELECTOR`
- `OFD_READER_SUBMIT_SELECTOR`
