# FastAPI + React 重构说明

## 启动

Python 3.12+、Node.js 22.12+。以下命令都从 Agent-master 根目录执行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

在 `.env` 中填写 `DASHSCOPE_API_KEY`。模型配置默认延续原项目的通义千问与 DashScope Embedding，支持 `CHAT_MODEL_NAME`、`EMBEDDING_MODEL_NAME` 覆盖。已有向量库的 embedding 模型/维度必须保持一致；需要切换时请使用新的 collection 和持久化目录重新建库，不能混用不同维度的索引。

`RERANKER_MODEL_PATH` 默认指向相邻的 `RAGNotebook-master/bge-reranker-v2-m3`，直接复用本地模型权重。迁移到其他机器时需要调整路径。`RERANKER_DEVICE=cpu` 可改为 `cuda`，前提是已有对应 CUDA/PyTorch 环境。

开发模式开两个终端：

```powershell
# 终端一：后端
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

# 终端二：前端
cd frontend
npm ci
npm run dev
```

访问 http://localhost:5173，注册账号后即可登录。Vite 把 `/api` 转发到 FastAPI，Cookie 在同一站点下使用。统一使用 localhost 或 127.0.0.1，不要在两个主机名之间混用登录状态。

也可以构建前端后由 FastAPI 在同一端口提供页面：

```powershell
cd frontend
npm ci
npm run build
cd ..
.\.venv\Scripts\python.exe app.py
```

访问 http://127.0.0.1:8000。前端构建完成后需要重启后端以挂载静态文件。API 文档：http://127.0.0.1:8000/docs。

## 数据与知识库

- `storage/chat.sqlite3`：新账号、登录会话、对话标题、消息和引用来源。自动建表，重启后保留。
- `chroma_db/zh`、`chroma_db/en` 和 `storage/knowledge_*.md5`：按界面语言隔离的产品知识库与导入记录。导入会检查集合中的实际片段，自动补齐缺失内容，不再仅凭 MD5 跳过文件。
- `data/zh/`、`data/en/`：中文与英文产品资料；每轮 RAG 按界面语言选择对应集合。知识库是服务端维护的公共产品手册；用户的聊天记录按账号隔离，不会写入公共知识库。
- `data/external/records.csv`：十个模拟用户的使用记录。启动时按已有账号注册顺序绑定 1001～1010，绑定保存在 `report_assignments`；新账号领取剩余名额，用完后提示暂无分配。报告无需使用真实登录 UUID 查询 CSV。

首次部署且尚未建库时：

```powershell
.\.venv\Scripts\python.exe -m rag.vector_store
```

该命令通过配置的 Embedding 服务处理资料，会产生相应模型请求。已有向量库不必重新导入。BM25 从同一个 Chroma 文本集合构建中文单字、双字词及英文词索引，缓存最长 60 秒；重启后立即刷新。

