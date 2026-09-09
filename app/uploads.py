"""4MiB顺序分片、重复分片校验、断点续传及不可变原文件。"""
from __future__ import annotations
import hashlib
from pathlib import Path
from app.db import Database, DomainError, now, uid
from app.settings import Settings

class Uploads:
    def __init__(self, db: Database, settings: Settings):
        self.db=db; self.settings=settings
        self.tmp=settings.data_dir/'uploads'; self.objects=settings.data_dir/'objects'
        self.tmp.mkdir(parents=True,exist_ok=True); self.objects.mkdir(parents=True,exist_ok=True)

    def create(self, project_id: str, name: str, size: int):
        name=name.replace('\\','/').split('/')[-1]
        if not name or len(name)>240 or any(ord(x)<32 for x in name): raise DomainError('无效文件名')
        upload_id=uid('UP')
        with self.db.connect(True) as c:
            if not c.execute('SELECT id FROM projects WHERE id=?',(project_id,)).fetchone(): raise DomainError('项目不存在',404)
            total=c.execute("SELECT COALESCE(SUM(size),0) FROM uploads WHERE project_id=? AND state NOT IN ('DUPLICATE','ABORTED')",(project_id,)).fetchone()[0]
            if size<0 or total+size>self.settings.project_bytes: raise DomainError(f'超出当前项目接收容量配置（{self.settings.project_bytes:,} bytes）',413)
            c.execute('INSERT INTO uploads(id,project_id,name,size,created_at) VALUES(?,?,?,?,?)',(upload_id,project_id,name,size,now()))
        (self.tmp/upload_id).touch()
        return self.get(upload_id)

    def get(self, upload_id: str):
        row = self.db.one('SELECT * FROM uploads WHERE id=?',(upload_id,))
        row['chunks'] = self.db.all('SELECT offset,size,checksum FROM upload_chunks WHERE upload_id=? ORDER BY offset',(upload_id,))
        return row

    def write_chunk(self, upload_id: str, offset: int, data: bytes):
        if not data or len(data)>self.settings.chunk_bytes: raise DomainError('分片为空或过大',413)
        checksum=hashlib.sha256(data).hexdigest()
        with self.db.connect(True) as c:
            row=c.execute('SELECT * FROM uploads WHERE id=?',(upload_id,)).fetchone()
            if not row: raise DomainError('上传任务不存在',404)
            if row['state']!='UPLOADING': raise DomainError('上传任务已关闭',409)
            existing=c.execute('SELECT * FROM upload_chunks WHERE upload_id=? AND offset=?',(upload_id,offset)).fetchone()
            if existing:
                if existing['checksum']!=checksum or existing['size']!=len(data): raise DomainError('重复分片内容不同',409)
                return {'offset':row['offset'],'replayed':True}
            if offset!=row['offset'] or offset+len(data)>row['size']: raise DomainError('分片位置或总长度不一致',409)
            p=self.tmp/upload_id
            with p.open('r+b') as f:
                f.seek(offset); f.write(data); f.truncate(offset+len(data)); f.flush()
                import os; os.fsync(f.fileno())
            c.execute('INSERT INTO upload_chunks VALUES(?,?,?,?)',(upload_id,offset,len(data),checksum))
            c.execute('UPDATE uploads SET offset=? WHERE id=?',(offset+len(data),upload_id))
        return {'offset':offset+len(data),'replayed':False}

    def complete(self, upload_id: str):
        row=self.get(upload_id)
        if row['state'] in ('COMPLETE','DUPLICATE'): return row
        if row['state']!='UPLOADING' or row['offset']!=row['size']: raise DomainError('文件尚未上传完整',409)
        source=self.tmp/upload_id
        h=hashlib.sha256()
        with source.open('rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
        digest=h.hexdigest()
        with self.db.connect(True) as c:
            # 完成操作幂等；同一内容的别名仍留在uploads清单。
            current=c.execute('SELECT * FROM uploads WHERE id=?',(upload_id,)).fetchone()
            if current['state'] in ('COMPLETE','DUPLICATE'): return dict(current)
            dup=c.execute('SELECT id FROM documents WHERE project_id=? AND sha256=?',(row['project_id'],digest)).fetchone()
            if dup: doc_id=dup['id']; state='DUPLICATE'
            else:
                doc_id=uid('DOC'); key=f'{row["project_id"]}/{digest}'
                target=self.objects/key; target.parent.mkdir(parents=True,exist_ok=True)
                # copy instead of rename: a crash before commit leaves the upload retryable.
                import shutil; shutil.copyfile(source,target)
                c.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?)',
                          (doc_id,row['project_id'],row['name'],row['size'],digest,key,now()))
                state='COMPLETE'
            c.execute('UPDATE uploads SET state=?,document_id=? WHERE id=?',(state,doc_id,upload_id))
        return self.get(upload_id)

    def object_path(self, doc: dict) -> Path:
        path=(self.objects/doc['object_key']).resolve()
        if self.objects.resolve() not in path.parents: raise DomainError('非法对象路径')
        return path
