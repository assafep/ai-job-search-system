"""Location aliases shared by feed parsing and discovery filtering."""

import re

ISRAEL_LOCATION = re.compile(
    r'\b(?:israel|isr|tel[ -]?aviv(?:[ -]yafo)?|herzli[yz]a|haifa|jerusalem|'
    r'ra[\W_]?anana|netanya|ramat[ -]gan|pet[ah]+[ -]ti[kq]va|kfar[ -]saba|'
    r'beer[ -]sheva|be.er[ -]sheva|rehovot|yokneam|holon|bnei[ -]brak|'
    r'kinneret|gush[ -]dan)\b|'
    r'ישראל|תל[ -]?אביב|ירושלים|חיפה|הרצליה|רעננה|נתניה|רמת[ -]?גן|'
    r'פתח[ -]?תקווה|כפר[ -]?סבא|באר[ -]?שבע|רחובות|יוקנעם|יקנעם|חולון|'
    r'בני[ -]?ברק|ראשון[ -]?לציון|גבעתיים|מודיעין|רמת[ -]?השרון', re.I)
