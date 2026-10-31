# systemd_service_py

Простейший Python-сервис (один файл, только стандартная библиотека) + unit-файл для
systemd в Ubuntu.

Единственный эндпоинт: `GET /health-check` — возвращает `200 OK` и JSON с состоянием сервиса.

## Состав

| Файл | Назначение |
|------|------------|
| `systemd_service_py.py` | сам HTTP-сервер (stdlib, Python 3.8+) |
| `systemd_service_py.service` | unit-файл systemd |

Никаких зависимостей ставить не нужно: используется `http.server.ThreadingHTTPServer`,
поэтому `pip install`/`venv` не требуются.

## Параметры (переменные окружения)

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `SYSTEMD_SERVICE_PY_HOST` | `0.0.0.0` | адрес прослушивания |
| `SYSTEMD_SERVICE_PY_PORT` | `8000` | порт |
| `SYSTEMD_SERVICE_PY_LOG_LEVEL` | `INFO` | уровень логов |

Значения по умолчанию уже прописаны в unit-файле в директивах `Environment=` —
меняйте их там.

## Установка на Ubuntu

```bash
# 1. Каталог приложения и сам скрипт
sudo install -d -m 0755 /opt/systemd_service_py
sudo install -m 0755 systemd_service_py.py /opt/systemd_service_py/systemd_service_py.py

# 2. Unit-файл
sudo install -m 0644 systemd_service_py.service /etc/systemd/system/systemd_service_py.service

# 3. Перечитать юниты, включить автозапуск и запустить
sudo systemctl daemon-reload
sudo systemctl enable --now systemd_service_py.service
```

Пользователя создавать не нужно: в юните стоит `DynamicUser=yes`, systemd сам
выделяет одноразового пользователя. Если нужен постоянный пользователь — в юните
закомментируйте `DynamicUser` и раскомментируйте `User=`/`Group=`.

Если Ubuntu с включённым AppArmor и вы поменяете каталог установки, не забудьте
обновить путь в `ExecStart=`. (Своего AppArmor-профиля у сервиса нет — достаточно
поменять каталог установки в `ExecStart=`/`WorkingDirectory=`.)

## Проверка

```bash
# статус юнита
systemctl status systemd_service_py.service

# эндпоинт
curl -i http://127.0.0.1:8000/health-check
```

Пример ответа:

```json
{
  "status": "ok",
  "service": "systemd_service_py",
  "version": "1.0.0",
  "started_at": "2026-10-30T10:55:12Z",
  "uptime_seconds": 12.481,
  "timestamp": "2026-10-30T10:55:24Z",
  "pid": 12345,
  "hostname": "ubuntu",
  "python": "3.12.3"
}
```

Поведение остальных запросов:

| Запрос | Ответ |
|--------|-------|
| `GET /health-check` (и `/health-check/`) | `200` + JSON |
| `HEAD /health-check` | `200`, заголовки без тела |
| `GET /unknown` | `404` + JSON |
| `POST /health-check` и др. | `405` + заголовок `Allow: GET, HEAD` |

Код возврата можно использовать в мониторинге: `curl -fsS http://127.0.0.1:8000/health-check >/dev/null`

## Логи и управление

```bash
journalctl -u systemd_service_py -f      # логи в реальном времени
sudo systemctl restart systemd_service_py
sudo systemctl stop systemd_service_py
sudo systemctl disable systemd_service_py
```

## Локальный запуск без systemd (для отладки)

```bash
SYSTEMD_SERVICE_PY_PORT=8010 python3 systemd_service_py.py
curl http://127.0.0.1:8010/health-check
```

Остановка — `Ctrl+C` (обрабатывается `SIGINT`); под systemd сервис корректно
завершается по `SIGTERM`.

## Замечания

- Сервис stateless, `Type=exec`: systemd присылает `SIGTERM`, сервер закрывает
  сокет и выходит с кодом 0 (`TimeoutStopSec=10s` — страховка).
- В юните включены базовые ограничения прав (`ProtectSystem=strict`,
  `NoNewPrivileges`, `SystemCallFilter=@system-service` и т. д.). Если сервис
  начнёт падать с `status=226/NAMESPACE` или `EPERM`, закомментируйте блок
  «Ограничение прав» и перезапустите юнит.
- Порт `8000` не требует root. Если нужен порт < 1024, добавьте в `[Service]`
  `AmbientCapabilities=CAP_NET_BIND_SERVICE`.
- Слушается IPv4 (`0.0.0.0`). Для IPv6 нужен отдельный сокет — при
  необходимости задайте `SYSTEMD_SERVICE_PY_HOST=::` (тогда `ThreadingHTTPServer`
  всё равно создаст IPv4-сокет, для полноценного dual-stack потребуется
  `address_family = socket.AF_INET6` в подклассе сервера).
