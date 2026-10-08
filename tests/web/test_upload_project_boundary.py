"""Exercise upload project capture with a synthetic no-network DOM."""
from pathlib import Path
import shutil
import subprocess


def test_upload_project_boundary_dom_contract():
    node = shutil.which('node')
    assert node is not None, 'Node is required for upload project boundary checks.'
    result = subprocess.run([node, '--test', 'tests/web/upload_project_boundary.test.mjs'],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', check=False)
    assert result.returncode == 0, result.stdout + result.stderr
