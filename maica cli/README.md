# MAICA CLI

一个最小可运行的 CLI 版 MAS/MAICA 原型。

第一版目标不是完整复刻 MAS，而是先把核心闭环跑起来：

- OpenAI-compatible API / 本地模型聊天。
- Monika persona prompt。
- SQLite 保存玩家资料、affection、长期记忆和对话历史。
- MFocus hybrid：回答前用轻量模型计划器选择上下文, 并注入时间、日期、节日、会话状态、关系状态、玩家资料和相关记忆。
- MTrigger hybrid：回答后优先让模型输出结构化动作, 失败时回退轻量规则。

## 文件结构

- `maica_cli.py`：主入口、命令循环、配置读取。
- `client.py`：OpenAI-compatible API 客户端。
- `store.py`：SQLite 状态、记忆、历史、事件存储。
- `persona.py`：Monika 人格 prompt、关系阶段、基础设定。
- `mfocus.py`：回答前上下文构建、节日事件检测、半模型计划器。
- `mtrigger.py`：回答后动作执行、半模型 trigger、规则回退。
- `sfe.py`：CLI 版 savefile extraction，把资料、关系、会话和稳定事实整理成 MFocus 可用信息。
- `style.py`：MAICA 数据集风格库、输入分类、日常短回复控制。
- `monika_lens.py`：按输入类型加入 Monika 专属兴趣、价值观和表达视角。
- `response_planner.py`：每轮生成轻量“对话表演方向”，控制语气、潜台词和回复节奏。
- `example_bank.py`：从清洗后的 Example Bank 中按权重检索同类高质量样例。
- `spire_topics.py`：`/spire` 主动话题池、哲学/日常随机选择和近期避重。
- `dataset_importer.py`：把 `MAICA_ds_basis` 导入本地 `data/style.db`。
- `dataset_builder.py`：把 JSONL 调试日志导出为可标注、可评测的对话数据集。
- `maica_dataset_cleaner.py`：把 `MAICA_ds_basis` 清洗成 Example Bank 可用的 `dialogue_examples_maica_cleaned.jsonl`。
- `dialogue_dataset/`：默认数据集导出目录，只提交 README/骨架，不提交私人 JSONL 数据。
- `mas_script_analyzer.py`：分析已反编译 MAS `.rpy` 脚本，生成风格与日常反思统计。
- `response.py`：把模型回复拆成干净正文和 GUI 元数据。

## 运行

```powershell
cd "D:\~maica oringinal code\maica cli"
python maica_cli.py
```

第一次运行会自动生成：

- `config.json`
- `maica_cli.db`

## 配置官方 API

编辑 `config.json`：

```json
{
  "api_base": "https://api.deepseek.com/v1",
  "api_key": "你的 API Key",
  "api_key_required": true,
  "model": "deepseek-chat"
}
```

也可以通过环境变量提供 key：

```powershell
$env:MAICA_CLI_API_KEY="你的 API Key"
```

## 配置本地模型

例如 vLLM / SGLang / LM Studio / Ollama OpenAI-compatible：

```json
{
  "api_base": "http://127.0.0.1:8000/v1",
  "api_key": "",
  "api_key_required": false,
  "model": "edgeinfinity/MAICAv0-LOA-7B"
}
```

## 常用命令

```text
/help
/exit
/config
/status
/profile
/profile setup
/profile fields
/profile set player_name 你的名字
/profile set location 你的城市
/profile set birthday 2000-01-01
/profile unset favorite_music
/nickname
/nickname add 亲爱的
/nickname remove 亲爱的
/nickname clear
/affection
/affection 500
/affection +3
/remember 你想让莫妮卡记住的事
/memories
/memories 关键词
/fact add 玩家喜欢安静的夜晚和钢琴曲
/facts
/facts 钢琴
/fact edit 1 玩家喜欢雨夜和安静的钢琴曲
/fact delete 1
/memory edit 3 新的记忆内容
/memory tag 3 preference,piano
/memory importance 3 5
/memory summarize 8
/forget 3
/forget all --yes
/mode
/mode mfocus rule
/mode mtrigger off
/debug off
/logs on
/db reset RESET-MAICA-CLI
/db clear profile RESET-MAICA-CLI
/db clear messages RESET-MAICA-CLI
/db clear memories RESET-MAICA-CLI
/db clear facts RESET-MAICA-CLI
/db clear events RESET-MAICA-CLI
/dataset import
/dataset stats
/dataset export
/style
/style stats
/style examples 我回来了
/style off
/vector check
/vector debug 我今天压力好大
/memory vector check
/memory vector search 考试
/events 10
/spire
/spire 聊聊今天的心情
/reset
```

