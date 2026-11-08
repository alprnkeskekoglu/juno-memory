import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from juno import hookcore
from juno.handlers.post_tool_use import handle

if __name__ == "__main__":
    sys.exit(hookcore.run("post_tool_use", handle))
