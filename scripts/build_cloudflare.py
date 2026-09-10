"""仅复制允许的网页资产，不上传源代码、数据目录或密钥。"""
from pathlib import Path
import json
import shutil
import argparse
ROOT = Path(__file__).resolve().parents[1]

def build(destination: Path) -> Path:
    destination = destination.resolve()
    if destination == ROOT or ROOT in destination.parents and '.local' not in destination.relative_to(ROOT).parts:
        raise ValueError('仓库内输出必须位于.local，不覆盖源码。')
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('输出目录非空；为避免发布旧数据，请使用新目录。')
    (destination / 'assets').mkdir()
    for name in ['style.css', 'i18n.js', 'app.js']:
        shutil.copyfile(ROOT / 'web' / name, destination / 'assets' / name)
    shutil.copyfile(ROOT / 'web/index.html', destination / 'index.html')
    shutil.copyfile(ROOT / 'deploy/cloudflare/worker.mjs', destination / '_worker.js')
    (destination / '_routes.json').write_text(json.dumps({'version': 1, 'include': ['/*'], 'exclude': []}), encoding='utf-8')
    (destination / 'robots.txt').write_text('User-agent: *\nDisallow: /\n', encoding='utf-8')
    return destination

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True)
    print(build(Path(parser.parse_args().output)))
