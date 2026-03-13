# 🧠 OpenClaw 核心机制深度解析

> **专注于4大核心功能**：记忆存储更新 | 上下文加载 | Agent协作 | 工具调用

---

## 📚 一、记忆系统：存储与更新

### **架构概览**

```
┌─────────────────────────────────────────────────┐
│           Memory Index Manager                  │
│   (src/memory/manager.ts - 核心管理器)          │
└──────────────┬──────────────────────────────────┘
               ↓
    ┌──────────┴──────────┐
    ↓                     ↓
┌─────────┐         ┌──────────┐
│ Vector  │         │   FTS    │
│ Search  │         │ (全文检索)│
│(向量检索)│         └──────────┘
└─────────┘
    ↓                     ↓
┌──────────────────────────────────┐
│    SQLite Database + Extensions   │
│  • chunks_vec (向量表)            │
│  • chunks_fts (全文检索表)         │
│  • embedding_cache (嵌入缓存)     │
└──────────────────────────────────┘
```

---

### **核心类：MemoryIndexManager**

**位置**: `src/memory/manager.ts:112`

```typescript
export class MemoryIndexManager implements MemorySearchManager {
  // 数据库连接
  private db: DatabaseSync;

  // 嵌入提供者（OpenAI/Gemini/Voyage）
  private provider: EmbeddingProvider;

  // 向量搜索配置
  private readonly vector: {
    enabled: boolean;
    available: boolean | null;
    extensionPath?: string;
    dims?: number;  // 向量维度
  };

  // 全文检索配置
  private readonly fts: {
    enabled: boolean;
    available: boolean;
  };

  // 监听文件系统变化
  private watcher: FSWatcher | null = null;

  // 脏标记（需要重新索引）
  private dirty = false;
  private sessionsDirty = false;
}
```

---

### **1. 记忆存储结构**

#### **数据库表设计**

```sql
-- 向量表 (使用 sqlite-vec 扩展)
CREATE TABLE chunks_vec (
  id INTEGER PRIMARY KEY,
  source TEXT,           -- 来源文件路径
  chunk_index INTEGER,   -- 块索引
  content TEXT,          -- 文本内容
  embedding BLOB,        -- 向量嵌入 (Float32Array)
  hash TEXT,             -- 内容哈希
  mtime_ms INTEGER       -- 修改时间
);

-- 全文检索表 (FTS5)
CREATE TABLE chunks_fts (
  content TEXT,          -- 用于全文搜索
  source TEXT,
  chunk_index INTEGER
) USING fts5(content);

-- 嵌入缓存表
CREATE TABLE embedding_cache (
  text_hash TEXT PRIMARY KEY,
  embedding BLOB,        -- 缓存的向量
  model TEXT,            -- 模型名称
  provider TEXT,         -- 提供者
  created_at INTEGER
);
```

#### **存储位置**

```bash
~/.clawdbot/agents/<agentId>/workspace/
├── memory.db              # 主记忆数据库
├── memory/                # 记忆文件目录
│   ├── notes.md
│   ├── knowledge/
│   └── docs/
└── sessions/              # 会话转录
    ├── main/
    └── <sessionKey>/
```

---

### **2. 记忆写入流程**

#### **A. 文件变化监听**

```typescript
// src/memory/manager.ts:300+
private setupWatcher() {
  this.watcher = chokidar.watch(memoryPaths, {
    ignoreInitial: true,
    persistent: true
  });

  this.watcher.on('change', (path) => {
    this.dirty = true;  // 标记为脏
    this.scheduleSync(); // 安排同步
  });

  this.watcher.on('add', (path) => {
    this.dirty = true;
    this.scheduleSync();
  });
}
```

#### **B. 文本分块 (Chunking)**

```typescript
// src/memory/internal.ts:150+
export function chunkMarkdown(params: {
  text: string;
  chunkTokens: number;    // 每块最大token数 (默认512)
  chunkOverlap: number;   // 重叠token数 (默认50)
}): MemoryChunk[] {
  const chunks: MemoryChunk[] = [];

  // 1. 按段落分割
  const paragraphs = text.split(/\n\n+/);

  // 2. 滑动窗口分块
  for (let i = 0; i < paragraphs.length; ) {
    let currentChunk = paragraphs[i];
    let tokens = estimateTokens(currentChunk);

    // 尝试填充到 chunkTokens
    while (tokens < chunkTokens && i + 1 < paragraphs.length) {
      i++;
      currentChunk += "\n\n" + paragraphs[i];
      tokens = estimateTokens(currentChunk);
    }

    chunks.push({
      text: currentChunk,
      startOffset: /* ... */,
      endOffset: /* ... */
    });

    // 重叠处理
    i -= Math.floor(chunkOverlap / avgTokensPerParagraph);
    i = Math.max(i + 1, 0);
  }

  return chunks;
}
```

#### **C. 向量嵌入生成**

```typescript
// src/memory/embeddings.ts:100+
async function generateEmbedding(text: string): Promise<number[]> {
  // 1. 检查缓存
  const cached = await getCachedEmbedding(textHash);
  if (cached) return cached;

  // 2. 调用嵌入API
  let embedding: number[];

  switch (provider) {
    case 'openai':
      embedding = await openai.embeddings.create({
        model: 'text-embedding-3-small',
        input: text
      });
      break;

    case 'voyage':
      embedding = await voyage.embed({
        model: 'voyage-3-lite',
        input: [text]
      });
      break;

    case 'gemini':
      embedding = await gemini.embedContent({
        content: { parts: [{ text }] },
        model: 'text-embedding-004'
      });
      break;
  }

  // 3. 缓存结果
  await cacheEmbedding(textHash, embedding);

  return embedding;
}
```

#### **D. 批量索引**

