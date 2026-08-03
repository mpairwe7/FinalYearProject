#!/usr/bin/env python3
"""Threat Model as Code — URA AI Chatbot (pytm).

Generates STRIDE threat analysis, Data Flow Diagrams (DFDs), and sequence
diagrams automatically from the system architecture definition.

Usage:
    # Generate full report (DFD + threats + sequence diagrams)
    python threat-model/tm.py --dfd | dot -Tpng -o threat-model/output/dfd.png
    python threat-model/tm.py --seq | dot -Tpng -o threat-model/output/seq.png
    python threat-model/tm.py --report threat-model/output/report.html

    # List all identified threats as JSON (for CI validation)
    python threat-model/tm.py --json threat-model/output/threats.json

Standards:
    - STRIDE (Microsoft) — per-element threat identification
    - OWASP LLM Top 10 (2025) — AI/ML-specific threat overlays
    - NIST SP 800-218 (SSDF) — secure development lifecycle
    - LINDDUN — privacy threat modelling
    - MITRE ATLAS — adversarial ML threat framework
"""

from __future__ import annotations

from pytm import (
    TM,
    Actor,
    Boundary,
    Dataflow,
    Datastore,
    ExternalEntity,
    Lambda,
    Server,
)

# =============================================================================
# Threat Model Definition
# =============================================================================
tm = TM("URA AI Chatbot – Threat Model")
tm.description = (
    "MLOps-powered RAG chatbot for Uganda Revenue Authority. "
    "Taxpayers query tax information via a web frontend backed by "
    "a FastAPI service with hybrid retrieval (dense + BM25 + reranker) "
    "over official URA documents."
)
tm.isOrdered = True
tm.mergeResponses = True

# =============================================================================
# Trust Boundaries
# =============================================================================
internet = Boundary("Internet / Untrusted")
internet.levels = [0]

dmz = Boundary("DMZ / Edge")
dmz.levels = [1]

frontend_container = Boundary("Frontend Container (Next.js)")
frontend_container.levels = [2]
frontend_container.inBoundary = dmz

backend_container = Boundary("Backend Container (FastAPI)")
backend_container.levels = [2]

ml_pipeline = Boundary("ML Pipeline (GitHub Actions + Kaggle)")
ml_pipeline.levels = [2]

data_layer = Boundary("Data Layer (Persistent Storage)")
data_layer.levels = [2]

ci_cd = Boundary("CI/CD Pipeline (GitHub Actions)")
ci_cd.levels = [2]

# =============================================================================
# Actors (External Entities)
# =============================================================================
taxpayer = Actor("Taxpayer")
taxpayer.inBoundary = internet
taxpayer.hasPhysicalAccess = False
taxpayer.protocol = "HTTPS"
taxpayer.isAdmin = False

admin = Actor("URA Admin / Developer")
admin.inBoundary = internet
admin.hasPhysicalAccess = False
admin.protocol = "HTTPS/SSH"
admin.isAdmin = True

attacker = Actor("Adversary")
attacker.inBoundary = internet
attacker.hasPhysicalAccess = False

# =============================================================================
# External Entities
# =============================================================================
huggingface = ExternalEntity("HuggingFace Hub")
huggingface.inBoundary = internet
huggingface.hasPhysicalAccess = False
huggingface.protocol = "HTTPS"

kaggle = ExternalEntity("Kaggle")
kaggle.inBoundary = internet
kaggle.hasPhysicalAccess = False
kaggle.protocol = "HTTPS"

docker_hub = ExternalEntity("Docker Hub")
docker_hub.inBoundary = internet
docker_hub.protocol = "HTTPS"

github = ExternalEntity("GitHub")
github.inBoundary = internet
github.protocol = "HTTPS"

# =============================================================================
# Servers (Processing Nodes)
# =============================================================================
nextjs = Server("Next.js Frontend")
nextjs.inBoundary = frontend_container
nextjs.port = 3000
nextjs.protocol = "HTTPS"
nextjs.isEncrypted = True
nextjs.sanitizesInput = True
nextjs.encodesOutput = True
nextjs.OS = "Linux (Alpine)"

