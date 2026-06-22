#!/usr/bin/env python3
"""Extract Supabase GraphQL schema including queries, mutations, types, and input types.

This script introspects a Supabase GraphQL endpoint and extracts comprehensive schema
information including:
- Root query and mutation fields
- All object types with fields
- Input types with input fields
- Enum types with values
- Scalar types
- Interface and Union types

Perfect for client-side development reference.

Usage:
  SUPABASE_URL=https://rpscbfgbkuebhaonlifh.supabase.co \
  SUPABASE_ANON_KEY=sb_publishable_Dr8iuOIx74RZeCLpM0MVfQ_EFH4lSZs \
  python3 scripts/extract_supabase_graphql.py --json > schema.json

Optional flags:
  --json   Emit machine-readable JSON instead of a text report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType {
      name
      fields {
        name
        description
        args {
          name
          description
          type {
            ...TypeRef
          }
          defaultValue
        }
        type {
          ...TypeRef
        }
        isDeprecated
        deprecationReason
      }
    }
    mutationType {
      name
      fields {
        name
        description
        args {
          name
          description
          type {
            ...TypeRef
          }
          defaultValue
        }
        type {
          ...TypeRef
        }
        isDeprecated
        deprecationReason
      }
    }
    types {
      kind
      name
      description
      fields {
        name
        description
        args {
          name
          description
          type {
            ...TypeRef
          }
          defaultValue
        }
        type {
          ...TypeRef
        }
        isDeprecated
        deprecationReason
      }
      inputFields {
        name
        description
        type {
          ...TypeRef
        }
        defaultValue
      }
      enumValues {
        name
        description
        isDeprecated
        deprecationReason
      }
      interfaces {
        name
      }
      possibleTypes {
        name
      }
    }
  }
}

fragment TypeRef on __Type {
  kind
  name
  ofType {
    kind
    name
    ofType {
      kind
      name
      ofType {
        kind
        name
        ofType {
          kind
          name
          ofType {
            kind
            name
            ofType {
              kind
              name
            }
          }
        }
      }
    }
  }
}
""".strip()


def unwrap_type(type_ref: dict[str, Any] | None) -> str:
    if not type_ref:
        return "Unknown"

    kind = type_ref.get("kind")
    name = type_ref.get("name")
    of_type = type_ref.get("ofType")

    if kind == "NON_NULL" and of_type:
        return f"{unwrap_type(of_type)}!"
    if kind == "LIST" and of_type:
        return f"[{unwrap_type(of_type)}]"
    return name or kind or "Unknown"


def format_field(field: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": field.get("name"),
        "description": field.get("description"),
        "returnType": unwrap_type(field.get("type")),
        "arguments": [
            {
                "name": argument.get("name"),
                "description": argument.get("description"),
                "type": unwrap_type(argument.get("type")),
                "defaultValue": argument.get("defaultValue"),
            }
            for argument in field.get("args", [])
        ],
        "deprecated": field.get("isDeprecated", False),
        "deprecationReason": field.get("deprecationReason"),
    }


def format_input_field(field: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": field.get("name"),
        "description": field.get("description"),
        "type": unwrap_type(field.get("type")),
        "defaultValue": field.get("defaultValue"),
    }


def format_enum_value(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": value.get("name"),
        "description": value.get("description"),
        "deprecated": value.get("isDeprecated", False),
        "deprecationReason": value.get("deprecationReason"),
    }


def format_type(type_obj: dict[str, Any]) -> dict[str, Any]:
    result = {
        "name": type_obj.get("name"),
        "kind": type_obj.get("kind"),
        "description": type_obj.get("description"),
    }
    
    if type_obj.get("fields"):
        result["fields"] = [format_field(field) for field in type_obj.get("fields", [])]
    
    if type_obj.get("inputFields"):
        result["inputFields"] = [format_input_field(field) for field in type_obj.get("inputFields", [])]
    
    if type_obj.get("enumValues"):
        result["enumValues"] = [format_enum_value(value) for value in type_obj.get("enumValues", [])]
    
    if type_obj.get("interfaces"):
        result["interfaces"] = [iface.get("name") for iface in type_obj.get("interfaces", [])]
    
    if type_obj.get("possibleTypes"):
        result["possibleTypes"] = [ptype.get("name") for ptype in type_obj.get("possibleTypes", [])]
    
    return result


