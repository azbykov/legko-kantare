"""Ошибка обращения к Keitaro: HTTP-статус и тело ответа (или описание сетевой проблемы)."""

from typing import Any


class KeitaroError(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(f"Keitaro {status}: {detail}")
        self.status = status
        self.detail = detail
