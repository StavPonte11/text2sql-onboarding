"""
Push the packaged Deep Agent prompts (agent/src/deep_agent/prompts/*.md) to Langfuse.

    uv run python scripts/sync_deep_agent_prompts.py --label staging            # create new versions labelled staging
    uv run python scripts/sync_deep_agent_prompts.py --label production --dry-run

Each run creates a new version of every `deep-agent/<key>` text prompt with the given label.
Promote a version by moving the `production` label in the Langfuse UI — the running agent
picks it up within DEEP_AGENT_PROMPT_CACHE_TTL_S, no redeploy.
"""

import argparse

from deep_agent.prompt_registry import PROMPT_KEYS, langfuse_name, local_prompt


def sync(client, label: str, dry_run: bool = False) -> list[str]:
    names = []
    for key in PROMPT_KEYS:
        name = langfuse_name(key)
        if not dry_run:
            client.create_prompt(name=name, prompt=local_prompt(key), labels=[label], type="text",
                                 commit_message="sync from agent/src/deep_agent/prompts")
        names.append(name)
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="staging")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from agent.langfuse_client import langfuse_client

    for name in sync(langfuse_client, args.label, args.dry_run):
        print(f"{'would push' if args.dry_run else 'pushed'} {name} [{args.label}]")
    if not args.dry_run:
        langfuse_client.flush()


if __name__ == "__main__":
    main()
