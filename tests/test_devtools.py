"""DEV-LOWCOST-001 / DEV-CHECK-001：真实脚本，不验证账户价格。"""
import json,sys,tomllib
from pathlib import Path
import pytest
from scripts.context_pack import build,ROOT
from scripts.build_bundle_manifest import source_files
from scripts.run_checks import build_commands

def test_context_pack_is_bounded_and_contains_only_selected_requirements():
    out=build('DEV-002')
    assert len(out)<=14000 and 'FR-OPTIONS-001' in out
    assert '全量读取所有文件' not in out
    assert 'api_key=' not in out.lower()

def test_context_rejects_path_traversal():
    with pytest.raises(ValueError):build('../.env')

def test_task_references_have_real_context_files():
    for p in (ROOT/'tasks').glob('*.json'):
        data=json.loads(p.read_text(encoding="utf-8"));assert not data['paid_calls_allowed']
        for name in data['context_files']:assert (ROOT/name).is_file()
        assert len(build(data['task_id']))<=data['max_context_chars']

def test_codex_config_is_small_and_does_not_weaken_security():
    data=tomllib.loads((ROOT/'.codex/config.toml').read_text(encoding="utf-8"))
    assert data=={'model_reasoning_effort':'low'}
    assert len((ROOT/'AGENTS.md').read_text(encoding="utf-8"))<3000

def test_source_bundle_manifest_excludes_runtime_outputs():
    included = [path.relative_to(ROOT).parts for path in source_files()]
    assert all('outputs' not in parts for parts in included)
    assert all('.git' not in parts for parts in included)

def test_all_checks_include_every_runtime_and_fail_closed_without_node():
    with pytest.raises(RuntimeError, match='Node.js is required'):
        build_commands('all',None)
    commands=build_commands('all','node')
    assert [sys.executable,'scripts/check_spec_sync.py'] in commands
    assert [sys.executable,'scripts/build_bundle_manifest.py','--check'] in commands
    assert ['node','--test','tests/web/i18n.test.mjs'] in commands
    assert ['node','--test','tests/deploy/worker.test.mjs'] in commands
