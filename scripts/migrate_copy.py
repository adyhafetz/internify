"""Move an old single-user copy into the hosted version (see docs/migrating_to_hosted.md).

    python scripts/migrate_copy.py /path/to/old/output [--name "Ady"] [--dry-run] [--replace] [--telegram-id 123456]

Run it inside the hosted bot's container (the old output folder mounted read-only), with the bot stopped or idle.
--dry-run shows what would be moved and changes nothing. The old folder is never changed."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Move an old Internify copy into the hosted version.")
    ap.add_argument("folder", help="the old copy's output folder (the one holding internify.db)")
    ap.add_argument("--name", default="", help="the name to show for this person")
    ap.add_argument("--dry-run", action="store_true", help="show what would be moved, change nothing")
    ap.add_argument("--replace", action="store_true", help="overwrite the data of an account that already exists")
    ap.add_argument("--telegram-id", type=int, default=None, help="use this Telegram id instead of the one the old copy recorded")
    a = ap.parse_args(argv[1:])
    os.environ.setdefault("MODE", "cloud")
    from bot.services import db
    from service import users
    if not users.cloud():
        print("MODE=cloud is not set here. Run this where the hosted bot's settings are.")
        return 1
    db.init_db()
    from bot import migrate
    try:
        r = migrate.migrate(a.folder, a.name, a.replace, a.dry_run, a.telegram_id)
    except migrate.MigrateError as e:
        print(f"Stopped: {e}")
        return 1
    c = r.counts
    print(("Would move" if r.dry_run else "Moved") + f": {r.facts} saved experience entries, {c.get('applications', 0)} applications, "
          f"{c.get('watch_sources', 0)} watched job pages, {c.get('seen_jobs', 0)} seen jobs, for Telegram id {r.telegram_id}.")
    if not r.dry_run:
        print(f"Account {r.user_id} ({'created' if r.created else 'replaced'}), plan Unlimited. "
              "Now open the hosted bot, send /start, and connect Google.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
