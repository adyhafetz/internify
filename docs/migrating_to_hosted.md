# Moving a personal copy into Internify Cloud

For the maintainer and friends who already use a personal copy. Each person keeps their saved experience, applications, watched pages and job-search settings, and gets the **Unlimited** plan (no limits).

What moves: saved experience, resume layout, applications, watched job pages, seen jobs, job-search settings, GitHub index, outcomes.
What does not: the Google connection. After the move each person sends `/start` in the hosted bot and connects Google, which makes a new sheet with their applications in it.

## Steps, for each copy

1. **Back up first.** On the old copy's server, copy its whole `output` folder somewhere safe.
2. **Sync the sheet.** In the old bot, send `/applied` so statuses you changed in the sheet are read into the database.
3. **Stop the old bot** so nothing changes during the move: `docker compose stop` in the old folder.
4. **Dry run** (changes nothing):

```bash
cd ~/internify-cloud
docker compose run --rm -v ~/internify-sofea/output:/old:ro telegram-bot python scripts/migrate_copy.py /old --name "Friend" --dry-run
```

It prints what would move and for which Telegram id. The id comes from the old copy; if the old copy never recorded one, add `--telegram-id 123456789`.

5. **Do it:** run the same command without `--dry-run`. If the account already exists (for example the person already pressed `/start`), add `--replace` to overwrite it.
6. **Check in Telegram** (the person's own account): `/plan` says Unlimited, `/applied` shows the applications, `/start` then connect Google.
7. **Only after everything checks out**, retire the old copy: `docker compose down` in the old folder, stop its tunnel and remove its DNS record. Keep the backup from step 1 for a few weeks.

The old `output` folder is opened read-only and is never changed, so you can always go back.

## For the maintainer's own copy

Same steps. Use the same Telegram id as the old copy so existing chat history continues to make sense. If you reuse the old tunnel token for the hosted site, stop the old copy first so the tunnel is free.