```typescript
// src/memory/manager.ts:500+
async syncMemoryFiles() {
  // 1. 扫描文件变化
  const files = await listMemoryFiles(memoryDir);
  const changed = files.filter(f =>
    !this.indexed.has(f.path) ||
    f.mtimeMs > this.indexed.get(f.path)!.mtime
  );

  // 2. 分块
  const allChunks: MemoryChunk[] = [];
  for (const file of changed) {
    const content = await fs.readFile(file.absPath, 'utf-8');
    const chunks = chunkMarkdown({
      text: content,
      chunkTokens: 512,
      chunkOverlap: 50
    });

    chunks.forEach((chunk, idx) => {
      allChunks.push({
        source: file.path,
        chunkIndex: idx,
        content: chunk.text,
        hash: hashText(chunk.text)
      });
    });
  }

  // 3. 批量生成嵌入
  const embeddings = await this.batchEmbed(
    allChunks.map(c => c.content)
  );

  // 4. 写入数据库
  const stmt = this.db.prepare(`
    INSERT OR REPLACE INTO chunks_vec
    (source, chunk_index, content, embedding, hash, mtime_ms)
    VALUES (?, ?, ?, ?, ?, ?)
  `);

  for (let i = 0; i < allChunks.length; i++) {
    stmt.run(
      allChunks[i].source,
      allChunks[i].chunkIndex,
      allChunks[i].content,
      vectorToBlob(embeddings[i]),  // Float32Array -> Buffer
      allChunks[i].hash,
      Date.now()
    );
  }

  // 5. 更新FTS索引
  const ftsStmt = this.db.prepare(`
    INSERT INTO chunks_fts (content, source, chunk_index)
    VALUES (?, ?, ?)
  `);

  for (const chunk of allChunks) {
    ftsStmt.run(chunk.content, chunk.source, chunk.chunkIndex);
  }
}
```

---

### **3. 记忆检索流程**

#### **A. 混合搜索策略**

```typescript
// src/memory/manager.ts:800+
async search(params: {
  query: string;
  limit?: number;
  threshold?: number;
}): Promise<MemorySearchResult[]> {
  const { query, limit = 10, threshold = 0.7 } = params;

  // 1. 向量搜索
  const queryEmbedding = await this.embedQuery(query);
  const vectorResults = await searchVector({
    db: this.db,
    embedding: queryEmbedding,
    limit: limit * 2,  // 取2倍结果
    threshold
  });

  // 2. 关键词搜索
  const keywordResults = await searchKeyword({
    db: this.db,
    query: buildFtsQuery(query),
    limit: limit * 2
  });

  // 3. 混合排序 (RRF - Reciprocal Rank Fusion)
  const merged = mergeHybridResults({
    vector: vectorResults,
    keyword: keywordResults,
    limit
  });

  return merged;
}
```

#### **B. 向量相似度搜索**

```typescript
// src/memory/manager-search.ts:50+
export function searchVector(params: {
  db: DatabaseSync;
  embedding: number[];
  limit: number;
  threshold: number;
}): MemorySearchResult[] {
  const stmt = db.prepare(`
    SELECT
      source,
      chunk_index,
      content,
      vec_distance_cosine(embedding, ?) as distance
    FROM chunks_vec
    WHERE distance < ?
    ORDER BY distance ASC
    LIMIT ?
  `);

  return stmt.all(
    vectorToBlob(embedding),
    1 - threshold,  // 距离阈值
    limit
  ) as MemorySearchResult[];
}
```

#### **C. 全文检索**

```typescript
// src/memory/manager-search.ts:100+
export function searchKeyword(params: {
  db: DatabaseSync;
  query: string;
  limit: number;
}): MemorySearchResult[] {
  const stmt = db.prepare(`
    SELECT
      source,
      chunk_index,
      content,
      rank
    FROM chunks_fts
    WHERE chunks_fts MATCH ?
    ORDER BY rank
    LIMIT ?
  `);

  return stmt.all(query, limit) as MemorySearchResult[];
}
```

---

### **4. 会话记忆集成**

#### **会话转录自动索引**

```typescript
// src/memory/sync-session-files.ts:50+
export async function syncSessionFiles(params: {
  agentId: string;
  manager: MemoryIndexManager;
}) {
  const sessionsDir = resolveSessionTranscriptsDirForAgent(agentId);

  // 1. 监听会话文件变化
  onSessionTranscriptUpdate((sessionKey, delta) => {
    manager.markSessionDirty(sessionKey);
  });

  // 2. 定期同步
  setInterval(async () => {
    const dirtyFiles = manager.getDirtySessionFiles();

    for (const file of dirtyFiles) {
      // 读取增量内容
      const newContent = await readSessionDelta(file);

      // 添加到索引
      await manager.addSessionChunks(file.sessionKey, newContent);
    }
  }, SESSION_DIRTY_DEBOUNCE_MS);
}
```

---

### **5. 记忆更新策略**

#### **增量更新 vs 全量重建**

```typescript
// src/memory/manager.ts:600+
async update(source: string, newContent: string) {
  const existing = await this.getChunks(source);

  // 计算内容哈希
  const newHash = hashText(newContent);
  const oldHash = existing[0]?.hash;

  if (newHash === oldHash) {
    return; // 内容未变化，跳过
  }

  // 删除旧块
  this.db.prepare(`
    DELETE FROM chunks_vec WHERE source = ?
  `).run(source);

  this.db.prepare(`
    DELETE FROM chunks_fts WHERE source = ?
  `).run(source);

  // 重新分块和索引
  const chunks = chunkMarkdown({ text: newContent });
  const embeddings = await this.batchEmbed(
    chunks.map(c => c.text)
  );

  // 插入新块
  for (let i = 0; i < chunks.length; i++) {
    await this.insertChunk({
      source,
      chunkIndex: i,
      content: chunks[i].text,
      embedding: embeddings[i]
    });
  }
}
```

