"""
parser.py — Extract Log*() call templates from Bitcoin Core C++ source files.

Usage:
    python parser.py <src_path> [options]

Output is written as Python dict literals (one per line), compatible with
ast.literal_eval. Diagnostics go to stderr; templates go to stdout or --output.
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_FUNCTIONS = {"LogDebug", "LogInfo", "LogWarning", "LogError"}

_LLVM_DEFAULTS = {
    "win32": [
        r"C:\Program Files\LLVM\bin",
        r"C:\Program Files (x86)\LLVM\bin",
    ],
    "darwin": [
        "/opt/homebrew/opt/llvm/lib",
        "/usr/local/opt/llvm/lib",
        "/usr/lib",
    ],
    "linux": [
        "/usr/lib/llvm-18/lib",
        "/usr/lib/llvm-17/lib",
        "/usr/lib/llvm-16/lib",
        "/usr/lib/llvm-15/lib",
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib",
    ],
}


# ---------------------------------------------------------------------------
# LLVM path resolution
# ---------------------------------------------------------------------------

def find_llvm_path(explicit: str | None = None) -> str:
    """
    Resolve the LLVM library directory.

    Resolution order:
      1. --llvm-path CLI argument (explicit)
      2. LLVM_PATH environment variable
      3. Platform-specific default locations

    Raises RuntimeError with an actionable message if nothing is found.
    """
    candidates: list[str] = []

    if explicit:
        candidates.append(explicit)

    env = os.environ.get("LLVM_PATH")
    if env:
        candidates.append(env)

    platform = sys.platform
    for plat, paths in _LLVM_DEFAULTS.items():
        if platform.startswith(plat):
            candidates.extend(paths)
            break

    tried = []
    for path in candidates:
        if os.path.isdir(path):
            log.debug("Using LLVM path: %s", path)
            return path
        tried.append(path)

    raise RuntimeError(
        "Could not find LLVM library directory. Tried:\n"
        + "\n".join(f"  {p}" for p in tried)
        + "\n\nSet --llvm-path or the LLVM_PATH environment variable."
    )


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def collect_files(src_path: Path, extensions: tuple[str, ...]) -> list[Path]:
    """
    Recursively collect all files under src_path matching the given extensions.

    Raises SystemExit if src_path does not exist or is not a directory.
    """
    if not src_path.exists():
        log.error("Source path does not exist: %s", src_path)
        sys.exit(1)
    if not src_path.is_dir():
        log.error("Source path is not a directory: %s", src_path)
        sys.exit(1)

    files = sorted(
        Path(root) / file
        for root, _dirs, filenames in os.walk(src_path)
        for file in filenames
        if file.endswith(extensions)
    )
    log.debug("Found %d files to scan under %s", len(files), src_path)
    return files


# ---------------------------------------------------------------------------
# Template extraction
# ---------------------------------------------------------------------------

def logstatement_to_dict(statements: list[str]) -> dict | None:
    """
    Convert a flat list of token-parts into a structured template dict.

    Returns None if the statement is malformed (too short or missing fields).

    Expected layout:
      [LogType, (BCLog::CATEGORY,)? "fmt_string", arg1, arg2, ...]
    """
    if len(statements) < 2:
        return None

    d: dict = {}
    d["type"] = statements[0]

    i = 1
    if i >= len(statements):
        return None

    if statements[i].startswith("BCLog::"):
        d["category"] = statements[i].replace("BCLog::", "")
        i += 1
    else:
        d["category"] = None

    if i >= len(statements):
        return None

    fmt_raw = statements[i]
    # Format string must be wrapped in quotes from tokenisation
    if len(fmt_raw) < 2:
        return None
    d["fmt"] = fmt_raw[1:-1]
    i += 1

    d["args"] = statements[i:]
    return d


def parse_file(tu) -> list[dict]:
    """
    Walk all tokens in a parsed TranslationUnit and extract Log*() templates.

    Returns a (possibly empty) list of template dicts.
    """
    results = []
    found_log = False
    skip_next_open_paren = False
    statement: list[str] = []
    part: list[str] = []

    for token in tu.get_tokens(extent=tu.cursor.extent):
        tk = token.spelling

        if tk in LOG_FUNCTIONS:
            statement = [tk]
            part = []
            found_log = True
            skip_next_open_paren = True
            continue

        if not found_log:
            continue

        match tk:
            case ",":
                statement.append("".join(part).replace("\\n", ""))
                part = []

            case "(":
                if skip_next_open_paren:
                    skip_next_open_paren = False
                else:
                    part.append(tk)

            case ";":
                found_log = False
                part_str = "".join(part).replace(");", "")
                statement.append(part_str)
                part = []
                template = logstatement_to_dict(statement)
                if template is not None:
                    results.append(template)
                statement = []

            case _:
                part.append(tk)

    return results


def parse_src(
    src_path: Path,
    llvm_path: str,
    extensions: tuple[str, ...],
) -> Iterator[dict]:
    """
    Yield template dicts extracted from all matching files under src_path.

    Files that fail to parse are skipped with a warning.
    """
    from clang.cindex import Config, Index  # imported here so --help works without clang

    Config.set_library_path(llvm_path)
    index = Index.create()

    files = collect_files(src_path, extensions)

    for file in files:
        try:
            tu = index.parse(str(file))
            for template in parse_file(tu):
                yield template
        except Exception as exc:  # noqa: BLE001
            log.warning("Skipping %s — %s: %s", file, type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="parser.py",
        description=(
            "Extract Log*() call templates from Bitcoin Core C++ source files.\n\n"
            "Walks SRC_PATH recursively, tokenises every .cpp/.h file via libclang,\n"
            "and emits one Python dict literal per Log*() call found. Output is\n"
            "compatible with ast.literal_eval and can be fed directly into oop_tree.py."
        ),
        epilog=(
            "Output format:\n"
            "  One Python dict literal per line, e.g.:\n"
            "    {'type': 'LogDebug', 'category': 'NET',\n"
            "     'fmt': 'Requesting block %s from peer=%d',\n"
            "     'args': ['hash.ToString()', 'peer_id']}\n"
            "\n"
            "  Keys:\n"
            "    type      Log function name (LogDebug / LogInfo / LogWarning / LogError)\n"
            "    category  BCLog category (e.g. NET, MEMPOOL) or null\n"
            "    fmt       printf-style format string\n"
            "    args      ordered list of C++ argument expressions\n"
            "\n"
            "LLVM / libclang:\n"
            "  Requires the clang Python package and the LLVM shared library.\n"
            "  Resolution order for the library directory:\n"
            "    1. --llvm-path argument\n"
            "    2. LLVM_PATH environment variable\n"
            "    3. Platform defaults:\n"
            "         Windows : C:\\Program Files\\LLVM\\bin\n"
            "         macOS   : /opt/homebrew/opt/llvm/lib\n"
            "         Linux   : /usr/lib/llvm-<ver>/lib\n"
            "  Install LLVM: https://releases.llvm.org  or  brew install llvm\n"
            "\n"
            "Exit codes:\n"
            "  0  Success\n"
            "  1  Error (path not found, LLVM missing, output file not writable)\n"
            "\n"
            "Examples:\n"
            "  # Print to stdout\n"
            "  python parser.py /path/to/bitcoin/src\n"
            "\n"
            "  # Write to file\n"
            "  python parser.py /path/to/bitcoin/src -o templates.txt\n"
            "\n"
            "  # Custom LLVM path\n"
            "  python parser.py /path/to/bitcoin/src --llvm-path /opt/homebrew/opt/llvm/lib\n"
            "\n"
            "  # Via environment variable\n"
            "  LLVM_PATH=/usr/lib/llvm-17/lib python parser.py /path/to/bitcoin/src\n"
            "\n"
            "  # Verbose diagnostic output\n"
            "  python parser.py /path/to/bitcoin/src --log-level DEBUG\n"
            "\n"
            "  # Scan only .cpp files\n"
            "  python parser.py /path/to/bitcoin/src --extensions .cpp\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "src_path",
        metavar="SRC_PATH",
        help=(
            "Path to the Bitcoin Core src/ directory. "
            "All matching files are scanned recursively."
        ),
    )
    parser.add_argument(
        "-o", "--output",
        metavar="FILE",
        default=None,
        help="Write templates to FILE instead of stdout. File is created or overwritten.",
    )
    parser.add_argument(
        "--llvm-path",
        metavar="DIR",
        default=None,
        help=(
            "LLVM bin/ (Windows) or lib/ (Unix) directory. "
            "See 'LLVM / libclang' section below for full resolution order."
        ),
    )
    parser.add_argument(
        "--extensions",
        metavar="EXT",
        default=".cpp,.h",
        help=(
            "Comma-separated file extensions to scan (default: .cpp,.h). "
            "e.g. --extensions .cpp,.h,.cc"
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Diagnostic verbosity written to stderr (default: INFO).",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    # Resolve LLVM path early so we fail fast before touching any source files
    try:
        llvm_path = find_llvm_path(args.llvm_path)
    except RuntimeError as exc:
        log.error("%s", exc)
        sys.exit(1)

    src_path = Path(args.src_path)
    extensions = tuple(
        ext.strip() if ext.strip().startswith(".") else f".{ext.strip()}"
        for ext in args.extensions.split(",")
    )

    # Open output destination before we start parsing (fail fast on bad path)
    if args.output is not None:
        try:
            out = open(args.output, "w", encoding="utf-8")  # noqa: WPS515
        except OSError as exc:
            log.error("Cannot open output file %s: %s", args.output, exc)
            sys.exit(1)
    else:
        out = sys.stdout

    try:
        start = time.perf_counter()
        file_count = len(collect_files(src_path, extensions))
        template_count = 0

        for template in parse_src(src_path, llvm_path, extensions):
            print(template, file=out)
            template_count += 1

        elapsed = time.perf_counter() - start
        log.info(
            "Parsed %d files, extracted %d templates in %.1fs",
            file_count,
            template_count,
            elapsed,
        )
    finally:
        if args.output is not None:
            out.close()


if __name__ == "__main__":
    main()
