#!/usr/bin/env python3
"""Wiki operations for Hindsight agent knowledge pages.

Subcommands:
  wiki.py list              — list all wiki pages
  wiki.py get <page-id>     — get a specific page
  wiki.py create <page-id> <name> <source-query>  — create a page
  wiki.py update <page-id> [--name ...] [--source-query ...]  — update a page
  wiki.py delete <page-id>  — delete a page
  wiki.py recall <query>    — search memories
  wiki.py ingest <title> --file <path>  — ingest a document
  wiki.py documents         — list retained documents

Uses the same bank resolution as the retain/recall hooks.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.bank import derive_bank_id
from lib.client import HindsightClient
from lib.config import load_config
from lib.daemon import get_api_url


def get_client_and_bank():
    """Resolve API URL and bank ID using the plugin's existing logic."""
    config = load_config()
    api_url = get_api_url(config)
    bank_id = derive_bank_id(config)
    token = config.get("hindsightApiToken")
    client = HindsightClient(api_url, api_token=token)
    return client, bank_id


def _api(client, method, bank_id, path, body=None):
    """Make a bank-scoped API request."""
    full_path = f"/v1/default/banks/{bank_id}{path}"
    return client._request(method, full_path, body)


# Wiki page defaults (opinionated for self-learning)
WIKI_TRIGGER = {
    "mode": "delta",
    "refresh_after_consolidation": True,
    "exclude_mental_models": True,
    "fact_types": ["observation"],
}


def cmd_list(_args):
    client, bank_id = get_client_and_bank()
    result = _api(client, "GET", bank_id, "/mental-models")
    print(json.dumps(result, indent=2))


def cmd_get(args):
    client, bank_id = get_client_and_bank()
    result = _api(client, "GET", bank_id, f"/mental-models/{args.page_id}")
    print(json.dumps(result, indent=2))


def cmd_create(args):
    client, bank_id = get_client_and_bank()
    payload = {
        "id": args.page_id,
        "name": args.name,
        "source_query": args.source_query,
        "trigger": WIKI_TRIGGER,
    }
    result = _api(client, "POST", bank_id, "/mental-models", payload)
    print(json.dumps(result, indent=2))


def cmd_update(args):
    if not args.name and not args.source_query:
        print("Error: at least one of --name or --source-query required", file=sys.stderr)
        sys.exit(1)
    client, bank_id = get_client_and_bank()
    payload = {}
    if args.name:
        payload["name"] = args.name
    if args.source_query:
        payload["source_query"] = args.source_query
    result = _api(client, "PATCH", bank_id, f"/mental-models/{args.page_id}", payload)
    print(json.dumps(result, indent=2))


def cmd_delete(args):
    client, bank_id = get_client_and_bank()
    _api(client, "DELETE", bank_id, f"/mental-models/{args.page_id}")
    print(json.dumps({"success": True}))


def cmd_recall(args):
    client, bank_id = get_client_and_bank()
    payload = {"query": args.query, "max_results": args.max_results}
    result = _api(client, "POST", bank_id, "/memories/recall", payload)
    print(json.dumps(result, indent=2))


def cmd_ingest(args):
    if args.file:
        with open(args.file) as f:
            content = f.read()
    elif args.content:
        content = args.content
    else:
        content = sys.stdin.read()

    if not content.strip():
        print("Error: no content provided", file=sys.stderr)
        sys.exit(1)

    client, bank_id = get_client_and_bank()
    doc_id = args.title.lower().replace(" ", "-")
    payload = {
        "items": [{"content": content, "document_id": doc_id}],
        "async": True,
    }
    result = _api(client, "POST", bank_id, "/memories", payload)
    print(json.dumps(result, indent=2))


def cmd_documents(_args):
    client, bank_id = get_client_and_bank()
    result = _api(client, "GET", bank_id, "/documents")
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Hindsight Wiki — knowledge pages for AI agents")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all wiki pages")

    p = sub.add_parser("get", help="Get a wiki page")
    p.add_argument("page_id")

    p = sub.add_parser("create", help="Create a wiki page")
    p.add_argument("page_id")
    p.add_argument("name")
    p.add_argument("source_query")

    p = sub.add_parser("update", help="Update a wiki page")
    p.add_argument("page_id")
    p.add_argument("--name")
    p.add_argument("--source-query", dest="source_query")

    p = sub.add_parser("delete", help="Delete a wiki page")
    p.add_argument("page_id")

    p = sub.add_parser("recall", help="Search memories")
    p.add_argument("query")
    p.add_argument("-n", "--max-results", type=int, default=10)

    p = sub.add_parser("ingest", help="Ingest a document")
    p.add_argument("title")
    p.add_argument("-f", "--file")
    p.add_argument("-c", "--content")

    sub.add_parser("documents", help="List retained documents")

    args = parser.parse_args()

    commands = {
        "list": cmd_list,
        "get": cmd_get,
        "create": cmd_create,
        "update": cmd_update,
        "delete": cmd_delete,
        "recall": cmd_recall,
        "ingest": cmd_ingest,
        "documents": cmd_documents,
    }

    try:
        commands[args.command](args)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
