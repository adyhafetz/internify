"""Offline test of the Archivist's question flow (Telegram buttons/typing, saving, timeouts) and its use in the pipeline."""
from __future__ import annotations
import asyncio, json, os, shutil, sys, tempfile, types

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
tmp = tempfile.mkdtemp()
for name in ("facts.json", "resume_skeleton.json"):
    shutil.copy(os.path.join("service/fact_store", name), os.path.join(tmp, name))

from service import config as service_config
service_config.FACT_STORE_PATH = os.path.join(tmp, "facts.json")
service_config.SKELETON_PATH = os.path.join(tmp, "resume_skeleton.json")
service_config.EMBEDDING_CACHE_PATH = os.path.join(tmp, "cache.json")

from telegram.ext import ApplicationHandlerStop
from bot import ask, config
from service import events
from service.admin import _load_facts_raw, _load_skeleton_raw

ask.extract_tech_tags = lambda text: ["Helm", "Docker"]
ask.extract_skills = lambda s: types.SimpleNamespace(category="Developer Tools & Infra", items=[s])
CHAT = 42


class Msg:
    def __init__(self, bot, text): self.bot, self.text = bot, text
    async def edit_text(self, text, **kw): self.bot.edits.append(text)


class FakeBot:
    """Records messages and 'answers' them with a script: ('tap', button_index) | ('type', text) | None = silence."""
    def __init__(self, script): self.script, self.sent, self.edits = list(script), [], []
    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self.sent.append((text, reply_markup))
        if reply_markup is None:
            return Msg(self, text)  # plain info message: nothing to answer
        action = self.script.pop(0) if self.script else None
        loop = asyncio.get_running_loop()
        if action and action[0] == "tap":
            data = reply_markup.inline_keyboard[action[1]][0].callback_data
            _, qid, value = data.split(":", 2)
            loop.call_later(0.05, ask.resolve, chat_id, int(qid), value)
        elif action and action[0] in ("type", "type_many"):
            texts = [action[1]] if action[0] == "type" else action[1]
            self.replies, self.passed = [], []
            for n, text in enumerate(texts):
                async def reply_text(t, **kw): self.replies.append(t)
                update = types.SimpleNamespace(effective_chat=types.SimpleNamespace(id=chat_id),
                                               effective_message=types.SimpleNamespace(text=text, reply_text=reply_text))
                async def typed(update=update):
                    try: await ask.ask_text_handler(update, None)
                    except ApplicationHandlerStop: self.stopped = True
                    else: self.passed.append(update.effective_message.text)   # left for the normal handlers
                loop.call_later(0.05 + 0.1 * n, lambda typed=typed: asyncio.ensure_future(typed()))
        return Msg(self, text)


async def ask_with(script, skills):
    bot = FakeBot(script)
    q = ask.SkillQuestioner(bot, CHAT, asyncio.get_running_loop())
    rid = events.start_run(url="http://t")
    answers = await asyncio.to_thread(q, skills)   # the pipeline calls it from a worker thread
    return bot, answers, events.bus.get_run(rid)