#### **缓存管理**

```typescript
// src/memory/manager.ts:700+
private async manageCach() {
  // 1. LRU淘汰
  if (this.cache.maxEntries) {
    const entries = await this.db.prepare(`
      SELECT text_hash, created_at
      FROM embedding_cache
      ORDER BY created_at ASC
    `).all();

    if (entries.length > this.cache.maxEntries) {
      const toDelete = entries.slice(
        0,
        entries.length - this.cache.maxEntries
      );

      for (const entry of toDelete) {
        await this.db.prepare(`
          DELETE FROM embedding_cache
          WHERE text_hash = ?
        `).run(entry.text_hash);
      }
    }
  }

  // 2. 过期清理
  const MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000; // 30天
  await this.db.prepare(`
    DELETE FROM embedding_cache
    WHERE created_at < ?
  `).run(Date.now() - MAX_AGE_MS);
}
```

---

## 🔄 二、上下文加载机制

### **上下文构建流程**

```
用户消息
    ↓
1. 加载系统提示词
    ↓
2. 加载引导文件 (bootstrap files)
    ↓
3. 加载会话历史
    ↓
4. 注入记忆搜索结果
    ↓
5. 添加工具定义
    ↓
6. 发送给 LLM
```

---

### **1. 系统提示词构建**

**位置**: `src/agents/system-prompt.ts:100+`

```typescript
export function buildSystemPrompt(params: {
  cfg: OpenClawConfig;
  agentId: string;
  sessionKey?: string;
  tools: Tool[];
}): string {
  const parts: string[] = [];

  // A. 身份和角色
  const identity = loadIdentityFile(agentId);
  if (identity) {
    parts.push(`# Identity\n${identity}`);
  }

  // B. 当前日期时间
  parts.push(`Current date and time: ${formatDateTime()}`);

  // C. 工作空间信息
  const workspace = resolveAgentWorkspaceDir(cfg, agentId);
  parts.push(`Workspace: ${workspace}`);

  // D. 工具说明
  if (tools.length > 0) {
    parts.push(`# Available Tools\n` +
      tools.map(t => `- ${t.name}: ${t.description}`).join('\n')
    );
  }

  // E. 技能提示词
  const skills = loadWorkspaceSkills(agentId);
  if (skills.length > 0) {
    parts.push(`# Skills\n` + skills.join('\n\n'));
  }

  // F. 自定义系统提示词覆盖
  const override = cfg.agents?.[agentId]?.systemPrompt;
  if (override) {
    parts.push(override);
  }

  return parts.join('\n\n');
}
```

---

### **2. Bootstrap 文件加载**

**位置**: `src/agents/bootstrap-files.ts:43-60`

```typescript
export async function resolveBootstrapContextForRun(params: {
  workspaceDir: string;
  config?: OpenClawConfig;
  sessionKey?: string;
  sessionId?: string;
  agentId?: string;
  warn?: (message: string) => void;
}): Promise<{
  bootstrapFiles: WorkspaceBootstrapFile[];
  contextFiles: EmbeddedContextFile[];
}> {
  const bootstrapFiles = await resolveBootstrapFilesForRun(params);
  const contextFiles = buildBootstrapContextFiles(bootstrapFiles, {
    maxChars: resolveBootstrapMaxChars(params.config),
    warn: params.warn,
  });
  return { bootstrapFiles, contextFiles };
}
```

**核心流程**:
1. 调用 `resolveBootstrapFilesForRun()` 加载工作空间中的引导文件列表
2. 通过 `buildBootstrapContextFiles()` 将文件内容转换为适合嵌入模型的格式
3. 返回原始文件列表和优化后的上下文文件列表

#### **加载哪些文件？**

通过 `loadWorkspaceBootstrapFiles`（`src/agents/workspace.ts:239-293`）加载以下工作空间文件：

| 文件名 | 用途 |
|--------|------|
| `AGENTS.md` | 代理配置与清单 |
| `SOUL.md` | 代理核心性格与行为准则 |
| `TOOLS.md` | 可用工具定义 |
| `IDENTITY.md` | 代理身份信息（首次运行后填充） |
| `USER.md` | 用户偏好与历史 |
| `HEARTBEAT.md` | 健康检查状态 |
| `BOOTSTRAP.md` | **首次运行引导脚本**（完成后删除） |
| 记忆文件（通过 `resolveMemoryBootstrapEntries`） | 长期记忆存储 |

#### **`BOOTSTRAP.md`：首次运行引导脚本**

`BOOTSTRAP.md` 是 OpenClaw **代理首次运行时**的交互式引导脚本，相当于"新生儿引导手册"或"入职培训"。

**核心作用**：
- **首次启动**：指导代理与用户完成身份构建对话
- **引导过程**：提供一个自然、友好的"初次见面"剧本
- **完成后**：**自动删除**（避免重复引导）

**引导剧本内容**：
```markdown
# BOOTSTRAP.md - Hello, World
*You just woke up. Time to figure out who you are.*

## The Conversation
Start with something like:
> "Hey. I just came online. Who am I? Who are you?"

Then figure out together:
1. **Your name** — What should they call you?
2. **Your nature** — What kind of creature are you?
3. **Your vibe** — Formal? Casual? Snarky? Warm?
4. **Your emoji** — Everyone needs a signature.
```

**工作流程**：
```
OpenClaw 首次启动
    ↓
加载 BOOTSTRAP.md（通过 bootstrap-files.ts）
    ↓
代理读取引导剧本
    ↓
与用户进行身份构建对话
    ↓
填写 IDENTITY.md、USER.md、完善 SOUL.md
    ↓
**删除 BOOTSTRAP.md**（使命完成）
    ↓
