"""Offline test of /profile: data operations for every kind of entry, screens, and the tap-and-type flows."""
from __future__ import annotations
import asyncio, os, shutil, sys, tempfile, types
from html.parser import HTMLParser

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
tmp = tempfile.mkdtemp()
for name in ("facts.json", "resume_skeleton.json"):
    shutil.copy(os.path.join("service/fact_store", name), os.path.join(tmp, name))
from service import config as service_config
service_config.FACT_STORE_PATH = os.path.join(tmp, "facts.json")
service_config.SKELETON_PATH = os.path.join(tmp, "resume_skeleton.json")
service_config.EMBEDDING_CACHE_PATH = os.path.join(tmp, "cache.json")

from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop
from service import editor, importer
from service.editor import EditError
from service.admin import (add_experience, AddExperienceRequest, add_project, AddProjectRequest, NewFactBullet,
                           add_education, AddEducationRequest, add_leadership, AddLeadershipRequest,
                           _load_facts_raw, _load_skeleton_raw)
from bot.handlers import profile_hub as eb

importer._clear_everything()
add_education(AddEducationRequest(institution="Universiti Example", location="Selangor", degree_line="B.Sc. Computer Science",
                                  dates="2022 -- 2026", courses=["Algorithms", "Databases"], honor_bullets=["Dean's List", "Scholarship"]))
acme = add_experience(AddExperienceRequest(title="Software Intern", company="Acme Sdn Bhd", location="KL", dates="Jun 2024 -- Aug 2024",
                      bullets=[NewFactBullet(text="Built REST APIs with FastAPI", tech_tags=["FastAPI"]), NewFactBullet(text="Containerized with Docker", tech_tags=["Docker"]),
                               NewFactBullet(text="Wrote unit tests", tech_tags=[])])).entry_id
longco = add_experience(AddExperienceRequest(title="Backend Developer", company="The Very Long Company Name Holdings International Berhad",
                        location="KL", dates="2025", bullets=[NewFactBullet(text="Did things")])).entry_id
many = add_project(AddProjectRequest(name="TaskFlow", tech_stack="React, Go", date="Dec 2024",
                   bullets=[NewFactBullet(text=f"Bullet number {i} " + "x" * 380) for i in range(1, 15)])).entry_id
odd = add_project(AddProjectRequest(name="my_app *v2*", tech_stack="Go", date="2025", bullets=[NewFactBullet(text="x_y_z <b>& [1] `q`")])).entry_id
add_leadership(AddLeadershipRequest(title="Treasurer", org="CS Society", location="Uni", dates="2023", bullets=["Managed a RM 5,000 budget", "Ran the annual dinner"]))
add_leadership(AddLeadershipRequest(title="Treasurer", org="Chess Club", location="Uni", dates="2024", bullets=["Collected fees"]))

# ================================================================== A. data layer
es = editor.list_entries()
assert [e.kind for e in es] == ["edu", "exp", "exp", "proj", "proj", "lead", "lead"], [e.kind for e in es]
assert es[1].label == "Software Intern · Acme Sdn Bhd" and es[1].count == 3 and es[3].count == 14 and es[5].ref == "0" and es[6].ref == "1"
assert es[0].label == "B.Sc. Computer Science · Universiti Example" and es[0].count == 2
print("A1 ok: entries listed in resume order with readable labels and counts")

v = editor.get_entry("exp", acme)
assert [i.text for i in v.items][0] == "Built REST APIs with FastAPI" and v.noun == "bullet point" and v.has_tags and "Jun 2024" in v.subtitle
assert all(len(i.token) == 8 for i in v.items) and len({i.token for i in v.items}) == 3
assert editor.get_entry("edu", "edu_universiti_example").courses == ["Algorithms", "Databases"]
assert editor.get_entry("lead", "1").items[0].text == "Collected fees" and editor.get_entry("lead", "9") is None and editor.get_entry("exp", "nope") is None
print("A2 ok: every kind opens as numbered items; unknown entries return None")

