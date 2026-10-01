"""Authenticated knowledge management routes; mutations require a curator account."""
import asyncio
import logging
import os
from typing import Literal

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from utils.language import request_language


class DocumentEdit(BaseModel):
    content: str = Field(min_length=1, max_length=1_000_000)
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')


def install_routes(app, current_user, supplied_manager=None):
    from rag.knowledge_storage import KnowledgeManager, KnowledgeError, MAX_BYTES
    manager = supplied_manager or KnowledgeManager()

    def can_manage(user):
        configured = os.getenv('KNOWLEDGE_ADMIN_USERS','').strip()
        if configured:
            return user['username'] in {name.strip() for name in configured.split(',') if name.strip()}
        with app.state.store.connect() as db:
            row = db.execute('SELECT id FROM users ORDER BY rowid LIMIT 1').fetchone()
            return bool(row and row['id']==user['id'])

    def curator(request:Request, user=Depends(current_user)):
        if not can_manage(user):
            raise HTTPException(403,'Only knowledge administrators can modify documents.' if request_language(request)=='en' else '只有知识库管理员可以修改文档')
        return user

    def invoke(request, method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except KnowledgeError as exc:
            raise HTTPException(exc.status,exc.en if request_language(request)=='en' else exc.zh) from None
        except Exception:
            logging.exception('Knowledge operation failed')
            message = 'Knowledge operation failed. Please retry; an interrupted update will be restored before the next operation.' if request_language(request)=='en' else '知识库操作失败，请重试；未完成的更新会在下次操作前恢复。'
            raise HTTPException(503,message) from None

    @app.get('/api/knowledge/{language}/documents')
    def listing(language:Literal['zh','en'], request:Request, user=Depends(current_user)):
        return {'can_manage':can_manage(user), 'documents':invoke(request,manager.list,language)}

    @app.get('/api/knowledge/{language}/document')
    def detail(language:Literal['zh','en'], name:str, request:Request, user=Depends(current_user)):
        return invoke(request,manager.read,language,name)

    @app.post('/api/knowledge/{language}/documents', status_code=201)
    async def upload(language:Literal['zh','en'], name:str, request:Request, user=Depends(curator)):
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>MAX_BYTES:
                raise HTTPException(413,'Files must be no larger than 10 MB.' if request_language(request)=='en' else '文件最大支持 10 MB')
        return await asyncio.to_thread(invoke,request,manager.mutate,language,name,bytes(data),action='upload',actor=user['username'])

    @app.put('/api/knowledge/{language}/document')
    def update(language:Literal['zh','en'], name:str, body:DocumentEdit, request:Request, user=Depends(curator)):
        return invoke(request,manager.mutate,language,name,body.content.encode('utf-8'),expected=body.revision,action='edit',actor=user['username'])

    @app.delete('/api/knowledge/{language}/document',status_code=204)
    def delete(language:Literal['zh','en'], name:str, revision:str, request:Request, user=Depends(curator)):
        invoke(request,manager.mutate,language,name,expected=revision,action='delete',actor=user['username'])