后续启动直接使用已建立的身份文件
```

**重要特性**：
- **一次性仪式**：像剪脐带一样，完成后代理独立运行
- **用户主导**：身份由用户与代理共同构建，而非预设
- **人格化体验**：让 AI 代理不是冷启动，而是"诞生"过程
- **状态检测**：`BOOTSTRAP.md` 的存在意味着"首次运行还未完成"
- **钩子扩展**：通过 `agent:bootstrap` 钩子允许插件动态添加/修改引导文件
- **会话过滤**：子代理会话只能访问 `AGENTS.md` 和 `TOOLS.md`（安全隔离）
- **字符数控制**：根据配置限制上下文总大小，防止 token 超限

**文件位置**：
```
~/.openclaw/workspace/
├── AGENTS.md
├── BOOTSTRAP.md    ← 首次运行引导脚本（存在=未完成引导）
├── IDENTITY.md     ← 身份信息（引导后填充）
├── SOUL.md         ← 行为准则
├── TOOLS.md
├── USER.md         ← 用户偏好
└── HEARTBEAT.md
```

**在 OpenClaw 架构中的位置**：
```
代理启动流程
    ↓
bootstrap‑files.ts（加载工作空间文件）
    ↓
应用钩子覆盖（`applyBootstrapHookOverrides`）
    ↓
过滤会话权限（`filterBootstrapFilesForSession`）
    ↓
构建上下文文件（`buildBootstrapContextFiles`）
    ↓
嵌入代理上下文（供模型使用）
```

**关键代码段**（`src/agents/workspace.ts:295-305`）：
```typescript
const SUBAGENT_BOOTSTRAP_ALLOWLIST = new Set([DEFAULT_AGENTS_FILENAME, DEFAULT_TOOLS_FILENAME]);

export function filterBootstrapFilesForSession(
  files: WorkspaceBootstrapFile[],
  sessionKey?: string,
): WorkspaceBootstrapFile[] {
  if (!sessionKey || !isSubagentSessionKey(sessionKey)) {
    return files;
  }
  return files.filter((file) => SUBAGENT_BOOTSTRAP_ALLOWLIST.has(file.name));
}
```


---

### **3. 会话历史加载**

**位置**: `src/agents/pi-embedded-runner/history.ts:50+`

```typescript
export async function loadSessionHistory(params: {
  sessionKey: string;
  limit?: number;
  compactOld?: boolean;
}): Promise<Message[]> {
  const transcriptPath = resolveSessionTranscriptPath(sessionKey);

  // 1. 读取会话文件
  const transcript = await fs.readFile(transcriptPath, 'utf-8');
  const messages = JSON.parse(transcript);

  // 2. 应用历史限制
  let history = messages;
  if (limit) {
    history = messages.slice(-limit);
  }

  // 3. 旧消息压缩（可选）
  if (compactOld && history.length > 20) {
    const recent = history.slice(-10);     // 保留最近10条
    const old = history.slice(0, -10);     // 压缩旧消息

    const compacted = await compactMessages(old);
    history = [compacted, ...recent];
  }

  // 4. 图片去重和清理
  history = sanitizeSessionImagesForHistory(history);

  return history;
}
```

#### **消息压缩策略**

```typescript
// src/agents/compaction.ts:100+
async function compactMessages(messages: Message[]): Promise<Message> {
  // 1. 提取关键信息
  const summary = messages
    .filter(m => m.role === 'user' || m.role === 'assistant')
    .map(m => `${m.role}: ${m.content.slice(0, 200)}`)
    .join('\n');

  // 2. 使用 LLM 总结
  const compacted = await llm.complete({
    prompt: `Summarize the following conversation:\n\n${summary}`,
    maxTokens: 500
  });

  return {
    role: 'system',
    content: `[Earlier conversation summary]\n${compacted}`
  };
}
```

---

### **4. 记忆检索注入**

**位置**: `src/agents/tools/memory-tool.ts:100+`

```typescript
export async function injectMemoryContext(params: {
  query: string;
  manager: MemoryIndexManager;
  maxResults?: number;
}): Promise<string> {
  // 1. 搜索相关记忆
  const results = await manager.search({
    query,
    limit: maxResults ?? 5,
    threshold: 0.75
  });

  if (results.length === 0) {
    return '';
  }

  // 2. 格式化为上下文
  const context = results.map((r, idx) => `
[Memory ${idx + 1}] (source: ${r.source}, relevance: ${r.score.toFixed(2)})
${r.content}
  `.trim()).join('\n\n');

  return `# Relevant Memory\n\n${context}`;
}
```

#### **自动记忆触发**

```typescript
// 在agent运行前自动搜索记忆
async function prepareContext(userMessage: string) {
  const systemPrompt = buildSystemPrompt({ ... });
  const bootstrapFiles = await loadBootstrapFiles({ ... });
  const history = await loadSessionHistory({ ... });

  // 自动记忆搜索
  let memoryContext = '';
  if (memorySearchEnabled) {
    memoryContext = await injectMemoryContext({
      query: userMessage,
      manager: memoryManager,
      maxResults: 5
    });
  }

  return {
    system: systemPrompt,
    messages: [
      { role: 'system', content: bootstrapFiles },
      { role: 'system', content: memoryContext },
      ...history,
      { role: 'user', content: userMessage }
    ]
  };
}
```

---

### **5. 上下文窗口管理**

**位置**: `src/agents/context-window-guard.ts:50+`

```typescript
export function guardContextWindow(params: {
  messages: Message[];
  maxTokens: number;
  model: string;
}): Message[] {
  let totalTokens = estimateTokenCount(messages, model);

  // 1. 如果超出限制
  if (totalTokens > maxTokens) {
    // 策略1: 移除最旧的非系统消息
    const protected = messages.filter(m =>
      m.role === 'system' ||
      messages.indexOf(m) >= messages.length - 5  // 保留最近5条
    );

    const removable = messages.filter(m =>
      !protected.includes(m)
    );

    // 逐条移除直到符合限制
    while (totalTokens > maxTokens && removable.length > 0) {
      removable.shift();
      totalTokens = estimateTokenCount([...protected, ...removable], model);
    }

    return [...protected, ...removable];
  }

  return messages;
}
```

---

## 🤝 三、Agent 之间的协作

### **多 Agent 协作架构**

```
┌─────────────────────────────────────────┐
│         Gateway (网关协调层)             │
│    • Agent 注册与发现                    │
│    • 消息路由                            │
│    • 会话隔离                            │
└────────────┬────────────────────────────┘
             ↓
      ┌──────┴──────┐
      ↓             ↓
