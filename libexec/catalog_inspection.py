"""Finite PostgreSQL catalog reads, with psql startup commands disabled."""

import re


def catalog_argv(words):
    if not words or words[0].rsplit("/", 1)[-1] != "psql":
        return None
    rest, query = list(words[1:]), None
    while rest:
        flag = rest.pop(0)
        if flag in {"-X", "--no-psqlrc", "-A", "-t", "-At", "-q"}:
            continue
        if (
            flag
            in {"-h", "-U", "-d", "-p", "--host", "--username", "--dbname", "--port"}
            and rest
        ):
            if rest.pop(0).startswith("-"):
                return None
            continue
        if flag in {"-c", "-Atc", "-Ac", "-tc", "--command"} and rest and query is None:
            query = rest.pop(0)
            continue
        return None
    if query is None or len(query) > 8192 or "\\" in query:
        return None
    # Quoted SQL strings are data, including doubled quotes. No comments,
    # subqueries, joins, user functions, COPY, psql escapes or second statements.
    text = re.sub(r"'(?:''|[^'])*'", "0", query).strip().rstrip(";").strip()
    match = re.fullmatch(
        r"select\s+(.+?)\s+from\s+pg_(policies|tables|indexes|views)\b(.*)", text, re.I
    )
    if not match or re.search(r"[^A-Za-z_0-9\s(),.*|=<>!-]", text):
        return None
    if "--" in text or len(re.findall(r"\bselect\b", text, re.I)) != 1:
        return None
    if re.search(r"\b(into|join|union|with|intersect|except|for|offset)\b", text, re.I):
        return None
    if any(
        name.lower() not in {"coalesce", "in"}
        for name in re.findall(r"\b([A-Za-z_]\w*)\s*\(", text)
    ):
        return None
    tail = match[3].strip()
    if tail and not re.match(r"^(where|order\s+by|limit)\b", tail, re.I):
        return None
    return [words[0], "-X", *words[1:]]
