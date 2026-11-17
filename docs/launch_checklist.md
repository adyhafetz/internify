# Launch checklist

Tick these in order. Anything not ticked is a reason to wait.

## Before anyone else signs up
- [ ] Hosted bot running with its own `.env` (see `docs/hosted_setup.md`); `/admin` works from your account only.
- [ ] Start-up warnings from the bot are all cleared (site address, payment text, Google, backups).
- [ ] A **real Google account** that is not yours completes Connect Google, makes a sheet and a resume lands in it. Disconnecting works and revokes access.
- [ ] A full test with a new Telegram account: `/start`, paste a job, get a PDF, hit the free limit, see the payment reference, then `/setplan` it and see it unlock.
- [ ] A backup exists, has been copied off the server, and a restore was tested.
- [ ] Uptime monitor on `/healthz` alerts your phone.
- [ ] `GOOGLE_TOKEN_KEY` and `.env` are stored privately away from the server.
- [ ] Google app: publish it ("In production") so connections do not expire after 7 days. The narrow `drive.file` scope avoids the full security review, but check Google's current rules.
- [ ] A person with legal knowledge has read `/privacy` and `/terms` and the data retention promise (6 months inactive).

## Move your own copies in
- [ ] Follow `docs/migrating_to_hosted.md` for yours and your friend's copy; retire the old copy only after both work.

## Publish
- [ ] Docker image published (push a `v*` tag, set the package to Public) so self-hosters can use it.
- [ ] Site shows the right price, bot link and payment instructions; link previews look right in Telegram.
- [ ] Submit `PUBLIC_URL/sitemap.xml` in Google Search Console.
- [ ] Soft launch to a small group first. Watch `/admin` for AI usage against the monthly ceiling.
