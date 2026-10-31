#!/usr/bin/env python3
"""Strip comments and docstrings from .py files, in place.

Part of the release pipeline: the Cython build compiles from the decommented
copy while the readable source stays in git. Uses the ast module (unparse
drops every comment and normalizes formatting); string literals are never
touched because only docstring expressions are removed.
"""
import ast
import sys


class _DocStripper(ast.NodeTransformer):
    def _strip(self, node):
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] if len(body) > 1 else [ast.Pass()]
        return node

    def visit_Module(self, node):
        self.generic_visit(node)
        return self._strip(node)

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = lambda self, node: self._strip_type(node)

    def _strip_type(self, node):
        self.generic_visit(node)
        return self._strip(node)


def decomment(source: str) -> str:
    return ast.unparse(_DocStripper().visit(ast.parse(source)))


if __name__ == "__main__":
    for path in sys.argv[1:]:
        src = open(path).read()
        out = decomment(src)
        compile(out, path, "exec")       # fail loudly before writing
        with open(path, "w") as fh:
            fh.write(out)
    print("decommented:", ", ".join(sys.argv[1:]))