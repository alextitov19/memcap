"""Independent admission contracts; fixtures never allocate real workload RAM."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))
GIB = 1048576


class AdmissionTests(unittest.TestCase):
    def sample(self, **changes):
        return dict(
            cap_kb=20 * GIB,
            tracked_kb=int(18.5 * GIB),
            available_kb=int(3.6 * GIB),
            pressure=2,
            fault=False,
            footprints={},
            tracked_pids=[],
            monotonic=100,
            **changes,
        )

    def decision(
        self,
        *,
        sample=None,
        mode="adaptive",
        jobs=None,
        controller=None,
        memory=GIB,
        explicit=False,
    ):
        from admission import decide

        return decide(
            dict(mode=mode, max_jobs=12, headroom_kb=2 * GIB, allowed_pressure=(1, 2)),
            sample or self.sample(),
            controller or dict(now=100, healthy_since=90, last_start=90),
            jobs or [],
            dict(memory_kb=memory, resource="", explicit=explicit),
        )

    def test_adaptive_admits_despite_soft_footprint_overage(self):
        sample = self.sample()
        sample["tracked_kb"] = int(22.54 * GIB)
        self.assertTrue(self.decision(sample=sample)["allow"])
        self.assertFalse(self.decision(sample=sample, mode="strict")["allow"])

    def test_unknown_request_is_not_free_and_explicit_allowance_still_counts(self):
        self.assertTrue(self.decision()["allow"])
        self.assertFalse(self.decision(memory=4 * GIB, explicit=True)["allow"])
        self.assertFalse(self.decision(memory=0)["allow"])

    def test_red_fault_and_stale_sample_block_heavy_work(self):
        for changes in (
            {"pressure": 4},
            {"fault": True},
            {"monotonic": 95},
            {"available_kb": -1},
            {"monotonic": float("nan")},
        ):
            self.assertFalse(
                self.decision(sample={**self.sample(), **changes})["allow"]
            )

    def test_new_start_must_wait_for_shared_observation_interval(self):
        self.assertFalse(
            self.decision(controller=dict(now=100, healthy_since=90, last_start=99))[
                "allow"
            ]
        )
        self.assertTrue(
            self.decision(controller=dict(now=100, healthy_since=90, last_start=98))[
                "allow"
            ]
        )

    def test_expired_valid_sample_is_sampling_not_measurement_failure(self):
        decision = self.decision(sample={**self.sample(), "monotonic": 97.999})
        self.assertFalse(decision["allow"])
        self.assertEqual(decision["reason"], "sampling")
        self.assertTrue(self.decision(sample={**self.sample(), "monotonic": 98})["allow"])
        for stamp in (101, float("nan"), "old", -1):
            decision = self.decision(sample={**self.sample(), "monotonic": stamp})
            self.assertFalse(decision["allow"])
            self.assertEqual(decision["reason"], "measurement")

    def test_unobserved_growth_is_reserved_across_jobs(self):
        jobs = [
            dict(
                status="running",
                resource="",
                memory_kb=2 * GIB,
                reservation_kb=2 * GIB,
                members={"123": "identity"},
            )
        ]
        sample = {
            **self.sample(),
            "footprints": {"123": GIB // 4},
            "tracked_pids": [123],
            "available_kb": 3 * GIB,
        }
        self.assertFalse(self.decision(sample=sample, jobs=jobs, memory=GIB)["allow"])

    def test_slot_limit_and_unknown_members_are_retained(self):
        jobs = [
            dict(status="running", resource="", memory_kb=GIB, members={})
            for _ in range(12)
        ]
        self.assertFalse(self.decision(jobs=jobs)["allow"])

    def test_strict_policy_keeps_original_budget_and_headroom_contract(self):
        self.assertFalse(self.decision(mode="strict", memory=2 * GIB)["allow"])
        self.assertTrue(self.decision(mode="strict", memory=GIB)["allow"])

    def test_pressure_recovery_requires_stable_observations(self):
        self.assertFalse(
            self.decision(controller=dict(now=100, healthy_since=99, last_start=90))[
                "allow"
            ]
        )

    def test_controller_does_not_call_accumulated_swap_pressure(self):
        from admission import advance

        sample = {**self.sample(), "swap_used_kb": 20 * GIB, "swap_out_kbps": 0}
        state = advance(dict(healthy_since=90, last_start=90), sample, 100)
        self.assertTrue(self.decision(sample=sample, controller=state)["allow"])


if __name__ == "__main__":
    unittest.main()