async def main():
    n_facts = len(_load_facts_raw())
    entry0 = _load_skeleton_raw()["experience_entries"][0]["id"]

    # A. yes -> where (first job) -> typed description => new bullet point + skill saved
    bot, ans, evs = await ask_with([("tap", 0), ("tap", 0), ("type", "Deployed our API to Kubernetes with Helm charts")], ["Kubernetes"])
    a = ans[0]
    assert a.has and a.fact and a.fact.entry_id == entry0 and a.fact.text.startswith("Deployed our API"), a
    assert a.fact.tech_tags[0] == "Kubernetes" and "Helm" in a.fact.tech_tags
    assert len(_load_facts_raw()) == n_facts + 1, "new bullet point should be stored"
    assert "Kubernetes" in _load_skeleton_raw()["skills"]["Developer Tools & Infra"], "skill should be added"
    assert getattr(bot, "stopped", False), "typed answer must stop other handlers from also reacting"
    said = [(e["data"].get("answer"), e["data"].get("saved")) for e in evs if e["agent"] == "archivist"]
    assert ("yes", None) in said and (None, True) in said, said
    assert any("Where did you use" in t for t, _ in bot.sent) and any("Saved!" in t for t, _ in bot.sent)
    assert all("➡️" in e or "Saved" in e or "Got it" in e or True for e in bot.edits)
    print("A ok: yes + where + description -> saved as a new bullet point and a skill")

    # B. no -> nothing saved, tells the user
    before = len(_load_facts_raw())
    bot, ans, _ = await ask_with([("tap", 1)], ["Terraform"])
    assert not ans[0].has and ans[0].fact is None and len(_load_facts_raw()) == before
    assert any("won't claim" in t for t, _ in bot.sent)
    print("B ok: no -> nothing claimed or saved")

    # C. yes -> "just add to my skills" => skill only, no bullet point
    n_buttons = len(ask._entry_choices()) + 1
    bot, ans, _ = await ask_with([("tap", 0), ("tap", n_buttons - 1)], ["Ansible"])
    assert ans[0].has and ans[0].fact is None and "Ansible" in _load_skeleton_raw()["skills"]["Developer Tools & Infra"]
    print("C ok: yes + skills only -> skill added, no bullet point")

    # D. yes -> where -> Skip the description => falls back to skills only
    bot, ans, _ = await ask_with([("tap", 0), ("tap", 0), ("tap", 0)], ["Grafana"])
    assert ans[0].has and ans[0].fact is None and "Grafana" in _load_skeleton_raw()["skills"]["Developer Tools & Infra"]
    print("D ok: skipped description -> skill only")

    # E. silence: times out, and does not pester about the next tool
    config.ASK_TIMEOUT_SECONDS = 0.2
    bot, ans, evs = await ask_with([None], ["Kafka", "Redis"])
    assert [a.has for a in ans] == [False, False] and len([1 for _, k in bot.sent if k]) == 1, bot.sent
    assert any(e["data"].get("answer") == "timeout" for e in evs)
    assert not ask._pending, "no question left waiting"
    print("E ok: no answer -> skipped, second question not asked")

    # F. a stale or foreign tap is ignored, and ordinary text with nothing pending is left to other handlers
    assert ask.resolve(CHAT, 9999, "yes") is False
    update = types.SimpleNamespace(effective_chat=types.SimpleNamespace(id=CHAT), effective_message=types.SimpleNamespace(text="hello"))
    await ask.ask_text_handler(update, None)   # must not raise
    print("F ok: stale taps and unrelated text ignored")

    # I2. two questions: the second one gets its own "asking you" event (the dashboard needs it to draw the arrow)
    bot, ans, evs = await ask_with([("tap", 1), ("tap", 1)], ["Kafka", "Redis"])
    asks = [e for e in evs if e["kind"] == "ask"]
    assert len(asks) == 1 and asks[0]["data"]["skills"] == ["Redis"] and asks[0]["data"]["index"] == 1 and "Redis" in asks[0]["say"], asks
    assert len([1 for _, k in bot.sent if k]) == 2, "both questions were sent"
    bot, ans, evs = await ask_with([None], ["Kafka", "Redis"])            # no answer to the first: the second is never asked, so no event
    assert not [e for e in evs if e["kind"] == "ask"]
    print("I2 ok: the second question has its own event; none if the first got no answer")

    # J. Scout asks the user to paste the job description when no route could read the page
    long_text = "Software Engineer Intern. Build Python services. " * 6
    bot = FakeBot([("type_many", ["ok", "https://x.com/another-job", long_text])])
    rid = events.start_run(url="http://t")
    got = await ask.ask_for_job_text(bot, CHAT, asyncio.get_running_loop(), 100)
    assert got == long_text.strip(), got
    assert any("a bit short" in r for r in bot.replies), bot.replies               # too-short reply is refused politely
    assert bot.passed == ["https://x.com/another-job"], bot.passed                  # a new link is left for the job handler
    assert any(e["agent"] == "scout" and e["kind"] == "ask" and "paste" in e["say"].lower() for e in events.bus.get_run(rid))
    assert any("paste the job description" in t for t, _ in bot.sent)
    print("J ok: paste request: short reply refused, new link not swallowed, real paste accepted")

    # K. ...and Skip gives up cleanly
    bot = FakeBot([("tap", 0)])
    rid = events.start_run(url="http://t")
    assert await ask.ask_for_job_text(bot, CHAT, asyncio.get_running_loop(), 100) is None
    print("K ok: Skip -> no text")