普通输入会发送给模型。

## 回复格式

主模型会被要求输出 JSON：

```json
{
  "text": "只放对话正文",
  "emotion": "smile",
  "action": {
    "type": "none"
  }
}
```

CLI 只显示 `text`。  
`emotion` 和 `action` 会作为 `assistant_meta` 事件写入数据库, 方便以后 GUI 读取。  
如果模型没有按 JSON 输出, 程序会尽量清理正文里的 `[微笑]`、`（抱抱）`、`*smiles*` 等标记, 避免它们污染对话正文。

`/forget <id>` 会按 `/memories` 显示的编号删除单条记忆。  
`/forget all --yes` 会清空所有长期记忆；没有 `--yes` 时不会执行。

`/db reset <password>` 是测试用清库命令，会清空本地用户数据库里的 profile、messages、memories、facts 和 events，并恢复默认 profile。  
默认密码在 `config.json` 的 `database_reset_password` 中配置，建议你改成自己的临时密码。

也可以只清空一部分：

```text
/db clear profile <password>
/db clear messages <password>
/db clear memories <password>
/db clear facts <password>
/db clear events <password>
```

`/mode` 可以切换 MFocus/MTrigger 的工作方式：

- `hybrid`：优先模型判断, 失败后回退规则。
- `rule`：只用规则。
- `off`：关闭对应模块。

`/memory summarize [turns]` 会调用模型, 从最近聊天中提炼长期记忆。

`/spire [hint]` 会让莫妮卡主动开启一个话题。`hint` 可选, 只作为方向提示, 不会固定台词。
不填写 `hint` 时, v0.5.4 起会从话题池里自动选择方向：普通日常话题和日常反思/轻哲学话题默认各占一半, 并尽量避开最近用过的 `/spire` 话题。

## 当前机制

### MFocus-lite

每轮聊天前，CLI 会参考 MAICA 的 savefile extraction 思路，把这些信息整理成事实注入给模型。

当前默认是 `hybrid` 模式：先让一个轻量模型调用判断本轮需要哪些上下文；如果模型计划失败，就退回规则判断。

- 玩家名、生日、地点。
- affection 和关系阶段。
- 第几次启动 CLI。
- 已经聊过多少轮。
- 初次见面距今约多少天。
- 上次离开 CLI 的时间。
- 今天是否是玩家生日、莫妮卡生日、情人节、万圣节、圣诞节、新年等特殊日期。
- 莫妮卡基础设定。
- CLI 版 SFE 稳定事实，包括 Monika 设定、玩家资料、关系状态、自定义事实。
- MAICA 数据集风格参考，包括本轮输入分类、建议回复长度、相似样本和反宏大叙事规则。
- Monika 视角提示，让回复更稳定地体现文学、钢琴、关心作息、自律、温柔和轻微俏皮。
- Response Planner 本轮表演方向，包括对话类别、回复模式、情绪底色、潜台词、回复节奏和应避免的问题。
- 与本轮输入相关的长期记忆。

特殊节日不会写死台词。MFocus 只会告诉后端“今天检测到某个事件”, 具体怎么说仍由模型自己生成。

### SFE-lite

`sfe.py` 参考原版 MAICA 的 `mfocus_sfe.py`，但不依赖 MAS 存档。它会从 CLI 自己的数据库里整理事实：

- 玩家名、生日、年龄、地点。
- 昵称列表，供莫妮卡自然称呼玩家。
- affection 和关系阶段。
- 启动次数、聊天轮数、初次见面距今天数、上次离开时间。
- Monika 的稳定设定。
- 你用 `/fact add` 手动加入的稳定事实。

`config.json` 里可以调整：

```json
{
  "mfocus_sfe_enabled": true,
  "sfe_level": 1,
  "sfe_fact_limit": 14
}
```

`sfe_level` 当前范围是：

- `0`：只放最基础事实。
- `1`：加入主要 Monika 设定，默认推荐。
- `2`：加入更多兴趣、偏好和世界观事实。

