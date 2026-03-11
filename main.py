"""
main.py

CLI entrypoint for local development and testing.
Run: python main.py

Supports multi-turn conversation — type 'quit' to exit, 'reset' to clear history.
"""

import sys
import os

# Ensure the project root is on sys.path so `agent` and `tools` are importable
# regardless of which directory python is invoked from.
sys.path.insert(0, os.path.dirname(__file__))

from langchain_core.messages import HumanMessage, AIMessage
from agents.coach_agent import build_agent_executor, ask_coach


def main():
    print("=" * 60)
    print("  KhadBot — WoW Coaching Agent (Stub Mode)")
    print("  Type 'quit' to exit, 'reset' to clear chat history")
    print("=" * 60)
    print()

    executor = build_agent_executor(verbose=True)
    chat_history: list = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            print("Goodbye!")
            break
        if user_input.lower() == "reset":
            chat_history = []
            print("[Chat history cleared]\n")
            continue

        result = ask_coach(executor, user_input, chat_history)

        print(f"\nCoach: {result['answer']}\n")

        # Accumulate history for multi-turn context
        chat_history.append(HumanMessage(content=user_input))
        chat_history.append(AIMessage(content=result["answer"]))

        # Show which tools were called (handy during development)
        if result["steps"]:
            tools_used = [step[0] for step in result["steps"]]
            print(f"  [tools called: {', '.join(tools_used)}]\n")


if __name__ == "__main__":
    main()
