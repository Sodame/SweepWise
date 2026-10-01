import asyncio
import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from utils.language import request_language, translate
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from backend.store import Store
from utils.config_handler import agent_conf
from utils.path_tool import get_abs_path
from utils.usage_records import load_usage_records

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
COOKIE = 'agent_session'


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)

    @field_validator('username')
    @classmethod
    def username_format(cls, value):
        if not re.fullmatch(r'[\w-]{3,32}', value):
            raise ValueError('用户名仅支持文字、数字、下划线和短横线')
        return value


class BrowserLocation(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    captured_at: float = Field(gt=0, allow_inf_nan=False)


class ChatInput(BaseModel):
    language: Literal['zh', 'en'] | None = None
    content: str = Field(min_length=1, max_length=12000)
    location: BrowserLocation | None = None

    @field_validator('content')
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError('消息不能为空')
        return value.strip()


def create_app(db_path=None, engine_factory=None, records_path=None, knowledge_manager=None):
    store = Store(db_path or os.getenv('DATABASE_PATH', str(ROOT / 'storage/chat.sqlite3')))
    report_path = str(records_path or get_abs_path(agent_conf['external_data_path']))

    @asynccontextmanager
    async def lifespan(app):
        try:
            records = await asyncio.to_thread(load_usage_records, report_path)
            store.demo_user_ids = tuple(records)
        except (OSError, ValueError):
            logging.exception('Demo usage records unavailable; report assignments skipped')
        await asyncio.to_thread(store.initialize)
        yield

    app = FastAPI(title='智扫通 Agent API', lifespan=lifespan)
    app.state.store = store
    app.state.engine_factory = engine_factory
    origins = [x.strip() for x in os.getenv('FRONTEND_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if x.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=['GET','POST','PUT','DELETE'], allow_headers=['Content-Type', 'Accept-Language'])

    @app.exception_handler(StarletteHTTPException)
    async def localized_error(request, exc):
        return JSONResponse({'detail': translate(exc.detail, request_language(request)) if isinstance(exc.detail, str) else exc.detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        detail = 'Invalid input. Check the required fields and try again.' if request_language(request) == 'en' else '输入格式不正确，请检查必填项后重试。'
        return JSONResponse({'detail': detail}, status_code=422)

    @app.middleware('http')
    async def check_origin(request: Request, call_next):
        origin = request.headers.get('origin')
        same_origin = str(request.base_url).rstrip('/')
        if request.method in {'POST', 'DELETE', 'PUT', 'PATCH'} and origin and origin not in origins and origin != same_origin:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': translate('请求来源不允许', request_language(request))}, status_code=403)
        return await call_next(request)

    def current_user(request: Request):
        token = request.cookies.get(COOKIE, '')
        user = store.user_for_token(token) if token else None
        if not user:
            raise HTTPException(401, '请先登录')
        return user

    def issue_cookie(response, user):
        response.set_cookie(COOKIE, store.login(user['id']), httponly=True, samesite='lax', secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true', max_age=604800, path='/')
        return user

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'model_configured': bool(os.getenv('DASHSCOPE_API_KEY'))}

    @app.post('/api/auth/register', status_code=201)
    def register(body: Credentials, response: Response):
        try:
            user = store.register(body.username, body.password)
        except sqlite3.IntegrityError:
            raise HTTPException(409, '用户名已存在') from None
        return issue_cookie(response, user)

    @app.post('/api/auth/login')
    def login(body: Credentials, response: Response):
        user = store.authenticate(body.username, body.password)
        if not user:
            raise HTTPException(401, '用户名或密码错误')
        return issue_cookie(response, user)

    @app.post('/api/auth/logout', status_code=204)
    def logout(request: Request, response: Response):
        store.logout(request.cookies.get(COOKIE, ''))
        response.delete_cookie(COOKIE, path='/')

    @app.get('/api/auth/me')
    def me(user=Depends(current_user)):
        return user

    @app.get('/api/conversations')
    def conversations(user=Depends(current_user)):
        return store.conversations(user['id'])

    @app.post('/api/conversations', status_code=201)
    def create_conversation(user=Depends(current_user)):
        return store.create_conversation(user['id'])

    @app.get('/api/conversations/{conversation_id}')
    def get_conversation(conversation_id: str, user=Depends(current_user)):
        result = store.get_conversation(user['id'], conversation_id)
        if result is None:
            raise HTTPException(404, '对话不存在')
        return result

    @app.delete('/api/conversations/{conversation_id}', status_code=204)
    def delete_conversation(conversation_id: str, user=Depends(current_user)):
        if store.get_conversation(user['id'], conversation_id) is None:
            raise HTTPException(404, '对话不存在')
        if not store.delete_conversation(user['id'], conversation_id):
            raise HTTPException(409, '对话正在生成，请稍后删除')

    @app.post('/api/conversations/{conversation_id}/messages')
    def chat(conversation_id: str, body: ChatInput, request: Request, user=Depends(current_user)):
        language = body.language or request_language(request)
        conversation = store.get_conversation(user['id'], conversation_id)
        if conversation is None:
            raise HTTPException(404, '对话不存在')
        factory = app.state.engine_factory
        if factory is None:
            if not os.getenv('DASHSCOPE_API_KEY'):
                raise HTTPException(503, '请在项目 .env 中配置 DASHSCOPE_API_KEY 后重启后端')
            from backend.runtime import create_engine
            factory = create_engine
        started = store.begin_turn(user['id'], conversation_id, body.content)
        if not started:
            raise HTTPException(409, '此对话正在生成回答，请等待完成')
        lease, answer_id = started
        # Use complete pairs only; a failed/cancelled turn is visible in history but not model context.
        history = []
        old = conversation['messages']
        for i in range(len(old) - 1):
            if old[i]['role'] == 'user' and old[i+1]['role'] == 'assistant' and old[i+1]['status'] == 'complete':
                history.extend([{'role': m['role'], 'content': m['content']} for m in old[i:i+2]])
        history = history[-20:] + [{'role': 'user', 'content': body.content}]
        events = queue.Queue(maxsize=128)
        stopped = threading.Event()

        def emit(event):
            while not stopped.is_set():
                try:
                    events.put(event, timeout=0.2)
                    return
                except queue.Full:
                    continue

        def worker():
            try:
                report_user = {**user, 'language': language, 'report_user_id': store.report_user_id(user['id']),
                               'report_records_path': report_path,
                               'browser_location': body.location.model_dump() if body.location else None}
                for event in factory().stream(history, report_user):
                    if stopped.is_set():
                        break
                    emit(event)
                emit({'type': 'done'})
            except Exception:
                logging.exception('Agent generation failed')
                emit({'type': 'error', 'message': translate('回答生成失败，请检查模型配置或稍后重试。', language)})

        async def stream():
            answer, sources, status = '', [], 'interrupted'
            try:
                threading.Thread(target=worker, daemon=True).start()
                yield 'data: ' + json.dumps({'type': 'start', 'message_id': answer_id}) + '\n\n'
                deadline = time.monotonic() + 180
                heartbeat = time.monotonic()
                while True:
                    if time.monotonic() > deadline:
                        status = 'error'
                        yield 'data: ' + json.dumps({'type': 'error', 'message': translate('生成超时，请重试。', language)}, ensure_ascii=False) + '\n\n'
                        break
                    try:
                        event = events.get_nowait()
                    except queue.Empty:
                        if time.monotonic() - heartbeat > 10:
                            yield ': heartbeat\n\n'
                            heartbeat = time.monotonic()
                        await asyncio.sleep(0.03)
                        continue
                    kind = event.get('type')
                    if kind == 'token':
                        answer += event.get('content', '')
                    elif kind == 'sources':
                        sources = event.get('sources', [])
                    elif kind == 'done':
                        status = 'complete' if answer.strip() else 'error'
                        if status == 'error':
                            event = {'type': 'error', 'message': translate('模型未返回文本，请重试。', language)}
                    elif kind == 'error':
                        status = 'error'
                    # Persist before the completion event so immediate history reload is consistent.
                    if kind in {'done', 'error'}:
                        store.finish_turn(user['id'], conversation_id, lease, answer_id, answer, status, sources)
                    yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
                    if kind in {'done', 'error'}:
                        break
            finally:
                stopped.set()
                store.finish_turn(user['id'], conversation_id, lease, answer_id, answer, status, sources)

        return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    from backend.knowledge import install_routes
    install_routes(app, current_user, knowledge_manager)

    dist = ROOT / 'frontend/dist'
    if dist.is_dir():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

        @app.get('/')
        def index():
            return FileResponse(dist / 'index.html')

    return app


app = create_app()
