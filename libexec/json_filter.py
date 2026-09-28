"""Finite jq selectors/formatters, including checked string interpolation.

No filter files, modules, user functions, recursion or input/range generators.
This is a command classifier, not a JSON evaluator or a memory hard limit.
"""
import re

FUNCTIONS = {
    "keys", "length", "join", "sort", "sort_by", "unique", "unique_by",
    "tostring", "tonumber", "select", "has", "type", "null", "true", "false",
    "tsv", "csv", "json", "text", "empty", "not", "and", "or", "del",
    "map", "map_values", "values", "strings", "numbers", "objects", "arrays",
    "ascii_downcase", "ascii_upcase", "startswith", "endswith", "contains",
}


def expression_safe(source):
    if len(source) > 16384:
        return False
    # Strings are inert except for \(...), which must undergo the same proof.
    def scan(i, interpolation=False, depth=0):
        if depth > 16:
            raise ValueError("nesting")
        code, parens = [], 0
        while i < len(source):
            char = source[i]
            if char == '"':
                code.append('""')
                i += 1
                while i < len(source) and source[i] != '"':
                    if source.startswith("\\(", i):
                        inner, i = scan(i + 2, True, depth + 1)
                        code.extend([" ", inner, " "])
                    elif source[i] == "\\":
                        i += 2
                    else:
                        i += 1
                if i >= len(source):
                    raise ValueError("string")
            elif char == "(":
                parens += 1
                code.append(char)
            elif char == ")":
                if interpolation and parens == 0:
                    return "".join(code), i + 1
                parens -= 1
                if parens < 0:
                    raise ValueError("parenthesis")
                code.append(char)
            else:
                code.append(char)
            i += 1
        if interpolation or parens:
            raise ValueError("parenthesis")
        return "".join(code), i

    try:
        code, _ = scan(0)
    except ValueError:
        return False
    if ".." in code or not re.fullmatch(r'[\w.\[\]()|,:/?!@<>=+\s"-]+', code):
        return False
    names = re.findall(r"(?<![\w.])([A-Za-z_][A-Za-z_0-9]*)", code)
    return len(names) <= 128 and all(name in FUNCTIONS for name in names)


def command_safe(args):
    args = list(args)
    while args and (
        re.fullmatch(r"-[rceMC]+", args[0])
        or args[0] in {"--raw-output", "--compact-output", "--exit-status", "--monochrome-output"}
    ):
        args.pop(0)
    return bool(args) and all(not a.startswith("-") for a in args[1:]) and expression_safe(args[0])
