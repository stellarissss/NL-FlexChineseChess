# NL-FlexChineseChess

> **用一句自然语言，当场改写一盘中国象棋的规则。**
>
> 这不是一套"预设玩法菜单"，而是一个真正跑在服务器上的 AI Agent：玩家说一句话，
> 它直接改写游戏代码与配置，经过自动验收后**热发布到正在进行的对局**——无需重开、无需刷新。

`NL-FlexChineseChess` 是线上多实例棋局服务 `stellaric.site/chess1|2|3` 的**开源核心版**：
剥离了大厅主页、访客令牌、IP 独占座位等线上运维机制，保留全部游戏内核，
可作为**免鉴权单用户服务**在本地或自建服务器上一键部署。

---

## 它和普通象棋 AI 有什么不同

| | 传统象棋程序 | NL-FlexChineseChess |
|---|---|---|
| 改规则 | 改源码、重新编译、重开对局 | 对话框说一句话，秒级热生效 |
| 规则模型 | 写死的 | 数据驱动 + 代码由 Agent 现场改写 |
| AI 的角色 | 只会按既有规则走棋 | 会**改规则本身**（把"马"改成"坦克"、加"长江黄河"、让"兵能一跃过河"……） |
| 部署 | 各异 | `./start.sh` 一条命令 |

玩家指令示例（都来自真实对局）：

- 「让我这边的马走日改成走田，还能踩河界」
- 「给双方各加 2 个随机步数」，「跳过回合」，「只能吃子不能普通移动」
- 「把棋盘中间两行变成长江黄河，马可以跳过去但不能停在河里」
- 「重开后兵可以走两步」——Agent 会区分"热并入当前对局"与"下一局生效"

---

## 核心架构

```
玩家自然语言 ──▶ FastAPI (/api/agent/command)
                      │
                      ▼
              Agent Runtime（agent/runtime.py）
              规划器 Planner → 复用/新建 → 分派工人
                      │  tool_calls
                      ▼
              Agent Tools（agent/tools.py）
      list_files / read_file / write_file / edit_file /
      patch_config / validate / spawn_workers / finish
                      │  改写
                      ▼
        staging 构建目录 ──validate 自检──▶ hot_publish
                      │                        │
                      ▼                        ▼
           games/xiangqi/ 引擎与配置     active.json 指针切换
        （engine / mechanism_engine /      （对局中热生效，
          rule_engine / configs）           局面不丢失）
                      │
                      ▼
               SSE 事件流 → 前端即时刷新
```

设计要点：

- **代码与状态分离**：可被 Agent 改写的代码放在应用根目录；对局状态在 `runtime/state/`，
  发布只切 `active.json` 指针，**不重置棋盘**。
- **单一真源**：模型、密钥、Base URL 统一由 `core/ai_config.py` 解析；引擎能力由 manifest 声明。
- **门控验收**：Agent 改完必须过 `agent/validate.py`（词法/语义/开局不变量/回合契约），
  错误级问题直接打回，警告回灌给 Agent 修正，杜绝"改坏了还发布"。
- **即时生效**：`hot_merge()` 把改动并入当前 `GameSession`，玩家无需重开即可看到新规则。

---

## 目录结构

```
NL-FlexChineseChess/
├── main.py                  # FastAPI 入口（免鉴权、单用户）
├── run.py                   # 启动器
├── start.sh                 # 一键部署脚本
├── config.example.json      # 配置示例（复制为 config.json 填密钥）
├── env.example              # 环境变量示例（复制为 .env）
├── requirements.txt         # Python 依赖
├── agent/                   # AI 改造 Agent
│   ├── prompts.py           #   系统提示词（缓存稳定的前缀）
│   ├── llm.py               #   DeepSeek 调用（含 thinking/reasoning 回传）
│   ├── runtime.py           #   规划-分派-验收-发布 主循环
│   ├── tools.py             #   文件/配置/校验工具集
│   ├── validate.py          #   构建验收与门控
│   └── workspace.py         #   构建/热发布/改动判定
├── core/                    # 内核
│   ├── state.py             #   GameSession：局面、热并入、发布
│   ├── engine.py            #   引擎加载与动作分发
│   ├── ai_config.py         #   模型/密钥/思考档位单一真源
│   ├── meter.py             #   token 计量
│   ├── client_diag.py       #   前端诊断日志
│   ├── json_patch_utils.py  #   配置补丁工具
│   └── paths.py             #   运行时路径布局
├── games/xiangqi/           # 象棋引擎与配置（Agent 改造的对象）
│   ├── engine.py            #   走子、胜负、回合
│   ├── mechanism_engine.py  #   随机步数/额外回合/控制阵营等机制
│   ├── rule_engine.py       #   可执行规则条件树
│   ├── ai.py                #   走棋 AI（难度/性格）
│   ├── manifest.json        #   能力声明
│   └── configs/             #   board / pieces / rules / ui_config
├── static/                  # 前端（Web Component 棋盘 + 对话侧栏）
└── tools/                   # 自检与回归脚本
```

