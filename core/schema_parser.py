#!/usr/bin/env python3
import json
from typing import Any

import pyarrow as pa


def json_type_to_arrow(json_type: str, nested_schema: Any = None) -> pa.DataType:
    """Maps JSON schema types to PyArrow types."""
    mapping = {
        "string": pa.string(),
        "int": pa.int64(),
        "float": pa.float64(),
        "bool": pa.bool_(),
        "null": pa.string(),  # Map null to string for robustness
    }

    if json_type == "array":
        # Recursive call for array elements
        if nested_schema and "schema" in nested_schema and len(nested_schema["schema"]) > 0:
            inner_type = json_type_to_arrow(
                nested_schema["schema"][0]["type"], nested_schema["schema"][0].get("schema")
            )
            return pa.list_(inner_type)
        return pa.list_(pa.string())  # Fallback

    if json_type == "object" or json_type == "key-value":
        # For simplicity and robustness across different schema versions,
        # we treat 'key-value' and 'object' (without a clear fixed schema) as JSON strings.
        # Fixed structs are only used for objects with a defined schema dictionary.

        if (
            nested_schema
            and "schema" in nested_schema
            and isinstance(nested_schema["schema"], dict)
            and json_type == "object"
        ):
            fields = []
            for sub_field, sub_info in nested_schema["schema"].items():
                f_type = json_type_to_arrow(sub_info[0]["type"], sub_info[0].get("schema"))
                fields.append(pa.field(sub_field, f_type))
            return pa.struct(fields)

        return pa.string()  # Fallback to JSON string for objects

    return mapping.get(json_type, pa.string())


def build_arrow_schema(schema_path: str, usage_threshold: float = 0.1) -> pa.Schema:
    """Builds a 'Clean Union' Arrow schema from a JSON schema file."""
    with open(schema_path) as f:
        data = json.load(f)
        json_fields = data[0]["schema"]

    arrow_fields = []

    # 1. Add Core Identity Fields (Always present, high priority)
    core_fields = ["id", "author", "subreddit", "link_id", "parent_id", "created_utc"]

    # 2. Iterate through all fields in the JSON schema
    # Sort fields to maintain consistent order
    for field in sorted(json_fields.keys()):
        info_list = json_fields[field]
        # Heuristic: Pick the most common type for the field
        best_type_info = info_list[0]
        usage = best_type_info["usage"]

        # Convert "always" to 1.0
        usage_val = 1.0 if usage == "always" else float(usage)

        if usage_val >= usage_threshold or field in core_fields:
            # Type conflict resolution:
            # If a field is sometimes bool and sometimes int (like 'edited'),
            # we map it to int64 for the unified schema.
            a_type = json_type_to_arrow(best_type_info["type"], best_type_info.get("schema"))

            # Special case for 'edited'
            if field == "edited":
                a_type = pa.int64()

            arrow_fields.append(pa.field(field, a_type))

    # 3. Add the Catchall column
    arrow_fields.append(pa.field("extra_json", pa.string()))

    return pa.schema(arrow_fields)


if __name__ == "__main__":
    # Test with a modern schema
    import sys

    path = "schemas/RC/2024/RC_2024-01.json"
    if len(sys.argv) > 1:
        path = sys.argv[1]
    schema = build_arrow_schema(path)
    print(f"Generated schema with {len(schema)} columns.")
    for field in schema:
        print(f"  {field.name}: {field.type}")
