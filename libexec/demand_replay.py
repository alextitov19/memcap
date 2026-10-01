"""Offline labeled-command replay. Classifies; never launches corpus commands."""
import hashlib
import json
from pathlib import Path
import random
import time

from analytics_reports import distribution


def replay(path, cwd=None, repetitions=5, classifier=None):
    if not 1 <= repetitions <= 50:
        raise ValueError('repetitions must be 1–50')
    with Path(path).open('rb') as stream:
        raw = stream.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError('replay corpus exceeds 5 MiB')
    cases = json.loads(raw)
    if not isinstance(cases, list) or not 1 <= len(cases) <= 1000:
        raise ValueError('replay requires 1–1000 labeled cases')
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get('command'), str)
                or len(case['command']) > 256 * 1024 or case.get('expected') not in {'light', 'heavy'}):
            raise ValueError('each case needs bounded command text and expected light/heavy')
    if classifier is None:
        from demand_policy import classify
        classifier = lambda command: classify(command, cwd).kind
    rows = []
    rng = random.Random(941)
    for repetition in range(repetitions):
        order = list(enumerate(cases))
        rng.shuffle(order)
        for index, case in order:
            started = time.perf_counter_ns()
            actual = classifier(case['command'])
            rows.append(dict(case=index, repetition=repetition, expected=case['expected'], actual=actual,
                             duration_ms=(time.perf_counter_ns() - started) / 1e6))
    return dict(schema=1, corpus_sha256=hashlib.sha256(raw).hexdigest(), cases=len(cases),
                repetitions=repetitions, observations=rows,
                false_heavy=sum(r['expected'] == 'light' and r['actual'] != 'light' for r in rows),
                false_light=sum(r['expected'] == 'heavy' and r['actual'] == 'light' for r in rows),
                classifier_ms=distribution([r['duration_ms'] for r in rows]),
                limits='Reviewed labels, not measured memory. No corpus commands executed. Classification latency excludes host hook startup and agent completion; no production speedup claim.')