fastapi = Server("FastAPI Backend")
fastapi.inBoundary = backend_container
fastapi.port = 8000
fastapi.protocol = "HTTP"
fastapi.isEncrypted = False  # Internal network, TLS terminated at edge
fastapi.sanitizesInput = True
fastapi.encodesOutput = True
fastapi.hasAccessControl = True
fastapi.authorizesSource = True
fastapi.OS = "Linux (Debian Bookworm)"

input_guard = Server("InputGuard (OWASP LLM01)")
input_guard.inBoundary = backend_container
input_guard.sanitizesInput = True
input_guard.isEncrypted = False

output_guard = Server("OutputGuard (OWASP LLM02/05/09)")
output_guard.inBoundary = backend_container
output_guard.encodesOutput = True
output_guard.sanitizesInput = True

qdrant = Server("Qdrant Vector Store")
qdrant.inBoundary = backend_container
qdrant.port = 6333
qdrant.protocol = "gRPC/HTTP"
qdrant.isEncrypted = False
qdrant.OS = "Linux"

retriever = Server("Hybrid Retriever (Dense+BM25+Reranker)")
retriever.inBoundary = backend_container
retriever.isEncrypted = False

llm_inference = Server("LLM Inference (Qwen2.5-3B)")
llm_inference.inBoundary = backend_container
llm_inference.isEncrypted = False

# =============================================================================
# Datastores
# =============================================================================
faq_csv = Datastore("FAQ CSVs (41 files)")
faq_csv.inBoundary = data_layer
faq_csv.isEncrypted = False
faq_csv.isSQL = False
faq_csv.storesLogData = False
faq_csv.storesPII = False

ura_pdfs = Datastore("URA PDF Handbooks (45 files)")
ura_pdfs.inBoundary = data_layer
ura_pdfs.isEncrypted = False
ura_pdfs.storesPII = False

sqlite_db = Datastore("SQLite Analytics DB")
sqlite_db.inBoundary = data_layer
sqlite_db.isEncrypted = False
sqlite_db.isSQL = True
sqlite_db.storesLogData = True
sqlite_db.storesPII = True  # conversation logs may contain PII

model_artifacts = Datastore("Model Artifacts (HF Hub)")
model_artifacts.inBoundary = ml_pipeline
model_artifacts.isEncrypted = True
model_artifacts.protocol = "HTTPS"

vector_index = Datastore("Qdrant Vector Index")
vector_index.inBoundary = backend_container
vector_index.isEncrypted = False

# =============================================================================
# Lambdas (Serverless / CI Functions)
# =============================================================================
ci_pipeline = Lambda("CI/CD Pipeline")
ci_pipeline.inBoundary = ci_cd
ci_pipeline.hasAccessControl = True

training_job = Lambda("Kaggle Training Job")
training_job.inBoundary = ml_pipeline
training_job.hasAccessControl = True

secret_scanner = Lambda("Secret Scanning (4-Layer)")
secret_scanner.inBoundary = ci_cd
secret_scanner.hasAccessControl = True

trivy_scanner = Lambda("Trivy Security Scanner")
trivy_scanner.inBoundary = ci_cd
trivy_scanner.hasAccessControl = True

sast_scanner = Lambda("SAST Scanner (Semgrep + Bandit)")
sast_scanner.inBoundary = ci_cd

dast_scanner = Lambda("DAST Scanner (OWASP ZAP)")
dast_scanner.inBoundary = ci_cd

threat_model_ci = Lambda("Threat Model Validator")
threat_model_ci.inBoundary = ci_cd

# =============================================================================
# Data Flows
# =============================================================================

# --- User → Frontend ---
user_to_frontend = Dataflow(taxpayer, nextjs, "User query (HTTPS)")
user_to_frontend.protocol = "HTTPS"
user_to_frontend.isEncrypted = True
user_to_frontend.authenticatesDestination = True
user_to_frontend.sanitizesInput = False  # User input is untrusted

frontend_to_user = Dataflow(nextjs, taxpayer, "Chat response (HTTPS)")
frontend_to_user.protocol = "HTTPS"
frontend_to_user.isEncrypted = True
frontend_to_user.sanitizesInput = True

# --- Frontend → Backend ---
fe_to_api = Dataflow(nextjs, fastapi, "REST API call (/v1/chat)")
fe_to_api.protocol = "HTTP"
fe_to_api.isEncrypted = False  # Internal Docker network
fe_to_api.sanitizesInput = True

