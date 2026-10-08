"""Run the real drawer function with a no-network synthetic DOM in the full gate."""
from pathlib import Path
import shutil
import subprocess


def test_quantity_review_dom_contract():
    node = shutil.which('node')
    assert node is not None, 'Node is required for quantity review checks.'
    result = subprocess.run([node, '--test', 'tests/web/quantity_review.test.mjs'],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', check=False)
    assert result.returncode == 0, result.stdout + result.stderr
