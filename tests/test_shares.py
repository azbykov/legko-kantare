import pytest

from app.shares import add_share, rebalance


@pytest.mark.parametrize("start", [[], [100], [50, 50], [34, 33, 33], [70, 20, 10], [1] * 7])
def test_add_always_sums_to_100(start):
    assert sum(add_share(start)) == 100


@pytest.mark.parametrize("start", [[100], [50, 50], [34, 33, 33], [70, 20, 10]])
def test_rebalance_sums_to_100(start):
    assert sum(rebalance(start[:-1] or start)) == 100


def test_add_first_gets_100():
    assert add_share([]) == [100]


def test_rebalance_keeps_proportions():
    assert rebalance([50, 25]) == [67, 33]


def test_add_keeps_ratio_70_30():
    assert add_share([70, 30]) == [47, 20, 33]


def test_no_share_below_one_percent():
    # раньше: [50, 1×50] + новый → одна доля становилась 0%
    result = add_share([50] + [1] * 50)
    assert min(result) >= 1 and sum(result) == 100


@pytest.mark.parametrize("seed", range(200))
def test_random_streams_keep_invariants(seed):
    import random

    rnd = random.Random(seed)
    n = rnd.randint(1, 99)
    shares = rebalance([rnd.randint(1, 1000) for _ in range(n)])
    assert min(shares) >= 1 and sum(shares) == 100
    added = add_share(shares)
    assert min(added) >= 1 and sum(added) == 100 and len(added) == n + 1
    removed = rebalance(added[: rnd.randint(1, n)])
    assert min(removed) >= 1 and sum(removed) == 100


def test_up_to_100_offers_then_rejected():
    from app.errors import TooManyOffers

    assert add_share([1] * 99) == [1] * 100
    with pytest.raises(TooManyOffers):
        add_share([1] * 100)
