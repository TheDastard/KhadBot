"""
main.py

CLI entrypoint for local development and testing.
Run: python main.py

Supports multi-turn conversation — type 'quit' to exit, 'reset' to clear history.
"""

import asyncio
import sys

from langchain_core.messages import AIMessage, HumanMessage

from khadbot.agent import ask_coach, build_agent_executor


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "cli"

    if mode == "cli":
        _run_cli()
    else:
        print(f"Unknown mode: {mode}.") # Use 'cli' or 'bot'.
        sys.exit(1)

def _run_cli():
    from khadbot.cli import run_cli

    executor = build_agent_executor(verbose=True)
    chat_history: list = []

    async def agent_fn(question: str, callbacks: list) -> str:
        """
        Bridge between the Rich CLI and ask_coach.

        ask_coach is currently synchronous (agent.invoke is sunc).
        We run it in a thread executor so it doesn't block the event loop
        and freeze the live tool panel.
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: ask_coach(executor, question, chat_history, callbacks=callbacks)
        )

        chat_history.append(HumanMessage(content=question))
        chat_history.append(AIMessage(content=result["answer"]))

        return result["answer"]

    asyncio.run(run_cli(agent_fn))


if __name__ == "__main__":
    main()
