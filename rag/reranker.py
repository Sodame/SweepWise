import os
import threading
from pathlib import Path
from langchain_core.documents import Document


class BGEReranker:
    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def rerank(self, query, documents, top_k=3):
        if not documents:
            return []
        with self._lock:
            if self._model is None:
                from sentence_transformers import CrossEncoder
                default = Path(__file__).resolve().parents[2] / 'RAGNotebook-master' / 'bge-reranker-v2-m3'
                path = Path(os.getenv('RERANKER_MODEL_PATH', str(default))).expanduser()
                if not path.is_absolute():
                    path = Path(__file__).resolve().parents[1] / path
                if not (path / 'config.json').is_file():
                    raise RuntimeError('请将 RERANKER_MODEL_PATH 配置为 bge-reranker-v2-m3 本地模型目录')
                self._model = CrossEncoder(str(path), max_length=512, device=os.getenv('RERANKER_DEVICE', 'cpu'), local_files_only=True)
            scores = self._model.predict([(query, doc.page_content) for doc in documents], batch_size=8, show_progress_bar=False)
        ranked = sorted(zip(documents, scores), key=lambda pair: float(pair[1]), reverse=True)
        return [Document(page_content=doc.page_content, metadata={**doc.metadata, 'rerank_score': float(score)}) for doc, score in ranked[:top_k]]


reranker = BGEReranker()
