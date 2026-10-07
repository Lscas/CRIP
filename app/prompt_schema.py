"""Local schema expansion shared by model envelopes and input authentication."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def expand_schema(schema: Any) -> Any:
    """Inline the same local references used by the historical Gateway."""
    if isinstance(schema,list):return [expand_schema(v) for v in schema]
    if not isinstance(schema,dict):return schema
    if '$ref' in schema:
        path,*fragment=schema['$ref'].split('#')
        obj=json.loads((ROOT/'spec/schemas'/path.rsplit('/',1)[-1]).read_text(encoding="utf-8"))
        if fragment and fragment[0]:
            for key in fragment[0].strip('/').split('/'):obj=obj[key]
        return expand_schema(obj)
    return {k:expand_schema(v) for k,v in schema.items() if k not in ('$id','$schema','title')}
