import random

import pytest

from routing.services.planner import Candidate, NoFeasibleRoute, plan_fuel_stops


def cand(sid: int, mile: float, price: float) -> Candidate:
    return Candidate(sid, f"S{sid}", "City", "ST", 0.0, 0.0, price, mile)


def greedy(*args, **kwargs):
    """The pure greedy optimum: stop elimination disabled (stop_penalty=0)."""
    return plan_fuel_stops(*args, stop_penalty=0, **kwargs)


def stops_summary(plan):
    """(mile, gallons, cost, arrival fuel) per stop."""
    return [
        (s.candidate.mile_marker, s.gallons_purchased, s.cost, s.fuel_on_arrival_gallons)
        for s in plan.stops
    ]


def test_trip_shorter_than_range_needs_no_stops():
    # 300 mi = 30 gal, covered by the free initial 50 gal -> nothing bought.
    plan = greedy(300, [cand(1, 100, 3.0), cand(2, 200, 2.0)])
    assert plan.stops == ()
    assert (plan.total_gallons_purchased, plan.total_cost) == (0.0, 0.0)


def test_long_trip_buys_at_the_cheap_station_at_mile_450():
    # 1,200 mi; stations every 100 mi plus a cheap one at 450.
    # Start 50 gal free. Go to 100 (first in range), arrive 40.
    # 100 ($3.50): first cheaper within 500 mi is 300 ($3.40), 20 gal away, have 40 -> buy 0.
    # 300: arrive 20. First cheaper is 450 ($3.00), 15 gal away, have 20 -> buy 0.
    # 450: arrive 5. Nothing cheaper within 500 mi (to 950); fill 45 gal * 3.00 = $135.00.
    #   Cheapest in (450, 950] is 900 ($3.45): 45 gal away -> arrive with 5.
    # 900 ($3.45): destination (300 mi = 30 gal) in reach, nothing cheaper -> buy 30 - 5 = 25 gal
    #   * 3.45 = $86.25.
    # Total 70 gal (120 burned - 50 free), $135.00 + $86.25 = $221.25.
    prices = {100: 3.50, 200: 3.60, 300: 3.40, 400: 3.70, 450: 3.00, 500: 3.80, 600: 3.90,
              700: 3.55, 800: 3.65, 900: 3.45, 1000: 3.75, 1100: 3.85}
    cands = [cand(i, mile, price) for i, (mile, price) in enumerate(prices.items())]
    plan = greedy(1200, cands)
    assert stops_summary(plan) == [(450, 45.0, 135.0, 5.0), (900, 25.0, 86.25, 5.0)]
    assert (plan.total_gallons_purchased, plan.total_cost) == (70.0, 221.25)


def test_cheaper_station_just_ahead_means_partial_purchase():
    # 800 mi; A at 400 ($3.50), B at 600 ($3.00)  (the docstring's worked example).
    # Arrive A with 10 gal. B is cheaper and 20 gal away -> buy only 10 gal * 3.50 = $35.00
    # (not the 40 gal the tank could take). Arrive B with 0; buy 20 gal * 3.00 = $60.00.
    # Total 30 gal, $95.00.
    plan = greedy(800, [cand(1, 400, 3.50), cand(2, 600, 3.00)])
    assert stops_summary(plan) == [(400, 10.0, 35.0, 10.0), (600, 20.0, 60.0, 0.0)]
    assert (plan.total_gallons_purchased, plan.total_cost) == (30.0, 95.0)


def test_only_pricier_stations_ahead_means_fill_the_tank():
    # 900 mi; 300 ($3.00), 500 ($3.50), 700 ($4.00). Arrive 300 with 20 gal.
    # Nothing cheaper within 500 mi -> fill 30 gal * 3.00 = $90.00 (tank = 50).
    # Cheapest in range is 500: arrive with 50 - 20 = 30. Destination is 400 mi = 40 gal away
    # and 700 is pricier -> buy 10 gal * 3.50 = $35.00.
    # Total 40 gal (90 burned - 50 free), $125.00.
    plan = greedy(900, [cand(1, 300, 3.00), cand(2, 500, 3.50), cand(3, 700, 4.00)])
    assert stops_summary(plan) == [(300, 30.0, 90.0, 20.0), (500, 10.0, 35.0, 30.0)]
    assert (plan.total_gallons_purchased, plan.total_cost) == (40.0, 125.0)


