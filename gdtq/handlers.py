"""Composition of existing job-board, scoring and submission adapters.

Only inject a submission strategy if it has been explicitly authorized. The
preparation writer remains an injected LLM capability; it cannot mutate state.
"""
import re
from pathlib import Path
from .stages import bookmark_discovered,validate_bookmarked,prepare_validated,submit_prepared

DEFAULT_CATALOG=("Java","Scala","Python","Kotlin","JavaScript","TypeScript","Go","Rust",
  "Spring Boot","Spring","Hibernate","Akka","Kafka","Spark","Apache NiFi","Airflow",
  "Kubernetes","Docker","AWS","Azure","GCP","PostgreSQL","MongoDB","MySQL",
  "Elasticsearch","Redis","React","Node.js","Terraform","LLM","Machine Learning",
  "Data Engineering","Distributed Systems","Microservices","SQS","Hadoop","HDFS")

def requested_skills(description,catalog=DEFAULT_CATALOG):
    """Deterministic named skills only; no inferred skills or silent additions."""
    from .scoring import occurrences
    return [skill for skill in catalog if occurrences(description,skill)]

def build_handlers(runtime,*,writer=None,resume_path=None,submission_strategies=(),
                   strong_reader=None,strong_submitter=None,match_threshold=0,
                   catalog=DEFAULT_CATALOG,recipe_registry=None,calendly_url=None):
    loader=runtime["loader"]
    index=runtime["index"]
    store=runtime["store"]

    def read(claimed):
        from adapters.job_boards.posting_details import fetch_posting_details
        item=loader.application(claimed.item_id)
        if not item.get("posting_url"):
            return {"agent":"bookmark_discovered","item_id":claimed.item_id,"code":"ERROR",
                    "execution_status":"PERMANENT_FAILURE","summary":"Missing posting URL","data":{}}
        return bookmark_discovered(item,fetch_posting_details,index,smart_reader=strong_reader,
                                   recipe_proposals=recipe_registry,store=store)

    def validate(claimed):
        item=loader.application(claimed.item_id)
        posting=loader.posting(item)
        item["description"]=posting["description"]
        return validate_bookmarked(item,loader.profile(),requested_skills(item["description"],catalog),match_threshold)

    def prepare(claimed):
        if writer is None or not resume_path:
            raise RuntimeError("Cover-letter provider and resume_path must be provisioned")
        item=loader.application(claimed.item_id)
        item["description"]=loader.posting(item)["description"]
        resume=store.read_path(resume_path)["content"]
        return prepare_validated(item,loader.profile(),resume,writer,store,calendly_url=calendly_url)

    def submit(claimed):
        item=loader.application(claimed.item_id)
        item["description"]=loader.posting(item)["description"]
        return submit_prepared(item,index,submission_strategies,
                               strong_agent=strong_submitter,recipe_proposals=recipe_registry)

    return {"Discovered":read,"Bookmarked":validate,"Validated":prepare,"Prepared":submit}
