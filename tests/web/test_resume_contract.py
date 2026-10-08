"""Execute the real resume control renderer with a synthetic no-network DOM."""
from pathlib import Path
import shutil
import subprocess


def test_resume_control_dom_contract():
    node=shutil.which('node')
    assert node is not None,'Node is required for resume control checks.'
    result=subprocess.run([node,'--test','tests/web/resume_contract.test.mjs'],
                          cwd=Path(__file__).resolve().parents[2],capture_output=True,
                          text=True,encoding='utf-8',errors='replace',check=False)
    assert result.returncode==0,result.stdout+result.stderr
