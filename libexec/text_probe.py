"""Recognize small, straight-line Python file-to-text inspections without eval.

Unknown syntax, calls, imports and large/unknown inputs use normal admission.
Only the runtime argument guard checks actual files, in the command's cwd.
"""

import ast
from pathlib import Path

MAX_FILE = 1024 * 1024
MAX_TEXT = 32 * 1024 * 1024


def eligible(argv, check_files=False):
    if (
        len(argv) != 3
        or Path(argv[0]).name not in {"python", "python3"}
        or argv[1] != "-c"
        or len(argv[2]) > 16384
    ):
        return False
    try:
        tree = ast.parse(argv[2])
        if len(list(ast.walk(tree))) > 256 or len(tree.body) > 32:
            return False
        values, imported, total = {}, set(), 0

        def literal(node, kind=str):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, kind):
                raise ValueError("nonliteral")
            return node.value

        def bound(node):
            nonlocal total
            if isinstance(node, ast.Constant) and isinstance(
                node.value, (str, int, float)
            ):
                return len(str(node.value))
            if isinstance(node, ast.Name) and node.id in values:
                return values[node.id]
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                return bound(node.left) + bound(node.right)
            if not isinstance(node, ast.Call):
                raise ValueError("unsupported expression")
            call = node.func
            if (
                isinstance(call, ast.Attribute)
                and call.attr == "read"
                and isinstance(call.value, ast.Call)
            ):
                opener = call.value
                if (
                    not isinstance(opener.func, ast.Name)
                    or opener.func.id != "open"
                    or len(opener.args) != 1
                    or opener.keywords
                    or node.args
                    or node.keywords
                ):
                    raise ValueError("unsupported file read")
                path = Path(literal(opener.args[0]))
                if check_files:
                    if not path.is_file():
                        raise ValueError("not a regular file")
                    size = path.stat().st_size
                    if size > MAX_FILE:
                        raise ValueError("large input")
                else:
                    size = 1  # syntax proof only; actual bound checked at runtime
                total += size
                return size
            if (
                not isinstance(call, ast.Attribute)
                or not isinstance(call.value, ast.Name)
                or call.value.id not in imported
            ):
                raise ValueError("unknown call")
            if call.value.id == "re" and call.attr == "sub" and len(node.args) == 3:
                literal(node.args[0])
                replacement = literal(node.args[1])
                for option in node.keywords:
                    if (
                        option.arg != "flags"
                        or not isinstance(option.value, ast.Attribute)
                        or not isinstance(option.value.value, ast.Name)
                        or option.value.value.id != "re"
                        or option.value.attr
                        not in {"S", "I", "M", "DOTALL", "IGNORECASE", "MULTILINE"}
                    ):
                        raise ValueError("unsupported regex flags")
                size = bound(node.args[2])
                # Conservative bound includes literal insertions/backreferences.
                size = (size + 1) * (len(replacement) + 1)
                total += size
                if size > MAX_TEXT or total > MAX_TEXT:
                    raise ValueError("large intermediate")
                return size
            if (
                call.value.id == "html"
                and call.attr == "unescape"
                and len(node.args) == 1
                and not node.keywords
            ):
                size = bound(node.args[0])
                total += size
                return size
            raise ValueError("unknown transformation")

        printed = False
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    if alias.name not in {"re", "html", "sys"} or alias.asname:
                        return False
                    imported.add(alias.name)
            elif (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                name = statement.targets[0].id
                if name in {"re", "html", "sys", "open", "print"}:
                    return False
                values[name] = bound(statement.value)
            elif (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Name)
                and statement.value.func.id == "print"
            ):
                if statement.value.keywords:
                    return False
                total += sum(bound(arg) for arg in statement.value.args)
                printed = True
            else:
                return False
            if total > MAX_TEXT:
                return False
        return printed
    except (SyntaxError, ValueError, OSError, RecursionError, OverflowError):
        return False
