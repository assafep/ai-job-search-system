"""Deterministic skill occurrence score: matching requirements minus absent skills."""
from collections import Counter
import re

def occurrences(text,phrase):
    phrase=re.escape(phrase.strip())
    return len(re.findall(r"(?<!\w)"+phrase+r"(?!\w)",text,flags=re.IGNORECASE)) if phrase else 0

def owned_skills(profile):
    """Profile accepts skills as list, grouped dict of lists, or core_skills."""
    values=profile.get("skills",profile.get("core_skills",[]))
    if isinstance(values,dict):
        values=[x for v in values.values() for x in (v if isinstance(v,list) else [v])]
    return {str(x).strip().casefold() for x in values if str(x).strip()}

def score_posting(description,requested_skills,profile):
    """Count occurrence frequency for each requested skill; preserve negative scores."""
    owned=owned_skills(profile)
    counts={skill:occurrences(description,skill) for skill in requested_skills}
    positive=sum(n for skill,n in counts.items() if skill.casefold() in owned)
    negative=sum(n for skill,n in counts.items() if skill.casefold() not in owned)
    return {"score":positive-negative,"matched_occurrences":positive,
            "unmatched_occurrences":negative,"counts":counts}

def decide_match(description,requested_skills,profile,threshold):
    result=score_posting(description,requested_skills,profile)
    return result | {"is_match":result["score"]>=threshold,"threshold":threshold}
