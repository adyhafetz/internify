"""Career and job pages that are a good place to start in Malaysia. One tap in /profile adds them to the daily check.

The list is plain data: edit it freely. These are pages that list many employers' openings (so the Matcher has plenty
to choose from) plus a couple of well-known employers. A page that stops working just shows "didn't answer" in the
daily check and can be removed."""
from __future__ import annotations

# (name shown to you, link, one-line note)
MALAYSIA: list[tuple[str, str, str]] = [
    ("JobStreet Malaysia, internships", "https://my.jobstreet.com/internship-jobs", "Malaysia's biggest job board"),
    ("Hiredly, jobs", "https://my.hiredly.com/jobs", "Malaysian job board popular with graduates"),
    ("Startup.jobs, Kuala Lumpur internships", "https://startup.jobs/locations/kuala-lumpur/internships", "Startups and scale-ups"),
    ("Built In, Kuala Lumpur tech jobs", "https://builtin.com/jobs/as/malaysia/kuala-lumpur", "Tech companies hiring in KL"),
    ("The Muse, Kuala Lumpur internships", "https://www.themuse.com/hiring/location/kuala-lumpur-malaysia/keyword/internships", "Internships at larger companies"),
    ("Grab Malaysia", "https://www.grab.careers/en/locations/malaysia/", "Grab's own careers page"),
    ("Shopee Malaysia", "https://careers.shopee.com.my", "Shopee's own careers page"),
]
