from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


PLUGIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
TEMPLATE_NAMES = (
    "hello_world",
    "read_only_memory_tool",
    "network_lookup_tool",
    "admin_only_output_tool",
    "production_plugin_package",
    "readonly_retrieval_plugin",
    "controlled_network_plugin",
    "file_summary_plugin",
)
TEMPLATE_ALIASES = {
    "production-package": "production_plugin_package",
    "production_package": "production_plugin_package",
    "readonly-retrieval": "readonly_retrieval_plugin",
    "readonly_retrieval": "readonly_retrieval_plugin",
    "controlled-network": "controlled_network_plugin",
    "controlled_network": "controlled_network_plugin",
    "file-summary": "file_summary_plugin",
    "file_summary": "file_summary_plugin",
}


class PluginScaffoldError(ValueError):
    """Raised when a plugin scaffold cannot be created."""


def create_plugin_scaffold(
    target_dir: str | Path,
    *,
    name: str,
    description: str = "Example model-callable plugin tool.",
    author: str = "plugin-author",
    tool_name: str = "echo",
    template: str | None = None,
) -> Path:
    plugin_name = _validate_name(name, "plugin name")
    public_tool_name = _validate_name(tool_name, "tool name")
    root = Path(target_dir).resolve()
    plugin_dir = root / plugin_name
    if plugin_dir.exists():
        raise PluginScaffoldError(f"plugin directory already exists: {plugin_dir}")
    if template:
        template = TEMPLATE_ALIASES.get(template, template)
        return _copy_template(
            plugin_dir,
            template=template,
            plugin_name=plugin_name,
            description=description,
            author=author,
        )
    src_dir = plugin_dir / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "main.py").write_text(
        _main_py(public_tool_name),
        encoding="utf-8",
    )
    tests_dir = plugin_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_placeholder.py").write_text(
        _test_placeholder(public_tool_name),
        encoding="utf-8",
    )
    (plugin_dir / "config_schema.json").write_text(
        _config_schema(),
        encoding="utf-8",
    )
    (plugin_dir / "README.md").write_text(
        _readme(plugin_name=plugin_name, tool_name=public_tool_name),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.yaml").write_text(
        _plugin_yaml(
            plugin_name=plugin_name,
            description=description,
            author=author,
            tool_name=public_tool_name,
        ),
        encoding="utf-8",
    )
    return plugin_dir


def _copy_template(
    plugin_dir: Path,
    *,
    template: str,
    plugin_name: str,
    description: str,
    author: str,
) -> Path:
    template = TEMPLATE_ALIASES.get(template, template)
    template_name = _validate_name(template, "template name")
    template_dir = Path(__file__).resolve().parent / "templates" / template_name
    if not template_dir.exists():
        raise PluginScaffoldError(f"unknown plugin template: {template_name}")
    shutil.copytree(template_dir, plugin_dir)
    plugin_yaml = plugin_dir / "plugin.yaml"
    text = plugin_yaml.read_text(encoding="utf-8")
    text = _replace_yaml_line(text, "name", plugin_name)
    text = _replace_yaml_line(text, "description", description)
    text = _replace_yaml_line(text, "author", author)
    plugin_yaml.write_text(text, encoding="utf-8")
    return plugin_dir


def _replace_yaml_line(text: str, key: str, value: str) -> str:
    return re.sub(rf"^{re.escape(key)}:\s*.*$", f"{key}: {value}", text, count=1, flags=re.MULTILINE)


def _validate_name(value: str, label: str) -> str:
    value = value.strip()
    if not PLUGIN_NAME_PATTERN.match(value):
        raise PluginScaffoldError(f"{label} must use lowercase letters, numbers, and underscores")
    return value


def _main_py(tool_name: str) -> str:
    return "\n".join(
        [
            f"def {tool_name}(args, api=None):",
            "    text = args['text']",
            "    # Use api for network, filesystem, memory, config, or output access.",
            "    return {'text': text}",
            "",
        ]
    )


