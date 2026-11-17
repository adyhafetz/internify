"""Put a hosted-version backup back. Stop the bot first.

    docker compose stop telegram-bot
    docker compose run --rm -v /path/to/backups:/backups telegram-bot python scripts/restore_backup.py /backups/internify-backup-XXXX.tar.gz
    docker compose start telegram-bot

The files go into ./output (the database and every person's folder). The current database is kept as internify.db.before-restore."""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot import backup  # noqa: E402
from bot.services import db  # noqa: E402
from service import config  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = argv[1]
    if not os.path.isfile(path):
        print(f"No such file: {path}")
        return 1
    if os.path.exists(db.DB_PATH):
        shutil.copy2(db.DB_PATH, db.DB_PATH + ".before-restore")
    try:
        backup.restore(path, config.OUTPUT_DIR)
    except ValueError as e:
        print(e)
        return 1
    print("Restored. Start the bot again.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