@pytest.mark.parametrize(
    "total, cands, from_mile, to_mile",
    [
        (1200, [cand(1, 100, 3.0), cand(2, 700, 3.0)], 100, 700),  # 600 mi gap between stations
        (600, [], 0, 600),  # no stations at all
        (700, [cand(1, 100, 3.0)], 100, 700),  # last station too far from the destination
        (1000, [cand(1, 500.1, 3.0)], 0, 500.1),  # first station just out of range
    ],
)
def test_gap_longer_than_range_raises(total, cands, from_mile, to_mile):
    with pytest.raises(NoFeasibleRoute) as exc:
        greedy(total, cands)
    assert exc.value.from_mile == from_mile
    assert exc.value.to_mile == to_mile
    assert exc.value.gap_miles == pytest.approx(to_mile - from_mile)
    assert "gap" in str(exc.value)


def test_trip_of_exactly_500_miles_needs_no_fuel():
    plan = greedy(500, [cand(1, 250, 3.0)])
    assert plan.stops == ()
    assert plan.total_cost == 0.0


def test_trip_of_500_point_1_miles_needs_one_hundredth_of_a_gallon():
    # 500.1 mi = 50.01 gal; start with 50. Go to 250 (arrive 25), need 25.01 -> buy 0.01 gal
    # * 3.00 = $0.03.
    plan = greedy(500.1, [cand(1, 250, 3.0)])
    assert stops_summary(plan) == [(250, 0.01, 0.03, 25.0)]
    # With no station there is no way to cover the extra 0.1 mi.
    with pytest.raises(NoFeasibleRoute):
        greedy(500.1, [])


def test_station_exactly_one_range_away_is_reachable():
    # 1000 mi; station at 500: arrive with 0, buy 50 gal * 3.00 = $150.00.
    plan = greedy(1000, [cand(1, 500, 3.0)])
    assert stops_summary(plan) == [(500, 50.0, 150.0, 0.0)]
    with pytest.raises(NoFeasibleRoute):
        greedy(1000, [cand(1, 500.1, 3.0)])


@pytest.mark.parametrize("order", [(1, 2), (2, 1)])
def test_same_mile_marker_keeps_the_cheaper_station(order):
    # 700 mi; two stations at mile 300: #1 $3.60, #2 $3.20. Arrive with 20 gal;
    # destination is 400 mi = 40 gal away -> buy 20 gal * 3.20 = $64.00 at #2.
    by_id = {1: cand(1, 300, 3.60), 2: cand(2, 300, 3.20)}
    plan = greedy(700, [by_id[i] for i in order])
    assert [s.candidate.station_id for s in plan.stops] == [2]
    assert (plan.total_gallons_purchased, plan.total_cost) == (20.0, 64.0)


def test_candidates_beyond_destination_and_unsorted_input_are_handled():
    # Same as the 800-mile worked example, shuffled, plus a station past the destination.
    cands = [cand(3, 900, 1.0), cand(2, 600, 3.00), cand(1, 400, 3.50)]
    snapshot = list(cands)
    plan = greedy(800, cands)
    assert plan.total_cost == 95.0
    assert cands == snapshot  # input untouched


def test_custom_range_and_mpg():
    # range 200, mpg 20 -> 10 gal tank. 300 mi: start 10 gal (200 mi). Station at 150
    # ($4): arrive with 2.5 gal; need 150/20 = 7.5 gal -> buy 5 gal * 4 = $20.00.
    plan = greedy(300, [cand(1, 150, 4.0)], range_miles=200, mpg=20)
    assert stops_summary(plan) == [(150, 5.0, 20.0, 2.5)]


def test_invalid_arguments():
    with pytest.raises(ValueError):
        greedy(-1, [])
    with pytest.raises(ValueError):
        greedy(100, [], mpg=0)


# --- property tests -------------------------------------------------------------------
TOL = 0.02  # gallons; results are rounded to 2 decimals


def _assert_plan_is_physically_valid(plan, total, cap=50.0, mpg=10.0, rng=500.0):
    pos, prev_fuel_after = 0.0, cap
    for s in plan.stops:
        mile = s.candidate.mile_marker
        assert mile - pos <= rng + 1e-6, "consecutive stops more than one range apart"
        arrival = prev_fuel_after - (mile - pos) / mpg
        assert arrival >= -TOL, "tank went negative"
        assert arrival == pytest.approx(s.fuel_on_arrival_gallons, abs=TOL)
        assert s.gallons_purchased > 0
        prev_fuel_after = s.fuel_on_arrival_gallons + s.gallons_purchased
        assert prev_fuel_after <= cap + TOL, "tank overfilled"
        pos = mile
    assert total - pos <= rng + 1e-6
    assert prev_fuel_after - (total - pos) / mpg >= -TOL, "ran dry before the destination"
    assert plan.total_cost == pytest.approx(sum(s.cost for s in plan.stops), abs=0.01 * len(plan.stops) + 0.01)


