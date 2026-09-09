"""离线配置检查；不显示Key，不请求供应商。"""
from pathlib import Path
import sys,importlib.util
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
modules=['fastapi','uvicorn','httpx','jsonschema','pdfplumber','PIL','openpyxl','defusedxml','filelock','dotenv']
missing=[m for m in modules if importlib.util.find_spec(m) is None]
print('Python:',sys.version.split()[0]);print('缺少依赖:', ', '.join(missing) or '无')
if missing:raise SystemExit(1)
from app.settings import Settings
s=Settings.from_env();print('模式:',s.provider);print('真实API开关:',s.live_enabled)
if s.provider!='mock':
 for message in s.live_errors():print('阻断:',message)
print('仅离线检查，不代表真实API已连通；不得把服务开放到公网。')
