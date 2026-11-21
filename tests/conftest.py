import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Keep the module-level default app from writing into the project directory.
os.environ.setdefault(
    "DATABASE", os.path.join(tempfile.mkdtemp(prefix="inspection-test-"), "default.db")
)
