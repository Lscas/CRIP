"""Thin read-only Autodesk APS and Procore document import adapters."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import re
from urllib.parse import quote,urljoin,urlsplit

import httpx

from app.db import DomainError
from app.local_credentials import (LocalCredentialError,forget_api_key,load_api_key,
                                   save_api_key)


PROVIDERS={
    'autodesk':{'label':'Autodesk Construction Cloud','api':'https://developer.api.autodesk.com'},
    'procore':{'label':'Procore','api':'https://api.procore.com'},
}
_AUTODESK_OBJECT=re.compile(r'^urn:adsk\.objects:os\.object:([-_.a-z0-9]{3,128})/(.+)$')
_SAFE_DOWNLOAD_SUFFIXES={'.autodesk.com','.procore.com','.amazonaws.com','.cloudfront.net'}


class ExternalConnectors:
    def __init__(self,settings,uploads,client: httpx.Client | None=None):
        self.settings=settings;self.uploads=uploads;self.memory={}
        self.client=client or httpx.Client(timeout=httpx.Timeout(90,connect=15),follow_redirects=False)

    def close(self):self.client.close()

    @staticmethod
    def _provider(provider: str) -> str:
        if provider not in PROVIDERS:raise DomainError('Unsupported external source',404)
        return provider

    def _local(self):
        if self.settings.remote_enabled:raise DomainError('External source connections are local-computer only',403)

    @staticmethod
    def _credential_name(provider: str) -> str:return 'connector-'+provider

    def configure(self,provider: str,access_token: str,context: dict,remember: bool):
        self._local();provider=self._provider(provider);token=access_token.strip()
        if not token or len(token)>16384 or any(ord(char)<32 for char in token):
            raise DomainError('A valid access token is required')
        clean={key:str(context.get(key) or '').strip() for key in ('hub_id','company_id','project_id','folder_id')}
        if not clean['project_id']:raise DomainError('Project ID is required')
        if provider=='autodesk' and not clean['hub_id']:raise DomainError('Autodesk hub ID is required')
        if provider=='procore' and (not clean['company_id'] or not clean['company_id'].isdigit()
                                   or not clean['project_id'].isdigit()):
            raise DomainError('Procore company and project IDs must be numbers')
        if any(len(value)>2048 for value in clean.values()):raise DomainError('External source identifier is too long')
        bundle={'version':1,'access_token':token,'context':clean}
        if remember:
            try:save_api_key(self.settings.data_dir,self._credential_name(provider),json.dumps(bundle))
            except (LocalCredentialError,OSError) as exc:raise DomainError('Windows could not save this connection securely') from exc
            self.memory.pop(provider,None)
        else:
            try:forget_api_key(self.settings.data_dir,self._credential_name(provider))
            except LocalCredentialError as exc:raise DomainError('Windows could not replace the saved connection') from exc
            self.memory[provider]=bundle
        return self.status(provider)

    def disconnect(self,provider: str):
        self._local();provider=self._provider(provider);self.memory.pop(provider,None)
        try:forget_api_key(self.settings.data_dir,self._credential_name(provider))
        except LocalCredentialError as exc:raise DomainError('Windows could not remove the saved connection') from exc
        return self.status(provider)

    def _bundle(self,provider: str) -> dict:
        self._local();provider=self._provider(provider);bundle=self.memory.get(provider)
        if bundle is None:
            try:raw=load_api_key(self.settings.data_dir,self._credential_name(provider))
            except LocalCredentialError as exc:raise DomainError('The saved connection cannot be decrypted by this Windows user',409) from exc
            if raw:
                try:bundle=json.loads(raw)
                except (TypeError,json.JSONDecodeError) as exc:raise DomainError('The saved connection is invalid',409) from exc
        if not isinstance(bundle,dict) or not bundle.get('access_token'):
            raise DomainError(f'{PROVIDERS[provider]["label"]} is not connected',409)
        return bundle

    def status(self,provider: str | None=None):
        self._local()
        names=[self._provider(provider)] if provider else list(PROVIDERS)
        result=[]
        for name in names:
            configured=name in self.memory
            if not configured:
                try:configured=load_api_key(self.settings.data_dir,self._credential_name(name)) is not None
                except LocalCredentialError:configured=False
            result.append({'provider':name,'label':PROVIDERS[name]['label'],'configured':configured,
                           'mode':'READ_ONLY','saved_for_current_windows_user':configured and name not in self.memory})
        return result[0] if provider else result

    def _json(self,provider: str,path: str,params: dict | None=None) -> dict | list:
        bundle=self._bundle(provider);url=PROVIDERS[provider]['api']+path
        headers={'Authorization':'Bearer '+bundle['access_token'],'Accept':'application/json'}
        if provider=='procore':headers['Procore-Company-Id']=bundle['context']['company_id']
        try:response=self.client.get(url,headers=headers,params=params)
        except httpx.HTTPError as exc:raise DomainError(f'{PROVIDERS[provider]["label"]} is unavailable',502) from exc
        if response.status_code in (401,403):raise DomainError(f'{PROVIDERS[provider]["label"]} denied this read-only request',403)
        if response.status_code>=400:raise DomainError(f'{PROVIDERS[provider]["label"]} returned HTTP {response.status_code}',502)
        try:return response.json()
        except ValueError as exc:raise DomainError(f'{PROVIDERS[provider]["label"]} returned invalid JSON',502) from exc

    def items(self,provider: str,folder_id: str | None=None) -> list[dict]:
        provider=self._provider(provider);bundle=self._bundle(provider);context=bundle['context']
        folder=(folder_id or context.get('folder_id') or '').strip()
        if len(folder)>2048:raise DomainError('External folder identifier is too long')
        if provider=='autodesk':
            if folder:
                payload=self._json(provider,f'/data/v1/projects/{quote(context["project_id"],safe="")}/folders/{quote(folder,safe="")}/contents')
            else:
                payload=self._json(provider,f'/project/v1/hubs/{quote(context["hub_id"],safe="")}/projects/{quote(context["project_id"],safe="")}/topFolders')
            raw=payload.get('data',[]) if isinstance(payload,dict) else []
            return [{'remote_id':str(item.get('id','')),'kind':'FOLDER' if item.get('type')=='folders' else 'FILE',
                     'name':str((item.get('attributes') or {}).get('displayName') or
                                (item.get('attributes') or {}).get('name') or 'Unnamed'),
                     'size':(item.get('attributes') or {}).get('storageSize')}
                    for item in raw if item.get('type') in {'folders','items'} and item.get('id')]
        path='/rest/v1.0/folders'+(f'/{quote(folder,safe="")}' if folder else '')
        payload=self._json(provider,path,{'project_id':context['project_id'],'exclude_folders':'false',
                                          'exclude_files':'false','show_latest_file_version_only':'true'})
        folders=payload.get('folders',[]) if isinstance(payload,dict) else []
        files=payload.get('files',[]) if isinstance(payload,dict) else []
        return ([{'remote_id':str(item['id']),'kind':'FOLDER','name':str(item.get('name') or 'Unnamed'),'size':None}
                 for item in folders if isinstance(item,dict) and item.get('id') is not None]+
                [{'remote_id':str(item['id']),'kind':'FILE','name':str(item.get('name') or 'Unnamed'),
                  'size':item.get('size')} for item in files if isinstance(item,dict) and item.get('id') is not None])

    @staticmethod
    def _download_host_allowed(provider: str,url: str) -> bool:
        parsed=urlsplit(url);host=(parsed.hostname or '').lower()
        if parsed.scheme!='https' or parsed.username is not None or parsed.password is not None:return False
        api_host=urlsplit(PROVIDERS[provider]['api']).hostname
        return host==api_host or any(host.endswith(suffix) for suffix in _SAFE_DOWNLOAD_SUFFIXES)

    def _download(self,provider: str,url: str,name: str,expected_size: int | None,project_id: str):
        if not self._download_host_allowed(provider,url):raise DomainError('Provider returned an untrusted download location',502)
        token=self._bundle(provider)['access_token']
        headers=({'Authorization':'Bearer '+token}
                 if urlsplit(url).hostname==urlsplit(PROVIDERS[provider]['api']).hostname else {})
        upload=None
        try:
            with ExitStack() as stack:
                current=url
                for redirect in range(4):
                    response=stack.enter_context(self.client.stream('GET',current,headers=headers))
                    if response.status_code not in (301,302,303,307,308):break
                    if redirect==3:raise DomainError('Provider download redirected too many times',502)
                    target=urljoin(current,response.headers.get('location',''))
                    if not self._download_host_allowed(provider,target):raise DomainError('Provider returned an untrusted download location',502)
                    same_host=urlsplit(target).hostname==urlsplit(current).hostname
                    current=target;headers=headers if same_host else {}
                if response.status_code in (401,403):raise DomainError(f'{PROVIDERS[provider]["label"]} denied the file download',403)
                if response.status_code>=400:raise DomainError(f'{PROVIDERS[provider]["label"]} download returned HTTP {response.status_code}',502)
                length=response.headers.get('content-length')
                size=int(length) if length and length.isdigit() else expected_size
                if not isinstance(size,int) or size<0:raise DomainError('Provider did not report a safe file size',502)
                if isinstance(expected_size,int) and expected_size>=0 and length and size!=expected_size:
                    raise DomainError('Provider file size changed; refresh the source list',409)
                upload=self.uploads.create(project_id,name,size);offset=0;buffer=bytearray()
                for part in response.iter_bytes():
                    buffer.extend(part)
                    while len(buffer)>=self.settings.chunk_bytes:
                        chunk=bytes(buffer[:self.settings.chunk_bytes]);del buffer[:self.settings.chunk_bytes]
                        self.uploads.write_chunk(upload['id'],offset,chunk);offset+=len(chunk)
                if buffer:self.uploads.write_chunk(upload['id'],offset,bytes(buffer));offset+=len(buffer)
                if offset!=size:raise DomainError('Provider file size changed during download',409)
            return self.uploads.complete(upload['id'])
        except Exception:
            if upload:
                self.uploads.db.execute("UPDATE uploads SET state='ABORTED' WHERE id=? AND state='UPLOADING'",(upload['id'],))
            raise

    def import_file(self,provider: str,remote_id: str,project_id: str):
        provider=self._provider(provider);bundle=self._bundle(provider);context=bundle['context']
        if not remote_id or len(remote_id)>2048:raise DomainError('Invalid external file identifier')
        if provider=='autodesk':
            payload=self._json(provider,f'/data/v1/projects/{quote(context["project_id"],safe="")}/items/{quote(remote_id,safe="")}/tip')
            data=payload.get('data',{}) if isinstance(payload,dict) else {};attributes=data.get('attributes') or {}
            storage=((data.get('relationships') or {}).get('storage') or {}).get('data') or {}
            match=_AUTODESK_OBJECT.fullmatch(str(storage.get('id') or ''))
            if not match:raise DomainError('Autodesk item has no downloadable storage object',422)
            bucket,object_key=match.groups();url=(PROVIDERS[provider]['api']+
                f'/oss/v2/buckets/{quote(bucket,safe="")}/objects/{quote(object_key,safe="")}')
            name=str(attributes.get('displayName') or attributes.get('name') or Path(object_key).name)
            size=attributes.get('storageSize');size=int(size) if isinstance(size,(int,float)) and size>=0 else None
        else:
            payload=self._json(provider,f'/rest/v1.0/files/{quote(remote_id,safe="")}',
                               {'project_id':context['project_id'],'show_latest_version_only':'true'})
            versions=payload.get('file_versions',[]) if isinstance(payload,dict) else []
            if not versions:raise DomainError('Procore file has no downloadable version',422)
            version=max(versions,key=lambda item:(
                int(str(item.get('number') or '0')) if str(item.get('number') or '0').isdigit() else 0,
                str(item.get('created_at') or '')))
            store=version.get('prostore_file') or {};url=str(store.get('url') or version.get('url') or '')
            name=str(payload.get('name') or store.get('filename') or store.get('name') or 'procore-file')
            size=version.get('size',payload.get('size'));size=int(size) if isinstance(size,(int,float)) and size>=0 else None
        return self._download(provider,url,name,size,project_id)
