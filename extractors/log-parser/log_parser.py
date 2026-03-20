"""
log_parser.py — Tail a Bitcoin Core debug.log file and publish each line to NATS.

Usage:
    python log_parser.py --log-file <path/to/debug.log> [options]

Options:
    --log-file       PATH   Path to the Bitcoin Core debug.log file (required).
    --nats-address   ADDR   NATS server address (default: 127.0.0.1:4222).
    --nats-username  USER   NATS username (optional).
    --nats-password  PASS   NATS password (optional).
    --log-level      LVL    Logging verbosity: DEBUG, INFO, WARNING, ERROR
                            (default: INFO). Diagnostics go to stderr.
"""

import argparse
import asyncio
import logging
import sys

import nats

log = logging.getLogger(__name__)

_NATS_SUBJECT = "log-extractor"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="log_parser.py",
        description=(
            "Tail a Bitcoin Core debug.log file and publish each line as a "
            "raw text message to the NATS log-extractor subject."
        ),
    )
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        required=True,
        help="Path to the Bitcoin Core debug.log file.",
    )
    parser.add_argument(
        "--nats-address",
        metavar="ADDR",
        default="127.0.0.1:4222",
        help="NATS server address (default: 127.0.0.1:4222).",
    )
    parser.add_argument(
        "--nats-username",
        metavar="USER",
        default=None,
        help="NATS username (optional).",
    )
    parser.add_argument(
        "--nats-password",
        metavar="PASS",
        default=None,
        help="NATS password (optional).",
    )
    parser.add_argument(
        "--log-level",
        metavar="LVL",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity written to stderr (default: INFO).",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Async tail + publish
# ---------------------------------------------------------------------------

async def tail_and_publish(nc, log_file: str) -> None:
    """Follow log_file from the end and publish each new line to NATS."""
    log.info("Opening log file: %s", log_file)
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)  # seek to end of file
        log.info("Tailing %s — publishing new lines to NATS subject %r", log_file, _NATS_SUBJECT)
        while True:
            line = f.readline()
            if not line:
                await asyncio.sleep(0.05)
                continue
            line = line.rstrip("\n")
            log.debug("Publishing line: %s", line)
            await nc.publish(_NATS_SUBJECT, line.encode("utf-8"))


async def main(args: argparse.Namespace) -> None:
    nats_url = f"nats://{args.nats_address}"

    connect_kwargs: dict = {}
    if args.nats_username and args.nats_password:
        connect_kwargs["user"] = args.nats_username
        connect_kwargs["password"] = args.nats_password
    elif args.nats_username or args.nats_password:
        log.warning(
            "Both --nats-username and --nats-password must be provided together; "
            "ignoring partial credentials."
        )

    log.info("Connecting to NATS at %s ...", nats_url)
    nc = await nats.connect(nats_url, **connect_kwargs)
    log.info("Connected to NATS at %s", nats_url)

    try:
        await tail_and_publish(nc, args.log_file)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Shutting down...")
    except FileNotFoundError:
        log.error("Log file not found: %s", args.log_file)
    finally:
        await nc.drain()
        log.info("NATS connection closed.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    asyncio.run(main(args))
