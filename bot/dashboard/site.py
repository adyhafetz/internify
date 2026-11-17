"""The public pages: home, how it works, pricing, privacy and terms, with the tags search engines and link previews read
(title, description, canonical address, Open Graph, structured data), plus robots.txt and the sitemap.

On the hosted version (or with MARKETING_SITE=1) the home page is the marketing page. On a normal copy it stays the short personal
page ("run by its owner for their own use")."""
from __future__ import annotations

import datetime as dt
import html as _html
import os
import re
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from bot import config as bot_config
from service import users

SITE_DIR = os.path.join(os.path.dirname(__file__), "static", "site")
OG_IMAGE_FILE = os.path.join(os.path.dirname(__file__), "static", "assets", "og-image.png")
env = Environment(loader=FileSystemLoader(SITE_DIR), autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)

# Pages that search engines may index, in sitemap order: (path, template, title, description)
PAGES = {
    "/": ("home.html", "Internify: a one-page resume for every tech internship",
          "Send a job link on Telegram. Get a one-page resume built only from your real experience, plus an application tracker. Made for tech interns in Malaysia."),
    "/how-it-works": ("how.html", "How Internify works: job link to one-page resume",
                      "Save your experience once, send a job link, and get a one-page PDF with a match report. See each step, what it checks and what it does not do."),
    "/pricing": ("pricing.html", "Internify pricing: free plan and {price} paid plan",
                 "Start free with {free} resumes a month. The paid plan adds {paid} resumes and a daily job search for tech internships. Pay by bank transfer or DuitNow."),
    "/privacy": ("privacy.html", "Privacy Policy · Internify", "How Internify handles your data: what is stored, who receives it, and how to delete it."),
    "/terms": ("terms.html", "Terms of Service · Internify", "The terms for using Internify: drafts you must check, plans and limits, and acceptable use."),
}
INDEXED = ("/", "/how-it-works", "/pricing", "/privacy", "/terms")


def marketing_on() -> bool:
    return users.cloud() or bot_config.MARKETING_SITE


def base_url() -> str:
    return bot_config.PUBLIC_URL


def bot_url() -> str:
    return f"https://t.me/{bot_config.BOT_USERNAME}" if bot_config.BOT_USERNAME else ""


def _price_number() -> str:
    m = re.search(r"\d+(?:\.\d+)?", bot_config.PAID_PRICE)
    return m.group(0) if m else "0"


def site_context() -> dict[str, Any]:
    url = bot_url()
    return {
        "project_url": bot_config.PROJECT_URL,
        "cta_url": f"{url}?start=web" if url else "",          # the one main call to action: open the bot
        "paid_cta_url": f"{url}?start=paid" if url else "",
        "price": bot_config.PAID_PRICE, "free_resumes": bot_config.FREE_RESUMES, "paid_resumes": bot_config.PAID_RESUMES,
        "paid_scan_max": bot_config.PAID_SCAN_MAX,
    }


def contact_html() -> Markup:
    email = bot_config.CONTACT_EMAIL
    issues = f'<a href="{_html.escape(bot_config.PROJECT_URL)}/issues">{_html.escape(bot_config.PROJECT_URL.replace("https://", ""))}/issues</a>'
    return Markup((f'<a href="mailto:{_html.escape(email)}">{_html.escape(email)}</a>, or open an issue at ' if email else "open an issue at ") + issues)


def faq() -> list[tuple[str, str]]:
    c = site_context()
    return [
        ("Is Internify free?", f"Yes, to start. The free plan includes {c['free_resumes']} resumes a month. The paid plan is {c['price']} a month for "
                              f"{c['paid_resumes']} resumes and the daily job search."),
        ("Will it make things up?", "It rewords the experience you saved, and it cannot add a tool, number or name that your original line does not contain. "
                                    "Code checks every bullet point against its source before the PDF is built. You should still read the resume before you send it."),
        ("What does the resume look like?", "A single-column, one-page layout based on Jake's Resume, which applicant tracking systems read well. It has Education, "
                                            "Work Experience, Projects, Leadership and Technical Skills."),
        ("Does it only work for computer science?", "It is built for computer science and tech roles. The layout, the skill groups and the GitHub check assume them. "
                                                    "Other fields can try it but will fit less well."),
        ("Which job sites can it read?", "Any public job page. For LinkedIn it uses the public posting page. If a site blocks automated visitors, "
                                         "the bot asks you to paste the job text instead."),
        ("What do you store about me?", "Your saved experience, job links, application tracker, settings and resume files, kept separately for your account. "
                                        "Send /deleteaccount in Telegram to delete all of it, or export it first from /profile, My data."),
        ("Does Internify apply to jobs for me?", "No. It writes the resume and tracks your applications. You apply yourself."),
        ("How do I pay?", "Pay the operator directly by bank transfer or DuitNow. Send /plan in Telegram for the details. Your plan is switched on after "
                          "payment, and the bot tells you when it is."),
    ]