## 接口

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/api/auth/register` | 注册并登录 |
| POST | `/api/auth/login` | 登录 |
| POST | `/api/auth/logout` | 撤销当前登录凭证 |
| GET | `/api/auth/me` | 当前账号 |
| GET / POST | `/api/conversations` | 列出 / 新建自己的对话 |
| GET / DELETE | `/api/conversations/{id}` | 查看 / 删除自己的对话 |
| POST | `/api/conversations/{id}/messages` | 发送消息并接收 SSE |
| GET | `/api/health` | 应用状态及是否已配置模型凭证 |

注册与登录请求：`{"username":"example","password":"at-least-8-characters"}`。
聊天请求：`{"content":"滚刷应该如何清理？"}`。
SSE 事件：`start`、`token`、`tool`、`sources`、`warning`、`done`、`error`。

## 实现要点

1. 登录凭证使用 HttpOnly / SameSite Cookie；数据库仅保存会话令牌的 SHA-256 摘要。密码使用独立随机盐与 scrypt 哈希。退出登录后令牌立即撤销。HTTPS 部署设置 `COOKIE_SECURE=true`。
2. 对话查询、发送、删除均在后端校验用户归属。SQLite 条件更新提供单会话生成互斥，避免两个请求串写；过期生成使用租约 ID 防止迟到结果覆盖新请求。
3. 每次发送从数据库读取最近 10 轮完整对话，真实传入 Agent。失败或中断的轮次保留展示，但不作为后续模型上下文。消息最多 12000 字符。
4. 主 Agent 使用 LangChain `updates` 流，等待本轮模型结束后，只将不带工具调用的最终正文经 FastAPI SSE 发送到 React；工具轮次的过渡/重试文字不进入正文和历史。工具阶段独立展示，正文首字需等待该轮完成。
5. RAG：向量召回与中文 BM25 各取候选，按查询长度调整权重，经加权 RRF 去重融合，再使用 BGE CrossEncoder 重排序，默认选取 3 个片段。候选数默认 12，可在 `config/chroma.yml` 中设置 `candidate_k`。引用随回答保存。
6. BGE 在首次相关请求时加载，复用模型实例，并对推理加锁。模型不可用时回退到混合召回顺序，同时向界面发送明确的 `warning`。
7. Agent 图与 RAG 服务按需初始化，认证、历史列表和启动过程不依赖云模型可用性。Agent 的用户身份、报告状态和引用列表使用每次请求独立的 ToolRuntime 上下文。
8. 报告工具从请求上下文读取模拟用户绑定，仅向模型开放月份参数；未指定月份默认最新数据（2025-12），明确指定但无记录时列出可用月份，报告标注模拟来源。天气改用 WeatherAPI：城市搜索后按经纬度查询，同名地点先确认；网页“使用当前位置”按钮获取授权坐标，最多共享 5 分钟，工具从请求上下文读取，不使用服务器 IP。后端 `.env` 配置 `WEATHERAPI_API_KEY`。
9. `model/tongyi_stream.py` 为流式工具调用显式启用增量输出并保留工具索引，避免累计响应差分触发 `KeyError: name`；修复不依赖修改第三方安装文件。

当前实现面向单实例本地应用，使用 SQLite 和进程内模型实例；未宣称已经完成多实例部署或负载测试。停止生成会关闭客户端流并保存已收到的部分回答，但已发出的底层同步模型请求可能仍需等待 SDK 返回。生成超时为 180 秒。原 Streamlit 入口保存在 `app_streamlit.py` 供参考，其内存历史不会自动迁入新账号数据库。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
cd frontend
npm run build
```

自动化测试覆盖注册登录、令牌撤销、跨用户隔离、生成失败、历史上下文、租约冲突、来源持久化、中文 BM25、加权融合、重排序，以及真实 LangChain 图上的工具调用。模型回答和检索文档在自动化测试中替换为固定测试数据，无需消耗 API 配额。

可选浏览器测试（需要 Windows Edge，测试服务仅用于测试）：

```powershell
# 终端一（根目录）
.\.venv\Scripts\python.exe -m uvicorn tests.browser_server:app --port 8017
# 终端二
cd frontend
npm install --no-save --package-lock=false @playwright/test
node browser-check.mjs
```

测试页面使用固定回复；正式 `backend.main:app` 使用真实 Agent，不存在演示回复开关。截图和测试数据库位于 `.qa/`，不要用于正式数据。

框架参考：[LangChain 流式接口](https://docs.langchain.com/oss/python/langchain/streaming)、[ToolRuntime 上下文](https://docs.langchain.com/oss/python/langchain/runtime)、[FastAPI 生命周期测试](https://fastapi.tiangolo.com/advanced/testing-events/)。

## 知识库管理页面

聊天侧栏进入知识库管理。首个注册账号默认管理员，可通过 `KNOWLEDGE_ADMIN_USERS` 指定账号；其他账号只读。支持中英文资料列表、TXT/PDF 上传、内容编辑和删除，同步更新对应语言向量索引与 BM25 缓存。PDF 编辑保存时转为同名 TXT，原文件自动备份。所有写操作校验认证、管理员权限、文件名和版本，失败或中断后恢复旧文件与索引。详细接口和限制见 README 的“网页知识库管理”。
