"""Small JSON-to-text inspections. Static proof only; never evaluate input code."""

import ast
from pathlib import Path

MAX_FILE = 65536


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
        if len(list(ast.walk(tree))) > 256:
            return False
        names, imports, files = set(), set(), set()
        reserved = {"json", "re", "open", "print"}

        def target(node):
            if not isinstance(node, ast.Name) or node.id in reserved:
                raise ValueError("invalid binding")
            names.add(node.id)
            return node.id

        def expr(node, depth=0):
            if depth > 20:
                raise ValueError("deep expression")
            if isinstance(node, ast.Constant) and type(node.value) in {
                str,
                int,
                bool,
                type(None),
            }:
                return
            if isinstance(node, ast.Name) and node.id in names:
                return
            if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) <= 64:
                for item in node.elts:
                    expr(item, depth + 1)
                return
            if isinstance(node, ast.Subscript):
                expr(node.value, depth + 1)
                if isinstance(node.slice, ast.Slice):
                    for part in (node.slice.lower, node.slice.upper, node.slice.step):
                        if part is not None:
                            expr(part, depth + 1)
                else:
                    expr(node.slice, depth + 1)
                return
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Add)
                and isinstance(node.right, ast.Constant)
                and type(node.right.value) is int
            ):
                if not (
                    isinstance(node.left, ast.Call)
                    and isinstance(node.left.func, ast.Attribute)
                    and node.left.func.attr in {"index", "rindex"}
                ):
                    raise ValueError("only string-index offsets")
                expr(node.left, depth + 1)
                return
            if isinstance(node, ast.Compare) and all(
                isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn))
                for op in node.ops
            ):
                expr(node.left, depth + 1)
                for part in node.comparators:
                    expr(part, depth + 1)
                return
            if isinstance(node, ast.DictComp) and len(node.generators) == 1:
                gen = node.generators[0]
                if (
                    gen.is_async
                    or not isinstance(gen.iter, ast.Call)
                    or not isinstance(gen.iter.func, ast.Attribute)
                    or gen.iter.func.attr != "items"
                ):
                    raise ValueError("only item projection")
                expr(gen.iter, depth + 1)
                if not isinstance(gen.target, ast.Tuple) or len(gen.target.elts) != 2:
                    raise ValueError("item binding required")
                prior = set(names)
                for item in gen.target.elts:
                    target(item)
                projected = {item.id for item in gen.target.elts}
                if any(
                    isinstance(n, ast.Name) and n.id not in projected
                    for value in (node.key, node.value, *gen.ifs)
                    for n in ast.walk(value)
                ):
                    raise ValueError("projection must select its own item")
                expr(node.key, depth + 1)
                expr(node.value, depth + 1)
                for condition in gen.ifs:
                    expr(condition, depth + 1)
                names.intersection_update(prior)
                return
            if not isinstance(node, ast.Call):
                raise ValueError("unsupported expression")
            call = node.func
            if (
                isinstance(call, ast.Name)
                and call.id == "open"
                and len(node.args) == 1
                and not node.keywords
            ):
                if not isinstance(node.args[0], ast.Constant) or not isinstance(
                    node.args[0].value, str
                ):
                    raise ValueError("literal file required")
                path = Path(node.args[0].value)
                if check_files and (
                    not path.is_file() or path.stat().st_size > MAX_FILE
                ):
                    raise ValueError("unknown or large file")
                files.add(str(path))
                if len(files) > 2:
                    raise ValueError("too many inputs")
                return
            if not isinstance(call, ast.Attribute):
                raise ValueError("unknown function")
            if (
                isinstance(call.value, ast.Name)
                and call.value.id == "json"
                and "json" in imports
            ):
                if call.attr not in {"load", "loads", "dumps"} or len(node.args) != 1:
                    raise ValueError("unknown JSON operation")
                if call.attr == "dumps" and not isinstance(
                    node.args[0], (ast.Name, ast.Subscript, ast.DictComp)
                ):
                    raise ValueError("only one bounded JSON rendering")
                for kw in node.keywords:
                    if (
                        call.attr != "dumps"
                        or kw.arg != "indent"
                        or not isinstance(kw.value, ast.Constant)
                        or type(kw.value.value) is not int
                        or not 0 <= kw.value.value <= 4
                    ):
                        raise ValueError("unknown option")
            else:
                if (
                    call.attr
                    not in {"get", "items", "split", "index", "rindex", "read"}
                    or node.keywords
                ):
                    raise ValueError("unknown method")
                if call.attr == "read" and (
                    node.args
                    or not isinstance(call.value, ast.Call)
                    or not isinstance(call.value.func, ast.Name)
                    or call.value.func.id != "open"
                ):
                    raise ValueError("literal read required")
                expr(call.value, depth + 1)
            for arg in node.args:
                expr(arg, depth + 1)

        def statements(body, loop=None):
            for statement in body:
                if isinstance(statement, ast.Import) and loop is None:
                    for alias in statement.names:
                        if alias.name not in {"json", "re"} or alias.asname:
                            raise ValueError("unknown import")
                        imports.add(alias.name)
                elif (
                    isinstance(statement, ast.Assign)
                    and loop is None
                    and len(statement.targets) == 1
                ):
                    if any(
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "dumps"
                        for n in ast.walk(statement.value)
                    ):
                        raise ValueError("rendering cannot feed another rendering")
                    expr(statement.value)
                    target(statement.targets[0])
                elif (
                    isinstance(statement, ast.For)
                    and loop is None
                    and not statement.orelse
                ):
                    expr(statement.iter)
                    item = target(statement.target)
                    statements(statement.body, item)
                elif (
                    isinstance(statement, ast.If)
                    and loop is not None
                    and not statement.orelse
                ):
                    expr(statement.test)
                    statements(statement.body, loop)
                elif (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Call)
                    and isinstance(statement.value.func, ast.Name)
                    and statement.value.func.id == "print"
                    and not statement.value.keywords
                ):
                    for arg in statement.value.args:
                        expr(arg)
                        if loop:
                            used = {
                                n.id for n in ast.walk(arg) if isinstance(n, ast.Name)
                            }
                            locals_ = {
                                n.id
                                for n in ast.walk(arg)
                                if isinstance(n, ast.Name)
                                and isinstance(n.ctx, ast.Store)
                            }
                            if used - locals_ - {loop, "json"}:
                                raise ValueError("loop output must be its current item")
                else:
                    raise ValueError("unknown statement")

        statements(tree.body)
        return bool(files and "json" in imports)
    except (OSError, SyntaxError, ValueError, TypeError, RecursionError):
        return False