`/fact add <text>` 适合保存“稳定事实”，例如玩家偏好、长期状态、关系设定。  
`/remember <text>` 更适合保存普通聊天记忆。简单说：`fact` 像档案，`memory` 像回忆。

### Style Database

v0.5 起，CLI 可以把 `MAICA_ds_basis` 导入本地风格库：

```powershell
python dataset_importer.py --source-root "..\MAICA_ds_basis"
```

也可以在 CLI 内执行：

```text
/dataset import
/style examples 我回来了
```

默认导入：

- `../MAICA_ds_basis/ds_new.jsonl`
- `../MAICA_ds_basis/moni_dataset_2603.jsonl`

导入结果保存在：

```text
data/style.db
```

`style.db` 不是你的个人记忆，而是可重建的 MAICA 风格样本库。每轮聊天时，CLI 会根据用户输入检索 2-3 条相似样本，作为“语气参考”，不会要求模型照抄。

相关配置：

```json
{
  "style_enabled": true,
  "style_db_path": "data/style.db",
  "style_example_limit": 3,
  "style_max_source_length": 220,
  "style_import_max_length": 300,
  "anti_grandiosity": true
}
```

风格层会把输入大致分成：

- `greeting`
- `return`
- `farewell`
- `love`
- `hug`
- `comfort`
- `memory`
- `event`
- `daily`
- `question`
- `serious`

普通日常默认会被限制为短回复，并避免主动升华到“虚拟与现实、永恒、命运、存在意义”等宏大叙事。

### Monika Lens

`monika_lens.py` 会根据本轮输入类型加入少量 Monika 视角提示。它不负责写台词，只负责让模型更稳定地表现出 Monika 的兴趣和价值观：

- 文学、诗歌、钢琴。
- 温柔但有主见。
- 关心[player]的作息和心情。
- 自律、努力、轻微俏皮。
- 严肃问题时成熟清晰，日常问题时短而亲近。

配置：

```json
{
  "monika_lens_enabled": true,
  "monika_lens_hint_limit": 2,
  "reflective_lens_enabled": true,
  "reflective_lens_hint_limit": 1,
  "spire_reflective_probability": 0.5,
  "spire_recent_topic_window": 8
}
```

`reflective_lens_enabled` 会在合适话题下加入“日常反思”提示，例如雨天、音乐、文学、时间、记忆、学习工作、疲惫、孤独、成长和星空。  
它的目标是让 Monika 偶尔像 MAS 原版那样从日常话题自然过渡到一点点思考，但最多只加一句，并把观察落在此刻、关系和具体生活上。

`spire_reflective_probability` 只控制 `/spire` 在没有手动 hint 时选择“日常反思型”话题的概率。默认 `0.5`，也就是一半轻哲学、一半普通日常。  
`spire_recent_topic_window` 控制 `/spire` 避开最近多少个已使用话题，默认 `8`。如果同一类话题池都用完了，才会重新允许重复。

v0.6.6 起，`/spire` 可以选配 Wikipedia 资料型话题。开启后，CLI 会在本地话题池之外随机抽取或根据 `/spire hint` 查询一小段 Wikipedia 摘要，再让 Monika 用自己的理解主动开启话题。

```json
{
  "spire_wikipedia_enabled": true,
  "spire_wikipedia_probability": 0.35,
  "spire_wikipedia_language": "zh",
  "spire_wikipedia_timeout": 6
}
```

### Response Planner

v0.5.5 起，CLI 会在每轮模型调用前生成一段很短的“本轮对话表演方向”。它不是最终回复，也不会调用模型，只是把分类、关系氛围和本轮语气整理成提示，让回复不只是在遵守规则，而是知道“这一轮为什么这样说”。

示例：

```text
本轮对话表演方向:
- 对话类别: comfort
- 回复模式: comfort_soft_tease
- 情绪底色: concerned
- 潜台词: 她察觉[player]有点累或难受，想靠近安慰，语气放在恋人之间的具体关心上。
- 回复节奏: medium
- 是否主动反问: no
- 风格要求: 先贴近当下感受，再给陪伴感；可以轻微撒娇或温柔吐槽，也可以给一个很轻的可选小动作。
```

配置：

```json
{
  "response_planner_enabled": true,
  "response_planner_debug": true,
  "response_planner_examples_enabled": true,
  "response_planner_example_limit": 2
}
```

当前分类包括：