def fetch_schema(endpoint: str, api_key: str) -> dict[str, Any]:
    payload = json.dumps({"query": INTROSPECTION_QUERY}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to reach GraphQL endpoint: {exc}") from exc

    if response_payload.get("errors"):
        raise RuntimeError(json.dumps(response_payload["errors"], indent=2))

    return response_payload["data"]["__schema"]


def build_report(schema: dict[str, Any]) -> dict[str, Any]:
    query_type = schema.get("queryType") or {}
    mutation_type = schema.get("mutationType") or {}
    
    # Filter and categorize types
    all_types = schema.get("types", [])
    
    # Exclude internal GraphQL types (starting with __)
    object_types = [t for t in all_types if t.get("kind") == "OBJECT" and not t.get("name", "").startswith("__")]
    input_types = [t for t in all_types if t.get("kind") == "INPUT_OBJECT" and not t.get("name", "").startswith("__")]
    enum_types = [t for t in all_types if t.get("kind") == "ENUM" and not t.get("name", "").startswith("__")]
    scalar_types = [t for t in all_types if t.get("kind") == "SCALAR" and not t.get("name", "").startswith("__")]
    interface_types = [t for t in all_types if t.get("kind") == "INTERFACE" and not t.get("name", "").startswith("__")]
    union_types = [t for t in all_types if t.get("kind") == "UNION" and not t.get("name", "").startswith("__")]

    return {
        "queryType": query_type.get("name"),
        "mutationType": mutation_type.get("name"),
        "queries": [format_field(field) for field in query_type.get("fields", [])],
        "mutations": [format_field(field) for field in mutation_type.get("fields", [])],
        "types": {
            "objects": [format_type(t) for t in sorted(object_types, key=lambda x: x.get("name", ""))],
            "inputs": [format_type(t) for t in sorted(input_types, key=lambda x: x.get("name", ""))],
            "enums": [format_type(t) for t in sorted(enum_types, key=lambda x: x.get("name", ""))],
            "scalars": [format_type(t) for t in sorted(scalar_types, key=lambda x: x.get("name", ""))],
            "interfaces": [format_type(t) for t in sorted(interface_types, key=lambda x: x.get("name", ""))],
            "unions": [format_type(t) for t in sorted(union_types, key=lambda x: x.get("name", ""))],
        },
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"Query root: {report['queryType'] or 'none'}")
    print(f"Mutation root: {report['mutationType'] or 'none'}")
    print()

    # Print queries and mutations
    for section_name in ("queries", "mutations"):
        section = report.get(section_name, [])
        title = section_name.capitalize()
        print(f"{title} ({len(section)})")
        print("-" * len(f"{title} ({len(section)})"))

        if not section:
            print("  none")
            print()
            continue

        for field in section:
            args = field["arguments"]
            args_text = ", ".join(
                f"{argument['name']}: {argument['type']}" for argument in args
            )
            signature = f"({args_text})" if args_text else ""
            line = f"- {field['name']}{signature}: {field['returnType']}"
            if field["deprecated"]:
                reason = field["deprecationReason"] or "deprecated"
                line += f" [deprecated: {reason}]"
            print(line)

        print()

    # Print types
    types_section = report.get("types", {})
    
    type_categories = [
        ("objects", "Object Types"),
        ("inputs", "Input Types"),
        ("enums", "Enum Types"),
        ("scalars", "Scalar Types"),
        ("interfaces", "Interface Types"),
        ("unions", "Union Types"),
    ]
    
    for category_key, category_title in type_categories:
        types_list = types_section.get(category_key, [])
        if not types_list:
            continue
        
        print(f"{category_title} ({len(types_list)})")
        print("-" * len(f"{category_title} ({len(types_list)})"))
        
        for type_obj in types_list:
            print(f"• {type_obj['name']}")
            if type_obj.get("description"):
                print(f"  Description: {type_obj['description']}")
            
            # Print fields for objects and interfaces
            if type_obj.get("fields"):
                print("  Fields:")
                for field in type_obj["fields"]:
                    args_text = ""
                    if field.get("arguments"):
                        args = ", ".join(f"{a['name']}: {a['type']}" for a in field["arguments"])
                        args_text = f"({args})"
                    print(f"    - {field['name']}{args_text}: {field['returnType']}")
                    if field.get("description"):
                        print(f"      {field['description']}")
            
            # Print input fields for input types
            if type_obj.get("inputFields"):
                print("  Input Fields:")
                for field in type_obj["inputFields"]:
                    default = f" = {field['defaultValue']}" if field.get("defaultValue") else ""
                    print(f"    - {field['name']}: {field['type']}{default}")
                    if field.get("description"):
                        print(f"      {field['description']}")
            
            # Print enum values
            if type_obj.get("enumValues"):
                print("  Values:")
                for value in type_obj["enumValues"]:
                    print(f"    - {value['name']}")
                    if value.get("description"):
                        print(f"      {value['description']}")
            
            # Print interfaces
            if type_obj.get("interfaces"):
                interfaces = ", ".join(type_obj["interfaces"])
                print(f"  Implements: {interfaces}")
            
            # Print possible types for unions
            if type_obj.get("possibleTypes"):
                types_list_str = ", ".join(type_obj["possibleTypes"])
                print(f"  Possible Types: {types_list_str}")
            
            print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract root queries and mutations from a Supabase GraphQL schema."
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("SUPABASE_GRAPHQL_URL"),
        help=(
            "GraphQL endpoint URL. Defaults to SUPABASE_GRAPHQL_URL, or "
            "${SUPABASE_URL}/graphql/v1 if SUPABASE_URL is set."
        ),
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        help=(
            "Supabase anon or service-role key. Defaults to SUPABASE_ANON_KEY "
            "or SUPABASE_SERVICE_ROLE_KEY."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the extracted schema as JSON.",
    )
    return parser.parse_args()


def resolve_endpoint(raw_endpoint: str | None) -> str:
    if raw_endpoint:
        return raw_endpoint.rstrip("/")

    supabase_url = os.getenv("SUPABASE_URL")
    if supabase_url:
        return f"{supabase_url.rstrip('/')}/graphql/v1"

    raise SystemExit(
        "Missing GraphQL endpoint. Set SUPABASE_GRAPHQL_URL or SUPABASE_URL."
    )


def main() -> int:
    args = parse_args()
    endpoint = resolve_endpoint(args.endpoint)
    api_key = args.api_key

    if not api_key:
        raise SystemExit(
            "Missing Supabase API key. Set SUPABASE_ANON_KEY or "
            "SUPABASE_SERVICE_ROLE_KEY."
        )

    schema = fetch_schema(endpoint, api_key)
    report = build_report(schema)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())