def test_random_plans_keep_tank_in_bounds_and_raise_only_on_real_gaps():
    rng = random.Random(1234)
    feasible_cases = 0
    for _ in range(500):
        total = rng.uniform(1, 3000)
        cands = [
            cand(i, round(rng.uniform(-50, total + 100), 1), round(rng.uniform(2.5, 4.5), 3))
            for i in range(rng.randint(0, 80))
        ]
        points = sorted({0.0, total, *(c.mile_marker for c in cands if 0 < c.mile_marker < total)})
        feasible = all(b - a <= 500 + 1e-9 for a, b in zip(points, points[1:]))
        if not feasible:
            with pytest.raises(NoFeasibleRoute):
                greedy(total, cands)
            continue
        feasible_cases += 1
        base = greedy(total, cands)
        _assert_plan_is_physically_valid(base, total)
        for penalty in (2.0, 25.0):
            plan = plan_fuel_stops(total, cands, stop_penalty=penalty)
            _assert_plan_is_physically_valid(plan, total)  # tank in bounds, gaps <= 500
            # Greedy is optimal, so eliminating stops never makes fuel cheaper...
            assert plan.total_cost >= base.total_cost - 0.01 * (len(base.stops) + 1)
            # ...never adds stops, and every elimination paid for itself in penalties.
            assert len(plan.stops) <= len(base.stops)
            saved = len(base.stops) - len(plan.stops)
            assert plan.total_cost - base.total_cost <= penalty * saved + 0.01 * (len(base.stops) + 1)
    assert feasible_cases > 150


def _optimal_cost_cents(total, stations):
    """Exact DP oracle for whole-gallon instances: stations = {mile: price_cents}."""
    points = [0] + sorted(stations) + [total]
    best = {50: 0}
    for i in range(len(points) - 1):
        price = stations.get(points[i])
        burn = (points[i + 1] - points[i]) // 10
        nxt: dict[int, int] = {}
        for fuel, cost in best.items():
            for buy in range(0, (50 - fuel + 1) if price is not None else 1):
                left = fuel + buy - burn
                if left >= 0:
                    total_cost = cost + buy * (price or 0)
                    if total_cost < nxt.get(left, 1 << 60):
                        nxt[left] = total_cost
        best = nxt
    return min(best.values()) if best else None


