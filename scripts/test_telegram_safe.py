"""Offline test: no message can fail on special characters, and the real handlers' output stays readable."""
from __future__ import annotations
import asyncio, os, random, re, shutil, sys, tempfile, types
from html import unescape
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

from bot.telegram_safe import SafeBot, markdown_to_html as md


class Checker(HTMLParser):
    """What Telegram accepts: only <b> <i> <code> <pre> <a>, properly nested, no raw < or & left over."""
    ALLOWED = {"b", "i", "code", "pre", "a"}
    def __init__(self): super().__init__(convert_charrefs=False); self.stack, self.text = [], []
    def handle_starttag(self, tag, attrs):
        assert tag in self.ALLOWED, f"tag <{tag}> not allowed"
        if self.stack and self.stack[-1] in ("code", "pre"): raise AssertionError("entity inside code/pre")
        self.stack.append(tag)
    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag, f"badly nested </{tag}>"
    def handle_data(self, data):
        assert "<" not in data and "&" not in data, f"raw character in {data!r}"
        self.text.append(data)
    def handle_entityref(self, name): assert name in ("amp", "lt", "gt"), name; self.text.append(unescape(f"&{name};"))
    def handle_charref(self, name): raise AssertionError("unexpected charref")


def check(markup: str) -> str:
    c = Checker(); c.feed(markup); c.close()
    assert not c.stack, f"unclosed {c.stack}"
    assert "\x00" not in markup
    return "".join(c.text)


# 1. the message that failed for the user: underscores in IDs survive, nothing is eaten
usage = "Usage: `/listfacts <entry_id>`\n_Run `/listentries` to see valid entry IDs (e.g. `exp_sample_tech` or `proj_sample_app`)._"
out = md(usage)
assert "exp_sample_tech" in check(out) and "proj_sample_app" in check(out), out
print("1 ok: IDs with underscores are shown correctly")

# 2. plain text stays plain; real formatting still works
cases = {
    "snake_case_name and 2*3*4 and 5 * 3": "snake_case_name and 2*3*4 and 5 * 3",
    "* lone star and _lone underscore": "* lone star and _lone underscore",
    "*Bold* and _italic_": "<b>Bold</b> and <i>italic</i>",
    "*bold _both_ here*": "<b>bold <i>both</i> here</b>",
    "a <b>tag</b> & more": "a &lt;b&gt;tag&lt;/b&gt; &amp; more",
    "[Drive](https://x.com/a?b=1&c=2)": '<a href="https://x.com/a?b=1&amp;c=2">Drive</a>',
    "```\ncode _x_\n```": "<pre>code _x_</pre>",
}
for src, want in cases.items():
    assert md(src) == want, (src, md(src), want)
print("2 ok: plain text stays plain, real formatting converts")

# 3. fuzz: whatever the text, the result is valid and no word is lost
random.seed(7)
alphabet = ["*", "_", "`", "[", "]", "(", ")", "<", ">", "&", " ", " ", "\n", "a", "b", "C++", "snake_case", "x*y", "1", "é", "<b>", "</i>", "&amp;"]
for _ in range(4000):
    s = "".join(random.choice(alphabet) for _ in range(random.randint(0, 40)))
    out = md(s)
    visible = check(out)
    assert re.findall(r"[a-zA-Z0-9é]", visible) == re.findall(r"[a-zA-Z0-9é]", s), (s, out)    # no letters or digits dropped or added
print("3 ok: 4000 random messages -> always valid HTML, no letters lost")

# 4. through the real python-telegram-bot message path
from telegram import Chat, Message, CallbackQuery, User
from telegram.ext import ExtBot
import datetime
captured = []
async def fake_send(self, chat_id, text, *a, **k): captured.append(("send", text, k.get("parse_mode")))
async def fake_edit(self, text, *a, **k): captured.append(("edit", text, k.get("parse_mode")))
ExtBot.send_message, ExtBot.edit_message_text = fake_send, fake_edit
bot = SafeBot(token="123:abc")
msg = Message(message_id=1, date=datetime.datetime.now(), chat=Chat(1, "private"), text="x"); msg.set_bot(bot)
asyncio.run(msg.reply_text("📋 *Entries* `exp_a`: Tech_Corp *Inc", parse_mode="Markdown"))
asyncio.run(msg.reply_text("plain _text_"))                                   # no parse mode: untouched
asyncio.run(msg.reply_text("<b>already html</b>", parse_mode="HTML"))           # HTML: untouched
asyncio.run(msg.edit_text("⏳ *Saving…*", parse_mode="Markdown"))
assert captured[0] == ("send", "📋 <b>Entries</b> <code>exp_a</code>: Tech_Corp *Inc", "HTML"), captured[0]
assert captured[1][1] == "plain _text_" and repr(captured[1][2]) == "None", captured[1]      # PTB passes a "default none" object
assert captured[2] == ("send", "<b>already html</b>", "HTML")
assert captured[3] == ("edit", "⏳ <b>Saving…</b>", "HTML"), captured[3]
print("4 ok: Message.reply_text / edit_text go through the converter; plain and HTML messages are untouched")

# 5. the real bot is a SafeBot
from bot import config as bot_config
bot_config.TELEGRAM_BOT_TOKEN = "123:abc"
from bot.main import create_bot_app
app = create_bot_app()
assert isinstance(app.bot, SafeBot)
print("5 ok: the application uses SafeBot")

# 6. the real handlers, with awkward data
from service.admin import (add_experience, AddExperienceRequest, NewFactBullet, add_project, AddProjectRequest,
                           add_education, AddEducationRequest)
exp = add_experience(AddExperienceRequest(title="C++ _Dev_ *Lead*", company="A&B_Corp <Ltd>", location="KL", dates="2024",
                     bullets=[NewFactBullet(text="Cut p99 latency_ms by 40% using *epoll* [see #12] & `perf`", tech_tags=["scikit_learn", "C++"])]))
add_project(AddProjectRequest(name="my_app *v2*", tech_stack="Go", date="2025", bullets=[NewFactBullet(text="x_y_z")]))
add_education(AddEducationRequest(institution="Uni_Name *X*", location="KL", degree_line="BSc [Hons]", dates="2025", courses=["C++ & Algo_1"]))
from bot.handlers import facts as facts_h, skills as skills_h, education as edu_h


class Cap:
    def __init__(self): self.sent = []
    async def reply_text(self, text, **kw): self.sent.append((text, kw.get("parse_mode")))


def call(fn, *args):
    m = Cap(); ctx = types.SimpleNamespace(args=list(args))
    asyncio.run(fn(types.SimpleNamespace(effective_message=m, effective_user=types.SimpleNamespace(id=1, username="t")), ctx))
    return m.sent

for label, fn, args in (("listentries", facts_h.listentries_command, ()), ("listfacts", facts_h.listfacts_command, (exp.entry_id,)),
                        ("listfacts usage", facts_h.listfacts_command, ()), ("listfacts unknown", facts_h.listfacts_command, ("nope_id",)),
                        ("listskills", skills_h.listskills_command, ()), ("listeducation", edu_h.listeducation_command, ())):
    for text, mode in call(fn, *args):
        shown = check(md(text)) if mode == "Markdown" else text
        for must in ("exp_", "proj_", "latency_ms", "scikit_learn") if label in ("listentries", "listfacts") else ():
            if must in text: assert must in shown, (label, must, shown)
print("6 ok: /listentries, /listfacts (incl. usage and unknown id), /listskills, /listeducation render valid with awkward names")
print("\n✅ telegram safe-formatting test passed")
