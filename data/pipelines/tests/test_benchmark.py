from whyuseguide_pipelines.benchmark import Spot, Summary, metrics, sample_pairs


def test_metrics() -> None:
    results = [
        (Summary(30, 1, "r1"), Summary(28, 1, "r1")),  # +2
        (Summary(20, 0, "r2"), Summary(30, 1, "r3")),  # −10
        (Summary(15, 0, None), Summary(14, 0, None)),  # +1, 걷기만
        (None, Summary(10, 0, "r1")),
        (Summary(10, 0, "r1"), None),
    ]
    m = metrics(results)
    assert (m.pairs, m.failed_ours, m.failed_ref, m.compared) == (5, 1, 1, 3)
    assert m.median_abs_diff_min == 2
    assert m.within_5_min_pct == 66.7
    assert m.mean_diff_min == -2.3
    assert m.transfers_match_pct == 66.7
    assert m.first_route_match_pct == 50.0  # 노선이 있는 2건 중 1건


def test_metrics_empty() -> None:
    m = metrics([])
    assert m.compared == 0 and m.median_abs_diff_min is None


def spots() -> list[Spot]:
    return [Spot(f"s{i}", f"s{i}", 35.65 + 0.01 * (i % 7), 139.65 + 0.015 * (i // 7), ()) for i in range(40)]


def test_sample_pairs_is_fixed_by_seed_and_respects_distance() -> None:
    a = sample_pairs(spots(), 30, seed=1)
    b = sample_pairs(list(reversed(spots())), 30, seed=1)
    assert a == b  # 입력 순서와 무관하게 같은 표본
    assert len(a) == 30
    assert len({(x.id, y.id) for x, y in a}) == 30
    assert sample_pairs(spots(), 30, seed=2) != a
