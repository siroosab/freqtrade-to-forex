"""Discovery and static validation for native Freqtrade strategy files."""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path


MAX_STRATEGY_BYTES = 2_000_000
_STRATEGY_FILENAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*\.py$")


@dataclass(frozen=True)
class StrategyFile:
    name: str
    file_name: str
    path: Path
    builtin: bool = False


def strategy_directories() -> tuple[Path, ...]:
    configured = Path(os.environ.get("FOREX_STRATEGIES_DIR", "user_data/strategies"))
    active = Path(os.environ.get("FOREX_STRATEGY_PATH", "user_data/strategies/ForexMasterStrategy.py"))
    return tuple(dict.fromkeys((configured, active.parent, Path("freqtrade/forex/strategies"), Path("freqtrade/forex"))))


def _strategy_classes(source: str, file_name: str) -> list[str]:
    try:
        tree = ast.parse(source, filename=file_name)
        compile(tree, file_name, "exec")
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"Invalid Python strategy source: {exc}") from exc

    aliases = {"IStrategy"}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "freqtrade.strategy":
            aliases.update(alias.asname or alias.name for alias in node.names if alias.name == "IStrategy")

    defined_classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    if len(defined_classes) != len(set(defined_classes)):
        raise ValueError("Strategy class names must be unique within the file")

    class_bases: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_bases[node.name] = {
                base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
                for base in node.bases
            }

    strategy_names: list[str] = []
    for name, bases in class_bases.items():
        seen = set(bases)
        while seen:
            base = seen.pop()
            if base in aliases:
                strategy_names.append(name)
                break
            seen.update(class_bases.get(base, set()) - seen)

    if not strategy_names:
        raise ValueError("Strategy file must define a class inheriting from IStrategy")
    return strategy_names


def discover_strategy_files() -> list[StrategyFile]:
    directories = strategy_directories()
    found: dict[str, StrategyFile] = {}
    for index, directory in enumerate(directories):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("__"):
                continue
            try:
                names = _strategy_classes(path.read_text(encoding="utf-8"), path.name)
            except (OSError, UnicodeError, ValueError):
                continue
            for name in names:
                found.setdefault(name, StrategyFile(name, path.name, path, index > 1))
    return sorted(found.values(), key=lambda strategy: strategy.name.casefold())


def validate_strategy_upload(file_name: str, content: str, *, replacing: Path | None = None) -> list[str]:
    if not _STRATEGY_FILENAME.fullmatch(file_name):
        raise ValueError("Strategy filename must be a Python file with a simple class-style name")
    if not content.strip() or len(content.encode("utf-8")) > MAX_STRATEGY_BYTES:
        raise ValueError("strategy file is empty or exceeds the 2 MB limit")
    class_names = _strategy_classes(content, file_name)
    existing_names = {
        strategy.name
        for strategy in discover_strategy_files()
        if replacing is None or strategy.path.resolve() != replacing.resolve()
    }
    duplicates = sorted(existing_names.intersection(class_names))
    if duplicates:
        raise ValueError(f"Duplicate strategy class name(s): {', '.join(duplicates)}")
    return class_names