# change things: jobs/projects (facts) AND leadership bullets / education honors (the gap)
tok = v.items[1].token
editor.edit_item("exp", acme, tok, "Containerized services with Docker and Compose", ["Docker", "Docker Compose"])
f = next(f for f in _load_facts_raw() if f["entry_id"] == acme and f["text"].startswith("Containerized services"))
assert f["tech_tags"] == ["Docker", "Docker Compose"]
new = editor.add_item("exp", acme, "  Cut build time   by 30%  ", ["CI"])
assert new.text == "Cut build time by 30%" and len(editor.get_entry("exp", acme).items) == 4
editor.delete_item("exp", acme, new.token)
assert len(editor.get_entry("exp", acme).items) == 3
editor.edit_item("lead", "0", "1", "Ran the annual dinner for 120 members")
editor.add_item("lead", "0", "Cut costs by 15%")
editor.delete_item("lead", "0", "0")
assert [i.text for i in editor.get_entry("lead", "0").items] == ["Ran the annual dinner for 120 members", "Cut costs by 15%"]
assert [i.text for i in editor.get_entry("lead", "1").items] == ["Collected fees"], "the other Treasurer role is untouched"
editor.edit_item("edu", "edu_universiti_example", "0", "Dean's List (all semesters)")
editor.add_item("edu", "edu_universiti_example", "Best Final Year Project")
editor.delete_item("edu", "edu_universiti_example", "1")
assert [i.text for i in editor.get_entry("edu", "edu_universiti_example").items] == ["Dean's List (all semesters)", "Best Final Year Project"]
print("A3 ok: add / edit / delete work for jobs, projects, leadership bullets and education honors")

for bad, why in ((lambda: editor.add_item("exp", acme, "   "), "empty"), (lambda: editor.add_item("exp", acme, "x" * 401), "too long"),
                 (lambda: editor.edit_item("exp", acme, "deadbeef", "x"), "stale fact"), (lambda: editor.delete_item("lead", "0", "99"), "stale line"),
                 (lambda: editor.add_item("exp", "nope", "x"), "unknown entry"), (lambda: editor.edit_item("lead", "7", "0", "x"), "unknown role")):
    try: bad()
    except EditError as e: assert str(e), why
    else: raise AssertionError(f"{why} should be refused")
print("A4 ok: empty, over-long and stale requests are refused with a plain message")

assert [m.ref for m in editor.find_entries("acme")] == [acme] and [m.ref for m in editor.find_entries("ACME intern")] == [acme]
assert [m.ref for m in editor.find_entries(acme)] == [acme], "an exact internal id still works as a shortcut"
assert len(editor.find_entries("treasurer")) == 2 and editor.find_entries("zzz") == [] and editor.find_entries("  ") == []
assert [m.kind for m in editor.find_entries("chess")] == ["lead"] and [m.ref for m in editor.find_entries("taskflow")] == [many]
print("A5 ok: entries are found by name (any word order/case), or by id")

assert editor.add_course("edu_universiti_example", "Operating Systems") and not editor.add_course("edu_universiti_example", "operating systems")
assert editor.remove_course("edu_universiti_example", 0) == "Algorithms"
try: editor.remove_course("edu_universiti_example", 9)
except EditError: pass
else: raise AssertionError
assert editor.get_entry("edu", "edu_universiti_example").courses == ["Databases", "Operating Systems"]
print("A6 ok: courses add (no duplicates) and remove")

n = editor.delete_entry("lead", "0")
assert n == 2 and [e.label for e in editor.list_entries() if e.kind == "lead"] == ["Treasurer · Chess Club"], "deleting one Treasurer must not remove the other"
assert editor.delete_entry("exp", longco) == 1 and not any(f["entry_id"] == longco for f in _load_facts_raw())
print("A7 ok: delete entry (by position for leadership, with its bullet points for jobs)")
longco = add_experience(AddExperienceRequest(title="Backend Developer", company="The Very Long Company Name Holdings International Berhad",
                        location="KL", dates="2025", bullets=[NewFactBullet(text="Did things")])).entry_id
add_leadership(AddLeadershipRequest(title="Treasurer", org="CS Society", location="Uni", dates="2023", bullets=["Managed a RM 5,000 budget"]))


# ================================================================== B. screens
class Checker(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=False); self.stack, self.text = [], []
    def handle_starttag(self, tag, attrs): assert tag in ("b", "i", "code", "pre", "a"), tag; self.stack.append(tag)
    def handle_endtag(self, tag): assert self.stack and self.stack.pop() == tag, tag
    def handle_data(self, d): assert "<" not in d and "&" not in d, d; self.text.append(d)
    def handle_entityref(self, n): assert n in ("amp", "lt", "gt"), n