def billing_faq() -> list[tuple[str, str]]:
    c = site_context()
    return [
        ("How does billing work?", f"The paid plan is {c['price']} a month, paid directly to the operator. It runs until the end date shown in /plan."),
        ("What happens when my plan ends?", "You move to the free plan. Your saved experience, tracker and settings stay. The bot reminds you a few days before the end date."),
        ("Can I cancel?", "Nothing renews automatically, so there is nothing to cancel. If you do not pay again, the plan ends on its end date."),
        ("What counts as a resume?", "Each resume the bot builds from a job link, including the ones the daily search writes. A resume that fails to build is not counted, "
                                     "and rebuilding a resume you already have is free."),
        ("Do unused resumes roll over?", "No. The count starts again on the 1st of each month."),
    ]


def _faq_ld(items: list[tuple[str, str]]) -> dict:
    return {"@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in items]}


def _app_ld(description: str) -> dict:
    c = site_context()
    return {"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "Internify", "applicationCategory": "BusinessApplication",
            "operatingSystem": "Telegram", "description": description, "url": (base_url() + "/") if base_url() else None,
            "inLanguage": "en", "areaServed": "MY",
            "offers": [{"@type": "Offer", "name": "Free", "price": "0", "priceCurrency": "MYR"},
                       {"@type": "Offer", "name": "Paid", "price": _price_number(), "priceCurrency": "MYR",
                        "description": f"{c['paid_resumes']} resumes a month and the daily job search"}]}


def og_image() -> str:
    return f"{base_url()}/assets/og-image.png" if base_url() and os.path.exists(OG_IMAGE_FILE) else ""


def render(path: str) -> str:
    """One public page, as HTML. ``path`` is one of PAGES."""
    template, title, description = PAGES[path]
    c = site_context()
    title, description = (t.format(price=c["price"], free=c["free_resumes"], paid=c["paid_resumes"]) for t in (title, description))   # prices come from the settings
    marketing = marketing_on()
    if path == "/" and not marketing:
        template, title, description = "index.html", "Internify", "Internify tailors a one-page resume to a job posting using only the experience you have saved."
    ld: list[dict] = []
    ctx: dict[str, Any] = {}
    if marketing and path in ("/", "/pricing", "/how-it-works"):
        ld.append(_app_ld(description))
    if marketing and path == "/":
        ld.append({"@context": "https://schema.org", "@type": "WebSite", "name": "Internify", "url": (base_url() + "/") if base_url() else None})
        ctx["faq"] = faq()
        ld.append(_faq_ld(ctx["faq"]))
    if marketing and path == "/pricing":
        ctx["billing_faq"] = billing_faq()
        ld.append(_faq_ld(ctx["billing_faq"]))
    ld = [{k: v for k, v in block.items() if v is not None} for block in ld]
    tpl = env.get_template(template)
    return tpl.render(title=title, description=description, page=path, marketing=marketing, site=site_context(), contact=contact_html(),
                      canonical=(base_url() + path) if base_url() else "", og_image=og_image(), json_ld=ld, noindex=False, **ctx)


def message_page(heading: str, text: str, *, status_title: Optional[str] = None, button_url: str = "", button_label: str = "",
                 link_url: str = "", link_label: str = "") -> str:
    """A small private page (sign-in, errors): never indexed."""
    return env.get_template("message.html").render(title=status_title or heading, description=text, page="", marketing=marketing_on(),
                                                    site=site_context(), contact=contact_html(), canonical="", og_image="", json_ld=[],
                                                    noindex=True, heading=heading, text=text, button_url=button_url, button_label=button_label,
                                                    link_url=link_url, link_label=link_label)


def admin_page(data: dict) -> str:
    return env.get_template("admin.html").render(title="Admin · Internify", description="Maintainer overview", page="/admin", marketing=marketing_on(),
                                                  site=site_context(), contact=contact_html(), canonical="", og_image="", json_ld=[], noindex=True, d=data)


def robots_txt() -> str:
    lines = ["User-agent: *", "Allow: /", "Disallow: /u/", "Disallow: /admin", "Disallow: /auth/", "Disallow: /connect/", "Disallow: /api/", "Disallow: /pdf/",
             "Disallow: /live", "Disallow: /dashboard"]
    if base_url():
        lines.append(f"Sitemap: {base_url()}/sitemap.xml")
    return "\n".join(lines) + "\n"


def sitemap_xml() -> str:
    """The indexable pages. Empty (no URLs) until PUBLIC_URL is set, because a sitemap needs absolute addresses."""
    rows = []
    if base_url() and marketing_on():
        for path in INDEXED:
            f = os.path.join(SITE_DIR, PAGES[path][0])
            lastmod = dt.datetime.fromtimestamp(os.path.getmtime(f), dt.timezone.utc).strftime("%Y-%m-%d") if os.path.exists(f) else None
            rows.append(f"  <url><loc>{_html.escape(base_url() + path)}</loc>" + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "") + "</url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(rows) + "\n</urlset>\n")
