"""Synchronize local knowledge files against the actual Chroma collection."""
import hashlib
import json
from pathlib import Path

from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from utils.config_handler import chroma_conf
from model.factory import embed_model
from utils.path_tool import get_abs_path
from utils.file_handler import pdf_loader, txt_loader, listdir_with_allowed_type, get_file_md5_hex
from utils.logger_handler import logger


class VectorStoreService:
    def __init__(self, language='zh'):
        if language not in {'zh', 'en'}:
            raise ValueError('Unsupported knowledge language')
        self.language = language
        self.config = {**chroma_conf, **chroma_conf['languages'][language]}
        self.vector_store = Chroma(
            collection_name=self.config['collection_name'],
            embedding_function=embed_model,
            persist_directory=get_abs_path(self.config['persist_directory']),
        )
        self.spliter = RecursiveCharacterTextSplitter(
            chunk_size=self.config['chunk_size'],
            chunk_overlap=self.config['chunk_overlap'],
            separators=self.config['separators'],
            length_function=len,
        )

    def get_retriever(self):
        from rag.hybrid_retriever import HybridRetriever
        return HybridRetriever(self.vector_store, candidate_k=self.config.get('candidate_k', 12))

    def load_document(self):
        from types import SimpleNamespace
        from rag.knowledge_storage import lock, recover, changed
        root = Path(get_abs_path('.')).resolve()
        service = SimpleNamespace(vector_store=self, retriever=self.get_retriever())
        with lock(root, self.language):
            recover(root, self.language, service)
            result = self._load_document()
            if result['imported']:
                changed(root, self.language, service)
            return result

    def _load_document(self):
        """Repair missing chunks; skip only when the current collection is complete.

        md5.text is retained for compatibility, never used as proof of indexing.
        Stable chunk IDs allow retries after interrupted or partial imports.
        Old chunks for a source are removed only after its new version is complete.
        """
        ledger = Path(get_abs_path(self.config['md5_hex_store']))
        known_hashes = set(ledger.read_text(encoding='utf-8').splitlines()) if ledger.exists() else set()
        folder = Path(get_abs_path(self.config['data_path']))
        if not folder.is_dir():
            raise FileNotFoundError(f'知识文档目录不存在：{folder}')
        paths = sorted(listdir_with_allowed_type(str(folder), tuple(self.config['allow_knowledge_file_type'])))
        if not paths:
            raise ValueError(f'Knowledge folder is empty: {folder}')
        result = {'imported': 0, 'skipped': 0, 'failed': [], 'chunks_added': 0}
        for path in paths:
            try:
                md5_hex = get_file_md5_hex(path)
                if not md5_hex:
                    raise ValueError('无法计算文档哈希')
                documents = txt_loader(path) if Path(path).suffix.lower() == '.txt' else pdf_loader(path)
                chunks = self.spliter.split_documents(documents)
                if not chunks:
                    raise ValueError('文档没有可索引的文本')
                ids = []
                # Portable source IDs let a verified index move with the project.
                source = (Path(self.config['data_path']) / Path(path).name).as_posix()
                for index, chunk in enumerate(chunks):
                    chunk.metadata.update(source=source, language=self.language)
                    key = json.dumps([source, chunk.metadata.get('page'), index, chunk.page_content], ensure_ascii=False)
                    ids.append(hashlib.sha256(key.encode('utf-8')).hexdigest())
                existing = set(self.vector_store.get(where={'source': source}, include=['metadatas'])['ids'])
                expected = set(ids)
                missing = [(chunk_id, chunk) for chunk_id, chunk in zip(ids, chunks) if chunk_id not in existing]
                if missing:
                    for start in range(0, len(missing), 64):
                        batch = missing[start:start + 64]
                        self.vector_store.add_documents([chunk for _, chunk in batch], ids=[chunk_id for chunk_id, _ in batch])
                    stored = set(self.vector_store.get(where={'source': source}, include=['metadatas'])['ids'])
                    if not expected.issubset(stored):
                        raise RuntimeError('文档分片写入不完整，请重试导入')
                stale = existing - expected
                if stale:
                    self.vector_store.delete(ids=sorted(stale))
                if missing or stale:
                    result['imported'] += 1
                    result['chunks_added'] += len(missing)
                    logger.info(f'[加载知识库]{path} 索引已同步，共 {len(ids)} 个片段')
                else:
                    result['skipped'] += 1
                    logger.info(f'[加载知识库]{path} 当前集合内片段完整，跳过')
                if md5_hex not in known_hashes:
                    ledger.parent.mkdir(parents=True, exist_ok=True)
                    with ledger.open('a', encoding='utf-8') as handle:
                        handle.write(md5_hex + '\n')
                    known_hashes.add(md5_hex)
            except Exception:
                result['failed'].append(Path(path).name)
                logger.exception(f'[加载知识库]{path} 加载失败')
        logger.info(f'[加载知识库]同步完成：{result}')
        return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Import separate Chinese/English knowledge indexes')
    parser.add_argument('--language', choices=['zh', 'en', 'all'], default='all')
    args = parser.parse_args()
    languages = ['zh', 'en'] if args.language == 'all' else [args.language]
    result = {language: VectorStoreService(language).load_document() for language in languages}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if any(item['failed'] for item in result.values()) else 0)