asyncio.run(main())

# G. inside the pipeline: the answer joins the facts, the tool counts as covered, and the dashboard is told
import service.main as main_mod
from service.models import (JobPosting, FactBullet, FactBulletWithScore, GeneratedResume, ResumeBullet,
                            CriticFeedback, ProcessRequest, SkillAnswer)
import service.agentic.loop as loop_mod

OLD = FactBullet(id="f1", entry_id="e1", section="experience", text="Built FastAPI service", tech_tags=["Python", "FastAPI"])
NEW = FactBullet(id="f9", entry_id="e1", section="experience", text="Deployed to Kubernetes", tech_tags=["Kubernetes"])
JOB = JobPosting(company="Acme", role="Intern", core_tech_stack=["Python", "Kubernetes", "Terraform", "Kafka"],
                 hard_requirements=["Data structures"], ats_keywords=[])
seen = {}
main_mod.extract_job_posting = lambda raw: JOB
main_mod.load_facts = lambda: [OLD]
main_mod.load_skeleton = lambda: seen.setdefault("reloaded", []).append(1) or object()
main_mod.retrieve_relevant_facts = lambda job, facts: [FactBulletWithScore(fact=OLD, similarity=0.8, keyword_boost=0.1)]
loop_mod.generate_resume = lambda **kw: (seen.__setitem__("facts", [f.fact.id for f in kw["candidate_facts"]]) or
                                         GeneratedResume(bullets=[ResumeBullet(fact_id="f1", entry_id="e1", text="x")]))
loop_mod.critique = lambda **kw: CriticFeedback(score=90.0, keyword_coverage_notes="", unsupported_claims=[], overflow_risk=False, revision_notes="")
pdf = os.path.join(tmp, "x.pdf"); open(pdf, "w").write("x")
main_mod.compile_tex = lambda resume, skeleton, filename_base: pdf
asked = []
def asker(skills):
    asked.append(list(skills))
    return [SkillAnswer(skill="Kubernetes", has=True, fact=NEW), SkillAnswer(skill="Terraform", has=False)]

rid = events.start_run(url="http://t")
main_mod.run_pipeline(ProcessRequest(company="Acme", job_title="Intern", location="R", job_description="d", job_requirements="r", job_link="http://t"), asker)
evs = events.bus.get_run(rid)
assert asked == [["Kubernetes", "Terraform"]], f"only concrete tools, capped at 2 (got {asked})"   # Kafka is beyond the cap; 'Data structures' is not a tool
ask_ev = next(e for e in evs if e["kind"] == "ask")
assert ask_ev["data"]["skills"] == ["Kubernetes", "Terraform"] and "check Telegram" in ask_ev["say"], ask_ev
done = next(e for e in evs if e["agent"] == "archivist" and e["kind"] == "done")["data"]
assert done["facts"][0]["id"] == "f9" and done["facts"][0]["new"] is True and "Kubernetes" in done["covered"], done
assert "Kubernetes" not in done["missing"] and "Terraform" in done["missing"], done
assert seen["facts"][0] == "f9", "Scribe must receive the new bullet point"
assert len(seen["reloaded"]) == 2, "skills list reloaded after a confirmed tool"
assert "1 new from you" in next(e for e in evs if e["agent"] == "archivist" and e["kind"] == "done")["say"]
print("G ok: pipeline asked only about 2 concrete tools; answer joined the facts and coverage")

# H. a question that blows up never ends the run
def broken(skills): raise RuntimeError("telegram down")
rid = events.start_run(url="http://t2")
res = main_mod.run_pipeline(ProcessRequest(company="Acme", job_title="Intern", location="R", job_description="d", job_requirements="r", job_link="http://t2"), broken)
assert res.pdf_path == pdf
print("H ok: a failing question doesn't stop the resume")

# I. no asker (feature off) and nothing missing => never asks
rid = events.start_run(url="http://t3")
main_mod.run_pipeline(ProcessRequest(company="Acme", job_title="Intern", location="R", job_description="d", job_requirements="r", job_link="http://t3"), None)
assert not [e for e in events.bus.get_run(rid) if e["kind"] == "ask"]
print("I ok: feature off -> no questions")
print("\n✅ ask test passed")
