"""hindsight-agent setup — one-shot agent onboarding.

Creates the Hindsight bank, installs the agent-knowledge skill
(with the agent ID baked in), and does harness-specific setup.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import click

from ..api import HindsightAPI
from ..config import AgentConfig, load_config, save_config

SKILL_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "skill"
OPENCLAW_PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugin" / "openclaw"


@click.command()
@click.argument("agent_id")
@click.option("--bank-id", required=True, help="Hindsight bank ID for this agent")
@click.option(
    "--api-url",
    default="http://localhost:8888",
    show_default=True,
    envvar="HINDSIGHT_API_URL",
    help="Hindsight API URL",
)
@click.option(
    "--api-token",
    default=None,
    envvar="HINDSIGHT_API_TOKEN",
    help="Hindsight API token (for cloud/authenticated instances)",
)
@click.option(
    "--harness",
    type=click.Choice(["openclaw", "hermes"]),
    required=True,
    help="Agent harness to configure",
)
@click.option("--workspace", type=click.Path(), default=None, help="Agent workspace directory (harness-specific default if omitted)")
@click.option("--model", default=None, help="LLM model ID for the agent (harness-specific)")
@click.option("--template", type=click.Path(exists=True), default=None, help="Bank template JSON file to import after bank creation")
@click.option("--content", type=click.Path(exists=True), default=None, help="Directory of files to ingest into the bank at setup time (async)")
def setup(
    agent_id: str,
    bank_id: str,
    api_url: str,
    api_token: str | None,
    harness: str,
    workspace: str | None,
    model: str | None,
    template: str | None,
    content: str | None,
) -> None:
    """Set up a new agent with Hindsight memory.

    AGENT_ID is the unique identifier for this agent.
    """
    workspace_path = _resolve_workspace(agent_id, harness, workspace)

    click.echo(f"Setting up agent '{agent_id}'")
    click.echo(f"  Bank:      {bank_id}")
    click.echo(f"  API:       {api_url}")
    click.echo(f"  Harness:   {harness}")
    click.echo(f"  Workspace: {workspace_path}")
    click.echo()

    # 0. Health check
    api = HindsightAPI(api_url, api_token=api_token)
    try:
        r = api.client.get("/health")
        r.raise_for_status()
    except Exception:
        raise click.ClickException(
            f"Cannot reach Hindsight at {api_url}. "
            "Make sure the server is running or pass --api-url / --api-token."
        )

    # 1. Create bank on Hindsight
    click.echo("Creating Hindsight bank...")

    # If template provided, import it first (this also creates the bank)
    if template:
        click.echo(f"  Importing bank template from {template}...")
        template_data = json.loads(Path(template).read_text())
        api.import_template(bank_id, template_data)
    else:
        api.ensure_bank(bank_id)
    click.echo("  Done.")

    # 2. Ingest content directory if provided
    if content:
        _ingest_content(api, bank_id, Path(content).expanduser().resolve())

    # 3. Save to global config
    click.echo("Saving agent config...")
    agents = load_config()
    agents[agent_id] = AgentConfig(
        bank_id=bank_id,
        api_url=api_url,
        harness=harness,
        workspace=str(workspace_path),
        api_token=api_token,
    )
    save_config(agents)
    click.echo("  Done.")

    # 4. Install skill into workspace
    click.echo("Installing agent-knowledge skill...")
    _install_skill(agent_id, workspace_path, harness)
    click.echo("  Done.")

    # 5. Harness-specific setup
    if harness == "openclaw":
        click.echo("Configuring OpenClaw...")
        _setup_openclaw(agent_id, workspace_path, model)
        click.echo("  Done.")
    elif harness == "hermes":
        click.echo("Configuring Hermes...")
        _setup_hermes(agent_id, workspace_path)
        click.echo("  Done.")

    click.echo()
    click.echo(f"Agent '{agent_id}' is ready.")
    if harness == "openclaw":
        click.echo("  Restart your openclaw gateway to pick up the new agent.")
    elif harness == "hermes":
        if agent_id == "default":
            click.echo("  Start chatting: hermes")
        else:
            click.echo(f"  Start chatting: hermes --profile {agent_id}")


CONTENT_EXTENSIONS = {".md", ".txt", ".html", ".json", ".csv", ".xml"}


def _ingest_content(api: HindsightAPI, bank_id: str, content_dir: Path) -> None:
    """Ingest all files from a directory into the bank (async)."""
    if not content_dir.is_dir():
        raise click.ClickException(f"Content path is not a directory: {content_dir}")

    files = [
        f for f in sorted(content_dir.iterdir())
        if f.is_file() and f.suffix.lower() in CONTENT_EXTENSIONS
    ]

    if not files:
        click.echo(f"  No files to ingest in {content_dir}")
        return

    click.echo(f"Ingesting {len(files)} file(s) from {content_dir}...")
    for f in files:
        text = f.read_text(errors="replace")
        if not text.strip():
            continue
        result = api.retain(bank_id, text, document_id=f.stem)
        op_id = result.get("operation_id", "")
        click.echo(f"  {f.name} → queued (operation: {op_id})")

    click.echo("  Content ingestion queued (async). Run consolidation after completion.")


HERMES_PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugin" / "hermes"


def _resolve_workspace(agent_id: str, harness: str, workspace: str | None) -> Path:
    if workspace:
        return Path(workspace).expanduser().resolve()
    if harness == "openclaw":
        return Path.home() / ".hindsight-agents" / "openclaw" / agent_id
    if harness == "hermes":
        # Hermes skills live in ~/.hermes/skills/
        return Path.home() / ".hermes"
    return Path.home() / ".hindsight-agents" / agent_id


def _install_skill(agent_id: str, workspace: Path, harness: str) -> None:
    """Copy the skill template into the workspace with agent_id baked in."""
    skill_dir = workspace / "skills" / "agent-knowledge"
    skill_dir.mkdir(parents=True, exist_ok=True)

    template = SKILL_TEMPLATE_DIR / "SKILL.md"
    if not template.exists():
        raise click.ClickException(f"Skill template not found at {template}")

    content = template.read_text()
    content = content.replace("{{AGENT_ID}}", agent_id)
    (skill_dir / "SKILL.md").write_text(content)

    # Harness-specific: patch startup to auto-load the skill
    if harness == "openclaw":
        agents_md = workspace / "AGENTS.md"
        if agents_md.exists():
            text = agents_md.read_text()
            skill_line = "5. Read `skills/agent-knowledge/SKILL.md` and **execute its mandatory startup sequence** (run the commands, don't just read them)"
            if "agent-knowledge/SKILL.md" not in text and "## Session Startup" in text:
                text = text.replace(
                    "Don't ask permission. Just do it.",
                    f"{skill_line}\n\nDon't ask permission. Just do it.",
                )
                agents_md.write_text(text)


def _setup_openclaw(agent_id: str, workspace: Path, model: str | None) -> None:
    """Create the OpenClaw agent and install the retain plugin."""
    # Ensure workspace exists with basic structure
    workspace.mkdir(parents=True, exist_ok=True)

    # Check if agent already exists in OpenClaw
    if _openclaw_agent_exists(agent_id):
        click.echo(f"  Agent '{agent_id}' already exists in OpenClaw, skipping creation.")
    else:
        # Create the agent
        cmd = [
            "openclaw", "agents", "add", agent_id,
            "--workspace", str(workspace),
            "--non-interactive",
        ]
        if model:
            cmd.extend(["--model", model])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise click.ClickException(f"Failed to create OpenClaw agent: {result.stderr}")
        click.echo(f"  Created OpenClaw agent '{agent_id}'.")

    # Install the retain plugin
    _install_openclaw_plugin(agent_id)


def _openclaw_agent_exists(agent_id: str) -> bool:
    """Check if an agent exists in OpenClaw."""
    result = subprocess.run(
        ["openclaw", "agents", "list", "--json"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False
    try:
        data = json.loads(result.stdout)
        return any(a.get("name") == agent_id for a in data.get("agents", []))
    except (json.JSONDecodeError, KeyError):
        return False


def _install_openclaw_plugin(agent_id: str) -> None:
    """Install the lightweight hindsight-agent retain plugin into OpenClaw."""
    # Check if plugin is already installed
    openclaw_config = Path.home() / ".openclaw" / "openclaw.json"
    if openclaw_config.exists():
        config = json.loads(openclaw_config.read_text())
        plugins = config.get("plugins", {}).get("entries", {})
        if "hindsight-agent" in plugins:
            click.echo("  Retain plugin already configured in OpenClaw.")
            return

    # Install the plugin package
    if not OPENCLAW_PLUGIN_DIR.exists():
        raise click.ClickException(
            f"OpenClaw plugin not found at {OPENCLAW_PLUGIN_DIR}. "
            "Make sure you're running from the hindsight-agent repo."
        )

    # Build the plugin if needed
    dist_dir = OPENCLAW_PLUGIN_DIR / "dist"
    if not dist_dir.exists():
        click.echo("  Building retain plugin...")
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=OPENCLAW_PLUGIN_DIR,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise click.ClickException(f"Failed to build plugin: {result.stderr}")

    # Install via openclaw CLI
    result = subprocess.run(
        ["openclaw", "plugins", "install", str(OPENCLAW_PLUGIN_DIR)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Fallback: manually add to config
        click.echo(f"  Plugin install via CLI failed ({result.stderr.strip()}), configuring manually...")
        _manually_configure_openclaw_plugin(openclaw_config)
    else:
        click.echo("  Retain plugin installed.")


def _manually_configure_openclaw_plugin(openclaw_config: Path) -> None:
    """Add the plugin config directly to openclaw.json."""
    config: dict = {}
    if openclaw_config.exists():
        config = json.loads(openclaw_config.read_text())

    plugins = config.setdefault("plugins", {}).setdefault("entries", {})
    plugins["hindsight-agent"] = {
        "enabled": True,
        "config": {},
    }
    openclaw_config.write_text(json.dumps(config, indent=2) + "\n")
    click.echo("  Retain plugin configured in openclaw.json.")


def _setup_hermes(agent_id: str, workspace: Path) -> None:
    """Install the hindsight-agent memory plugin into Hermes and create profile if needed."""
    hermes_home = Path.home() / ".hermes"

    if not HERMES_PLUGIN_DIR.exists():
        raise click.ClickException(
            f"Hermes plugin not found at {HERMES_PLUGIN_DIR}. "
            "Make sure you're running from the hindsight-agent repo."
        )

    # Create Hermes profile if it doesn't exist (skip for "default")
    if agent_id != "default":
        result = subprocess.run(
            ["hermes", "profile", "create", agent_id, "--clone", "--no-alias"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            click.echo(f"  Created Hermes profile '{agent_id}'.")
        elif "already exists" in result.stderr.lower():
            click.echo(f"  Profile '{agent_id}' already exists.")
        else:
            click.echo(f"  Note: Create profile manually: hermes profile create {agent_id}")

    # Memory plugins live in the hermes-agent repo checkout
    plugin_dest = hermes_home / "hermes-agent" / "plugins" / "memory" / "hindsight_agent"
    plugin_dest.mkdir(parents=True, exist_ok=True)
    for src_file in HERMES_PLUGIN_DIR.iterdir():
        if src_file.is_file():
            shutil.copy2(src_file, plugin_dest / src_file.name)

    # Fix plugin name to match directory (no hyphens)
    plugin_yaml = plugin_dest / "plugin.yaml"
    if plugin_yaml.exists():
        text = plugin_yaml.read_text()
        text = text.replace("name: hindsight-agent", "name: hindsight_agent")
        plugin_yaml.write_text(text)

    click.echo(f"  Retain plugin installed to {plugin_dest}")

    # Set memory provider (--profile flag must come before subcommand)
    if agent_id != "default":
        config_cmd = ["hermes", "--profile", agent_id, "config", "set", "memory.provider", "hindsight_agent"]
    else:
        config_cmd = ["hermes", "config", "set", "memory.provider", "hindsight_agent"]
    result = subprocess.run(config_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        click.echo("  Memory provider set to hindsight_agent")
    else:
        click.echo(f"  Note: Set memory provider with: hermes config set memory.provider hindsight_agent")
