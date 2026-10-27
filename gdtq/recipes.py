"""Quarantined learning from successful high-effort AI fallback.

Generated code is never automatically imported or executed. Review, test,
signing, and approval are required before an adapter becomes executable.
"""
import hashlib,json
from urllib.parse import urlsplit
from datetime import datetime,timezone

class RecipeProposalRegistry:
    def __init__(self,store):
        self.store=store

    def propose(self,posting_url,source,operation):
        domain=(urlsplit(posting_url).hostname or "").lower()
        if not domain: raise ValueError("Recipe requires domain")
        if operation not in {"read","submit"}: raise ValueError("Invalid recipe operation")
        data={"domain":domain,"operation":operation,"source":source,
              "status":"pending_review","created_at":datetime.now(timezone.utc).isoformat()}
        digest=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()[:20]
        path=f"System/recipe-proposals/{operation}/{domain}/{digest}.json"
        self.store.save_path(path,json.dumps(data,indent=2).encode(),"application/json","recipe-"+digest)
        return path