┌──────────┐   ┌──────────┐
│ Agent A  │   │ Agent B  │
│ (main)   │←──│ (helper) │
└──────────┘   └──────────┘
      ↓             ↑
      └─────────────┘
   sessions/spawn (子代理生成)
```

---

### **1. 子代理生成 (Subagent Spawning)**

**位置**: `src/agents/tools/sessions-spawn-tool.ts:50+`

```typescript
export async function sessionsSpawnTool(params: {
  agentId: string;      // 目标 agent
  message: string;      // 发送的消息
  sessionKey?: string;  // 会话标识
  model?: string;       // 指定模型
  thinking?: 'low' | 'medium' | 'high';
  waitForReply?: boolean;  // 是否等待回复
}): Promise<SessionSpawnResult> {
  // 1. 验证跨 agent 权限
  const allowed = isSubagentAllowed(currentAgentId, params.agentId);
  if (!allowed) {
    throw new Error(`Agent ${currentAgentId} cannot spawn ${params.agentId}`);
  }

  // 2. 创建或加入会话
  const sessionKey = params.sessionKey ?? generateSessionKey();

  // 3. 注册子代理
  await registerSubagent({
    parentAgentId: currentAgentId,
    childAgentId: params.agentId,
    sessionKey,
    announceTarget: determineAnnounceTarget(params)
  });

  // 4. 发送消息到目标 agent
  const response = await sendToAgent({
    agentId: params.agentId,
    sessionKey,
    message: params.message,
    model: params.model,
    thinking: params.thinking,
    wait: params.waitForReply
  });

  return {
    sessionKey,
    response: params.waitForReply ? response : null,
    status: 'spawned'
  };
}
```

#### **子代理配置示例**

```json
{
  "agents": {
    "main": {
      "subagents": {
        "allowedAgents": ["research", "coding", "analysis"],
        "defaultModel": "claude-sonnet-4.5",
        "crossAgentSpawning": true
      }
    },
    "research": {
      "model": "claude-opus-4.6",
      "subagents": {
        "allowedAgents": ["*"]  // 允许生成任何子代理
      }
    }
  }
}
```

---

### **2. Agent 间消息传递**

**位置**: `src/agents/subagent-announce.ts:50+`

```typescript
export async function announceToParent(params: {
  sessionKey: string;
  message: string;
  metadata?: Record<string, any>;
}) {
  // 1. 查找父 agent
  const registry = await loadSubagentRegistry();
  const entry = registry.sessions.get(params.sessionKey);

  if (!entry || !entry.parentAgentId) {
    return; // 没有父 agent，跳过
  }

  // 2. 格式化公告消息
  const announcement = formatSubagentAnnouncement({
    from: entry.childAgentId,
    to: entry.parentAgentId,
    sessionKey: params.sessionKey,
    message: params.message,
    timestamp: Date.now(),
    metadata: params.metadata
  });

  // 3. 发送到父 agent 会话
  await appendToSession({
    agentId: entry.parentAgentId,
    sessionKey: entry.announceTarget || 'main',
    message: {
      role: 'system',
      content: announcement
    }
  });

  // 4. 通过网关通知
  if (gateway.isConnected()) {
    await gateway.send({
      type: 'subagent:announce',
      data: {
        parentAgentId: entry.parentAgentId,
        childAgentId: entry.childAgentId,
        sessionKey: params.sessionKey,
        message: params.message
      }
    });
  }
}
```

#### **公告消息格式**

```markdown
[Subagent Announcement]
From: research-agent
Session: research-20260214-1234
Status: completed

Research findings:
- Found 5 relevant papers
- Key insight: ...

[End Announcement]
```

---

### **3. 子代理注册表**

**位置**: `src/agents/subagent-registry.ts:100+`

```typescript
class SubagentRegistry {
  private sessions: Map<string, SubagentSession>;
  private store: KeyValueStore;

  async register(params: {
    sessionKey: string;
    parentAgentId: string;
    childAgentId: string;
    announceTarget?: string;
  }) {
    const session: SubagentSession = {
      sessionKey: params.sessionKey,
      parentAgentId: params.parentAgentId,
      childAgentId: params.childAgentId,
      announceTarget: params.announceTarget || 'main',
      createdAt: Date.now(),
      status: 'active'
    };

    this.sessions.set(params.sessionKey, session);

    // 持久化
    await this.store.set(
      `subagent:${params.sessionKey}`,
      JSON.stringify(session)
    );
  }

