"""Proves with the real python-telegram-bot Application that an answer is processed WHILE a long job handler is
still running (needs block=False on the job handler). Offline: no network, no token."""
import asyncio, os, sys, time
sys.path.insert(0, os.path.abspath("."))
from telegram import Update, User
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, MessageHandler, filters, ExtBot


class OfflineBot(ExtBot):
    async def initialize(self):                   # the real one calls Telegram (get_me); fake the bot's identity
        object.__setattr__(self, "_bot_user", User(id=1, first_name="bot", is_bot=True, username="testbot"))
        object.__setattr__(self, "_initialized", True)
    async def shutdown(self): return None


async def scenario(block: bool) -> str:
    app: Application = ApplicationBuilder().bot(OfflineBot("123:abc")).build()
    answer: asyncio.Future = asyncio.get_running_loop().create_future()

    async def job(update, context):            # like job_url_handler: waits for the user's answer mid-run
        await asyncio.wait_for(answer, 2)
        job.finished = True
    job.finished = False

    async def tap(update, context):            # like ask_callback
        if not answer.done(): answer.set_result("yes")

    app.add_handler(CallbackQueryHandler(tap, pattern="^ask:"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("https?://"), job, block=block))
    await app.initialize(); await app.start()
    msg = {"message_id": 1, "date": int(time.time()), "chat": {"id": 7, "type": "private"},
           "from": {"id": 1, "is_bot": False, "first_name": "T"}, "text": "https://x.com/job"}
    await app.update_queue.put(Update.de_json({"update_id": 1, "message": msg}, app.bot))
    await asyncio.sleep(0.2)                                   # the job is now running and waiting
    await app.update_queue.put(Update.de_json({"update_id": 2, "callback_query": {
        "id": "1", "from": {"id": 1, "is_bot": False, "first_name": "T"}, "chat_instance": "c", "data": "ask:1:yes",
        "message": {**msg, "text": "question"}}}, app.bot))
    await asyncio.sleep(0.6)
    result = "answer processed during the run" if answer.done() else "answer stuck behind the running job"
    await app.stop(); await app.shutdown()
    return result


async def main():
    blocking = await scenario(block=True)
    nonblocking = await scenario(block=False)
    print("block=True  (old behaviour):", blocking)
    print("block=False (what the bot uses now):", nonblocking)
    assert nonblocking == "answer processed during the run"
    assert blocking == "answer stuck behind the running job", "if this changes, block=False is no longer needed"
    print("\n✅ concurrency test passed")

asyncio.run(main())
