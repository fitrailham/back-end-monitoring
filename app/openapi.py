"""Add executable fixture examples to the documented response schemas."""
import json

from app import config


def install_examples(app):
    original = app.openapi

    def openapi():
        schema = original()
        path = config.PROJECT_ROOT / "docs" / "frontend-api-examples.json"
        if not path.exists():
            return schema
        fixtures = json.loads(path.read_text(encoding="utf-8"))
        for group in ("endpoints", "errors"):
            for name, example in fixtures[group].items():
                operation = schema["paths"].get(example["path"], {}).get(example["method"].lower())
                if operation is None:
                    continue
                response = operation["responses"].get(str(example["status"]))
                if response is None:
                    continue
                content = response.setdefault("content", {}).setdefault("application/json", {})
                content.setdefault("examples", {})[name] = {
                    "summary": "Fixture ilustratif: " + name,
                    "value": example["response"],
                }
        return schema

    app.openapi = openapi