- `greeting`
- `return`
- `farewell`
- `love`
- `hug`
- `comfort`
- `serious`
- `question`
- `memory`
- `event`
- `playful`
- `daily`

`response_plan` 会写进 debug plan。回复正文仍然必须走原有 JSON 格式，情绪和动作继续放在 `emotion/action` 字段，不进入正文。

v0.6 起，如果模型返回的 `emotion` 仍是 `neutral`，但 `response_plan` 已经给出更具体的情绪底色，CLI 会用 planner 的情绪作为 fallback。这个 fallback 只影响 `emotion` 元数据，不会把表情或动作写进正文。

v0.6.1 起，Response Planner 会给 `love`、`comfort`、`serious`、`memory`、`playful`、`daily` 等类别附带 1-2 条手写“参考节奏”。这些示例用于参考停顿、亲近感和语气。

同一版也修正了 `晚安` / `good night` 的分类：它们现在会归入 `farewell`，而不是普通 `greeting`。planner 的潜台词也会优先使用 profile 里的 `player_name`，降低模型把 `[player]` 占位符原样说出来的概率。

v0.6.2 起，Response Planner 会优先使用 Example Bank。它会先模糊筛选 `quality >= 4` 且长度合适的样例，再按 `category / mode / emotion / quality / 关键词重合` 加权评分，选取评分最高的 3 条作为“同类高质量参考样例”发送给模型。样例中的 `{player}` 会在注入前替换成当前昵称或 `player_name`。

v0.6.6 起，提示词机制进一步靠近原版 MAICA：基础 persona 变短，MFocus 负责提供“可参考信息”，Response Planner 只给本轮表演方向，Example Bank 负责提供同类节奏片段。CLI 同时支持一个空的 Core Example Bank 接口，方便你后续手写高质量样例并让它优先于清洗数据集。

v0.6.7 起，Example Bank 改为 intent-aware 检索。`category` 继续控制大语气，`intent` 用来识别更细的场景，例如 `morning_greeting`、`direct_love`、`miss_you`、`fatigue`、`hesitation`、`night_farewell`。检索会先扩大候选池，再按 `intent / 用户文本相似度 / mode family / emotion / quality / core 优先级` 重排，并过滤低相关样例。现在宁可少给参考，也尽量不把弱相关样例塞进 prompt。

v0.6.8 起，`general_daily` 被进一步拆分。常见日常输入会优先进入 `acknowledgement`、`small_confirmation`、`identity_question`、`desire_ambiguous`、`recommendation`、`travel_place`、`appearance_clothes`、`relationship_check`、`casual_affection`、`project_work`、`task_planning`、`boredom_low_energy`、`special_day` 等小类。部分 intent 也会反向修正大类，例如身份/推荐/旅行问题走 `question`，压力和自我怀疑走 `comfort`，任务排序走 `serious`，特殊日期走 `event`。

v0.6.9 起，Example Bank 增加 `retrieval_text` 字段。它会把 `category / intent / mode / emotion / user / notes / assistant_style` 合成一段更适合检索的语义文本，当前本地检索会把它纳入相似度评分；后续接入 Qwen3-Embedding + FAISS 时也会直接对这个字段向量化，而不是只对很短的 `user` 字段做 embedding。

v0.7.0 加入本地 `Qwen3-Embedding + FAISS` 语义检索层。向量索引会使用根目录的 `Qwen3-Embedding-0.6B`，把 Example Bank 的 `retrieval_text` 生成 embedding，并写入本地 FAISS 索引。CLI 默认仍会使用 v0.6.9 的规则检索；只有执行 `/vector on` 后，聊天才会优先使用向量检索，并在失败时自动回退到规则检索。

安装可选依赖：

```powershell
cd "D:\~maica oringinal code\maica cli"
py -3.13 -m pip install -r requirements.txt
```

检查向量检索准备状态：

```text
/vector check
```

构建索引：

```text
/vector build
```

预览检索：

```text
/vector search 我今天压力好大
```

启用或关闭聊天中的向量检索：

```text
/vector on
/vector off
```

预留配置：

