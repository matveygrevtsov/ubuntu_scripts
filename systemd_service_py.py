#!/usr/bin/env python3
"""systemd_service_py - минимальный HTTP-сервис, рассчитанный на запуск под systemd.

Эндпоинты:
    GET /health-check   -> 200 + JSON с состоянием сервиса
    HEAD /health-check  -> те же заголовки, без тела
    Остальные пути      -> 404 (JSON), остальные методы -> 405 (JSON)

Зависимости: только стандартная библиотека Python 3.8+ (ничего не нужно ставить через pip).

Настройки через переменные окружения (удобнее всего задавать их в unit-файле):
    SYSTEMD_SERVICE_PY_HOST       - адрес прослушивания (по умолчанию 0.0.0.0)
    SYSTEMD_SERVICE_PY_PORT       - порт (по умолчанию 8000)
    SYSTEMD_SERVICE_PY_LOG_LEVEL  - уровень логов (по умолчанию INFO)

Логи пишутся в stdout/stderr, поэтому systemd/journald собирает их автоматически
(см. `journalctl -u systemd_service_py`).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

SERVICE_NAME = "systemd_service_py"
SERVICE_VERSION = "1.0.0"

DEFAULT_HOST = "177.203.58.26"
DEFAULT_PORT = 8080
DEFAULT_LOG_LEVEL = "INFO"

HEALTH_PATH = "/health-check"

# Время старта процесса: нужно для расчёта uptime в /health-check.
STARTED_AT = time.time()

logger = logging.getLogger(SERVICE_NAME)


def _iso_utc(timestamp: float) -> str:
    """Время в формате ISO 8601 (UTC, с суффиксом Z)."""
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def build_health_payload() -> Dict[str, Any]:
    """Тело ответа /health-check. Сервис stateless, поэтому статус всегда "ok"."""
    now = time.time()
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "started_at": _iso_utc(STARTED_AT),
        "uptime_seconds": round(now - STARTED_AT, 3),
        "timestamp": _iso_utc(now),
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "python": sys.version.split()[0],
    }


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Обработчик запросов: единственный рабочий эндпоинт - /health-check."""

    server_version = f"{SERVICE_NAME}/{SERVICE_VERSION}"
    sys_version = ""  # не раскрываем версию Python в заголовке Server
    # Content-Length в ответах всегда есть, поэтому keep-alive безопасен.
    protocol_version = "HTTP/1.1"

    # --- маршрутизация ---------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - имя задано базовым классом
        self._handle_request(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle_request(send_body=False)

    def _method_not_allowed(self) -> None:
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {
                "status": "error",
                "error": "method_not_allowed",
                "message": f"Метод {self.command} не поддерживается, используйте GET или HEAD",
            },
            extra_headers={"Allow": "GET, HEAD"},
        )

    # Остальные методы отвечают 405 вместо стандартного 501.
    do_POST = _method_not_allowed  # noqa: N815
    do_PUT = _method_not_allowed  # noqa: N815
    do_PATCH = _method_not_allowed  # noqa: N815
    do_DELETE = _method_not_allowed  # noqa: N815
    do_OPTIONS = _method_not_allowed  # noqa: N815

    def _handle_request(self, send_body: bool) -> None:
        path = urlsplit(self.path).path
        if len(path) > 1:  # "/health-check/" считаем тем же эндпоинтом
            path = path.rstrip("/")

        if path == HEALTH_PATH:
            self._send_json(HTTPStatus.OK, build_health_payload(), send_body=send_body)
            return

        self._send_json(
            HTTPStatus.NOT_FOUND,
            {
                "status": "error",
                "error": "not_found",
                "message": f"Неизвестный путь: {path}. Доступен только {HEALTH_PATH}",
            },
            send_body=send_body,
        )

    # --- helpers ---------------------------------------------------------

    def _send_json(
        self,
        status: HTTPStatus,
        payload: Dict[str, Any],
        send_body: bool = True,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"

        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            # Тело ответа на HEAD по HTTP отправлять нельзя.
            if send_body and self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Клиент закрыл соединение раньше времени - это не ошибка сервиса.
            logger.debug("Клиент %s разорвал соединение", self.client_address[0])

    def log_message(self, fmt: str, *args: Any) -> None:
        """Access-лог идёт в общий logger (-> journald), а не в stderr напрямую."""
        logger.info("%s %s", self.address_string(), fmt % args)


def _env_int(name: str, default: int) -> int:
    """Читает целое число из переменной окружения (с понятной ошибкой)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"Переменная окружения {name} должна быть целым числом, получено: {raw!r}")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("SYSTEMD_SERVICE_PY_LOG_LEVEL", DEFAULT_LOG_LEVEL).upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )

    host = os.environ.get("SYSTEMD_SERVICE_PY_HOST", DEFAULT_HOST)
    port = _env_int("SYSTEMD_SERVICE_PY_PORT", DEFAULT_PORT)

    try:
        server = ThreadingHTTPServer((host, port), HealthCheckHandler)
    except OSError as exc:
        logger.error("Не удалось занять %s:%d: %s", host, port, exc)
        return 1

    # Потоки-обработчики не должны мешать остановке процесса.
    server.daemon_threads = True

    def shutdown(_signum: int, _frame: Any) -> None:
        logger.info("Получен сигнал остановки, завершаем работу")
        # server.shutdown() блокируется до выхода из serve_forever(), поэтому
        # вызываем его из отдельного потока, а не из обработчика сигнала.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info(
        "Сервис %s запущен на http://%s:%d (проверка: %s)",
        SERVICE_NAME,
        host,
        port,
        HEALTH_PATH,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        logger.info("Сервис %s остановлен", SERVICE_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())

