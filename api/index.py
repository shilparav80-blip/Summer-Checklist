import sys
from pathlib import Path

# Vercel runs this file from api/, so the project root isn't on sys.path by default.
# Insert it so `import main` resolves correctly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app  # noqa: F401
