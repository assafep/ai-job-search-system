# Shared schemas

Store provider-neutral domain and workload schemas here. Agent output schemas
remain next to their agent instructions.

`applicant-profile.schema.json` defines the stable envelope used by onboarding,
the Application Service, and application workloads. Its inner profile fields remain
open so the resume/profile extraction process can evolve without changing the
workflow contract.
