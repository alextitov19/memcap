"""Pure admission decisions: charged footprint and physical pressure are distinct.

No process probes, signals, file writes or policy changes occur in this module.
The supervisor atomically commits startup credit after rechecking live pressure.
"""

from scheduler_metrics import number

GIB = 1048576


def advance(controller: dict, sample: dict, now: float) -> dict:
    state = {**controller, "now": now}
    stamp = sample.get("monotonic", now)
    if stamp == state.get("last_sample"):
        return state
    if sample.get("boot_id") != state.get("boot_id"):
        state = {"now": now, "healthy_since": now, "boot_id": sample.get("boot_id")}
    state["last_sample"] = stamp
    if sample.get("fault") or sample.get("pressure") not in (1, 2):
        state.update(healthy_since=now, cooldown_until=now + 6)
    else:
        state.setdefault("healthy_since", now)
    # Accumulated swap is intentionally not an input. This is ongoing traffic,
    # sustained across distinct samples while macOS is already under pressure.
    paging = sample.get("swap_out_kbps")
    if sample.get("pressure") == 2 and number(paging) and paging >= 128 * 1024:
        state.setdefault("paging_since", now)
        if now - state["paging_since"] >= 10:
            state["cooldown_until"] = now + 6
    else:
        state.pop("paging_since", None)
    return state


def decide(
    policy: dict, sample: dict, controller: dict, jobs: list, candidate: dict
) -> dict:
    result = dict(
        allow=False,
        reason="measurement",
        reservation_kb=0,
        startup_credit=0,
        sample_revision=sample.get("monotonic", 0),
        policy_revision=policy.get("revision", ""),
    )

    def deny(reason):
        return {**result, "reason": reason}

    try:
        mode = policy["mode"]
        memory = candidate["memory_kb"]
        if sample.get("busy"):
            return deny("sampling")
        if (
            mode not in ("strict", "adaptive")
            or type(memory) is not int
            or memory <= 0
            or sample["fault"]
            or sample["pressure"] not in policy["allowed_pressure"]
        ):
            return deny("pressure_or_measurement")
        if not all(number(sample[k]) for k in ("tracked_kb", "cap_kb", "available_kb")):
            return deny("measurement")
        active = [j for j in jobs if j["status"] == "running"]
        if (
            not candidate["resource"]
            and sum(not j["resource"] for j in active) >= policy["max_jobs"]
        ):
            return deny("slots")
        accounted = sample["tracked_kb"]
        outstanding = 0
        counted = set(map(str, sample["tracked_pids"]))
        for job in active:
            values = [sample["footprints"].get(p, 0) for p in job["members"]]
            if not all(number(v) for v in values):
                return deny("measurement")
            measured = sum(values)
            reserve = job.get("reservation_kb", job["memory_kb"])
            if not number(reserve):
                return deny("measurement")
            reserve = max(reserve, measured)
            already = sum(
                sample["footprints"].get(p, 0) for p in job["members"] if p in counted
            )
            accounted += reserve - already
            outstanding += max(0, reserve - measured)
        if mode == "strict":
            if accounted + memory > sample["cap_kb"]:
                return deny("budget")
            headroom = policy["headroom_kb"]
        else:
            now = controller["now"]
            stamp = sample["monotonic"]
            if not number(now) or not number(stamp) or not 0 <= now - stamp <= 2:
                return deny("measurement")
            if now < controller.get("cooldown_until", 0):
                return deny("paging")
            if now - controller.get("healthy_since", now) < 2:
                return deny("stabilizing")
            if now - controller.get("last_start", 0) < 2:
                return deny("startup")
            # Headroom in adaptive mode is an emergency physical margin, not
            # another full per-job allowance. Charged footprint is a soft target.
            headroom = min(policy["headroom_kb"], GIB // 2)
        result.update(
            outstanding_kb=outstanding,
            available_kb=sample["available_kb"],
            request_kb=memory,
            headroom_kb=headroom,
            headroom_deficit_kb=max(
                0, headroom + outstanding + memory - sample["available_kb"]
            ),
        )
        if sample["available_kb"] - outstanding - memory < headroom:
            return deny("headroom")
        return {
            **result,
            "allow": True,
            "reason": "available",
            "reservation_kb": memory,
            "startup_credit": int(mode == "adaptive"),
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return deny("measurement")