```json
{
  "embedding_enabled": false,
  "embedding_model_path": "../Qwen3-Embedding-0.6B",
  "embedding_device": "cpu",
  "embedding_batch_size": 16,
  "embedding_index_path": "data/example_vectors.faiss",
  "embedding_meta_path": "data/example_vectors_meta.jsonl",
  "embedding_top_k": 30,
  "embedding_min_score": 0.55,
  "example_bank_model_filtering": true,
  "example_bank_strict_relevance": false,
  "example_bank_min_vector_score": 0.62,
  "memory_embedding_enabled": false,
  "memory_embedding_index_path": "data/memory_vectors.faiss",
  "memory_embedding_meta_path": "data/memory_vectors_meta.jsonl",
  "memory_embedding_top_k": 8,
  "memory_embedding_inject_limit": 5,
  "memory_embedding_min_score": 0.55,
  "memory_embedding_fallback_lexical": true
}
```

v0.7.1 新增 `/vector debug <text>`，用于查看本轮 `category / intent / mode / retrieval_text / example_bank` 摘要，不输出完整 prompt。记忆向量检索也加入了独立准备层：

```text
/memory vector check
/memory vector build
/memory vector search <text>
/memory vector on
/memory vector off
```

v0.7.2 起，`/memory vector on` 后 MFocus 会优先使用记忆向量检索；如果索引缺失、检索出错或没有结果，会按 `memory_embedding_fallback_lexical` 回退到原来的关键词记忆检索。

v0.8.0 起，核心聊天流程已封装为 `MaicaEngine`。CLI 现在只是一个前端；未来 GUI、Live2D、TTS 和 STT 可以直接复用同一个后端入口：

```python
from engine import MaicaEngine

engine = MaicaEngine()
result = engine.chat("你好")
print(result["text"], result["emotion"], result["action"])
engine.close()
```

`MaicaEngine.chat()` 和 `MaicaEngine.spire()` 会返回稳定结构，供 GUI 使用：

```json
{
  "ok": true,
  "source": "chat",
  "user": "你好",
  "text": "回复正文",
  "emotion": "smile",
  "action": {},
  "mfocus_plan": {},
  "mtrigger_notices": [],
  "response_time": 1.23,
  "error": ""
}
```

相关配置：

```json
{
  "example_bank_enabled": true,
  "example_bank_core_paths": [
    "data/dialogue_examples_core.jsonl"
  ],
  "example_bank_paths": [
    "data/dialogue_examples_maica_cleaned.jsonl"
  ],
  "example_bank_limit": 3,
  "example_bank_candidate_limit": 40,
  "example_bank_min_quality": 4,
  "example_bank_min_score": 120,
  "example_bank_max_assistant_length": 220
}
```

Example Bank 没有可用样例时，才会回退到 v0.6.1 的手写参考节奏。

### Dialogue Dataset

v0.6 起，CLI 可以把 `logs/YYYY-MM/YYYY-MM-DD.jsonl` 导出成更适合人工标注、检索和未来训练的数据资产。

命令行：

```powershell
python dataset_builder.py --output dialogue_dataset
```

CLI 内：

```text
/dataset export
/dataset export dialogue_dataset_v2
```

默认输出：

```text
dialogue_dataset/
  cleaned_pairs.jsonl
  labeled_pairs.jsonl
  style_examples.jsonl
  bad_outputs.jsonl
  preference_pairs.jsonl
  manifest.json
  raw_logs/
```

字段大致包括：

- `user_input`
- `assistant_reply`
- `scene_category`
- `emotion`
- `reply_strategy`
- `length`
- `quality_score`
- `notes`
- `source_file`
- `source_line`

`cleaned_pairs.jsonl`、`labeled_pairs.jsonl` 和 `style_examples.jsonl` 会由日志自动生成。  
`bad_outputs.jsonl` 和 `preference_pairs.jsonl` 默认是空文件，适合你手动记录“不自然回复”和“好坏对照”。

### MAICA Dataset Cleaning

可以把 `MAICA_ds_basis` 清洗成 Example Bank 候选数据：

```powershell
python maica_dataset_cleaner.py
```

默认读取：

```text
../MAICA_ds_basis/moni_dataset_2603.jsonl
../MAICA_ds_basis/ds_new.jsonl
```

默认输出：

```text
data/dialogue_examples_maica_cleaned.jsonl
```

清洗流程包括：

- 只取 `user + assistant`。
- 跳过 `system`。
- 去掉 `[微笑]`、`[担心]` 等情绪标签，并保存为 `emotion`。
- 把 `[player]` / `[player_nickname]` 规范化为 `{player}`，后续由 Example Bank 根据当前 profile 动态替换成 `player_name` 或昵称。
- 去重。
- 删除空 user 和明显 command-like 触发词。
- 分类到 `greeting / farewell / love / hug / comfort / serious / playful / daily / memory / event`。

