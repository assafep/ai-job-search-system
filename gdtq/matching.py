"""Conservative normalized organization matching for correspondence and Calendar."""
import re
def matching_application(text,applications):
    normalized=" "+re.sub(r"[^a-z0-9]+"," ",str(text or "").casefold())+" "
    matches=[]
    for app in applications:
        company=re.sub(r"[^a-z0-9]+"," ",str(app.get("company","")).casefold()).strip()
        if len(company)>=4 and " "+company+" " in normalized:matches.append(app)
    return matches[0] if len(matches)==1 else None
