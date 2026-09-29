"""Finite inspection loops with runtime checks on expansion-supplied argv."""

import re
import shlex


def guard_for_loop(command, executable, session_key):
    from inspection import spans, substitute_reference, guard_stages
    from lightweight import local_variable
    from scheduler_policy import literal_shell, light_shell, normalized_lines

    text = normalized_lines(command)
    if len(text) > 65536:
        return None
    tokens = list(spans(text))
    starts = [i for i, token in enumerate(tokens) if token[2] == "for"]
    if len(starts) != 1:
        return None
    start = starts[0]
    if start and tokens[start - 1][2] not in {";", "&&"}:
        return None
    if start + 3 >= len(tokens) or tokens[start + 2][2] != "in":
        return None
    variable = tokens[start + 1][2]
    if not local_variable(variable):
        return None
    delimiter = next((i for i in range(start + 3, len(tokens)) if tokens[i][2] == ";"), None)
    if delimiter is None or delimiter + 1 >= len(tokens) or tokens[delimiter + 1][2] != "do":
        return None
    end = next((i for i in range(delimiter + 2, len(tokens)) if tokens[i][2] == "done"), None)
    if end is None or tokens[end - 1][2] != ";":
        return None
    values_text = text[tokens[start + 2][1]:tokens[delimiter][0]]
    if any(t[2] in {"|", "||", "&&", "&", "<", ">", ">>", "<&", ">&"} for t in tokens[start + 3:delimiter]):
        return None
    if not literal_shell(values_text, allow_bare_globs=True):
        return None
    try:
        values = shlex.split(values_text)
    except ValueError:
        return None
    if not 1 <= len(values) <= 64 or not all(values):
        return None
    before, after = text[:tokens[start][0]], text[tokens[end][1]:]
    if not light_shell(before + " true " + after):
        return None
    body_start, body_end = tokens[delimiter + 1][1], tokens[end][0]
    body = text[body_start:body_end]
    if re.search(r"(?:^|[;\s])" + re.escape(variable) + r"=", body):
        return None
    # The only arithmetic allowed here is an offset from a literal numeric
    # loop value. Never evaluate arbitrary shell arithmetic or environment data.
    arithmetic = re.compile(r"\$\(\(\s*" + re.escape(variable) + r"\s*([+-])\s*([0-9]{1,7})\s*\)\)")
    numeric = all(re.fullmatch(r"[0-9]{1,7}", value) for value in values)
    proofs = values if numeric else ["/__MEMCAP_LOOP_FILE__"]
    for value in proofs:
        proof = body
        if numeric:
            proof = arithmetic.sub(lambda m: str(int(value) + (1 if m[1] == "+" else -1) * int(m[2])), proof)
        proof = substitute_reference(proof, variable, value)
        if not light_shell(proof, allow_bare_globs=True):
            return None
    guarded = guard_stages(body, executable, session_key, variable, force=True)
    if guarded is None:
        return None
    return text[:body_start] + guarded + text[body_end:]
