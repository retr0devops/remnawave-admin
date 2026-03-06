# План развития Remnawave Admin

## 1. Технический долг

### 1.1 Очистка мёртвого кода
- [ ] `src/handlers/basic.py` — 583 строки, 400+ строк комментариев, один реальный обработчик `cb_input_skip`. Вычистить или удалить.
- [ ] `src/services/collector.py` — устаревший. Актуальный коллектор в `web/backend/api/v2/collector.py`.
- [ ] `basic_router` в `src/handlers/__init__.py` — помечен как "временно" для обратной совместимости.

### 1.2 Покрытие тестами
- [x] `billing.py` — CRUD providers, history, nodes, summary + RBAC
- [x] `backup.py` — list, log, create, download, delete, restore, import + RBAC
- [x] `api_keys.py` — status, list, scopes, create, update, delete + validation + RBAC
- [x] `webhooks.py` — CRUD + dispatch_webhook_event + HMAC + RBAC
- [x] `fleet.py` — agents list, command-log + RBAC
- [x] `scripts.py` — CRUD, exec, import, helpers + RBAC
- [ ] `collector.py` — batch endpoint, violation detection pipeline
- [ ] `advanced_analytics.py` — агрегации, тренды
- [ ] `reports.py` — генерация отчётов
- [ ] `mailserver.py` — SMTP endpoints

### 1.3 E2E тесты (Playwright)
- [ ] Login → Dashboard flow
- [ ] Users → UserDetail navigation
- [ ] Violations → resolve flow
- [ ] Settings → RBAC roles

## 2. Функциональные улучшения

### 2.1 API v3 — расширение публичного API
Сейчас: `users:read`, `nodes:read`, `stats:read`.
- [ ] `users:write` — создание/удаление/редактирование пользователей
- [ ] `hosts:read` / `hosts:write` — управление хостами
- [ ] `violations:read` — доступ к нарушениям
- [ ] Документация Swagger для v3

### 2.2 Экспорт/импорт конфигурации между инстансами
- [ ] Экспорт: роли, автоматизации, пороги нарушений, настройки
- [ ] Импорт: валидация совместимости, merge/overwrite стратегии

### 2.3 Webhook-система — расширение событий
Сейчас 7 событий. Добавить:
- [ ] `user.blocked` — при блокировке через violations
- [ ] `node.metrics` — периодическая отправка метрик
- [ ] `automation.triggered` — при срабатывании автоматизации
- [ ] Retry с экспоненциальным backoff при неудачах

### 2.4 Dashboard — кастомизация виджетов
- [ ] Drag-and-drop расположение виджетов
- [ ] Выбор видимых виджетов
- [ ] Сохранение layout в настройках пользователя

### 2.5 Уведомления — дополнительные каналы
Сейчас: Telegram + in-app.
- [ ] Discord webhook
- [ ] Email (SMTP-инфраструктура уже есть)
- [ ] Настройка каналов per-event