def test_greedy_matches_exact_dp_optimum():
    rng = random.Random(99)
    compared = 0
    for _ in range(300):
        total = rng.randint(1, 120) * 10
        stations = {m * 10: rng.randint(250, 450) for m in range(1, total // 10) if rng.random() < 0.3}
        optimum = _optimal_cost_cents(total, stations)
        cands = [cand(m, m, cents / 100) for m, cents in stations.items()]
        if optimum is None:
            with pytest.raises(NoFeasibleRoute):
                greedy(total, cands)
            continue
        compared += 1
        assert greedy(total, cands).total_cost == pytest.approx(optimum / 100, abs=0.006)
        # With elimination the cost can only rise, by less than the penalty per stop dropped.
        assert plan_fuel_stops(total, cands).total_cost >= optimum / 100 - 0.006
    assert compared > 100


# --- micro-stop elimination -------------------------------------------------------------
def micro_stop_pool():
    # 1100 mi. P at 450 ($3.00), Q at 468 ($3.10), R at 900 ($3.30).
    return [cand(1, 450, 3.00), cand(2, 468, 3.10), cand(3, 900, 3.30)]


def test_pure_greedy_makes_the_micro_stop():
    # Start 50 gal -> P: arrive 5. Nothing cheaper within 500 mi: fill 45 gal * 3.00 = $135.00.
    # Q (18 mi on): arrive 48.2; R is pricier and the destination is out of reach, so the greedy
    # tops up 1.8 gal * 3.10 = $5.58. R: 43.2 gal burned -> arrive 6.8; destination 200 mi = 20 gal
    # -> buy 13.2 gal * 3.30 = $43.56.  Total 60 gal, $184.14.
    plan = greedy(1100, micro_stop_pool())
    assert stops_summary(plan) == [
        (450, 45.0, 135.0, 5.0), (468, 1.8, 5.58, 48.2), (900, 13.2, 43.56, 6.8)
    ]
    assert (plan.total_gallons_purchased, plan.total_cost) == (60.0, 184.14)


def test_micro_top_up_is_eliminated_by_default_penalty():
    # Without Q: P fill 45 gal = $135.00, go straight to R (450 mi): arrive 5.0, then
    # 20 - 5 = 15 gal * 3.30 = $49.50. Total 60 gal, $184.50. Removing Q saves one stop for
    # $0.36 extra fuel (< $2.00), so it goes. P and R cannot be removed (infeasible).
    plan = plan_fuel_stops(1100, micro_stop_pool())  # default penalty $2.00
    assert stops_summary(plan) == [(450, 45.0, 135.0, 5.0), (900, 15.0, 49.5, 5.0)]
    # Reported cost is the real fuel cost: no penalty is ever added.
    assert (plan.total_gallons_purchased, plan.total_cost) == (60.0, 184.5)


def test_small_penalty_keeps_the_stop_that_saves_more_than_it():
    # Removing Q costs $0.36 extra fuel, so a $0.30 penalty keeps it and $0.40 removes it.
    assert len(plan_fuel_stops(1100, micro_stop_pool(), stop_penalty=0.30).stops) == 3
    assert len(plan_fuel_stops(1100, micro_stop_pool(), stop_penalty=0.40).stops) == 2


def test_stop_saving_more_than_the_penalty_is_kept():
    # 1300 mi. P 450 ($3.00), Q 700 ($3.10), S 800 ($3.60), R 1100 ($3.50).
    # Greedy: P fill 45 gal = $135.00; Q (cheapest in range) arrive 25 -> fill 25 gal * 3.10 =
    # $77.50; R (cheapest in range) arrive 10 -> 10 gal * 3.50 = $35.00. Total $247.50.
    # Without Q the plan must go P -> S -> R: 135 + 15*3.60 + 20*3.50 = $259.00, i.e. Q saves
    # $11.50, well over the $2.00 penalty, so it stays.
    pool = [cand(1, 450, 3.00), cand(2, 700, 3.10), cand(3, 800, 3.60), cand(4, 1100, 3.50)]
    plan = plan_fuel_stops(1300, pool)
    assert [s.candidate.station_id for s in plan.stops] == [1, 2, 4]
    assert stops_summary(plan) == [(450, 45.0, 135.0, 5.0), (700, 25.0, 77.5, 25.0), (1100, 10.0, 35.0, 10.0)]
    assert plan.total_cost == 247.5


def test_replacing_a_stop_with_another_is_not_an_elimination():
    # Same pool: removing R makes the greedy use S instead -- also 3 stops, for $248.50 (+$1.00,
    # under the $2.00 penalty). That saves no stop, so it must not be adopted.
    pool = [cand(1, 450, 3.00), cand(2, 700, 3.10), cand(3, 800, 3.60), cand(4, 1100, 3.50)]
    assert plan_fuel_stops(1300, pool, stop_penalty=2.0).total_cost == 247.5
    assert greedy(1300, pool[:3]).total_cost == 248.5  # what the swap would have cost


def test_infeasible_removals_are_never_applied_even_with_a_huge_penalty():
    # Only P, Q, R exist for the 1300 mi trip above: dropping any one of them leaves a gap
    # over 500 miles, so all three stay no matter how high the penalty is.
    pool = [cand(1, 450, 3.00), cand(2, 700, 3.10), cand(4, 1100, 3.50)]
    plan = plan_fuel_stops(1300, pool, stop_penalty=1_000_000)
    assert [s.candidate.station_id for s in plan.stops] == [1, 2, 4]
    assert plan.total_cost == 247.5


def test_elimination_does_not_mutate_the_input_pool():
    pool = micro_stop_pool()
    snapshot = list(pool)
    plan_fuel_stops(1100, pool, stop_penalty=50)
    assert pool == snapshot


def test_zero_penalty_matches_pure_greedy_and_negative_is_rejected():
    assert plan_fuel_stops(1100, micro_stop_pool(), stop_penalty=0) == greedy(1100, micro_stop_pool())
    with pytest.raises(ValueError):
        plan_fuel_stops(100, [], stop_penalty=-1)
