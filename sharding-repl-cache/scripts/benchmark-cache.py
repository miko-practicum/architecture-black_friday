"""Measure complete HTTP responses with the standard library (no browser cache)."""

import argparse
import json
import statistics
import time
from urllib.request import urlopen


def fetch(url):
    start = time.perf_counter()
    with urlopen(url, timeout=15) as response:
        body = response.read()
        assert response.status == 200, response.status
    elapsed_ms = (time.perf_counter() - start) * 1000
    return json.loads(body), elapsed_ms


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8080/helloDoc/users")
    parser.add_argument("--requests", type=int, default=20)
    args = parser.parse_args()
    if args.requests < 2:
        parser.error("--requests must be at least 2")

    first, first_ms = fetch(args.url)
    assert len(first["users"]) == 1000, "Expected the initialized helloDoc collection"
    timings = []
    for _ in range(args.requests):
        result, elapsed_ms = fetch(args.url)
        assert result == first, "Cached response differs from the initial response"
        timings.append(elapsed_ms)
    report = {
        "url": args.url,
        "users": len(first["users"]),
        "first_request_ms": round(first_ms, 2),
        "warm_requests": len(timings),
        "warm_min_ms": round(min(timings), 2),
        "warm_median_ms": round(statistics.median(timings), 2),
        "warm_max_ms": round(max(timings), 2),
        "warm_all_below_100ms": all(value < 100 for value in timings),
        "warm_times_ms": [round(value, 2) for value in timings],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["warm_all_below_100ms"]:
        raise SystemExit("FAIL: at least one repeated response took >= 100 ms")


if __name__ == "__main__":
    main()