def _plugin_yaml(
    *,
    plugin_name: str,
    description: str,
    author: str,
    tool_name: str,
) -> str:
    return "\n".join(
        [
            f"name: {plugin_name}",
            "version: 1.0.0",
            f"description: {description}",
            f"author: {author}",
            "license: MIT",
            "",
            "extensions:",
            "  - type: tool",
            f"    name: {tool_name}",
            f"    entry: src.main:{tool_name}",
            "    description: Echo text supplied by the model.",
            "    params:",
            "      text:",
            "        type: string",
            "        description: Text to echo.",
            "        required: true",
            "    returns:",
            "      type: object",
            "      required:",
            "        - text",
            "      properties:",
            "        text:",
            "          type: string",
            "          maxLength: 4096",
            "      additionalProperties: false",
            "",
            "permissions:",
            "  - compute: true",
            "",
            "runtime:",
            "  mode: sub_process",
            "  trust: third_party",
            "  timeout_seconds: 5",
            "  max_concurrency: 1",
            "",
        ]
    )


def _config_schema() -> str:
    return "\n".join(
        [
            "{",
            '  "$schema": "https://json-schema.org/draft/2020-12/schema",',
            '  "type": "object",',
            '  "properties": {},',
            '  "additionalProperties": false',
            "}",
            "",
        ]
    )


def _readme(*, plugin_name: str, tool_name: str) -> str:
    return "\n".join(
        [
            f"# {plugin_name}",
            "",
            "Model-callable plugin scaffold.",
            "",
            "## Tool",
            "",
            f"- `{tool_name}` accepts `text` and returns `{ '{' }\"text\": string{ '}' }`.",
            "- Results are treated as untrusted tool data by PluginToolService.",
            "",
            "## Local Checks",
            "",
            "```bash",
            "python -m modules.plugin_system.manifest_lint . --json",
            "python -m modules.plugin_system.llm_tools . --provider openai --approved --json",
            "python -m modules.plugin_system.production_policy_check . --json",
            "```",
            "",
            "## Production Notes",
            "",
            "- Keep third-party plugins in `sub_process` runtime.",
            "- Use the injected `api` Gateway for network, filesystem, memory, config, and output access.",
            "- Declare exact per-tool permissions and result schemas.",
            "- Sign packages with Ed25519, include `manifest.lock`, `sbom.cdx.json`, and scanner evidence.",
            "- Side-effecting tools should use confirmation and stable idempotency keys when invoked by model loops.",
            "",
        ]
    )


def _test_placeholder(tool_name: str) -> str:
    return "\n".join(
        [
            "from src.main import " + tool_name,
            "",
            "",
            f"def test_{tool_name}_returns_text():",
            f"    assert {tool_name}({{'text': 'hello'}}) == {{'text': 'hello'}}",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a plugin scaffold")
    parser.add_argument("target_dir", nargs="?")
    parser.add_argument("name", nargs="?")
    parser.add_argument("--output")
    parser.add_argument("--description", default="Example model-callable plugin tool.")
    parser.add_argument("--author", default="plugin-author")
    parser.add_argument("--tool-name", default="echo")
    parser.add_argument("--template", choices=[*TEMPLATE_NAMES, *TEMPLATE_ALIASES])
    args = parser.parse_args(argv)
    target_dir = args.output or args.target_dir
    if not target_dir:
        parser.error("target_dir or --output is required")
    plugin_name = args.name
    if not plugin_name:
        template_name = TEMPLATE_ALIASES.get(str(args.template or ""), str(args.template or "plugin"))
        plugin_name = template_name if template_name else "plugin"
    plugin_dir = create_plugin_scaffold(
        target_dir,
        name=plugin_name,
        description=args.description,
        author=args.author,
        tool_name=args.tool_name,
        template=args.template,
    )
    print(plugin_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