例如：

```json
{
  "user": "我今天好累",
  "bad_reply": "辛苦了，建议你早点休息，保持良好作息。",
  "good_reply": "又把自己累成这样……先别逞强了，今天剩下的时间让我陪你安静一点，好不好？",
  "reason": "bad_reply 太像客服建议，good_reply 有关系感、停顿和情绪位置",
  "category": "comfort",
  "strategy": "comfort_soft_tease"
}
```

这些导出文件可能包含私人对话，已经在 `.gitignore` 中默认忽略。不要把生成后的 JSONL 数据直接发给别人或上传公开仓库。

### MAS Script Analysis

如果你用 `unrpyc` 把 MAS 原版 `.rpyc` 反编译到了单独目录，例如：

```text
MAS_decompiled/
```

可以运行：

```powershell
python mas_script_analyzer.py --input "..\MAS_decompiled" --output "analysis"
```

它会生成：

```text
analysis/mas_dialogue_units.jsonl
analysis/mas_script_stats.json
analysis/mas_reflective_profile.json
```

这个工具不会反编译 `.rpyc`，也不会修改 MAS 原文件。它只分析已经存在的 `.rpy` 明文脚本，用于观察 MAS 的话题结构、句子长度、表情码、触发条件和日常反思主题。

### Profile Setup

`/profile setup` 会交互式初始化 SFE 会读取的 profile 字段。回车会跳过当前字段，输入 `-` 会清空当前字段。

常用字段包括：

```text
player_name
birthday
location
nicknames
pronouns
gender
favorite_color
favorite_music
favorite_food
likes_rain
likes_horror
likes_poetry
personality
appearance
family_note
health_note
study_work
```

查看字段说明：

```text
/profile fields
```

清空单个字段：

```text
/profile unset favorite_music
```

### JSONL Logs

v0.5 起，每轮对话会写入调试日志：

```text
logs/YYYY-MM/YYYY-MM-DD.jsonl
```

日志包含用户输入、干净正文、emotion/action、MFocus 计划、使用的 style example id、MTrigger 通知和模型原始输出。  
这些日志用于调试“为什么这一轮太长/太宏大/样本不合适”，不参与模型记忆。

开关：

```text
/logs on
/logs off
```

### 固定节日事件

在 `config.json` 的 `special_events` 里添加固定日期事件：

```json
{
  "date": "09-22",
  "name": "莫妮卡的生日",
  "description": "莫妮卡的生日."
}
```

支持两种日期格式：

- `MM-DD`：每年固定日期。
- `YYYY-MM-DD`：只在指定年份日期触发。

### MTrigger hybrid

每轮回复后，CLI 会参考 MAICA 的 trigger 思路，让模型输出结构化动作：

```json
{
  "actions": [
    {
      "type": "alter_affection",
      "value": 1.2,
      "reason": "用户表达了关心"
    },
    {
      "type": "remember",
      "text": "玩家喜欢雨天和钢琴曲",
      "importance": 2
    }
  ]
}
```

目前允许的动作：

- `alter_affection`
- `remember`
- `set_profile`

如果模型 JSON 解析失败, 会回退到轻量规则：

- 普通关心或问候：约 `+1.0`。
- 称赞：约 `+0.8`。
- 短句表白：约 `+1.5`。
- 长表白或强陪伴表达：最多约 `+3.0`。
- 明显冒犯：约 `-1.5` 到 `-3.0`。

## 推荐测试流程

1. 启动 CLI。
2. 用 `/profile set player_name 你的名字` 设置名字。
3. 用 `/affection 400` 设置关系阶段。
4. 用 `/remember 我喜欢雨天和钢琴曲` 写入一条长期记忆。
5. 直接输入 `你好，我回来了`。

## 后续模块

- `MFocus`：从简单上下文注入升级到工具调用。
- `MTrigger`：从规则升级到模型结构化动作判断。
- `memory`：从 SQLite LIKE 检索升级到 embedding/向量检索。
- `dataset`：把 `MAICA_ds_basis` 做成 few-shot 风格样本库。
- `ui`：CLI 稳定后接 Textual 或 PySide6。
