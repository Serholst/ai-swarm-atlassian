"""Shared constants for LLM prompt construction."""

from ..constants import CORE_DOC_MAX_CHARS, SUPPORTING_DOC_MAX_CHARS  # noqa: F401

LAYER_CODES_BLOCK = """\
Layer codes:
- `BE` - Backend, API, Microservices, Workers
- `FE` - Frontend, UI/UX implementation
- `INFRA` - Terraform, K8s, CI/CD pipelines
- `DB` - Migrations, SQL, Schema changes
- `QA` - Tests (E2E, Integration), Automation
- `DOCS` - Documentation, Technical writing
- `GEN` - General (fallback for cross-cutting)"""