def valid(markup: str) -> None:
    c = Checker(); c.feed(markup); c.close(); assert not c.stack


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def all_screens():
    yield "list", eb.list_screen()
    yield "contact", eb.contact_screen()
    yield "skills", eb.skills_screen()
    yield "add menu", eb.add_menu_screen()
    for i, _, _ in editor.list_skill_categories():
        yield f"skill category {i}", eb.skill_category_screen(i)
    for e in editor.list_entries():
        yield e.label, eb.entry_screen(e.kind, e.ref)
        v = editor.get_entry(e.kind, e.ref)
        for page in range(1, (len(v.items) - 1) // eb.ITEMS_PER_PAGE + 1):
            yield f"{e.label} p{page + 1}", eb.entry_screen(e.kind, e.ref, page)


internal = {e.ref for e in editor.list_entries() if e.kind != "lead"} | {i.token for e in editor.list_entries() for i in editor.get_entry(e.kind, e.ref).items if len(i.token) == 8}   # entry ids and fact tokens (list positions are just the visible numbers)
for label, (text, markup) in all_screens():
    valid(text)
    assert len(text) < 4096, (label, len(text))
    for b in buttons(markup):
        assert len(b.callback_data.encode()) <= 64, (label, b.callback_data, len(b.callback_data.encode()))
    for secret in internal:
        assert secret not in text, f"internal reference {secret!r} leaked into {label!r}"
print("B1 ok: all screens are valid, under Telegram's size limits, every button fits (64 bytes), no internal ids shown")

text, markup = eb.entry_screen("proj", many)
assert "page 1 of 3" in text and "1. Bullet number 1" in text and "6. Bullet number 6" in text and "7." not in text
assert [b.text for b in buttons(markup)][:4] == ["✏️ 1", "🗑 1", "✏️ 2", "🗑 2"] and "Next ▶" in [b.text for b in buttons(markup)] and "◀ Previous" not in [b.text for b in buttons(markup)]
text3, markup3 = eb.entry_screen("proj", many, 2)
assert "13. Bullet number 13" in text3 and "14." in text3 and "◀ Previous" in [b.text for b in buttons(markup3)] and "Next ▶" not in [b.text for b in buttons(markup3)]
print("B2 ok: long lists are paged (6 per page) with Previous / Next")

text, markup = eb.entry_screen("proj", odd)
assert "my_app *v2*" in text and "x_y_z &lt;b&gt;&amp; [1] `q`" in text, text
print("B3 ok: awkward characters in names and bullet points are shown as typed")
assert eb.entry_screen("exp", "gone") is None
importer._clear_everything()
assert "Nothing saved yet" in eb.list_screen()[0] and "/importresume" in eb.list_screen()[0]
print("B4 ok: missing entry -> None; empty profile points to /importresume")
add_experience(AddExperienceRequest(title="Software Intern", company="Acme Sdn Bhd", location="KL", dates="Jun 2024 -- Aug 2024",
               bullets=[NewFactBullet(text="Built REST APIs with FastAPI", tech_tags=["FastAPI"]), NewFactBullet(text="Containerized with Docker", tech_tags=["Docker"]),
                        NewFactBullet(text="Wrote unit tests", tech_tags=[])]))
add_education(AddEducationRequest(institution="Universiti Example", location="Selangor", degree_line="B.Sc. Computer Science",
                                  dates="2022 -- 2026", courses=["Algorithms", "Databases"], honor_bullets=["Dean's List", "Scholarship"]))
add_leadership(AddLeadershipRequest(title="Treasurer", org="CS Society", location="Uni", dates="2023", bullets=["Managed a RM 5,000 budget", "Ran the annual dinner"]))
add_leadership(AddLeadershipRequest(title="Treasurer", org="Chess Club", location="Uni", dates="2024", bullets=["Collected fees"]))
ACME = next(e.ref for e in editor.list_entries() if e.label.startswith("Software Intern"))
EDU = next(e.ref for e in editor.list_entries() if e.kind == "edu")


# ================================================================== C. flows
eb.extract_tech_tags = lambda text: ["Docker", "Python"]
USER = types.SimpleNamespace(id=1, username="t")


class FakeQuery:
    def __init__(self, data, log, fail=None): self.data, self.log, self.fail, self.message = data, log, fail, types.SimpleNamespace(chat_id=1)
    async def answer(self, *a, **k): pass
    async def edit_message_text(self, text, parse_mode=None, reply_markup=None, **k):
        if self.fail: raise self.fail
        valid(text); self.log.append((text, reply_markup))


def tap(ctx, data, fail=None):
    log = []
    asyncio.run(eb.xp_callback(types.SimpleNamespace(callback_query=FakeQuery(data, log, fail), effective_user=USER, effective_message=None), ctx))
    return log[-1] if log else None


def find(markup, prefix):
    return next(b.callback_data for b in buttons(markup) if b.text.startswith(prefix))


class FakeMsg:
    def __init__(self, text=""): self.text, self.sent = text, []
    async def reply_text(self, text, parse_mode=None, reply_markup=None, **k): valid(text) if parse_mode == "HTML" else None; self.sent.append((text, reply_markup))


def say(ctx, text):
    msg = FakeMsg(text)
    try: asyncio.run(eb.xp_text_handler(types.SimpleNamespace(effective_message=msg, effective_user=USER), ctx))
    except ApplicationHandlerStop: return msg, True
    return msg, False


def command(args, text="/experience"):
    msg = FakeMsg(text); ctx = types.SimpleNamespace(args=args, user_data={})
    asyncio.run(eb.profile_command(types.SimpleNamespace(effective_message=msg, effective_user=USER), ctx))
    return msg.sent[-1]


ctx = types.SimpleNamespace(user_data={})
# C1 edit a bullet point: open list -> open job -> tap ✏️ 2 -> type the new text
text, markup = tap(ctx, "xp:l")
assert "Your profile" in text and [b.text for b in buttons(markup)][0] == "👤 Contact details" and [b.text for b in buttons(markup)][-2:][0].startswith("🔧 Skills") and [b.text for b in buttons(markup)][-1] == "➕ Add something"
text, markup = tap(ctx, find(markup, "💼"))
assert "1. Built REST APIs with FastAPI" in text and "2. Containerized with Docker" in text
text, markup = tap(ctx, find(markup, "✏️ 2"))
assert "Editing bullet point 2" in text and "Containerized with Docker" in text and ctx.user_data[eb.WAIT_KEY]["action"] == "edit"
msg, consumed = say(ctx, "Containerized services with Docker and Compose")
shown, markup2 = msg.sent[-1]
assert consumed and "✅ Updated." in shown and "2. Containerized services with Docker and Compose" in shown and eb.WAIT_KEY not in ctx.user_data
saved = next(f for f in _load_facts_raw() if f["text"].startswith("Containerized services"))
assert saved["tech_tags"] == ["Docker", "Python"], "tags are re-extracted for the new text"
print("C1 ok: open -> ✏️ -> type -> saved, tags refreshed, updated screen shown")

# C2 add (lands on the page of the new line), C3 delete with confirmation
text, markup = tap(ctx, f"xp:o:exp:{ACME}:0")
tap(ctx, find(markup, "➕"))
msg, consumed = say(ctx, "Cut build time by 30%")
assert consumed and "✅ Added." in msg.sent[-1][0] and "4. Cut build time by 30%" in msg.sent[-1][0]
markup = msg.sent[-1][1]
text, conf = tap(ctx, find(markup, "🗑 4"))
assert "Delete this bullet point?" in text and "Cut build time by 30%" in text
text, markup = tap(ctx, find(conf, "No"))
assert "4. Cut build time by 30%" in text, "No keeps it"
text, conf = tap(ctx, find(markup, "🗑 4")); text, markup = tap(ctx, find(conf, "Yes"))
assert "Deleted bullet point 4" in text and "Cut build time" not in text.split("Deleted bullet point 4")[1]
print("C2/C3 ok: add shows the new line; delete asks first (No keeps, Yes deletes)")

# C4 the gap: leadership bullets and education honors are editable
lead0 = next(e.ref for e in editor.list_entries() if e.kind == "lead" and "CS Society" in e.label)
text, markup = tap(ctx, f"xp:o:lead:{lead0}:0")
tap(ctx, find(markup, "✏️ 1")); msg, _ = say(ctx, "Managed a RM 8,000 budget")
assert "2." in msg.sent[-1][0] and "1. Managed a RM 8,000 budget" in msg.sent[-1][0]
text, markup = tap(ctx, f"xp:o:edu:{EDU}:0")
assert "Courses:" in text and "Algorithms, Databases" in text and "Honors" in text
tap(ctx, find(markup, "➕ Add a course")); msg, consumed = say(ctx, "Operating Systems")
assert consumed and "✅ Course added." in msg.sent[-1][0] and "Operating Systems" in msg.sent[-1][0]
text, remove = tap(ctx, find(msg.sent[-1][1], "➖ Remove a course"))
assert "Which course" in text
text, markup = tap(ctx, find(remove, "➖ Algorithms"))
assert "Removed the course “Algorithms”" in text and "Algorithms" not in text.split("Courses:")[1].split("\n")[0]
tap(ctx, find(markup, "✏️ 1")); msg, _ = say(ctx, "Dean's List, all semesters")
assert "1. Dean's List, all semesters" in msg.sent[-1][0]
print("C4 ok: leadership bullets, education honors and courses are editable by tapping")

# C5 delete a whole entry
text, markup = tap(ctx, f"xp:o:lead:{next(e.ref for e in editor.list_entries() if e.kind == 'lead' and 'Chess' in e.label)}:0")
text, conf = tap(ctx, find(markup, "🗑 Delete this entry"))
assert "Delete “Treasurer · Chess Club” and its 1 bullet point?" in text and "can't be undone" in text
text, lst = tap(ctx, find(conf, "Yes"))
assert "Deleted “Treasurer · Chess Club” and its 1 line." in text and not any("Chess" in b.text for b in buttons(lst))
print("C5 ok: delete entry asks first and reports what went")

# C6 cancelling, expiry, over-long text, no pending question
ctx2 = types.SimpleNamespace(user_data={})
text, markup = tap(ctx2, f"xp:o:exp:{ACME}:0"); text, prompt = tap(ctx2, find(markup, "✏️ 1"))
assert eb.WAIT_KEY in ctx2.user_data
tap(ctx2, find(prompt, "⬅ Back"))
assert eb.WAIT_KEY not in ctx2.user_data and say(ctx2, "hello")[1] is False
text, markup = tap(ctx2, f"xp:o:exp:{ACME}:0"); tap(ctx2, find(markup, "✏️ 1"))
ctx2.user_data[eb.WAIT_KEY]["at"] -= eb.WAIT_SECONDS + 5
assert say(ctx2, "late answer")[1] is False and eb.WAIT_KEY not in ctx2.user_data
text, markup = tap(ctx2, f"xp:o:exp:{ACME}:0"); tap(ctx2, find(markup, "✏️ 1"))
msg, consumed = say(ctx2, "x" * 500)
assert consumed and "stay under 400" in msg.sent[-1][0] and eb.WAIT_KEY in ctx2.user_data, "too long: ask again, keep waiting"
msg, consumed = say(ctx2, "Short and good")
assert "✅ Updated." in msg.sent[-1][0] and eb.WAIT_KEY not in ctx2.user_data
print("C6 ok: Back cancels; an old prompt expires; over-long text is refused politely and the question stays open")

# C7 stale buttons (an old message in the chat) and an unchanged screen
old = tap(ctx2, f"xp:o:exp:{ACME}:0")[1]; edit_cb = find(old, "✏️ 3"); del_cb = find(old, "🗑 3")
editor.delete_item("exp", ACME, edit_cb.split(":")[-1])
text, _ = tap(ctx2, edit_cb); assert "already changed" in text
text, _ = tap(ctx2, del_cb); assert "already changed" in text
assert tap(ctx2, f"xp:o:exp:{ACME}:0", fail=BadRequest("Message is not modified")) is None, "tapping the same screen again is harmless"
text, lst = tap(ctx2, "xp:o:exp:gone:0"); assert "no longer exists" in text
print("C7 ok: stale buttons say so instead of acting on the wrong line; re-tapping a screen is harmless")

# C8 typed shortcuts open the same screens
text, markup = command([]); assert "Your profile" in text
text, markup = command(["acme"]); assert "Software Intern · Acme Sdn Bhd" in text and "Bullet points" in text
text, markup = command(["treasurer"]); assert "Treasurer · CS Society" in text, "a single match opens the entry directly"
text, markup = command([ACME]); assert "Software Intern" in text
text, markup = command(["zzz"]); assert "couldn't find anything called “zzz”" in text and buttons(markup)
add_leadership(AddLeadershipRequest(title="Treasurer", org="Robotics Club", location="Uni", dates="2025", bullets=["x"]))
text, markup = command(["treasurer"]); assert "Which one did you mean by “treasurer”?" in text and len(buttons(markup)) == 2
print("C8 ok: /experience [name], /listentries and /listfacts <name or id> open the same screens (one match, several, none)")

# C9 wiring
from bot import config as bot_config
bot_config.TELEGRAM_BOT_TOKEN = "123:abc"
from bot.main import create_bot_app
app = create_bot_app()
assert -2 in app.handlers and -1 in app.handlers, "Archivist/Scout answers (-2) and experience edits (-1) need separate groups"
names = {c for h in app.handlers[0] for c in getattr(h, "commands", [])}
assert {"profile", "experience", "myprofile", "listentries", "listfacts", "listeducation", "listskills"} <= names
print("C9 ok: handlers registered (separate groups for the two typed-answer handlers)")

# ================================================================== D. contact details, skills, adding, shortcuts, menu
ctx = types.SimpleNamespace(user_data={})
text, markup = tap(ctx, "xp:c")
assert "Contact details" in text and "Name:" in text and "Email:" in text
assert [b.text for b in buttons(markup)][:5] == ["✏️ Name", "✏️ Phone", "✏️ Email", "✏️ LinkedIn", "✏️ GitHub"]
text, prompt = tap(ctx, find(markup, "✏️ GitHub")); assert "New GitHub" in text and "Send - to remove it" in text
msg, consumed = say(ctx, "adystudent"); assert consumed and "✅ GitHub updated." in msg.sent[-1][0] and "github.com/adystudent" in msg.sent[-1][0]
text, markup = tap(ctx, "xp:c"); tap(ctx, find(markup, "✏️ Email"))
msg, consumed = say(ctx, "not an email"); assert consumed and "doesn't look like an email" in msg.sent[-1][0] and eb.WAIT_KEY in ctx.user_data, "bad value: explain, keep waiting"
msg, _ = say(ctx, "a_b@uni.edu.my"); assert "✅ Email updated." in msg.sent[-1][0] and "a_b@uni.edu.my" in msg.sent[-1][0]
text, markup = tap(ctx, "xp:c"); tap(ctx, find(markup, "✏️ LinkedIn")); msg, _ = say(ctx, "-")
assert "<i>not set</i>" in msg.sent[-1][0] and _load_skeleton_raw()["header"]["linkedin_url"] == ""
text, markup = tap(ctx, "xp:c"); tap(ctx, find(markup, "✏️ Name")); msg, _ = say(ctx, "-")
assert "can't be removed" in msg.sent[-1][0] and eb.WAIT_KEY in ctx.user_data and _load_skeleton_raw()["header"]["name"] not in ("", "-"), "the name can never be removed or blanked"
msg, _ = say(ctx, "12345"); assert "doesn't look like a name" in msg.sent[-1][0]
msg, _ = say(ctx, "Ady Student"); assert "✅ Name updated." in msg.sent[-1][0] and "<b>Name:</b> Ady Student" in msg.sent[-1][0]
tap(ctx, "xp:c")
print("D1 ok: contact details are edited by tapping (bare GitHub name becomes a link, bad email refused, '-' removes an optional one, name can't be blanked)")

# skills: categories -> category -> add by typing -> remove by tapping
text, markup = tap(ctx, "xp:s")
assert "Skills" in text and [b.text.split(" (")[0] for b in buttons(markup)][:5] == ["Languages", "Frameworks & Libraries", "Databases", "AI/ML", "Developer Tools & Infra"]
text, cat = tap(ctx, find(markup, "Databases")); assert "Databases" in text and "No skills here yet" in text and "Tap a skill to remove it." not in text
text, prompt = tap(ctx, find(cat, "➕ Add to Databases")); assert "separated by commas" in text
msg, consumed = say(ctx, "Redis, redis , Neo4j"); shown, cat = msg.sent[-1]
assert consumed and "✅ Added: Redis, Neo4j" in shown and "Already there: redis" in shown and "Tap a skill to remove it." in shown, shown   # the 2nd "redis" in one message is a duplicate
tap(ctx, find(cat, "➕ Add to Databases")); msg, _ = say(ctx, "neo4j, Cassandra")
assert "✅ Added: Cassandra" in msg.sent[-1][0] and "Already there: neo4j" in msg.sent[-1][0]
cat = msg.sent[-1][1]; text, cat = tap(ctx, find(cat, "✕ Redis"))
assert "✕ Removed Redis." in text and "Redis" not in [b.text[2:] for b in buttons(cat)]
stale = find(cat, "✕ Neo4j"); tap(ctx, stale); text, _ = tap(ctx, stale); assert "already changed" in text, "a second tap on the same chip is harmless"
cat = tap(ctx, find(tap(ctx, "xp:s")[1], "Languages"))[1]; tap(ctx, find(cat, "➕ Add to Languages")); msg, _ = say(ctx, ", ".join(f"Lang{i}" for i in range(20)))
text, cat = tap(ctx, "xp:sc:0:0"); assert "page 1 of 2" in text and "Next ▶" in [b.text for b in buttons(cat)]
text, cat = tap(ctx, "xp:sc:0:1"); assert "page 2 of 2" in text and "◀ Previous" in [b.text for b in buttons(cat)]
print("D2 ok: skills by category: add (duplicates reported), remove by tapping, stale tap harmless, paged")

# add something: menu -> describe in your own words -> entry shown with what was understood
text, menu = tap(ctx, "xp:n")
assert [b.text for b in buttons(menu)] == ["💼 A job or internship", "🛠 A project", "🏅 A leadership role or activity", "🎓 A degree", "🔧 Skills", "⬅ Back to profile"]
from service.admin import AddExperienceRequest as _AER, AddProjectRequest as _APR, AddEducationRequest as _AEdR, AddLeadershipRequest as _ALR
eb.extract_experience = lambda t: _AER(title="Data Intern", company="Globex", location="KL", dates="2025", bullets=[NewFactBullet(text="Cleaned data with pandas", tech_tags=["pandas"])],
                                       suggested_skills={"Frameworks & Libraries": ["pandas"]})
eb.extract_project = lambda t: _APR(name="Chatbot", tech_stack="Python", date="Jan 2025", bullets=[NewFactBullet(text="Built a chatbot")])
eb.extract_education = lambda t: _AEdR(institution="Tech Uni", location="KL", degree_line="Diploma", dates="2020")
eb.extract_leadership = lambda t: _ALR(title="Mentor", org="Code Club", location="KL", dates="2024", bullets=["Taught 20 students"])
text, prompt = tap(ctx, find(menu, "💼")); assert "Describe your job or internship" in text and "For example" in text and ctx.user_data[eb.WAIT_KEY]["action"] == "new"
msg, consumed = say(ctx, "I was a data intern at Globex")
assert consumed and msg.sent[0][0].startswith("⏳"), "tells you it is working"
shown = msg.sent[-1][0]; assert "✅ Added. Here's what I understood" in shown and "Data Intern · Globex" in shown and "1. Cleaned data with pandas" in shown and "New skills: pandas" in shown
for what, mark in (("🛠", "Chatbot"), ("🎓", "Diploma"), ("🏅", "Mentor · Code Club")):
    tap(ctx, find(tap(ctx, "xp:n")[1], what)); msg, _ = say(ctx, "text"); assert mark in msg.sent[-1][0], (what, msg.sent[-1][0])
assert any(e.label == "Mentor · Code Club" for e in editor.list_entries()) and editor.get_entry("lead", next(e.ref for e in editor.list_entries() if "Code Club" in e.label)).items[0].text == "Taught 20 students"
def boom(t): raise RuntimeError("the AI is down")
eb.extract_project = boom
tap(ctx, find(tap(ctx, "xp:n")[1], "🛠")); msg, consumed = say(ctx, "text")
assert consumed and "couldn't save that" in msg.sent[-1][0] and "the AI is down" in msg.sent[-1][0] and eb.WAIT_KEY in ctx.user_data
print("D3 ok: ➕ Add something -> describe it -> saved and shown (job, project, degree, leadership); an AI failure keeps the question open")

# typed shortcuts
def typed(cmd_factory, args, text):
    msg = FakeMsg(text); c = types.SimpleNamespace(args=args, user_data={})
    asyncio.run(cmd_factory(types.SimpleNamespace(effective_message=msg, effective_user=USER), c)); return msg, c
eb.extract_project = lambda t: _APR(name="Chatbot2", tech_stack="Python", date="Jan 2025", bullets=[NewFactBullet(text="Built chatbot 2")])
msg, c = typed(eb.make_add_command("proj"), ["a", "chatbot", "project"], "/addproject a chatbot project")
assert "Chatbot2" in msg.sent[-1][0] and eb.WAIT_KEY not in c.user_data, "with text: saved at once"
msg, c = typed(eb.make_add_command("proj"), [], "/addproject")
assert c.user_data[eb.WAIT_KEY]["kind"] == "proj" and "Describe your project" in msg.sent[-1][0], "without text: asks"
from types import SimpleNamespace as NS
eb.extract_skills = lambda t: NS(category="Languages", items=["Zig"])
msg, c = typed(eb.addskill_command, ["Zig"], "/addskill Zig"); assert "✅ Added to Languages: Zig" in msg.sent[-1][0]
msg, c = typed(eb.addskill_command, [], "/addskill"); assert "Pick a category" in msg.sent[-1][0]
for alias, expect in (("/myprofile", "Contact details"), ("/listskills", "<b>Skills</b>"), ("/listeducation", "Your profile"), ("/experience", "Your profile"), ("/listentries", "Your profile")):
    msg, c = typed(eb.profile_command, [], alias); assert expect in msg.sent[-1][0], (alias, msg.sent[-1][0])
msg, c = typed(eb.profile_command, ["acme"], "/listfacts@MyBot acme"); assert "Software Intern · Acme Sdn Bhd" in msg.sent[-1][0]
c.user_data[eb.WAIT_KEY] = {"action": "edit", "at": 0}
from bot.handlers.common import cancel_command
asyncio.run(cancel_command(types.SimpleNamespace(effective_message=FakeMsg(), effective_user=USER), c)); assert c.user_data == {}
print("D4 ok: typed /add… shortcuts (with and without text), /addskill, the shortcut names open the right screen, /cancel clears a waiting question")

# the old commands are gone, and the menu and description say what the bot does
registered = {c for g in app.handlers.values() for h in g for c in getattr(h, "commands", [])}
gone = {"setname", "setphone", "setemail", "setlinkedin", "setgithub", "deleteeducation", "deleteexperience", "deleteproject",
        "deleteleadership", "deleteskill", "deletecourse", "addcourse", "deletefact", "updatefact"}
assert not (gone & registered), gone & registered
assert not any(getattr(h, "pattern", None) and h.pattern.pattern.startswith("^(del_|edit_)") for g in app.handlers.values() for h in g)
assert {"start", "help", "history", "cancel", "resume", "importresume", "profile", "addexperience", "addproject", "addleadership", "addeducation", "addskill"} <= registered
sent = {}
class _B:
    async def set_my_commands(self, c): sent["commands"] = [(x.command, x.description) for x in c]
    async def set_my_short_description(self, d): sent["short"] = d
    async def set_my_description(self, d): sent["long"] = d
from bot.main import post_init
asyncio.run(post_init(types.SimpleNamespace(bot=_B())))
assert [c for c, _ in sent["commands"]] == ["profile", "importresume", "history", "resume", "help", "cancel"], sent["commands"]
assert all(3 <= len(d) <= 256 and d[0].isupper() for _, d in sent["commands"]) and "job link" in sent["short"] and "resume file" in sent["long"]
print("D5 ok: old commands removed; the menu has 6 entries with clear descriptions; the bot description tells new users what to do:")
for c, d in sent["commands"]: print(f"     /{c:13} {d}")
print("\n✅ browser test passed")