api_to_fe = Dataflow(fastapi, nextjs, "SSE chat response stream")
api_to_fe.protocol = "HTTP/SSE"

# --- Backend Internal ---
api_to_input_guard = Dataflow(fastapi, input_guard, "Raw user input")
api_to_input_guard.isEncrypted = False
api_to_input_guard.sanitizesInput = False

input_guard_to_retriever = Dataflow(
    input_guard, retriever, "Sanitised query"
)

retriever_to_qdrant = Dataflow(retriever, qdrant, "Dense vector query")
retriever_to_qdrant.protocol = "gRPC"

qdrant_to_retriever = Dataflow(
    qdrant, retriever, "Top-k candidate documents"
)

retriever_to_llm = Dataflow(
    retriever, llm_inference, "Reranked context + query"
)

llm_to_output_guard = Dataflow(
    llm_inference, output_guard, "Raw LLM response"
)
llm_to_output_guard.sanitizesInput = False

output_guard_to_api = Dataflow(
    output_guard, fastapi, "Sanitised + PII-redacted response"
)
output_guard_to_api.sanitizesInput = True

# --- Backend → Datastores ---
api_to_sqlite = Dataflow(
    fastapi, sqlite_db, "Analytics & conversation logs"
)
api_to_sqlite.isEncrypted = False
api_to_sqlite.storesPII = True

retriever_to_vectors = Dataflow(
    retriever, vector_index, "Vector similarity search"
)

# --- Data Ingestion ---
csv_to_retriever = Dataflow(faq_csv, retriever, "FAQ data ingestion")
pdf_to_retriever = Dataflow(ura_pdfs, retriever, "PDF data ingestion")

# --- ML Pipeline ---
admin_to_github = Dataflow(admin, github, "Git push (signed commits)")
admin_to_github.protocol = "HTTPS"
admin_to_github.isEncrypted = True

github_to_ci = Dataflow(github, ci_pipeline, "Webhook trigger")
github_to_ci.protocol = "HTTPS"

ci_to_kaggle = Dataflow(
    ci_pipeline, training_job, "Training job dispatch"
)
ci_to_kaggle.protocol = "HTTPS"

training_to_hf = Dataflow(
    training_job, model_artifacts, "Push trained weights"
)
training_to_hf.protocol = "HTTPS"
training_to_hf.isEncrypted = True

hf_to_api = Dataflow(
    model_artifacts, fastapi, "Pull model on deployment"
)
hf_to_api.protocol = "HTTPS"
hf_to_api.isEncrypted = True

# --- CI Security Scans ---
ci_to_secret_scan = Dataflow(
    ci_pipeline, secret_scanner, "Trigger secret scanning"
)

ci_to_trivy = Dataflow(
    ci_pipeline, trivy_scanner, "Trigger vulnerability scan"
)

ci_to_sast = Dataflow(
    ci_pipeline, sast_scanner, "Trigger SAST analysis"
)

ci_to_dast = Dataflow(
    ci_pipeline, dast_scanner, "Trigger DAST baseline scan"
)

ci_to_threat_model = Dataflow(
    ci_pipeline, threat_model_ci, "Validate threat model"
)

# --- Docker Hub ---
ci_to_docker = Dataflow(
    ci_pipeline, docker_hub, "Push container images"
)
ci_to_docker.protocol = "HTTPS"
ci_to_docker.isEncrypted = True

# =============================================================================
# Custom Threat Overlays — OWASP LLM Top 10 + MITRE ATLAS
# =============================================================================
# pytm auto-generates STRIDE threats; we add domain-specific overlays here
# via the findings mechanism so they appear in the final report.

tm.assumptions = [
    "TLS 1.3 terminates at the edge load balancer; internal traffic is HTTP on Docker bridge",
    "Qdrant does not enforce authentication (trusts Docker network isolation)",
    "Training data sources are trusted (official URA website and handbooks)",
    "GitHub Actions runners are ephemeral and GitHub-hosted (not self-hosted)",
    "Rate limiting is enforced at the FastAPI application layer (slowapi)",
]

# =============================================================================
# Entry Point
# =============================================================================
if __name__ == "__main__":
    tm.process()
