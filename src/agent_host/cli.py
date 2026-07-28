"""CLI entry point for the simplified pipeline."""

from __future__ import annotations

import argparse
import sys

from rich.console import Console

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ask the policy-gated agent pipeline a question.",
        prog="agent-harness",
    )
    parser.add_argument("question", nargs="?", help="Question to ask the pipeline.")
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Thread ID for conversation continuity (optional).",
    )
    args = parser.parse_args()

    if not args.question:
        parser.print_help()
        sys.exit(1)

    from agent_host.graph import ask, resume

    response = ask(args.question, thread_id=args.thread_id)

    # Handle clarification loop
    while response.interrupted:
        console.print(f"\n[yellow]Clarification needed:[/yellow] {response.clarification_prompt}")
        try:
            user_reply = input("> ").strip()
        except EOFError, KeyboardInterrupt:
            console.print("\n[red]Aborted.[/red]")
            sys.exit(1)
        if not user_reply:
            console.print("[red]Empty reply — aborting.[/red]")
            sys.exit(1)
        response = resume(user_reply, thread_id=response.thread_id or "")

    if not response.allowed:
        console.print(f"\n[red]Blocked:[/red] {response.policy_reason}")
        console.print(f"\n{response.answer}")
        sys.exit(1)

    console.print(f"\n{response.answer}")
    if response.intent:
        console.print(
            f"\n[dim]Intent: {response.intent} (confidence: {response.intent_confidence:.2f})[/dim]"
        )
    if response.used_tools:
        console.print(f"[dim]Tools: {', '.join(response.used_tools)}[/dim]")
    console.print(f"[dim]Run: {response.run_id} | Thread: {response.thread_id}[/dim]")


if __name__ == "__main__":
    main()