---

## 快速开始

### 环境

- Python 3.10+
- 一个 [DeepSeek](https://platform.deepseek.com) API Key

### 本地一键启动

```bash
git clone https://github.com/stellarissss/NL-FlexChineseChess.git
cd NL-FlexChineseChess

# 方式 A：环境变量（推荐）
cp env.example .env
#   编辑 .env，把 DEEPSEEK_API_KEY 改成你的真实密钥

# 方式 B：配置文件
cp config.example.json config.json
#   编辑 config.json，把 api_key 改成你的真实密钥

./start.sh
```

打开 <http://localhost:9401>，在侧栏输入一句改造指令即可。

> `config.json` 与 `.env` 都已被 `.gitignore` 排除，**不会、也不应提交到仓库**。

### 自建服务器部署

`start.sh` 默认监听 `0.0.0.0:9401`，可直接运行，或在前面挂 nginx 反代：

```nginx
location / {
    proxy_pass http://127.0.0.1:9401;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";   # SSE 事件流需要
}
```

也可用 systemd 托管（在 `[Service]` 里用 `Environment=DEEPSEEK_API_KEY=...`
或 `EnvironmentFile=/path/to/.env` 提供密钥，`ExecStart=/path/to/start.sh`）。

### 多实例（可选）

多个实例共用同一份代码、仅隔离状态：

```bash
CHESS_PORT=9402 CHESS_RUNTIME_DIR=./runtime2 ./start.sh
CHESS_PORT=9403 CHESS_RUNTIME_DIR=./runtime3 ./start.sh
```

---

## 配置说明

密钥解析优先级（高 → 低）：

```
环境变量 DEEPSEEK_API_KEY  >  config.json["api_key"]  >  （无默认值，必须提供）
```

| 环境变量 | 作用 | 默认 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek 密钥 | 必填 |
| `DEEPSEEK_MODEL` | 模型 ID | `deepseek-flash` |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com/v1` |
| `DEEPSEEK_AGENT_THINK` | 整条流水线思考档位 `off/low/high/max` | `off` |
| `DEEPSEEK_FIRST_TURN_THINK` | 仅首轮理解指令的思考档位 | `low` |
| `CHESS_PORT` | 服务端口 | `9401` |
| `CHESS_HOST` | 监听地址 | `0.0.0.0` |
| `CHESS_RUNTIME_DIR` | 运行时状态目录 | `./runtime` |

模型说明：`deepseek-flash` 对应 DeepSeek-V4.1-Flash；`deepseek-v4.1` 不是合法 ID。
首轮默认开启 `low` 思考以提升语义消歧准确率，后续步骤关闭以保证速度——
这是"准确率 / 速度"权衡后的结果。

---

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 游戏页面 |
| GET | `/api/game/meta` | 当前棋种元数据 |
| GET | `/api/state` | 当前局面 |
| POST | `/api/move` | 玩家走子 |
| POST | `/api/ai_move` | AI 走子（含行动点预算） |
| POST | `/api/valid_moves` | 合法落点 |
| POST | `/api/agent/command` | **自然语言改造指令**（核心） |
| GET | `/api/agent/events` | 改造进度（SSE） |
| GET | `/api/agent/status` | 改造任务状态 |
| GET | `/api/mechanisms` | 当前活动机制 |
| POST | `/api/undo` | 悔棋 |
| GET | `/api/token_stats` | token 用量 |

完整路由见 `main.py`。

---

## 自检

```bash
python3 tools/selftest.py . xiangqi 60
```

输出单行 JSON `{ok, errors, warnings, stats}`，覆盖引擎级随机对局、状态不变量、
"合法动作必须可执行"硬契约、能力契约等。

---

## 安全说明

- 仓库内**不含任何真实密钥**；只有 `config.example.json` / `env.example` 里的占位符。
- 请勿把填写后的 `config.json` 或 `.env` 提交、上传或分享。
- 本版为**免鉴权单用户**设计：任何能访问服务端口的人都可操作对局。
  公网部署请自行通过防火墙 / 反向代理 / 内网限制访问控制。

---

## 许可与致谢

开源用于学习与技术交流。游戏内核由作者长期迭代打磨，线上实例运行于
`stellaric.site/chess1|2|3`。模型由 DeepSeek 提供。
