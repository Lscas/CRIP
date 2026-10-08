"""隔离解析进程。不执行宏，不访问文档外部链接。"""
from __future__ import annotations
import json
import sys
import traceback
from pathlib import Path
from app.parsers import PARSER_VERSION, parse_file

def save_result(destination: Path, result: dict) -> None:
    temporary=destination.with_name(destination.name+'.tmp')
    temporary.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    temporary.replace(destination)

def safe_failure_warning(exc: Exception) -> str:
    """Retain a useful code location without exposing a source path or message."""
    frames=traceback.extract_tb(exc.__traceback__)
    frame=next((value for value in reversed(frames)
                if Path(value.filename).name in {'parsers.py','visual_pipeline.py','cad.py'}),None)
    location=f' at {Path(frame.filename).name}:{frame.lineno}' if frame else ''
    return f'Parse failed: {type(exc).__name__}{location}.'

def main():
    arguments=sys.argv[1:]
    if len(arguments)==3:
        source,name,destination=arguments;workers=1
    else:
        source,name,workers_text,destination=arguments;workers=int(workers_text)
    destination=Path(destination)
    try:
        result=parse_file(Path(source),name,progress=lambda current:save_result(destination,current),workers=workers)
    except Exception as exc:
        result={'status':'FAILED','fragments':[],'pages':[],
                'warnings':[safe_failure_warning(exc)],'parser_version':PARSER_VERSION}
    save_result(destination,result)

if __name__=='__main__':main()