  async complete(sessionKey: string, result: any) {
    const session = this.sessions.get(sessionKey);
    if (!session) return;

    session.status = 'completed';
    session.result = result;
    session.completedAt = Date.now();

    // 通知父 agent
    await announceToParent({
      sessionKey,
      message: `Subagent completed`,
      metadata: { result }
    });

    await this.store.set(
      `subagent:${sessionKey}`,
      JSON.stringify(session)
    );
  }
}
```

---

### **4. 协作模式**

#### **模式 A: 任务委派 (Delegation)**

```typescript
// 主 agent 将任务委派给专门的子 agent
async function delegateResearch(query: string) {
  const result = await sessionsSpawnTool({
    agentId: 'research',
    message: `Research: ${query}`,
    waitForReply: true,  // 等待结果
    thinking: 'high'
  });

  return result.response;
}
```

#### **模式 B: 并行执行 (Parallel)**

```typescript
// 同时启动多个子 agent 并行处理
async function parallelAnalysis(tasks: string[]) {
  const promises = tasks.map(task =>
    sessionsSpawnTool({
      agentId: 'analysis',
      message: task,
      waitForReply: true
    })
  );

  const results = await Promise.all(promises);
  return results;
}
```

#### **模式 C: 流水线 (Pipeline)**

```typescript
// Agent A -> Agent B -> Agent C
async function pipeline(input: string) {
  // Step 1: 数据收集
  const data = await sessionsSpawnTool({
    agentId: 'collector',
    message: `Collect data: ${input}`,
    waitForReply: true
  });

  // Step 2: 分析
  const analysis = await sessionsSpawnTool({
    agentId: 'analyzer',
    message: `Analyze: ${data.response}`,
    waitForReply: true
  });

  // Step 3: 报告生成
  const report = await sessionsSpawnTool({
    agentId: 'reporter',
    message: `Generate report: ${analysis.response}`,
    waitForReply: true
  });

  return report.response;
}
```

---

### **5. 会话隔离**

```typescript
// src/agents/agent-scope.ts:50+
export function resolveAgentSessionDir(params: {
  agentId: string;
  sessionKey: string;
}): string {
  // 每个 agent 有独立的会话目录
  return path.join(
    HOME,
    '.clawdbot',
    'agents',
    params.agentId,
    'sessions',
    params.sessionKey
  );
}

// 会话数据完全隔离
// ~/.clawdbot/agents/
//   ├── main/sessions/main/
//   ├── research/sessions/research-001/
//   └── coding/sessions/coding-abc/
```

---

## 🛠️ 四、工具调用机制

### **工具调用流程**

```
LLM 响应
    ↓
检测 tool_use 块
    ↓
提取工具参数
    ↓
权限检查
    ↓
执行工具函数
    ↓
获取结果
    ↓
