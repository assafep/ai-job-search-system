"""Render the onboarded structured resume as deterministic CV HTML."""

from html import escape


def _text(value):
    return escape(str(value).strip()) if value is not None else ""


def profile_to_cv_html(profile):
    if not isinstance(profile, dict):
        return ""
    personal = profile.get("personal") or {}
    professional = profile.get("professional") or {}
    name = _text(personal.get("name"))
    if not name:
        return ""
    contact = [personal.get("email"), personal.get("location"), personal.get("linkedin_url")]
    sections = ["<h1>{}</h1>".format(name)]
    sections.append("<p>{}</p>".format(" · ".join(_text(item) for item in contact if item)))
    if professional.get("headline"):
        sections.append("<h2>{}</h2>".format(_text(professional["headline"])))
    if professional.get("summary"):
        sections.append("<p>{}</p>".format(_text(professional["summary"])))
    skills = profile.get("skills") or {}
    skill_values = []
    for value in skills.values() if isinstance(skills, dict) else []:
        if isinstance(value, list):
            skill_values.extend(str(item) for item in value if str(item).strip())
    if skill_values:
        sections.append("<h2>Skills</h2><p>{}</p>".format(_text(", ".join(skill_values))))
    employment = profile.get("employment") or []
    if employment:
        items = ["<h2>Experience</h2>"]
        for job in employment:
            if not isinstance(job, dict):
                continue
            heading = "{} — {}".format(job.get("title", ""), job.get("company", "")).strip(" —")
            dates = "{} – {}".format(job.get("start", ""), job.get("end", "")).strip(" –")
            items.append("<h3>{}</h3><p>{}</p>".format(_text(heading), _text(dates)))
            highlights = [item for item in job.get("highlights", []) if str(item).strip()]
            if highlights:
                items.append("<ul>{}</ul>".format("".join("<li>{}</li>".format(_text(item)) for item in highlights)))
        sections.extend(items)
    education = profile.get("education") or []
    if education:
        items = ["<h2>Education</h2>"]
        for item in education:
            if isinstance(item, dict):
                items.append("<p>{} — {} {}</p>".format(
                    _text(item.get("institution")), _text(item.get("credential")), _text(item.get("year"))))
        sections.extend(items)
    return "".join(sections)
