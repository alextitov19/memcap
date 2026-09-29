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
    "if", "then", "elif", "else", "end",
    "group_by", "min", "max", "min_by", "max_by", "add",
}


def expression_safe(source, variables=()):
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
    # Object labels and shorthand fields are data names, not function calls.
    # Preserve every value expression so {jobs: [recurse]} still fails closed.
    parts, stack, i = [], [], 0
    while i < len(code):
        char = code[i]
        if stack and stack[-1] == ["{", True]:
            label = re.match(r'[A-Za-z_][A-Za-z_0-9]*(?=\s*[:,}])', code[i:])
            if label:
                i += len(label[0])
                stack[-1][1] = False
                continue
        if char in "{[(":
            stack.append([char, char == "{"])
        elif char in "}])":
            if not stack or stack[-1][0] != {"}": "{", "]": "[", ")": "("}[char]:
                return False
            stack.pop()
        elif stack and stack[-1][0] == "{" and char in ",:":
            stack[-1][1] = char == ","
        elif stack and stack[-1][0] == "{" and not char.isspace():
            stack[-1][1] = False
        parts.append(char)
        i += 1
    if stack:
        return False
    code = "".join(parts)
    for variable in variables:
        code = re.sub(r'\$' + re.escape(variable) + r'\b', 'null', code)
    if ".." in code or not re.fullmatch(r'[\w.\[\]{}()|,:/?!@<>=+\s"-]+', code):
        return False
    names = re.findall(r"(?<![\w.])([A-Za-z_][A-Za-z_0-9]*)", code)
    return len(names) <= 128 and all(name in FUNCTIONS for name in names)


def command_safe(args):
    args = list(args)
    variables = []
    while args:
        if (re.fullmatch(r"-[nrceMC]+", args[0]) or args[0] in {
            "--raw-output", "--compact-output", "--exit-status", "--monochrome-output", "--null-input"
        }):
            args.pop(0)
        elif (args[0] == "--arg" and len(args) >= 3
              and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", args[1])):
            variables.append(args[1])
            args = args[3:]
        else:
            break
    return bool(args) and all(not a.startswith("-") for a in args[1:]) and expression_safe(args[0], variables)