作为 tool_result 返回 LLM
```

---

### **1. 工具定义注册**

**位置**: `src/agents/pi-tools.ts:100+`

```typescript
export function createOpenClawTools(params: {
  cfg: OpenClawConfig;
  agentId: string;
  sessionKey: string;
  gateway?: GatewayClient;
}): Tool[] {
  const tools: Tool[] = [];

  // A. Bash 工具
  tools.push({
    name: 'bash',
    description: 'Execute bash commands',
    inputSchema: {
      type: 'object',
      properties: {
        command: { type: 'string', description: 'Command to execute' },
        background: { type: 'boolean', description: 'Run in background' }
      },
      required: ['command']
    },
    execute: async (params) => executeBashTool(params)
  });

  // B. 文件工具
  tools.push({
    name: 'file_read',
    description: 'Read file contents',
    inputSchema: {
      type: 'object',
      properties: {
        path: { type: 'string' }
      },
      required: ['path']
    },
    execute: async (params) => readFileTool(params)
  });

  // C. 浏览器工具
  if (cfg.browser?.enabled) {
    tools.push({
      name: 'browser',
      description: 'Control web browser',
      inputSchema: { /* ... */ },
      execute: async (params) => browserTool(params)
    });
  }

  // D. 记忆搜索工具
  if (cfg.memorySearch?.enabled) {
    tools.push({
      name: 'memory_search',
      description: 'Search knowledge base',
      inputSchema: { /* ... */ },
      execute: async (params) => memorySearchTool(params)
    });
  }

  // E. 消息发送工具
  tools.push({
    name: 'send_message',
    description: 'Send message to channel',
    inputSchema: { /* ... */ },
    execute: async (params) => sendMessageTool(params)
  });

  // F. 子代理工具
  tools.push({
    name: 'sessions_spawn',
    description: 'Spawn subagent',
    inputSchema: { /* ... */ },
    execute: async (params) => sessionsSpawnTool(params)
  });

  return tools;
}
```

---

### **2. 工具执行引擎**

**位置**: `src/agents/pi-embedded-subscribe.ts:200+`

```typescript
export async function executeToolCall(params: {
  toolName: string;
  toolInput: Record<string, any>;
  tools: Tool[];
  context: ExecutionContext;
}): Promise<ToolResult> {
  const { toolName, toolInput, tools, context } = params;

  // 1. 查找工具
  const tool = tools.find(t => t.name === toolName);
  if (!tool) {
    return {
      type: 'tool_result',
      tool_use_id: context.toolUseId,
      content: `Error: Unknown tool '${toolName}'`,
      is_error: true
    };
  }

  // 2. 权限检查
  const allowed = await checkToolPolicy({
    toolName,
    agentId: context.agentId,
    sessionKey: context.sessionKey,
    config: context.cfg
  });

  if (!allowed) {
    return {
      type: 'tool_result',
      tool_use_id: context.toolUseId,
      content: `Error: Tool '${toolName}' not allowed for this agent`,
      is_error: true
    };
  }

  // 3. 验证输入参数
  const validation = validateToolInput(tool.inputSchema, toolInput);
  if (!validation.valid) {
    return {
      type: 'tool_result',
      tool_use_id: context.toolUseId,
      content: `Invalid input: ${validation.errors.join(', ')}`,
      is_error: true
    };
  }

  // 4. 执行工具
  try {
    const result = await tool.execute(toolInput, context);

    return {
      type: 'tool_result',
      tool_use_id: context.toolUseId,
      content: formatToolResult(result),
      is_error: false
    };
  } catch (error) {
    return {
      type: 'tool_result',
      tool_use_id: context.toolUseId,
      content: `Error: ${error.message}`,
      is_error: true
    };
  }
}
```

---

### **3. 核心工具实现**

#### **A. Bash 工具**

**位置**: `src/agents/bash-tools.exec.ts:100+`

```typescript
export async function executeBashTool(params: {
  command: string;
  background?: boolean;
  timeout?: number;
  workingDir?: string;
}): Promise<BashResult> {
  const { command, background, timeout = 120000, workingDir } = params;

  if (background) {
    // 后台执行
    const processId = await startBackgroundProcess({
      command,
      workingDir,
      pty: true  // 使用伪终端
    });

    return {
      processId,
      status: 'started',
      message: `Background process ${processId} started`
    };
  } else {
    // 前台执行
    const result = await runCommand({
      command,
      workingDir,
      timeout,
      pty: true,
      captureOutput: true
    });

    return {
      exitCode: result.exitCode,
      stdout: result.stdout,
      stderr: result.stderr,
      duration: result.duration
    };
  }
}
```

#### **B. 文件读取工具**

**位置**: `src/agents/pi-tools.read.ts:50+`

```typescript
export async function readFileTool(params: {
  path: string;
  offset?: number;
  limit?: number;
}): Promise<string> {
  const { path: filePath, offset = 0, limit = 50000 } = params;

  // 1. 安全检查
  const safePath = resolveSafePath(filePath);
  if (!safePath) {
    throw new Error(`Path outside workspace: ${filePath}`);
  }

  // 2. 检查文件是否存在
  const exists = await fs.access(safePath).then(() => true).catch(() => false);
  if (!exists) {
    throw new Error(`File not found: ${filePath}`);
  }

  // 3. 读取文件
  const content = await fs.readFile(safePath, 'utf-8');

  // 4. 应用偏移和限制
  const slice = content.slice(offset, offset + limit);

  // 5. 如果被截断，添加提示
  if (content.length > offset + limit) {
    return slice + `\n\n[... ${content.length - offset - limit} more characters]`;
  }

  return slice;
}
```

#### **C. 浏览器工具**

**位置**: `src/agents/tools/browser-tool.ts:100+`

```typescript
export async function browserTool(params: {
  action: 'navigate' | 'click' | 'screenshot' | 'extract';
  url?: string;
  selector?: string;
  screenshot?: boolean;
}): Promise<BrowserResult> {
  const { action, url, selector, screenshot } = params;

  // 获取或创建浏览器实例
  const browser = await getBrowserInstance();
  const page = await browser.newPage();

  try {
    switch (action) {
      case 'navigate':
        await page.goto(url!, { waitUntil: 'networkidle' });

        if (screenshot) {
          const buffer = await page.screenshot({ fullPage: true });
          return {
            action: 'navigate',
            url,
            screenshot: buffer.toString('base64')
          };
        }

        return { action: 'navigate', url };

      case 'click':
        await page.click(selector!);
        await page.waitForLoadState('networkidle');
        return { action: 'click', selector };

      case 'extract':
        const text = await page.textContent(selector!);
        return { action: 'extract', selector, text };

      case 'screenshot':
        const buffer = await page.screenshot({ fullPage: true });
        return {
          action: 'screenshot',
          screenshot: buffer.toString('base64')
        };
    }
  } finally {
    await page.close();
  }
}
```

#### **D. 记忆搜索工具**

**位置**: `src/agents/tools/memory-tool.ts:50+`

```typescript
export async function memorySearchTool(params: {
  query: string;
  limit?: number;
}): Promise<string> {
  const { query, limit = 5 } = params;

  // 获取记忆管理器
  const manager = await MemoryIndexManager.get({
    cfg,
    agentId: context.agentId
  });

  if (!manager) {
    return 'Memory search is not enabled for this agent.';
  }

  // 搜索
  const results = await manager.search({
    query,
    limit,
    threshold: 0.75
  });

  if (results.length === 0) {
    return 'No relevant memories found.';
  }

  // 格式化结果
  return results.map((r, idx) => `
[${idx + 1}] ${r.source} (score: ${r.score.toFixed(2)})
${r.content}
  `.trim()).join('\n\n');
}
```

#### **E. 消息发送工具**

**位置**: `src/agents/tools/message-tool.ts:100+`

```typescript
export async function sendMessageTool(params: {
  channel: string;
  to: string;
  message: string;
  image?: string;  // base64 encoded
}): Promise<SendResult> {
  const { channel, to, message, image } = params;

  // 1. 查找渠道
  const channelAdapter = getChannelAdapter(channel);
  if (!channelAdapter) {
    throw new Error(`Unknown channel: ${channel}`);
  }

  // 2. 规范化接收者
  const recipient = normalizeRecipient(channel, to);

  // 3. 准备消息
  const msg: OutgoingMessage = {
    text: message,
    to: recipient
  };

  // 4. 添加图片（如果有）
  if (image) {
    const buffer = Buffer.from(image, 'base64');
    msg.media = {
      type: 'image',
      data: buffer
    };
  }

  // 5. 发送
  const result = await channelAdapter.send(msg);

  return {
    messageId: result.id,
    channel,
    to: recipient,
    status: 'sent',
    timestamp: Date.now()
  };
}
```

---

### **4. 工具权限控制**

**位置**: `src/agents/tool-policy.ts:50+`

```typescript
export async function checkToolPolicy(params: {
  toolName: string;
  agentId: string;
  sessionKey: string;
  config: OpenClawConfig;
}): Promise<boolean> {
  const { toolName, agentId, config } = params;

  // 1. 获取 agent 配置
  const agentConfig = config.agents?.[agentId];
  if (!agentConfig) return true;  // 默认允许

  // 2. 检查工具策略
  const policy = agentConfig.toolPolicy;
  if (!policy) return true;

  // 3. 黑名单检查
  if (policy.deny?.includes(toolName)) {
    return false;
  }

  // 4. 白名单检查
  if (policy.allow && !policy.allow.includes(toolName)) {
    return false;
  }

  // 5. 特殊规则
  if (toolName === 'bash' && policy.bashRestricted) {
    // Bash 工具的额外限制
    return false;
  }

  return true;
}
```

**工具策略配置示例**:

```json
{
  "agents": {
    "restricted": {
      "toolPolicy": {
        "allow": ["memory_search", "file_read", "send_message"],
        "deny": ["bash", "browser"],
        "bashRestricted": true
      }
    },
    "full-access": {
      "toolPolicy": {
        "allow": ["*"]
      }
    }
  }
}
```

---

### **5. 流式工具执行**

```typescript
// src/agents/pi-embedded-subscribe.ts:400+
async function streamToolExecution(params: {
  toolCall: ToolUseBlock;
  onProgress: (chunk: string) => void;
}) {
  const { toolCall, onProgress } = params;

  // 特殊处理：bash 工具支持流式输出
  if (toolCall.name === 'bash') {
    const process = await startBashProcess({
      command: toolCall.input.command,
      pty: true
    });

    // 实时流式输出
    process.stdout.on('data', (chunk) => {
      onProgress(chunk.toString());
    });

    process.stderr.on('data', (chunk) => {
      onProgress(chunk.toString());
    });

    // 等待完成
    const exitCode = await process.wait();

    return {
      exitCode,
      output: process.getOutput()
    };
  } else {
    // 其他工具：一次性执行
    const result = await executeToolCall(toolCall);
    onProgress(JSON.stringify(result, null, 2));
    return result;
  }
}
```

---

## 🎯 总结：四大核心机制关联

```
┌─────────────────────────────────────────────────┐
│                 用户请求                         │
└──────────────────┬──────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────┐
│  1. 上下文加载 (Context Loading)                 │
│    • 系统提示词                                   │
│    • Bootstrap 文件                              │
│    • 会话历史 ←─────────────┐                    │
│    • 记忆检索 ←─┐            │                    │
└──────┬──────────│────────────│────────────────────┘
       ↓          │            │
