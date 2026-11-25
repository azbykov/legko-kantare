"""Доменные ошибки сервиса (не Keitaro)."""


class ServiceError(Exception):
    status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class OfferAlreadyInStream(ServiceError):
    status = 409


class OfferNotInStream(ServiceError):
    status = 404


class StreamNotEditable(ServiceError):
    status = 409


class LastOfferInStream(ServiceError):
    status = 409


class TooManyOffers(ServiceError):
    status = 409


class StaleStream(ServiceError):
    """Поток изменился с момента, когда его показали пользователю."""

    status = 409


class ForeignCampaign(ServiceError):
    status = 403


class InvalidName(ServiceError):
    status = 422


class StreamNotInCampaign(ServiceError):
    status = 404


class BindingNotFound(ServiceError):
    """Домен/группа/источник из конфига не найдены в Keitaro — ошибка настройки сервиса."""

    status = 500


class CampaignNotCreated(ServiceError):
    """Сбой после создания кампании; кампания в Keitaro удалена."""

    status = 500


class RollbackFailed(ServiceError):
    """Сбой после создания кампании, и удалить её не удалось — в Keitaro осталась недосозданная кампания."""

    status = 502
