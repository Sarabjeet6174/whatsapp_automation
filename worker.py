import argparse
import logging
import time

from main import process_pending_messages_from_sql


logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process pending WhatsApp messages from SQL Server."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help=(
            "If >0, keep running and sleep this many seconds between cycles. "
            "If 0, run only once and exit."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    # Hard-coded test override numbers.
    test_from_number: str | None = "7014671454"
    test_to_number: str | None = "6375196831"

    def run_once() -> None:
        process_pending_messages_from_sql(
            pause_seconds=0,
            test_override_from_no=test_from_number,
            test_override_to_no=test_to_number,
        )

    if args.interval <= 0:
        logger.info("Running a single processing cycle...")
        run_once()
    else:
        logger.info(
            "Starting continuous worker with interval=%s seconds",
            args.interval,
        )
        while True:
            run_once()
            time.sleep(args.interval)


if __name__ == "__main__":
    main()

