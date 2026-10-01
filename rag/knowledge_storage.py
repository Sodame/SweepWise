"""File/index transactions and cross-process coordination for managed documents."""
import hashlib
import io
import json
import os
import re
import uuid
from pathlib import Path

from filelock import FileLock
from langchain_core.documents import Document
from pypdf import PdfReader
from utils.path_tool import get_abs_path

MAX_BYTES = 10 * 1024 * 1024


class KnowledgeError(Exception):
    def __init__(self, status, zh, en):
        self.status, self.zh, self.en = status, zh, en
        super().__init__(en)


def root_path(root=None):
    return Path(root or get_abs_path('.')).resolve()


def validate_language(language):
    if language not in {'zh', 'en'}:
        raise KnowledgeError(400, '不支持的知识库语言', 'Unsupported knowledge language.')


def document_path(root, language, name):
    validate_language(language)
    if (not name or len(name) > 150 or name != name.strip() or name.startswith('.')
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith('.')
            or re.match(r'^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)', name, re.I)
            or Path(name).suffix.lower() not in {'.txt', '.pdf'}):
        raise KnowledgeError(400, '文件名无效，仅支持 TXT 和 PDF', 'Invalid filename. Only TXT and PDF files are supported.')
    folder = (root / 'data' / language).resolve()
    folder.relative_to(root)
    path = folder / name
    if path.is_symlink() or path.resolve().parent != folder:
        raise KnowledgeError(400, '文件路径无效', 'Invalid document path.')
    return path


def lock(root, language):
    validate_language(language)
    folder = root / 'storage' / 'knowledge_transactions'
    folder.mkdir(parents=True, exist_ok=True)
    return FileLock(str(folder / f'{language}.lock'), timeout=180)


def pending_path(root, language):
    return root / 'storage' / 'knowledge_transactions' / f'{language}.pending.json'


def revision(root, language):
    path = root / 'storage' / 'knowledge_transactions' / f'{language}.revision'
    return path.read_text(encoding='utf-8') if path.exists() else ''


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('wb') as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def changed(root, language, service):
    path = root / 'storage' / 'knowledge_transactions' / f'{language}.revision'
    atomic_write(path, uuid.uuid4().hex.encode())
    service.retriever.invalidate()


def parse(name, content):
    if not content or len(content) > MAX_BYTES:
        raise KnowledgeError(413, '文件不能为空，最大支持 10 MB', 'Files must be nonempty and no larger than 10 MB.')
    try:
        if Path(name).suffix.lower() == '.txt':
            text = content.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
            if '\x00' in text: raise ValueError('binary text')
            docs = [Document(page_content=text)]
        else:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted or len(reader.pages) > 500: raise ValueError('unsupported PDF')
            docs = [Document(page_content=page.extract_text() or '', metadata={'page':i}) for i, page in enumerate(reader.pages)]
        if sum(len(d.page_content) for d in docs) > 1_000_000: raise ValueError('too much text')
        if not any(d.page_content.strip() for d in docs): raise ValueError('no searchable text')
        return docs
    except Exception as exc:
        raise KnowledgeError(400, '无法解析文档：TXT 需为 UTF-8，PDF 需包含可提取文本且未加密', 'Cannot read document. Use UTF-8 TXT or an unencrypted PDF with extractable text (up to 500 pages).') from exc


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recover(root, language, service):
    """Rollback an unfinished transaction. Caller holds the language lock."""
    pending = pending_path(root, language)
    if not pending.exists(): return
    transaction = json.loads(pending.read_text(encoding='utf-8'))
    backup = (root / transaction['backup']).resolve(); backup.relative_to(root / 'storage' / 'knowledge_backups')
    snapshot = json.loads((backup / 'index.json').read_text(encoding='utf-8'))
    collection = service.vector_store.vector_store._collection
    # Keep the journal until every restore step succeeds; retries are idempotent.
    collection.delete(where={'source': {'$in': transaction['sources']}})
    if snapshot['ids']:
        collection.upsert(ids=snapshot['ids'], documents=snapshot['documents'], metadatas=snapshot['metadatas'], embeddings=snapshot['embeddings'])
    for name, existed in transaction['files'].items():
        path = document_path(root, language, name)
        if existed: atomic_write(path, (backup / name).read_bytes())
        else: path.unlink(missing_ok=True)
    changed(root, language, service)
    pending.unlink()


