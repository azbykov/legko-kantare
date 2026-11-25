"""Расчёт долей офферов.

Инварианты: сумма активных долей — ровно 100, каждая доля — не меньше 1%,
соотношение между существующими офферами сохраняется (с точностью до округления).
Отсюда предел: не больше 100 активных офферов в потоке.
"""

from app.errors import TooManyOffers

TOTAL = 100
MAX_OFFERS = TOTAL  # при минимуме 1% больше не поместится


def _allocate(weights: list[float], total: int) -> list[int]:
    """Целые доли пропорционально весам: сумма = total, каждая ≥ 1 (метод наибольшего остатка)."""
    n = len(weights)
    if not n:
        return []
    if n > total:
        raise TooManyOffers(f"В потоке может быть не больше {MAX_OFFERS} активных офферов (минимальная доля — 1%)")
    s = sum(weights)
    if s <= 0:
        weights, s = [1.0] * n, float(n)
    raw = [w * total / s for w in weights]
    out = [max(1, int(x)) for x in raw]
    # не хватает до total — добавляем тем, у кого больше дробный остаток
    for i in sorted(range(n), key=lambda i: raw[i] - out[i], reverse=True)[: max(0, total - sum(out))]:
        out[i] += 1
    # перебор (из-за подъёма до 1%) — снимаем с самых «переплаченных», не опуская ниже 1
    while sum(out) > total:
        i = max((i for i in range(n) if out[i] > 1), key=lambda i: out[i] - raw[i])
        out[i] -= 1
    return out


def rebalance(shares: list[int]) -> list[int]:
    """Привести доли к сумме 100, сохранив пропорции."""
    return _allocate([float(x) for x in shares], TOTAL)


def add_share(shares: list[int]) -> list[int]:
    """Новый оффер получает равную долю (100/(n+1)), остальные ужимаются пропорционально.

    Новый оффер — последний элемент результата.
    """
    n = len(shares) + 1
    if n > MAX_OFFERS:
        raise TooManyOffers(f"В потоке может быть не больше {MAX_OFFERS} активных офферов (минимальная доля — 1%)")
    if not shares:
        return [TOTAL]
    new = max(1, round(TOTAL / n))
    return [*_allocate([float(x) for x in shares], TOTAL - new), new]
