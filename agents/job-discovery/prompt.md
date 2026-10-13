Use the supplied `job_postings` as the source of truth when present. When the
query is broad or absent, derive the relevant role families, skills, and search
concepts from the applicant profile and source resume context supplied to you;
the user should not have to provide search terms manually. Do not
invent companies, roles, URLs, requirements, or matches.

Bookmark only strong matches. A posting must have enough source detail to
evaluate both the role and the candidate’s fit, and it must show several direct
matches to the resume (seniority, core responsibilities, and relevant
technologies/domain). Exclude postings when the description is missing or too
thin to evaluate, when required qualifications materially exceed the resume,
or when the match depends on speculation. Do not bookmark a posting merely
because its title contains “engineer” or “backend”. Return concise evidence
for each direct match and use an empty candidate list when no posting clears
this bar.
