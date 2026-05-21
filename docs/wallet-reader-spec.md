# Wallet Reader Spec

## Цель

`Wallet reader` работает только в режиме `read-only`. Он не подтверждает сделки и не отправляет команды в `Wallet`. Его задача:

- открыть `Telegram -> Wallet -> P2P`
- собрать список сделок
- вытащить ключевые поля
- сохранить скриншот или ссылку на артефакт
- отправить batch в `POST /wallet/deals/import`

## Минимальный набор полей

Для каждой сделки reader должен передавать:

- `external_id`
- `expected_fiat_amount`
- `fiat_currency`
- `counterparty_name`
- `counterparty_wallet_id`
- `payment_method`
- `opened_at`
- `completed_at`
- `status`
- `notes`
- `raw_payload`

## Рекомендации по raw_payload

В `raw_payload` стоит сохранить:

- исходный статус в интерфейсе Wallet
- текст карточки сделки
- ссылку на локальный скриншот
- сумму в криптовалюте
- курс
- банк/реквизиты
- отметку `reader_session_id`
- время считывания

## Карта статусов

Статусы reader желательно приводить к значениям сервиса:

- `new`, `active` -> `awaiting_payment`
- `paid` -> `awaiting_payment`
- `completed` -> `matched`
- `cancelled` -> `canceled`
- `disputed` -> `third_party_review`
- `refund`, `refunded`, `returned`, `reversed` -> `return_detected`

Если reader не уверен в трактовке статуса, лучше отправлять `detected`.

## Batch запрос

```json
{
  "deals": [
    {
      "external_id": "wallet-123",
      "expected_fiat_amount": "15000.00",
      "fiat_currency": "RUB",
      "counterparty_name": "Ivan Petrov",
      "counterparty_wallet_id": "u_7788",
      "payment_method": "Sberbank",
      "opened_at": "2026-04-29T09:15:00Z",
      "completed_at": null,
      "status": "awaiting_payment",
      "notes": "Imported from Wallet UI",
      "raw_payload": {
        "ui_status": "paid",
        "crypto_amount": "145.34",
        "crypto_asset": "USDT",
        "rate": "103.20",
        "screenshot_path": "artifacts/2026-04-29/wallet-123.png",
        "reader_session_id": "session-001",
        "captured_at": "2026-04-29T09:18:42Z"
      }
    }
  ]
}
```

## Следующий технический шаг

Собрать отдельный модуль `reader`, который:

1. запускает официальный Telegram-клиент или Android-эмулятор
2. открывает `Wallet`
3. считывает список сделок
4. нормализует поля в формат API
5. постит их в этот сервис
