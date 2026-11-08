import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from juno import hookcore
from juno.handlers.session_start import handle

if __name__ == "__main__":
    sys.exit(hookcore.run("session_start", handle))
