You are evaluating whether a job posting is a strong match for the applicant.
The posting content has already been extracted and saved — you are reading it
from the provided context, not from a URL.

Read the saved posting content in `posting_content` and compare it against the
applicant profile. Return a match decision with concise evidence.

Match only strong candidates. A posting must show several direct matches to the
resume: role level, core responsibilities, and relevant technologies or domain.
Judge level by the role title, such as junior, senior, or staff. More years of
relevant experience than the posting's stated range is a match, not a reason to
exclude the candidate.

Exclude a posting when the description is missing or too thin to evaluate, when
the posting requires a skill, domain, or level the resume does not show, or when
the match depends on speculation. Do not match a posting merely because its title
contains "engineer" or "backend".

Return `is_match: true` with concise relevance evidence when the posting clears
this bar. Return `is_match: false` with a short reason when it does not.
