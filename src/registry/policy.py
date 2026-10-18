"""Static safety policy applied to tool source before it is imported."""
from __future__ import annotations

import ast

FORBIDDEN_CALLS = {"exec", "eval", "compile", "__import__"}
FORBIDDEN_MODULES = {
    "subprocess", "socket", "ssl", "ctypes", "pty", "telnetlib", "ftplib",
    "smtplib", "http.client", "urllib.request", "requests", "httpx", "aiohttp",
}
FORBIDDEN_OS_ATTRS = {
    "system", "popen", "fork", "forkpty", "kill", "killpg", "startfile",
    "execv", "execve", "execl", "execle", "execlp", "execvp", "execvpe",
    "spawnl", "spawnle", "spawnlp", "spawnv", "spawnve", "spawnvp",
}


def _module_forbidden(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in FORBIDDEN_MODULES)


def check_source(source: str, filename: str = "<tool>") -> list[str]:
    """Return a list of policy violations found in ``source`` (empty = OK).

    Raises SyntaxError if the source cannot be parsed.
    """
    tree = ast.parse(source, filename=filename)
    problems: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _module_forbidden(alias.name):
                    problems.append(f"line {line}: import of '{alias.name}' is forbidden")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if _module_forbidden(mod):
                problems.append(f"line {line}: import from '{mod}' is forbidden")
            for alias in node.names:
                if _module_forbidden(f"{mod}.{alias.name}") or (
                    mod == "os" and alias.name in FORBIDDEN_OS_ATTRS
                ):
                    problems.append(f"line {line}: import of '{mod}.{alias.name}' is forbidden")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in FORBIDDEN_CALLS:
                problems.append(f"line {line}: call to '{fn.id}()' is forbidden")
            elif (
                isinstance(fn, ast.Attribute)
                and isinstance(fn.value, ast.Name)
                and fn.value.id == "os"
                and fn.attr in FORBIDDEN_OS_ATTRS
            ):
                problems.append(f"line {line}: call to 'os.{fn.attr}()' is forbidden")
            elif isinstance(fn, ast.Attribute) and fn.attr == "import_module":
                problems.append(f"line {line}: dynamic import_module() is forbidden")
    return problems
