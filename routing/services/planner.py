"""Fuel-stop planner: pure functions, no Django, no network, no DB.

Classic greedy gas-station algorithm. The tank holds ``range_miles / mpg`` gallons
(50 by default) and starts FULL; that initial fuel is free. At every stop we either

* buy just enough to reach the first station that is cheaper than here, or
* if nothing cheaper is within a full tank, fill up and drive to the cheapest
  station in range.

The destination counts as a free "station", so the last stop buys only what is
needed to arrive. Stops where nothing is bought are not reported.

Worked example: 800 miles, 10 mpg, 50 gal tank, stations A (mile 400, $3.50/gal)
and B (mile 600, $3.00/gal).

1. Start with 50 gal (free). Destination is 800 mi away, more than the 500 mi range,
   so drive to the first station in range, A. Arrive with 50 - 40 = 10 gal.
2. At A the destination (800) is within a full tank (900) and B is cheaper.
   B is 200 mi away = 20 gal, we hold 10, so buy 10 gal * $3.50 = $35.00.
   Arrive at B with 10 + 10 - 20 = 0 gal.
3. At B nothing is cheaper; the destination is 200 mi = 20 gal away, so buy
   20 gal * $3.00 = $60.00 and drive to the end.
Total: 30 gal, $95.00 (= 80 gal burned - 50 gal free).

Micro-stop elimination: the greedy happily buys 1.8 gal at a station 18 miles after a
full fill to save a few cents. ``stop_penalty`` (dollars) is a decision rule only: a stop
is dropped when re-planning without it (feasibly) raises the fuel cost by less than
``stop_penalty`` per stop saved. Each round tries dropping one stop; only if none
qualifies does it try dropping two neighbouring stops together (a single removal often
just swaps in another micro-stop). The penalty is never added to the reported cost.
``stop_penalty=0`` is the pure greedy optimum.

Stop elimination is a heuristic. It is a local search around the greedy plan (remove one
stop, else two neighbouring stops, re-plan without them), not an exact optimizer of
``fuel cost + stop_penalty * stops``. It can miss a better plan that needs a substitute
station excluded as well: on the real Chicago -> Los Angeles test route an exact solver
finds a 6-stop plan costing $1.28 more than the 7-stop plan returned here. What it
guarantees is that every elimination it makes saved at least one stop for less than
``stop_penalty`` per stop, that plans stay feasible, and that reported totals are real
fuel costs.

Rounding to 2 decimals happens only when the result is built, so the plan total
can differ by a cent from the sum of the rounded per-stop costs.
"""
from bisect import bisect_right
from dataclasses import dataclass
from typing import Sequence

_EPS = 1e-9
DEFAULT_STOP_PENALTY = 2.0  # dollars; trip.py passes settings.FUEL_STOP_PENALTY explicitly


class NoFeasibleRoute(Exception):
    """Raised when two consecutive fuel points are farther apart than the range."""

    def __init__(self, from_mile: float, to_mile: float, range_miles: float) -> None:
        self.from_mile = from_mile
        self.to_mile = to_mile
        self.gap_miles = to_mile - from_mile
        self.range_miles = range_miles
        super().__init__(
            f"No feasible route: nothing reachable within {range_miles:g} miles of mile "
            f"{from_mile:.1f}; the next fuel stop or destination is at mile {to_mile:.1f} "
            f"(gap of {self.gap_miles:.1f} miles)."
        )


@dataclass(frozen=True)
class Candidate:
    """A fuel station projected onto the route."""

    station_id: int
    name: str
    city: str
    state: str
    lat: float
    lng: float
    price: float
    mile_marker: float


@dataclass(frozen=True)
class Stop:
    """A station where fuel is bought."""

    candidate: Candidate
    gallons_purchased: float
    cost: float
    fuel_on_arrival_gallons: float


@dataclass(frozen=True)
class Plan:
    """Result of planning: where to buy fuel and how much it costs in total."""

    stops: tuple[Stop, ...]
    total_gallons_purchased: float
    total_cost: float


def _usable_candidates(candidates: Sequence[Candidate], total_miles: float) -> list[Candidate]:
    """Candidates strictly between start and destination, sorted, cheapest per mile marker."""
    inside = sorted(
        (c for c in candidates if 0 < c.mile_marker < total_miles),
        key=lambda c: c.mile_marker,
    )
    result: list[Candidate] = []
    for cand in inside:
        if result and result[-1].mile_marker == cand.mile_marker:
            if cand.price < result[-1].price:
                result[-1] = cand
        else:
            result.append(cand)
    return result


_Purchase = tuple[Candidate, float, float]  # (station, gallons bought, fuel on arrival)


