from __future__ import annotations

import itertools
import threading
import time
import unittest
from collections import Counter

from scripts.validate.check_streams import HostLimiter, ProbeTarget, spread_by_host


def target(url: str, index: int = 0) -> ProbeTarget:
    return ProbeTarget(
        source="test",
        channel_id=f"channel-{index}",
        identifier=f"id-{index}",
        url=url,
        headers={},
        ephemeral=False,
    )


class HostLimiterTests(unittest.TestCase):
    def test_same_host_is_capped(self) -> None:
        limiter = HostLimiter(3)
        inflight: Counter[str] = Counter()
        peak: Counter[str] = Counter()
        guard = threading.Lock()

        def hit(url: str) -> None:
            with limiter.limit(url):
                host = url.split("/")[2]
                with guard:
                    inflight[host] += 1
                    peak[host] = max(peak[host], inflight[host])
                time.sleep(0.02)
                with guard:
                    inflight[host] -= 1

        threads = [threading.Thread(target=hit, args=(f"https://busy{i}.uk/x",)) for i in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertTrue(peak)
        self.assertLessEqual(max(peak.values()), 3)

    def test_different_hosts_are_not_serialised(self) -> None:
        """The cap must be per host, not global."""
        limiter = HostLimiter(1)
        lock = limiter.limit("https://a.example/x")
        with lock:
            # A different host still has its own permit.
            other = limiter.limit("https://b.example/x")
            self.assertTrue(other.acquire(blocking=False))
            other.release()
            lock.release()

    def test_zero_is_clamped_to_one(self) -> None:
        limiter = HostLimiter(0)
        self.assertEqual(limiter.max_per_host, 1)

    def test_host_matching_is_case_insensitive(self) -> None:
        limiter = HostLimiter(4)
        self.assertIs(limiter.limit("https://EXAMPLE.com/x"), limiter.limit("https://example.com/x"))


class SpreadByHostTests(unittest.TestCase):
    def test_no_two_adjacent_targets_share_a_host(self) -> None:
        targets = [target(f"https://h{i // 8}.uk/x", i) for i in range(64)]
        targets += [target(f"https://solo{i}.uk/x", 100 + i) for i in range(3)]
        ordered = spread_by_host(targets)
        hosts = [t.url.split("/")[2] for t in ordered]
        longest = max(len(list(group)) for _, group in itertools.groupby(hosts))
        self.assertEqual(longest, 1)

    def test_no_targets_are_lost_or_duplicated(self) -> None:
        targets = [target(f"https://h{i // 5}.uk/x", i) for i in range(50)]
        ordered = spread_by_host(targets)
        self.assertEqual(len(ordered), len(targets))
        self.assertEqual(
            sorted(t.identifier for t in ordered), sorted(t.identifier for t in targets)
        )

    def test_empty_input(self) -> None:
        self.assertEqual(spread_by_host([]), [])

    def test_single_host_input_is_preserved(self) -> None:
        targets = [target("https://only.example/x", i) for i in range(4)]
        self.assertEqual(len(spread_by_host(targets)), 4)


if __name__ == "__main__":
    unittest.main()
