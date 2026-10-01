# English knowledge base

This is the English translation of all six PDF/TXT knowledge documents now stored in Agent-master/data/zh. The Chinese originals are preserved. The source PDF is provided here as an English UTF-8 TXT document so its content can be used by the existing document loader without PDF layout artifacts.

| Chinese source | English copy |
| --- | --- |
| 扫地机器人100问.pdf | robot_vacuum_100_questions.en.txt |
| 扫地机器人100问2.txt | robot_vacuum_faq_100.en.txt |
| 扫拖一体机器人100问.txt | vacuum_mop_robot_100_questions.en.txt |
| 故障排除.txt | troubleshooting_200.en.txt |
| 维护保养.txt | maintenance_and_care.en.txt |
| 选购指南.txt | buying_guide_200.en.txt |

## Translation and verification

The translations preserve the source headings, question/list numbering, technical specifications, conditions and cautions. Source technical claims are translated as written, rather than independently fact-checked or updated. The content may contain overlapping or differing recommendations across documents, as in the original collection.

Translation was generated using the project's configured Qwen model. Automated checks verify coverage of every nonempty source line, numerical values (allowing equivalent English quantity notation), ordered entry numbering and absence of untranslated Chinese characters in the English documents. Representative passages were reviewed against their Chinese sources. See manifest.json for source mappings, SHA-256 checksums and coverage counts.

The external/records.csv file contains structured usage-report data, not knowledge-base documents, and is outside this translation collection.

## Active RAG integration

These English documents are now stored in `data/en`; Chinese originals are in `data/zh`. The English UI routes each retrieval to the `agent_en` collection under `chroma_db/en`. The Chinese UI uses `agent_zh` under `chroma_db/zh`. Dense retrieval, BM25 and reranking remain isolated by language. Existing conversation text and citations are not translated when switching UI language.

From the project root, run `python -m rag.vector_store --language en` to import English documents, `--language zh` for Chinese, or `--language all` (the default) for both. Restart the backend after importing to refresh its retrieval caches. TXT files contain the translated corpus; this README and manifest.json are not indexed.
