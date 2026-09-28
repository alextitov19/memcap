"""Memory attribution only. These identities NEVER authorize signals or waits."""
from collections import deque


def footprint_members(job):
    return job.get("footprint_members", job["members"])


def refresh_footprint_members(jobs, table, uid):
    active = [job for job in jobs if job["status"] == "running"]
    owners = {str(job["owner"]) for job in active}
    assigned = {}
    children = {}
    for pid, row in table.items():
        if row["uid"] == uid and pid not in owners and row.get("ppid") is not None:
            children.setdefault(str(row["ppid"]), []).append(pid)

    # Registered groups take precedence over inherited/remembered attribution.
    # This avoids counting a nested managed job under two reservations.
    for index, job in enumerate(active):
        for pid, start in job["members"].items():
            row = table.get(pid)
            if row and row["uid"] == uid and row["start"] == start and pid not in owners:
                assigned[pid] = index

    def descendants():
        pending = deque(assigned)
        while pending:
            parent = pending.popleft()
            for pid in children.get(parent, ()):
                if pid not in assigned:
                    assigned[pid] = assigned[parent]
                    pending.append(pid)

    # Present ancestry wins over historical attribution when an existing child
    # becomes the root of a separately managed job between observations.
    descendants()
    for index, job in enumerate(active):
        for pid, start in footprint_members(job).items():
            row = table.get(pid)
            if row and row["uid"] == uid and row["start"] == start and pid not in owners:
                assigned.setdefault(pid, index)
    descendants()
    for job in active:
        job["footprint_members"] = {}
    for pid, index in assigned.items():
        active[index]["footprint_members"][pid] = table[pid]["start"]
