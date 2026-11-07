"""Offline check that the real pipeline emits agent events. Mocks every LLM/LaTeX call; needs no API keys."""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ["OUTPUT_DIR"] = tempfile.mkdtemp()

from service import events
from service.models import (JobPosting, FactBullet, FactBulletWithScore, GeneratedResume, ResumeBullet,
                            CriticFeedback, ProcessRequest)
import service.main as main
import service.agentic.loop as loop
from bot.progress import TelegramProgress

FACT = FactBullet(id="f1", entry_id="e1", section="experience", text="Built FastAPI service", tech_tags=["Python", "FastAPI"])
JOB = JobPosting(company="Acme", role="Intern", core_tech_stack=["Python", "Kubernetes"], hard_requirements=["FastAPI"], ats_keywords=["Python"])
scores = iter([40.0, 90.0])

main.extract_job_posting = lambda raw: JOB
main.load_facts = lambda: [FACT]
main.load_skeleton = lambda: object()
main.retrieve_relevant_facts = lambda job, facts: [FactBulletWithScore(fact=FACT, similarity=0.8, keyword_boost=0.1)]
loop.generate_resume = lambda **kw: GeneratedResume(bullets=[ResumeBullet(fact_id="f1", entry_id="e1", text="Built FastAPI service")])
loop.critique = lambda **kw: CriticFeedback(score=next(scores), keyword_coverage_notes="", unsupported_claims=[], overflow_risk=False, revision_notes="add more")
pdf = os.path.join(os.environ["OUTPUT_DIR"], "x.pdf"); open(pdf, "w").write("x")
main.compile_tex = lambda resume, skeleton, filename_base: pdf


class FakeMsg:
    def __init__(self): self.texts = []
    async def edit_text(self, t, **kw): self.texts.append(t)


async def run():
    msg = FakeMsg()
    rid = events.start_run(url="http://x")
    prog = TelegramProgress(msg, rid, asyncio.get_running_loop())
    req = ProcessRequest(company="Acme", job_title="Intern", location="R", job_description="d", job_requirements="r", job_link="http://x")
    res = await asyncio.to_thread(main.process, req)   # same call shape as the bot handler
    await asyncio.sleep(1.6)
    await prog.close()
    events.emit_run("finished")   # same task/context as start_run, like the real handler
    return rid, res, msg


rid, res, msg = asyncio.run(run())
evs = events.bus.get_run(rid)
seq = [(e["agent"], e["kind"], e["to"]) for e in evs if e["kind"] != "run"]
print("\n".join(f"  {a:10} {k:9} {t or ''}" for a, k, t in seq))

assert res.pdf_path == pdf
assert ("scribe", "handoff", "critic") in seq and ("critic", "handoff", "scribe") in seq, "Scribe<->Critic loop missing"
assert ("critic", "handoff", "publisher") in seq and ("archivist", "handoff", "scribe") in seq
arch = next(e for e in evs if e["agent"] == "archivist" and e["kind"] == "done")
assert arch["data"]["missing"] == ["Kubernetes"], arch["data"]
assert [e["data"]["score"] for e in evs if e["agent"] == "critic" and e["kind"] == "done"] == [40.0, 90.0]
assert msg.texts and "Scribe" in msg.texts[-1] and "40 → 90" in msg.texts[-1], msg.texts[-1:]
assert events.load_trace(rid), "trace not saved"
# outside a run, emit is a harmless no-op
events.use_run(None); events.emit("scout", "start", "x")
print("\nTelegram progress message (final):\n" + msg.texts[-1])
print("\n✅ events test passed")