def _greedy(total_miles: float, cands: list[Candidate], range_miles: float, mpg: float) -> list[_Purchase]:
    """Pure greedy plan over a usable, sorted, de-duplicated pool. Raises NoFeasibleRoute."""
    capacity = range_miles / mpg
    markers = [c.mile_marker for c in cands]

    purchases: list[tuple[Candidate, float, float]] = []  # (station, gallons, arrival fuel)
    pos, fuel = 0.0, capacity
    here: Candidate | None = None
    arrival = fuel

    while total_miles - pos > fuel * mpg + _EPS:
        window = cands[bisect_right(markers, pos) : bisect_right(markers, pos + range_miles + _EPS)]
        dest_in_reach = total_miles - pos <= range_miles + _EPS
        here_price = here.price if here else float("inf")
        cheaper = next((c for c in window if c.price < here_price), None)

        fill = False
        if cheaper is not None:
            nxt: Candidate | None = cheaper
        elif dest_in_reach:
            nxt = None  # the destination itself
        elif window:
            nxt, fill = min(window, key=lambda c: c.price), True
        else:
            upcoming = bisect_right(markers, pos)
            next_mile = markers[upcoming] if upcoming < len(markers) else total_miles
            raise NoFeasibleRoute(pos, next_mile, range_miles)

        target_mile = nxt.mile_marker if nxt else total_miles
        distance = target_mile - pos
        if here is not None:
            wanted = capacity - fuel if fill else distance / mpg - fuel
            gallons = min(max(wanted, 0.0), capacity - fuel)
            if gallons > _EPS:
                purchases.append((here, gallons, arrival))
                fuel += gallons
        fuel = max(0.0, fuel - distance / mpg)
        if nxt is None:
            break
        pos, here, arrival = nxt.mile_marker, nxt, fuel

    return purchases


def _fuel_cost(purchases: list[_Purchase]) -> float:
    return sum(gallons * station.price for station, gallons, _ in purchases)


def _best_removal(
    total_miles: float,
    pool: list[Candidate],
    purchases: list[_Purchase],
    removals: list[tuple[Candidate, ...]],
    stop_penalty: float,
    range_miles: float,
    mpg: float,
) -> tuple[tuple[Candidate, ...], list[_Purchase]] | None:
    """The qualifying removal with the smallest cost increase, with its re-planned purchases.

    A removal qualifies when re-planning without those stations is feasible and
    ``cost increase < stop_penalty * stops saved``. Requiring a saved stop stops a removal
    from being "paid for" by the greedy simply picking a different station instead.
    """
    cost = _fuel_cost(purchases)
    best: tuple[float, tuple[Candidate, ...], list[_Purchase]] | None = None
    for removal in removals:
        try:
            trial = _greedy(total_miles, [c for c in pool if not any(c is r for r in removal)], range_miles, mpg)
        except NoFeasibleRoute:
            continue
        increase = _fuel_cost(trial) - cost
        stops_saved = len(purchases) - len(trial)
        if increase < stop_penalty * stops_saved - _EPS and (best is None or increase < best[0]):
            best = (increase, removal, trial)
    return None if best is None else (best[1], best[2])


def _eliminate_stops(
    total_miles: float,
    pool: list[Candidate],
    purchases: list[_Purchase],
    stop_penalty: float,
    range_miles: float,
    mpg: float,
) -> list[_Purchase]:
    """Backward elimination of stops that do not save at least ``stop_penalty`` each.

    Each round first tries dropping one stop (re-planning without its station). Only when
    no single removal qualifies does it try dropping two neighbouring stops together: a
    single removal often just makes the greedy swap in another micro-stop, while dropping
    both lets one station absorb the fuel. The qualifying removal with the smallest cost
    increase is adopted and its stations stay out of the pool; rounds repeat until none applies.
    """
    pool = list(pool)
    while purchases:
        stops = [station for station, _, _ in purchases]
        best = None
        for removals in ([(s,) for s in stops], list(zip(stops, stops[1:]))):  # singles, then pairs
            best = _best_removal(total_miles, pool, purchases, removals, stop_penalty, range_miles, mpg)
            if best is not None:
                break
        if best is None:
            break
        removed, purchases = best
        pool = [c for c in pool if not any(c is r for r in removed)]
    return purchases


def plan_fuel_stops(
    total_miles: float,
    candidates: Sequence[Candidate],
    range_miles: float = 500.0,
    mpg: float = 10.0,
    stop_penalty: float = DEFAULT_STOP_PENALTY,
) -> Plan:
    """Plan the cheapest fuel purchases for a trip of ``total_miles``.

    ``candidates`` may be in any order and is never mutated. Raises NoFeasibleRoute
    when a gap between consecutive fuel points (start, stations, destination)
    exceeds ``range_miles``. Stops that save less than ``stop_penalty`` dollars are
    eliminated (0 disables this); totals are always the real fuel cost.
    """
    if total_miles < 0 or range_miles <= 0 or mpg <= 0 or stop_penalty < 0:
        raise ValueError("total_miles and stop_penalty must be >= 0; range_miles and mpg must be > 0.")
    pool = _usable_candidates(candidates, total_miles)
    purchases = _greedy(total_miles, pool, range_miles, mpg)
    if stop_penalty > 0:
        purchases = _eliminate_stops(total_miles, pool, purchases, stop_penalty, range_miles, mpg)

    stops = tuple(
        Stop(c, round(g, 2), round(g * c.price, 2), round(arr, 2)) for c, g, arr in purchases
    )
    return Plan(
        stops,
        round(sum(g for _, g, _ in purchases), 2),
        round(sum(g * c.price for c, g, _ in purchases), 2),
    )
