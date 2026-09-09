"""隔离解析进程。不执行宏，不访问文档外部链接。"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from app.parsers import parse_file

def main():
    source,name,destination=sys.argv[1:]
    try:
        result=parse_file(Path(source),name)
    except Exception as exc:
        result={'status':'FAILED','fragments':[],'pages':[],
                'warnings':['解析失败：'+type(exc).__name__],'parser_version':'text-baseline-1'}
    Path(destination).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')

if __name__=='__main__':main()