┌──────────────────│────────────│────────────────────┐
│  2. Agent 处理  │            │                      │
│    • LLM 推理   │            │                      │
│    • 工具调用 ──┼────────────┘                      │
└────────┬────────│──────────────────────────────────┘
         ↓        ↓
┌────────────────────────────────────────────────────┐
│  3. 工具执行 (Tool Execution)                      │
│    • Bash 命令                                      │
│    • 文件操作 ──────→ 4. 记忆更新                  │
│    • 浏览器自动化           ↓                       │
│    • 消息发送         (写入记忆文件)                │
│    • 子代理生成 ─────→ Agent 协作                  │
└────────┬───────────────────────────────────────────┘
         ↓
┌────────────────────────────────────────────────────┐
│  响应返回                                          │
└────────────────────────────────────────────────────┘
```

---

## 📖 关键文件速查表

| 功能 | 核心文件 | 行数参考 |
|------|---------|---------|
| **记忆管理器** | `src/memory/manager.ts` | 112-1000+ |
| **记忆搜索** | `src/memory/manager-search.ts` | 50-200 |
| **嵌入生成** | `src/memory/embeddings.ts` | 100-300 |
| **系统提示词** | `src/agents/system-prompt.ts` | 100-400 |
| **Bootstrap加载** | `src/agents/bootstrap-files.ts` | 50-150 |
| **会话历史** | `src/agents/pi-embedded-runner/history.ts` | 50-200 |
| **工具定义** | `src/agents/pi-tools.ts` | 100-500 |
| **工具执行** | `src/agents/pi-embedded-subscribe.ts` | 200-600 |
| **Bash工具** | `src/agents/bash-tools.exec.ts` | 100-500 |
| **子代理生成** | `src/agents/tools/sessions-spawn-tool.ts` | 50-300 |
| **子代理注册表** | `src/agents/subagent-registry.ts` | 100-300 |
| **工具权限** | `src/agents/tool-policy.ts` | 50-150 |

---

## 🚀 实战示例

### **场景：带记忆的研究助手**

```typescript
// 1. 用户发送研究请求
const userMessage = "研究 OpenClaw 的记忆系统";

// 2. 加载上下文
const systemPrompt = buildSystemPrompt({ agentId: 'research' });
const bootstrapFiles = await loadBootstrapFiles({ agentId: 'research' });
const history = await loadSessionHistory({ sessionKey: 'main' });

// 3. 搜索相关记忆
const memoryContext = await memorySearchTool({
  query: userMessage,
  limit: 5
});

// 4. 组合消息
const messages = [
  { role: 'system', content: systemPrompt },
  { role: 'system', content: bootstrapFiles },
  { role: 'system', content: memoryContext },
  ...history,
  { role: 'user', content: userMessage }
];

// 5. 调用 LLM
const response = await llm.complete({ messages });

// 6. LLM 可能调用工具
if (response.toolCalls) {
  for (const toolCall of response.toolCalls) {
    if (toolCall.name === 'web_search') {
      const results = await webSearchTool(toolCall.input);

      // 将结果保存到记忆
      await fs.writeFile(
        `~/.clawdbot/agents/research/workspace/memory/openclaw-research.md`,
        results
      );

      // 自动触发重新索引
      // MemoryIndexManager 会监听文件变化并更新索引
    }
  }
}
```

---

**文档版本**: 1.0
**最后更新**: 2026-02-14
**项目版本**: 2026.2.9
