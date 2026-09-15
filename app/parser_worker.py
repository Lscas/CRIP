"""隔离解析进程。不执行宏，不访问文档外部链接。"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from app.parsers import PARSER_VERSION, parse_file

def save_result(destination: Path, result: dict) -> None:
    temporary=destination.with_name(destination.name+'.tmp')
    temporary.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    temporary.replace(destination)

def main():
    source,name,destination=sys.argv[1:]
    destination=Path(destination)
    try:
        result=parse_file(Path(source),name,progress=lambda current:save_result(destination,current))
    except Exception as exc:
        result={'status':'FAILED','fragments':[],'pages':[],
                'warnings':['解析失败：'+type(exc).__name__],'parser_version':PARSER_VERSION}
    save_result(destination,result)

if __name__=='__main__':main()
