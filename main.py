"""
main.py

Thin shim so ``python main.py`` keeps working; the real entry point is khadbot.main.
"""

from khadbot.main import main

if __name__ == "__main__":
    main()