class KnowledgeManager:
    def __init__(self, root=None, service_factory=None):
        self.root = root_path(root)
        self.service_factory = service_factory

    def service(self, language):
        validate_language(language)
        if self.service_factory: return self.service_factory(language)
        from agent.tools.agent_tools import get_rag
        return get_rag(language)

    def describe(self, path, counts):
        source = path.relative_to(self.root).as_posix()
        return {'name':path.name, 'size':path.stat().st_size, 'updated':path.stat().st_mtime,
                'revision':fingerprint(path), 'kind':path.suffix[1:].lower(), 'chunks':counts.get(source,0)}

    def counts(self, service):
        counts = {}
        for meta in service.vector_store.vector_store.get(include=['metadatas'])['metadatas']:
            source = meta.get('source',''); counts[source] = counts.get(source,0)+1
        return counts

    def list(self, language):
        service = self.service(language)
        with lock(self.root, language):
            recover(self.root, language, service)
            folder = self.root/'data'/language; folder.mkdir(parents=True, exist_ok=True)
            counts = self.counts(service)
            return [self.describe(document_path(self.root,language,p.name),counts) for p in sorted(folder.iterdir()) if p.is_file() and p.suffix.lower() in {'.txt','.pdf'}]

    def read(self, language, name):
        path = document_path(self.root, language, name); service = self.service(language)
        with lock(self.root, language):
            recover(self.root, language, service)
            if not path.is_file(): raise KnowledgeError(404,'文档不存在','Document not found.')
            docs = parse(name,path.read_bytes())
            return {**self.describe(path,self.counts(service)), 'content':'\n\n'.join(d.page_content for d in docs)}

    def mutate(self, language, name, content=None, expected=None, action='upload', actor=''):
        path = document_path(self.root,language,name)
        new_name = str(Path(name).with_suffix('.txt')) if action=='edit' and path.suffix.lower()=='.pdf' else name
        dest = document_path(self.root,language,new_name)
        documents = parse(new_name,content) if action!='delete' else []
        service = self.service(language)
        with lock(self.root,language):
            recover(self.root,language,service)
            if action=='upload' and path.exists(): raise KnowledgeError(409,'同名文件已存在，请编辑原文档或更换文件名','A document with this name exists. Edit it or choose a different filename.')
            if action!='upload':
                if not path.is_file(): raise KnowledgeError(404,'文档不存在','Document not found.')
                if expected != fingerprint(path): raise KnowledgeError(409,'文档已被更新，请重新打开后再修改','Document changed. Reopen it before editing or deleting.')
            if dest != path and dest.exists(): raise KnowledgeError(409,'同名 TXT 已存在，请先处理该文件','A TXT document with this name already exists.')
            sources = list(dict.fromkeys([p.relative_to(self.root).as_posix() for p in [path,dest]]))
            vector = service.vector_store.vector_store
            snapshot = vector.get(where={'source':{'$in':sources}}, include=['documents','metadatas','embeddings'])
            embeddings = snapshot['embeddings']
            snapshot = {k:snapshot[k] for k in ['ids','documents','metadatas']}
            snapshot['embeddings'] = embeddings.tolist() if hasattr(embeddings,'tolist') else embeddings
            backup = self.root/'storage/knowledge_backups'/uuid.uuid4().hex; backup.mkdir(parents=True)
            files = {p.name:p.exists() for p in [path,dest]}
            for filename, existed in files.items():
                if existed: (backup/filename).write_bytes(document_path(self.root,language,filename).read_bytes())
            (backup/'index.json').write_text(json.dumps(snapshot,ensure_ascii=False),encoding='utf-8')
            transaction = {'backup':backup.relative_to(self.root).as_posix(),'sources':sources,'files':files,'action':action,'actor':actor}
            (backup/'operation.json').write_text(json.dumps(transaction,ensure_ascii=False),encoding='utf-8')
            pending = pending_path(self.root,language)
            atomic_write(pending,json.dumps(transaction,ensure_ascii=False).encode('utf-8'))
            try:
                chunks = service.vector_store.spliter.split_documents(documents)
                ids = []
                for index,chunk in enumerate(chunks):
                    chunk.metadata.update(source=dest.relative_to(self.root).as_posix(),language=language)
                    key=json.dumps([chunk.metadata['source'],chunk.metadata.get('page'),index,chunk.page_content],ensure_ascii=False)
                    ids.append(hashlib.sha256(key.encode()).hexdigest())
                if action!='delete' and not chunks: raise ValueError('No chunks')
                for start in range(0,len(chunks),64): vector.add_documents(chunks[start:start+64],ids=ids[start:start+64])
                if ids and not set(ids).issubset(set(vector.get(where={'source':sources[-1]})['ids'])): raise RuntimeError('Index verification failed')
                stale = set(snapshot['ids'])-set(ids)
                if stale: vector.delete(ids=list(stale))
                if action=='delete': path.unlink()
                else:
                    atomic_write(dest,content)
                    if dest!=path:path.unlink()
                changed(self.root,language,service)
                pending.unlink()
            except Exception:
                recover(self.root,language,service)
                raise
            return None if action=='delete' else {**self.describe(dest,self.counts(service)), 'content':'\n\n'.join(d.page_content for d in documents)}
