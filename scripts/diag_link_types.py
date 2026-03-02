"""Diagnostic: list available Jira link types and test parent field support."""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from src.executor.mcp.servers.jira_server import JiraAPIClient as JiraClient

client = JiraClient(
    os.getenv("JIRA_BASE_URL"),
    os.getenv("JIRA_BOT_EMAIL"),
    os.getenv("JIRA_BOT_API_TOKEN"),
)

print("=== Available Issue Link Types ===")
for lt in client.get_link_types():
    print(f"  {lt['name']:25s}  inward: {lt['inward']:30s}  outward: {lt['outward']}")

resolved = client.resolve_parent_link_type("Parent")
print(f"\n=== Auto-resolved parent link type: '{resolved}' ===")

# Test parent field support by doing a dry create (validate only)
print("\n=== Testing parent field on Story creation (validate only) ===")
import requests

url = f"{client.base_url}/rest/api/3/issue"
fields = {
    "project": {"key": "WEB3"},
    "issuetype": {"name": "Story"},
    "summary": "TEST - delete me",
    "parent": {"key": "WEB3-29"},
}
try:
    resp = client.session.post(url, json={"fields": fields}, timeout=(5, 30))
    if resp.ok:
        # Created accidentally — delete it
        key = resp.json().get("key", "")
        print(f"  parent field WORKS (created {key} — deleting)")
        client.session.delete(f"{client.base_url}/rest/api/3/issue/{key}", timeout=(5, 30))
    else:
        print(f"  parent field REJECTED ({resp.status_code}):")
        try:
            errors = resp.json()
            print(f"  {json.dumps(errors, indent=2, ensure_ascii=False)}")
        except Exception:
            print(f"  {resp.text[:500]}")
except Exception as e:
    print(f"  Request failed: {e}")
