class XiangqiBoard extends HTMLElement {
    static get observedAttributes() {
        return ['api-base', 'rpg-mode', 'player-side'];
    }

    constructor() {
        super();
        this.configs = {};
        this.boardState = null;
        this.uiConfig = null;
        this.selectedPiece = null;
        this.validMoves = [];
        this.validCaptures = null;
        this.lastMove = null;
        this.aiThinking = false;
        this.coordInsertMode = false;
        this.selectMode = 'coord';
        this.regionPoints = [];
        this.coordDots = [];
        this.thinkingPollInterval = null;
        this.gameMeta = null;
        this.interaction = 'move';
        this._humanSide = '';
        // 能力声明（随机/异步/实时）与局面版本：前端据此决定是否轮询、失败是否重同步
        this.capabilities = {};
        this.actableSides = null;
        this.aiSides = null;
        this._playerActed = false;
        this._stateRev = 0;
        this._statePollTimer = null;
        this._agentTimer = null;
        this._agentJobId = null;
        this._bootTs = Date.now();   // 页面加载时刻：用来判断改造是否是在本页打开期间结束的
        this._buildStamp = '';
        this._agentSteps = [];
        this._lastRuleKey = '';
        this._keydownHandler = null;

        // 房间门禁（/chessN 均为一张独立的桌子）
        this._roomStatus = null;
        this._leaseGen = 0;
        this._gatePassed = false;
        this._gateEl = null;
        this._gateTimer = null;
        this._hbTimer = null;
        this._leaveBound = false;
        this.__cid = '';

        this._personalityInfo = {
            normal:     { icon: '🧠', name: '标准型', subtitle: 'Normal',    desc: '攻守平衡的标准AI',          agg: 0.5, def: 0.5 },
            aggressive: { icon: '⚔️', name: '激进型', subtitle: 'Aggressive', desc: '进攻至上，全力出击',        agg: 0.9, def: 0.2 },
            defensive:  { icon: '🛡️', name: '保守型', subtitle: 'Defensive',  desc: '稳扎稳打，防守反击',        agg: 0.2, def: 0.9 },
            random:     { icon: '🎲', name: '随机型', subtitle: 'Random',     desc: '天马行空，随心所欲',        agg: 0.5, def: 0.5 },
            custom:     { icon: '✨', name: '自定义', subtitle: 'Custom',     desc: '独一无二的神秘风格',        agg: 0.5, def: 0.5 },
        };

        this._mechanismMeta = {
            skip_turns:     { icon: '⏸️',  label: '冻结',     type: 'skip',   unit: '回合' },
            ai_control:     { icon: '🤖',  label: 'AI接管',   type: 'ai',     unit: '回合' },
            player_control: { icon: '🎮',  label: '玩家控制', type: 'player', unit: '' },
            random_moves:   { icon: '🎲',  label: '随机走棋', type: 'random', unit: '步'   },
            extra_turns:    { icon: '⚡',  label: '额外回合', type: 'extra',  unit: '回合' },
            move_limits:    { icon: '🚶',  label: '多步行走', type: 'limit',  unit: '步'   },
        };
    }

    get apiBase() {
        return this.getAttribute('api-base') || '';
    }

    get rpgMode() {
        return this.hasAttribute('rpg-mode');
    }

    get playerSide() {
        return this._humanSide || this.getAttribute('player-side') || 'red';
    }

    attributeChangedCallback(name, oldVal, newVal) {
        if (name === 'rpg-mode') {
            this._updateRpgMode();
        }
    }

    _updateRpgMode() {
        const host = this.shadowRoot?.host;
        if (!host) return;
        const sidePanel = this.shadowRoot.querySelector('.side-panel');
        const inputSection = this.shadowRoot.querySelector('.input-section');
        if (this.rpgMode) {
            if (sidePanel) sidePanel.style.display = 'none';
            if (inputSection) inputSection.style.display = 'none';
        } else {
            if (sidePanel) sidePanel.style.display = '';
            if (inputSection) inputSection.style.display = '';
        }
    }

    connectedCallback() {
        this.attachShadow({ mode: 'open' });
        this._renderShadowDom();
        this._initTicTacToe();
        this._updateRpgMode();
    }

    disconnectedCallback() {
        this.destroy();
    }

    _renderShadowDom() {
        const style = document.createElement('style');
        style.textContent = this._getStyles();
        this.shadowRoot.appendChild(style);

        const container = document.createElement('div');
        container.id = 'app';
        container.innerHTML = this._getHtmlTemplate();
        this.shadowRoot.appendChild(container);
    }

    _getHtmlTemplate() {
        return `
        <div id="thinking-overlay" class="thinking-overlay">
            <div class="thinking-content">
                <div class="thinking-loading">
                    <div class="thinking-spinner"></div>
                    <div class="thinking-text" id="thinking-text">ChatAI 正在理解您的意图...</div>
                    <div class="thinking-stage" id="thinking-stage">阶段: 意图解析</div>
                    <div class="agent-progress" id="agent-progress"><div class="agent-progress-fill" id="agent-progress-fill"></div></div>
                    <div class="agent-hud" id="agent-hud"></div>
                    <div class="agent-steps" id="agent-steps"></div>
                    <button type="button" class="agent-cancel" id="agent-cancel">取消本次改造</button>
                </div>
                <div class="ttt-wrap">
                    <div class="ttt-title">井字棋 · 消遣一局（不保存）</div>
                    <div class="ttt-board" id="ttt-board">
                        <div class="ttt-cell" data-idx="0"></div>
                        <div class="ttt-cell" data-idx="1"></div>
                        <div class="ttt-cell" data-idx="2"></div>
                        <div class="ttt-cell" data-idx="3"></div>
                        <div class="ttt-cell" data-idx="4"></div>
                        <div class="ttt-cell" data-idx="5"></div>
                        <div class="ttt-cell" data-idx="6"></div>
                        <div class="ttt-cell" data-idx="7"></div>
                        <div class="ttt-cell" data-idx="8"></div>
                    </div>
                    <div class="ttt-status" id="ttt-status">你执 X · 随机先手</div>
                    <button type="button" class="ttt-restart" id="ttt-restart">重新开始</button>
                </div>
            </div>
        </div>

        <div id="settings-modal" class="modal">
            <div class="modal-content">
                <h3>设 置</h3>
                <div class="form-group">
                    <label>DeepSeek API Key</label>
                    <input type="password" id="api-key-input" placeholder="输入您的API密钥">
                    <small>您的API密钥仅保存在本地，不会发送到我们的服务器</small>
                </div>
                <div class="form-group">
                    <label>AI难度</label>
                    <select id="difficulty-select">
                        <option value="easy">简单</option>
                        <option value="medium" selected>中等</option>
                        <option value="hard">困难</option>
                    </select>
                </div>
                <div class="modal-buttons">
                    <button id="close-settings" class="btn">取消</button>
                    <button id="save-settings" class="btn-primary">保存</button>
                </div>
            </div>
        </div>

        <div id="logs-modal" class="modal">
            <div class="modal-content logs-content">
                <h3>AI 对话日志</h3>
                <div id="logs-container" class="logs-container"></div>
                <div class="modal-buttons">
                    <button id="btn-clear-logs" class="btn danger">清空日志</button>
                    <button id="btn-refresh-logs" class="btn">刷新</button>
                    <button id="close-logs" class="btn-primary">关闭</button>
                </div>
            </div>
        </div>

        <header class="header">
            <h1 id="game-title">无限制象棋<span class="version-badge" title="AI Agent 驱动版"><b>v2.1</b>&nbsp;beta</span></h1>
            <div class="header-actions">
                <span id="turn-indicator">红方回合</span>
                <button id="btn-settings" class="btn-icon" title="设置">⚙</button>
            </div>
        </header>

        <main class="main">
            <div class="board-section">
                <div id="board-container"></div>
            </div>

            <aside class="side-panel">
                <div class="panel-section">
                    <h3 data-num="01">AI 助手</h3>
                    <div id="ai-messages" class="messages"></div>
                </div>

                <div class="panel-section">
                    <h3 data-num="02">游戏目标</h3>
                    <div id="game-objectives" class="objectives-list">
                        <span class="empty">加载中...</span>
                    </div>
                </div>

                <div class="panel-section">
                    <h3 data-num="03">Token 消耗</h3>
                    <div id="token-stats" class="token-stats">
                        <div class="token-stat">
                            <span class="stat-label">总消耗</span>
                            <span class="stat-value" id="token-total">0</span>
                        </div>
                        <div class="token-stat">
                            <span class="stat-label">今日消耗</span>
                            <span class="stat-value" id="token-today">0</span>
                        </div>
                        <div class="token-stat">
                            <span class="stat-label">调用次数</span>
                            <span class="stat-value" id="token-calls">0</span>
                        </div>
                        <div class="token-stat cost">
                            <span class="stat-label">估算费用</span>
                            <span class="stat-value" id="token-cost">¥0.0000</span>
                        </div>
                    </div>
                </div>

                <div class="panel-section">
                    <h3 data-num="04">AI 性格<span class="h3-hint">可以通过文本框命令修改</span></h3>
                    <div id="ai-personality" class="personality-card">
                        <div class="personality-header">
                            <span class="personality-icon" id="personality-icon">🧠</span>
                            <div class="personality-title">
                                <span class="personality-type" id="personality-type">标准型</span>
                                <span class="personality-subtitle" id="personality-subtitle">Normal</span>
                            </div>
                        </div>
                        <p class="personality-desc" id="personality-desc">攻守平衡的标准AI</p>
                        <div class="personality-bars">
                            <div class="personality-bar">
                                <span class="bar-label">进攻</span>
                                <span class="bar-chars aggressive" id="bar-agg">█████░░░░░</span>
                                <span class="bar-percent" id="bar-agg-pct">50%</span>
                            </div>
                            <div class="personality-bar">
                                <span class="bar-label">防守</span>
                                <span class="bar-chars defensive" id="bar-def">█████░░░░░</span>
                                <span class="bar-percent" id="bar-def-pct">50%</span>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="panel-section">
                    <h3 data-num="05">已激活规则</h3>
                    <div id="active-rules" class="rules-list">
                        <span class="empty">暂无激活规则 —— 发送指令让 AI 改造游戏</span>
                    </div>
                </div>

                <div class="panel-section controls">
                    <h3 data-num="06">操 作</h3>
                    <button id="btn-undo" class="btn">悔 棋</button>
                    <button id="btn-undo-config" class="btn">撤回AI修改</button>
                    <button id="btn-restart" class="btn">重新开始</button>
                    <button id="btn-logs" class="btn">查看日志</button>
                    <button id="btn-reset-configs" class="btn danger">重置所有配置</button>
                </div>
            </aside>
        </main>

        <footer class="input-section">
            <div class="input-wrapper">
                <input
                    type="text"
                    id="command-input"
                    placeholder="输入指令，例如：让我的马可以斜着走、悔一步棋..."
                    autocomplete="off"
                >
                <div class="coord-select-btn-wrapper">
                    <button id="btn-insert-coord" class="btn" title="点击后在棋盘上选择格子，自动插入坐标到输入框">📍 选坐标</button>
                    <button id="btn-toggle-coord-mode" class="btn-toggle-mode" title="切换选坐标/选区域">⇄</button>
                </div>
                <button id="btn-send" class="btn-primary">发送</button>
            </div>
            <div class="hints">
                试试："車变成两个"、"兵可以后退"、"象可以过河"、"把象棋改成五子棋"、"把棋盘变成20行20列"
            </div>
        </footer>
        `;
    }

    _getStyles() {
        return `
@font-face {
    font-family: 'Resource Han Rounded CN';
    src: url('../../shared/assets/fonts/resource-han-rounded-cn.woff2');
    src: url('../../shared/assets/fonts/resource-han-rounded-cn.woff2') format('woff2'),
         url('../../shared/assets/fonts/resource-han-rounded-cn.woff2') format('woff2'),
         url('../../shared/assets/fonts/resource-han-rounded-cn.woff2') format('woff2'),
         url('../../shared/assets/fonts/resource-han-rounded-cn.woff2') format('woff2'),
         url('../../shared/assets/fonts/resource-han-rounded-cn.woff2') format('woff2');
    font-weight: normal;
    font-style: normal;
    font-display: swap;
    unicode-range: U+3000-303F, U+3040-309F, U+30A0-30FF, U+3400-4DBF, U+4E00-9FFF, U+F900-FAFF, U+FF00-FFEF;
}

:host {
    --paper: #fafaf8;
    --paper-warm: #f5f3ef;
    --paper-dark: #ebe8e2;
    --ink: #1a1a1a;
    --ink-soft: #2d2d2d;
    --ink-medium: #4a4a4a;
    --ink-light: #7a7a7a;
    --ink-faint: #b8b8b8;
    --line: #e0ddd7;
    --line-strong: #c9c5be;

    --board-bg: #f0d9b5;
    --board-line: #5c3a1e;
    --red-piece: #cc0000;
    --black-piece: #1a1a1a;

    --neon-cyan: #00f0ff;
    --neon-magenta: #ff00aa;
    --neon-pink: #ff2d6f;
    --neon-green: #39ff14;
    --neon-gold: #ffd700;

    --highlight: #1a1a1a;
    --valid-move: #3d7a3d;
    --last-move: #8b5a2b;
    --danger: #9b2c2c;
    --success: #2d6a4f;
    --warning: #8b6914;
    --text-light: #4a4a4a;

    --muted-ink: #7a7a7a;
    --accent-green: #2d6a4f;

    display: block;
    width: 100%;
    height: 100%;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

#app {
    display: flex;
    flex-direction: column;
    height: 100%;
    width: 100%;
    position: relative;
    font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Resource Han Rounded CN', 'PingFang SC', 'Microsoft YaHei', sans-serif;
    color: var(--ink);
    background-color: var(--paper);
    background-image:
        url("data:image/svg+xml,%3Csvg viewBox='0 0 400 400' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E");
    background-repeat: repeat;
    background-size: 200px 200px;
    background-blend-mode: multiply;
    overflow: hidden;
}

/* Header */
.header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 20px 32px;
    background: var(--paper);
    border-bottom: 1px solid var(--line);
    position: relative;
    animation: fadeInUp 0.6s cubic-bezier(0.22, 1, 0.36, 1) 0.1s both;
}

.header::after {
    content: '';
    position: absolute;
    left: 32px; right: 32px; bottom: -1px;
    height: 1px;
    background: var(--ink);
    transform: scaleX(0);
    transform-origin: left;
    animation: scaleIn 0.8s cubic-bezier(0.22, 1, 0.36, 1) 0.4s forwards;
}

.header h1 {
    font-family: 'Playfair Display', Georgia, 'Resource Han Rounded CN', 'PingFang SC', serif;
    font-weight: 500;
    font-size: 1.5rem;
    letter-spacing: 0.02em;
    color: var(--ink);
    font-style: italic;
}

.header h1::before {
    content: '象 棋';
    display: block;
    font-family: 'Playfair Display', serif;
    font-size: 0.65rem;
    font-weight: 400;
    letter-spacing: 0.3em;
    color: var(--ink-light);
    text-transform: uppercase;
    margin-bottom: 2px;
    font-style: normal;
}

.header-actions {
    display: flex;
    align-items: center;
    gap: 18px;
}

#turn-indicator {
    padding: 8px 20px;
    background: var(--ink);
    color: var(--paper);
    font-family: 'DM Sans', sans-serif;
    font-size: 0.75rem;
    font-weight: 500;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    border: none;
    position: relative;
    overflow: hidden;
    transition: all 0.3s cubic-bezier(0.22, 1, 0.36, 1);
}

#turn-indicator::before {
    content: '';
    position: absolute;
    top: 0; left: -100%;
    width: 100%; height: 100%;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent);
    transition: left 0.5s ease;
}

#turn-indicator:hover::before {
    left: 100%;
}

.btn-icon {
    padding: 8px 12px;
    border: none;
    background: transparent;
    color: var(--ink);
    cursor: pointer;
    font-size: 1.1rem;
    font-family: 'JetBrains Mono', monospace;
    transition: all 0.25s cubic-bezier(0.22, 1, 0.36, 1);
    position: relative;
}

.btn-icon::after {
    content: '';
    position: absolute;
    bottom: 4px;
    left: 50%;
    transform: translateX(-50%) scaleX(0);
    width: 60%;
    height: 1px;
    background: var(--ink);
    transition: transform 0.25s cubic-bezier(0.22, 1, 0.36, 1);
}

.btn-icon:hover::after {
    transform: translateX(-50%) scaleX(1);
}

.btn-icon:hover {
    color: var(--ink-soft);
}

/* Main */
.main {
    display: flex;
    flex: 1;
    overflow: hidden;
    padding: 24px 32px;
    gap: 32px;
}

.board-section {
    flex: 1;
    display: flex;
    justify-content: center;
    align-items: center;
    position: relative;
    animation: fadeInUp 0.6s cubic-bezier(0.22, 1, 0.36, 1) 0.2s both;
}

#board-container {
    position: relative;
    width: calc(min(90vmin, 600px) * 0.75);
    height: calc(min(90vmin, 600px) * 0.75 * 10 / 9);
    background: var(--board-bg);
    border-radius: 4px;
    box-shadow:
        0 0 0 1px rgba(26, 26, 26, 0.1),
        0 4px 20px rgba(0, 0, 0, 0.08),
        0 20px 60px rgba(0, 0, 0, 0.12);
}

/* Side Panel */
.side-panel {
    width: 300px;
    display: flex;
    flex-direction: column;
    gap: 20px;
    overflow-y: auto;
    padding-right: 4px;
}

.side-panel::-webkit-scrollbar { width: 4px; }
.side-panel::-webkit-scrollbar-track { background: transparent; }
.side-panel::-webkit-scrollbar-thumb {
    background: var(--line-strong);
    border-radius: 2px;
}

.panel-section {
    position: relative;
    background: var(--paper-warm);
    border: 1px solid var(--line);
    padding: 20px 22px;
    animation: fadeInUp 0.5s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.panel-section::before {
    content: '';
    position: absolute;
    top: 0; left: 0;
    width: 24px; height: 24px;
    border-top: 1px solid var(--ink);
    border-left: 1px solid var(--ink);
    pointer-events: none;
}

.panel-section::after {
    content: '';
    position: absolute;
    bottom: 0; right: 0;
    width: 24px; height: 24px;
    border-bottom: 1px solid var(--ink);
    border-right: 1px solid var(--ink);
    pointer-events: none;
}

.panel-section:nth-child(1) { animation-delay: 0.25s; }
.panel-section:nth-child(2) { animation-delay: 0.32s; }
.panel-section:nth-child(3) { animation-delay: 0.39s; }
.panel-section:nth-child(4) { animation-delay: 0.46s; }
.panel-section:nth-child(5) { animation-delay: 0.53s; }

.panel-section h3 {
    font-family: 'Playfair Display', Georgia, serif;
    font-weight: 500;
    font-size: 1rem;
    margin-bottom: 14px;
    color: var(--ink);
    letter-spacing: 0.02em;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--line);
    position: relative;
    font-style: italic;
}

.panel-section h3::before {
    content: attr(data-num);
    display: inline-block;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    font-weight: 400;
    color: var(--ink-light);
    margin-right: 10px;
    font-style: normal;
    vertical-align: middle;
}

.panel-section h3 .h3-hint {
    float: right;
    font-family: 'DM Sans', 'Resource Han Rounded CN', sans-serif;
    font-style: normal;
    font-weight: 400;
    font-size: 0.63rem;
    letter-spacing: 0.05em;
    color: #9c7c2e;
    padding-top: 5px;
    opacity: 0.85;
}

/* Messages */
.messages {
    max-height: 220px;
    overflow-y: auto;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.85rem;
    line-height: 1.6;
    padding-right: 6px;
}

.messages::-webkit-scrollbar { width: 4px; }
.messages::-webkit-scrollbar-track { background: transparent; }
.messages::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 2px; }

.message {
    padding: 10px 0 10px 16px;
    margin-bottom: 4px;
    border-left: 2px solid var(--ink-faint);
    color: var(--ink-soft);
    position: relative;
    animation: slideInLeft 0.4s cubic-bezier(0.22, 1, 0.36, 1);
    transition: all 0.2s ease;
}

.message:hover {
    border-left-color: var(--ink);
    background: rgba(26, 26, 26, 0.02);
    padding-left: 20px;
}

.message.success {
    border-left-color: var(--success);
    color: var(--ink-soft);
}

.message.success:hover {
    background: rgba(45, 106, 79, 0.04);
}

.message.error {
    border-left-color: var(--danger);
    color: var(--ink-soft);
}

.message.error:hover {
    background: rgba(155, 44, 44, 0.04);
}

.message.fun {
    border-left-color: var(--ink);
    color: var(--ink);
    font-style: italic;
}

.message.fun:hover {
    background: rgba(26, 26, 26, 0.03);
}

/* Rules list */
.rules-list {
    font-size: 0.82rem;
    font-family: 'DM Sans', sans-serif;
}

.rules-list .empty {
    color: var(--ink-light);
    font-style: italic;
    font-size: 0.8rem;
    text-align: center;
    padding: 16px 0;
    border: 1px dashed var(--line);
}

.rule-item {
    padding: 8px 0 8px 28px;
    margin-bottom: 2px;
    border-bottom: 1px dotted var(--line);
    color: var(--ink-soft);
    font-size: 0.8rem;
    position: relative;
    transition: all 0.2s ease;
    counter-increment: rule-counter;
}

.rules-list {
    counter-reset: rule-counter;
}

.rule-item::before {
    content: counter(rule-counter, decimal-leading-zero);
    position: absolute;
    left: 0;
    top: 8px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    font-weight: 500;
    color: var(--ink-light);
    letter-spacing: 0.05em;
}

.rule-item:hover {
    color: var(--ink);
    border-bottom-color: var(--ink-faint);
}

.rule-item:hover::before {
    color: var(--ink);
}

/* 合并面板的小节标题与玩家指令条目（2026-11-01） */
.rules-subhead {
    margin: 10px 0 6px;
    font-size: 0.68rem;
    letter-spacing: 0.22em;
    color: var(--ink-light);
    font-family: 'DM Sans', sans-serif;
}
.rules-subhead:first-child { margin-top: 2px; }

.rule-item.feed-item {
    display: flex;
    align-items: baseline;
    gap: 6px;
    padding-left: 6px;
}
.rule-item.feed-item::before { content: none; }

.feed-state {
    flex: none;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    transform: translateY(-1px);
    background: var(--ink-light);
}
.feed-item.state-running .feed-state {
    background: #c98d4f;
    box-shadow: 0 0 5px rgba(201, 141, 79, 0.8);
    animation: feedPulse 1.1s ease-in-out infinite;
}
.feed-item.state-done .feed-state { background: var(--success, #3f9d5c); }
.feed-item.state-failed .feed-state { background: var(--neon-red, #ff3b3b); }
.feed-item.state-cancelled .feed-state { background: var(--ink-light); }

@keyframes feedPulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
}

.feed-text {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.feed-time {
    flex: none;
    font-size: 0.68rem;
    color: var(--ink-light);
    font-family: 'DM Sans', sans-serif;
}

/* Token stats */
.token-stats {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1px;
    background: var(--line);
    border: 1px solid var(--line);
}

.token-stat {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    padding: 12px 14px;
    background: var(--paper);
    transition: background 0.2s ease;
}

.token-stat:hover {
    background: var(--paper-warm);
}

.token-stat .stat-label {
    font-family: 'DM Sans', sans-serif;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--ink-light);
    margin-bottom: 4px;
    font-weight: 500;
}

.token-stat .stat-value {
    font-family: 'Playfair Display', Georgia, serif;
    font-size: 1.3rem;
    font-weight: 500;
    color: var(--ink);
    font-style: italic;
}

.token-stat.cost {
    grid-column: span 2;
    background: var(--ink);
    color: var(--paper);
}

.token-stat.cost:hover {
    background: var(--ink-soft);
}

.token-stat.cost .stat-label {
    color: rgba(250, 250, 248, 0.6);
}

.token-stat.cost .stat-value {
    color: var(--paper);
    font-size: 1.5rem;
}

/* Controls */
.controls {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

/* Board elements */
.board-grid {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
}

.river-text {
    position: absolute;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    font-size: 1.2rem;
    color: var(--board-line);
    font-family: 'KaiTi', serif;
    letter-spacing: 0.5em;
    writing-mode: horizontal-tb;
    opacity: 0.55;
}

/* Pieces */
/* 视觉与定位分层：外层 .piece 只负责百分比定位（依赖 transform: translate(-50%,-50%)），
   背景边框阴影放在无 transform 的内层 .piece-body 上 —— iOS Safari 存在已知渲染缺陷：
   对「transform 定位 + border-radius:50%」的元素，box-shadow 会按未变形的方形盒子、
   在未平移的位置绘制，表现为棋子旁出现一块偏移的色框。 */
.piece {
    position: absolute;
    width: 10%;
    height: 9%;
    font-size: 1.4rem;
    font-weight: bold;
    font-family: 'KaiTi', 'SimSun', serif;
    cursor: pointer;
    user-select: none;
    z-index: 10;
}

.piece-body {
    width: 100%;
    height: 100%;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 2px solid transparent;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(0, 0, 0, 0.3);
    transition: box-shadow 0.2s, filter 0.2s;
}

.piece:hover {
    transform: translate(-50%, -50%) scale(1.05);
}

.piece:hover .piece-body {
    filter: brightness(1.1);
}

.piece.red .piece-body {
    background: #fff5e6;
    color: var(--red-piece);
    border-color: var(--red-piece);
    box-shadow:
        0 2px 8px rgba(0, 0, 0, 0.4),
        0 0 8px rgba(204, 0, 0, 0.55),
        0 0 0 1px rgba(255, 80, 80, 0.6);
}

.piece.black .piece-body {
    background: #e6e6e6;
    color: var(--black-piece);
    border-color: var(--black-piece);
    box-shadow:
        0 2px 8px rgba(0, 0, 0, 0.5),
        0 0 8px rgba(0, 240, 255, 0.35),
        0 0 0 1px rgba(0, 240, 255, 0.5);
}

.piece.selected {
    z-index: 20;
}

.piece.selected .piece-body {
    box-shadow:
        0 0 0 3px var(--neon-cyan),
        0 0 16px rgba(0, 240, 255, 0.8),
        0 0 32px rgba(0, 240, 255, 0.5);
}

.piece.last-moved .piece-body {
    box-shadow:
        0 0 0 3px var(--neon-pink),
        0 0 14px rgba(255, 45, 111, 0.7),
        0 0 28px rgba(255, 45, 111, 0.4);
}

.piece.ai-moved {
    z-index: 100;
}

.piece.ai-moved .piece-body {
    border: 3px solid var(--neon-gold);
    box-shadow: 0 0 15px rgba(255, 215, 0, 0.85), 0 0 30px rgba(255, 215, 0, 0.45);
}

/* 提示点外框 = 一整个棋格的热区，里面的小圆才是视觉上的「点」：
   这样「可吃的子」整颗都能点，不必精准点到中间那个小点。 */
.valid-move-indicator {
    position: absolute;
    width: 10%;
    height: 9%;
    display: flex;
    align-items: center;
    justify-content: center;
    pointer-events: none;
    z-index: 5;
}

.valid-move-dot {
    width: 40%;
    height: 40%;
    border-radius: 50%;
    background: radial-gradient(circle, var(--neon-green) 0%, rgba(57, 255, 20, 0.4) 60%, transparent 100%);
    box-shadow: 0 0 8px var(--neon-green), 0 0 16px rgba(57, 255, 20, 0.5);
    opacity: 0.85;
}

/* 吃子落点上站着敌方棋子（.piece 是 z-index:10），提示点若还在 5 就会被
   整颗棋子盖住——「能吃子却看不到可吃的子」正是这么来的，必须抬到棋子上方。 */
.valid-move-indicator.capture-move-indicator {
    z-index: 60;
    pointer-events: auto;
}

/* 红点落在棋子上，会和棋子本身的字色糊在一起（红字上画红点＝看不见），
   所以给红点补一圈白边：浅色木子、深色石子上都读得清。 */
.valid-move-indicator.capture-move-indicator .valid-move-dot {
    width: 34%;
    height: 34%;
    opacity: 1;
    background: radial-gradient(circle, var(--neon-red, #ff3b3b) 0%,
                var(--neon-red, #ff3b3b) 52%, rgba(255, 59, 59, 0.42) 74%, transparent 100%);
    box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.92),
                0 0 0 3.5px rgba(255, 59, 59, 0.5),
                0 0 10px var(--neon-red, #ff3b3b),
                0 0 20px rgba(255, 59, 59, 0.6);
}

/* 可吃掉的棋子本身再罩一层红光：红点落在深色棋子上不够醒目，
   用 filter 而不是覆盖 box-shadow，免得抹掉棋子自身的立体阴影。 */
.piece.capture-target .piece-body {
    filter: drop-shadow(0 0 5px rgba(255, 59, 59, 0.95)) drop-shadow(0 0 12px rgba(255, 59, 59, 0.55));
}

/* 将军提示：被将军一方的将/帅常亮红光 + 呼吸脉冲（解将后自动恢复）。
   与吃子提示的静息红光区分：将军光更宽、带脉冲。 */
.piece.in-check .piece-body {
    filter: drop-shadow(0 0 7px rgba(255, 59, 59, 1)) drop-shadow(0 0 18px rgba(255, 59, 59, 0.75));
    animation: checkPulse 1.1s ease-in-out infinite;
}

@keyframes checkPulse {
    0%, 100% { filter: drop-shadow(0 0 7px rgba(255, 59, 59, 1)) drop-shadow(0 0 18px rgba(255, 59, 59, 0.75)); }
    50% { filter: drop-shadow(0 0 11px rgba(255, 70, 70, 1)) drop-shadow(0 0 26px rgba(255, 59, 59, 0.95)); }
}

/* Input section */
.input-section {
    padding: 20px 32px 24px;
    background: var(--paper);
    border-top: 1px solid var(--line);
    position: relative;
    animation: fadeInUp 0.6s cubic-bezier(0.22, 1, 0.36, 1) 0.5s both;
}

.input-section::before {
    content: '';
    position: absolute;
    left: 32px; right: 32px; top: -1px;
    height: 1px;
    background: var(--ink);
    transform: scaleX(0);
    transform-origin: right;
    animation: scaleIn 0.8s cubic-bezier(0.22, 1, 0.36, 1) 0.6s forwards;
}

.input-wrapper {
    display: flex;
    gap: 12px;
    align-items: stretch;
}

#command-input {
    flex: 1;
    padding: 14px 18px;
    border: 1px solid var(--line-strong);
    background: var(--paper-warm);
    color: var(--ink);
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.88rem;
    letter-spacing: 0.01em;
    border-radius: 0;
    transition: all 0.25s cubic-bezier(0.22, 1, 0.36, 1);
}

#command-input::placeholder {
    color: var(--ink-faint);
    font-style: italic;
    font-family: 'DM Sans', sans-serif;
}

#command-input:focus {
    outline: none;
    border-color: var(--ink);
    background: var(--paper);
    box-shadow: 0 2px 0 var(--ink);
}

.hints {
    margin-top: 10px;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.72rem;
    color: var(--ink-light);
    letter-spacing: 0.03em;
    font-style: italic;
}

.hints::before {
    content: '—  ';
    color: var(--ink-faint);
    font-style: normal;
}

/* Buttons */
.btn {
    padding: 12px 20px;
    border: 1px solid var(--ink);
    background: transparent;
    color: var(--ink);
    cursor: pointer;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.8rem;
    font-weight: 500;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    border-radius: 0;
    transition: all 0.25s cubic-bezier(0.22, 1, 0.36, 1);
    position: relative;
    overflow: hidden;
}

.btn::before {
    content: '';
    position: absolute;
    bottom: 0; left: 0;
    width: 100%;
    height: 0;
    background: var(--ink);
    transition: height 0.25s cubic-bezier(0.22, 1, 0.36, 1);
    z-index: -1;
}

.btn:hover {
    color: var(--paper);
}

.btn:hover::before {
    height: 100%;
}

.btn-primary {
    padding: 12px 24px;
    border: none;
    background: var(--ink);
    color: var(--paper);
    cursor: pointer;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.82rem;
    font-weight: 500;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    border-radius: 0;
    transition: all 0.25s cubic-bezier(0.22, 1, 0.36, 1);
    position: relative;
    overflow: hidden;
}

.btn-primary::after {
    content: '→';
    display: inline-block;
    margin-left: 8px;
    transition: transform 0.25s cubic-bezier(0.22, 1, 0.36, 1);
}

.btn-primary:hover {
    background: var(--ink-soft);
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(26, 26, 26, 0.2);
}

.btn-primary:hover::after {
    transform: translateX(4px);
}

.btn.danger {
    border-color: var(--danger);
    color: var(--danger);
}

.btn.danger::before {
    background: var(--danger);
}

.btn.danger:hover {
    color: var(--paper);
}

/* Modal */
.modal {
    display: none;
    position: absolute;
    top: 0; left: 0;
    width: 100%; height: 100%;
    background: rgba(26, 26, 26, 0.5);
    backdrop-filter: blur(2px);
    -webkit-backdrop-filter: blur(2px);
    z-index: 100;
    justify-content: center;
    align-items: center;
    animation: fadeIn 0.3s ease;
}

.modal.show {
    display: flex;
}

.modal-content {
    position: relative;
    background: var(--paper);
    padding: 36px 32px;
    width: 90%;
    max-width: 440px;
    border: 1px solid var(--line-strong);
    box-shadow:
        0 20px 60px rgba(0, 0, 0, 0.15),
        0 2px 0 var(--ink);
    animation: modalIn 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.modal-content::before {
    content: '';
    position: absolute;
    top: 12px; left: 12px; right: 12px; bottom: 12px;
    border: 1px solid var(--line);
    pointer-events: none;
}

.modal-content h3 {
    font-family: 'Playfair Display', Georgia, serif;
    font-weight: 500;
    font-size: 1.6rem;
    margin-bottom: 24px;
    color: var(--ink);
    letter-spacing: 0.01em;
    font-style: italic;
    text-align: center;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--line);
    position: relative;
}

.modal-content h3::after {
    content: '';
    position: absolute;
    bottom: -1px; left: 50%;
    transform: translateX(-50%);
    width: 40px;
    height: 1px;
    background: var(--ink);
}

.form-group {
    margin-bottom: 18px;
}

.form-group label {
    display: block;
    margin-bottom: 8px;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--ink-medium);
    font-weight: 500;
}

.form-group input,
.form-group select {
    width: 100%;
    padding: 12px 14px;
    border: 1px solid var(--line-strong);
    border-radius: 0;
    background: var(--paper-warm);
    color: var(--ink);
    font-family: 'DM Sans', sans-serif;
    font-size: 0.88rem;
    transition: all 0.25s cubic-bezier(0.22, 1, 0.36, 1);
}

.form-group input:focus,
.form-group select:focus {
    outline: none;
    border-color: var(--ink);
    background: var(--paper);
    box-shadow: 0 2px 0 var(--ink);
}

.form-group small {
    display: block;
    margin-top: 6px;
    color: var(--ink-light);
    font-family: 'DM Sans', sans-serif;
    font-size: 0.72rem;
    font-style: italic;
}

.modal-buttons {
    display: flex;
    gap: 12px;
    justify-content: center;
    margin-top: 28px;
}

.modal-buttons .btn {
    min-width: 100px;
}

/* Responsive */
@media (max-width: 900px) {
    /* 移动端：放开滚动，避免固定视口裁掉内容 */
    #app { height: auto; min-height: 100%; overflow-y: auto; }
    .main { overflow: visible; }
    /* 棋盘按屏宽自适应（保持 9:10 比例），并允许整页滚动 */
    /* 棋盘宽度扣掉两侧 padding，保证不横向溢出；禁止双击缩放 */
    #board-container { width: min(calc(100vw - 32px), 420px); height: calc(min(calc(100vw - 32px), 420px) * 10 / 9); }
    #app, #app * { touch-action: manipulation; }
    .main {
        flex-direction: column;
        padding: 16px 20px;
        gap: 20px;
    }

    .side-panel {
        width: 100%;
        flex-direction: row;
        flex-wrap: wrap;
        max-height: 200px;
    }

    .panel-section {
        flex: 1;
        min-width: 220px;
    }

    .messages {
        max-height: 120px;
    }

    .header {
        padding: 16px 20px;
    }

    .header::after {
        left: 20px; right: 20px;
    }

    .input-section {
        padding: 16px 20px 20px;
    }

    .input-section::before {
        left: 20px; right: 20px;
    }
}

@media (max-width: 600px) {
    .header h1 {
        font-size: 1.2rem;
    }

    .header-actions {
        gap: 10px;
    }

    #turn-indicator {
        padding: 6px 14px;
        font-size: 0.7rem;
    }

    .side-panel {
        flex-direction: column;
        max-height: none;
    }

    .panel-section {
        min-width: auto;
    }

    .controls {
        flex-direction: row;
        flex-wrap: wrap;
    }

    .controls .btn {
        flex: 1;
        min-width: 120px;
    }

    .modal-content {
        padding: 24px 20px;
    }
}

/* Animations */
@keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
}

@keyframes fadeInUp {
    from {
        opacity: 0;
        transform: translateY(16px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

@keyframes slideInLeft {
    from {
        opacity: 0;
        transform: translateX(-12px);
    }
    to {
        opacity: 1;
        transform: translateX(0);
    }
}

@keyframes scaleIn {
    from { transform: scaleX(0); }
    to { transform: scaleX(1); }
}

@keyframes spin {
    to { transform: rotate(360deg); }
}

@keyframes modalIn {
    from {
        opacity: 0;
        transform: translateY(20px) scale(0.98);
    }
    to {
        opacity: 1;
        transform: translateY(0) scale(1);
    }
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

/* Game Over Overlay */
.game-over-overlay {
    position: absolute;
    top: 0; left: 0;
    width: 100%; height: 100%;
    background: rgba(250, 250, 248, 0.95);
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    z-index: 50;
    border-radius: 4px;
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
    animation: fadeIn 0.4s ease;
}

.game-over-overlay::before {
    content: '';
    position: absolute;
    top: 24px; left: 24px; right: 24px; bottom: 24px;
    border: 1px solid var(--line-strong);
    pointer-events: none;
}

.game-over-overlay::after {
    content: '';
    position: absolute;
    top: 32px; left: 32px; right: 32px; bottom: 32px;
    border: 1px solid var(--line);
    pointer-events: none;
}

.game-over-overlay h2 {
    font-family: 'Playfair Display', Georgia, serif;
    font-weight: 500;
    font-size: 2.8rem;
    color: var(--ink);
    margin-bottom: 12px;
    letter-spacing: 0.02em;
    font-style: italic;
    position: relative;
    animation: fadeInUp 0.6s cubic-bezier(0.22, 1, 0.36, 1) 0.2s both;
}

.game-over-overlay h2::before,
.game-over-overlay h2::after {
    content: '—';
    display: inline-block;
    margin: 0 16px;
    font-weight: 300;
    color: var(--ink-faint);
    font-style: normal;
    vertical-align: middle;
    font-size: 0.5em;
}

.game-over-overlay p {
    font-family: 'DM Sans', sans-serif;
    color: var(--ink-medium);
    margin-bottom: 32px;
    font-size: 0.95rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    text-align: center;
    padding: 0 20px;
    animation: fadeInUp 0.6s cubic-bezier(0.22, 1, 0.36, 1) 0.3s both;
}

.game-over-overlay button {
    padding: 14px 32px;
    border: 1px solid var(--ink);
    background: var(--ink);
    color: var(--paper);
    cursor: pointer;
    font-family: 'DM Sans', sans-serif;
    font-weight: 500;
    font-size: 0.85rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    transition: all 0.25s cubic-bezier(0.22, 1, 0.36, 1);
    animation: fadeInUp 0.6s cubic-bezier(0.22, 1, 0.36, 1) 0.4s both;
}

.game-over-overlay button::after {
    content: ' ↻';
    display: inline-block;
    margin-left: 8px;
    transition: transform 0.3s ease;
}

.game-over-overlay button:hover {
    background: var(--ink-soft);
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(26, 26, 26, 0.25);
}

.game-over-overlay button:hover::after {
    transform: rotate(-180deg);
}

/* AI Thinking Overlay */
.thinking-overlay {
    display: none;
    position: absolute;
    top: 0; left: 0;
    width: 100%; height: 100%;
    background: rgba(250, 250, 248, 0.9);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    z-index: 200;
    justify-content: center;
    align-items: center;
    animation: fadeIn 0.3s ease;
}

.thinking-overlay.show {
    display: flex;
}

.thinking-content {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 48px;
    flex-wrap: wrap;
    color: var(--ink);
    z-index: 1;
}

.thinking-loading {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
}

/* 井字棋 mini game */
.ttt-wrap {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    padding: 18px;
    border: 1px solid var(--line-strong);
    border-radius: 14px;
    background: rgba(128, 128, 128, 0.06);
    box-shadow: 0 0 24px rgba(0, 0, 0, 0.3);
}

.ttt-title {
    font-family: 'DM Sans', sans-serif;
    font-size: 0.78rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--ink);
    opacity: 0.7;
}

.ttt-board {
    display: grid;
    grid-template-columns: repeat(3, 52px);
    grid-template-rows: repeat(3, 52px);
    gap: 6px;
}

.ttt-cell {
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--line-strong);
    border-radius: 8px;
    background: rgba(128, 128, 128, 0.08);
    font-family: 'Playfair Display', Georgia, serif;
    font-size: 1.9rem;
    font-weight: 700;
    color: var(--ink);
    cursor: pointer;
    user-select: none;
    transition: all 0.15s ease;
}

.ttt-cell:hover:not(.taken):not(.over) {
    border-color: var(--neon-green);
    box-shadow: 0 0 10px var(--neon-green);
}

.ttt-cell.taken { cursor: default; }
.ttt-cell.x { color: var(--neon-green); text-shadow: 0 0 8px var(--neon-green); }
.ttt-cell.o { color: #f87171; text-shadow: 0 0 8px rgba(248, 113, 113, 0.6); }
.ttt-cell.win { background: rgba(248, 113, 113, 0.18); box-shadow: inset 0 0 12px rgba(248, 113, 113, 0.4); }

.ttt-status {
    font-family: 'DM Sans', sans-serif;
    font-size: 0.78rem;
    color: var(--ink);
    opacity: 0.8;
    min-height: 1.2em;
    text-align: center;
}

.ttt-restart {
    font-family: 'DM Sans', sans-serif;
    font-size: 0.75rem;
    letter-spacing: 0.05em;
    padding: 6px 18px;
    border: 1px solid var(--line-strong);
    border-radius: 20px;
    background: transparent;
    color: var(--ink);
    cursor: pointer;
    transition: all 0.15s ease;
}

.ttt-restart:hover {
    border-color: var(--neon-green);
    color: var(--neon-green);
    box-shadow: 0 0 10px var(--neon-green);
}

.thinking-spinner {
    position: relative;
    width: 64px;
    height: 64px;
    margin: 0 auto 28px;
    border-radius: 50%;
}

.thinking-spinner::before,
.thinking-spinner::after {
    content: '';
    position: absolute;
    border-radius: 50%;
    border: 1px solid transparent;
}

.thinking-spinner::before {
    inset: 0;
    border-top-color: var(--ink);
    border-right-color: var(--ink);
    animation: spin 1.2s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}

.thinking-spinner::after {
    inset: 12px;
    border-bottom-color: var(--ink-faint);
    border-left-color: var(--ink-faint);
    animation: spin 0.9s cubic-bezier(0.4, 0, 0.2, 1) infinite reverse;
}

.thinking-text {
    font-family: 'Playfair Display', Georgia, serif;
    font-weight: 500;
    font-size: 1.4rem;
    margin-bottom: 8px;
    color: var(--ink);
    letter-spacing: 0.02em;
    font-style: italic;
}

.thinking-stage {
    font-family: 'DM Sans', sans-serif;
    font-size: 0.8rem;
    color: var(--ink-light);
    letter-spacing: 0.1em;
    text-transform: uppercase;
}

.thinking-stage::after {
    content: '';
    display: inline-block;
    width: 20px;
    text-align: left;
    animation: dots 1.5s steps(4, end) infinite;
}

@keyframes dots {
    0% { content: ''; }
    25% { content: '.'; }
    50% { content: '..'; }
    75% { content: '...'; }
    100% { content: ''; }
}

/* Logs Modal */
.logs-content {
    width: 90%;
    max-width: 780px;
    max-height: 85vh;
    display: flex;
    flex-direction: column;
    background: var(--paper);
    border: 1px solid var(--line-strong);
    padding: 28px;
    box-shadow:
        0 20px 60px rgba(0, 0, 0, 0.15),
        0 2px 0 var(--ink);
    position: relative;
    animation: modalIn 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.logs-content::before {
    content: '';
    position: absolute;
    top: 10px; left: 10px; right: 10px; bottom: 10px;
    border: 1px solid var(--line);
    pointer-events: none;
}

.logs-container {
    flex: 1;
    overflow-y: auto;
    background: var(--paper-warm);
    padding: 20px 24px;
    margin: 16px 0;
    max-height: 65vh;
    border: 1px solid var(--line);
    position: relative;
}

.logs-container::before {
    content: '';
    position: absolute;
    top: 0; left: 50px;
    width: 1px;
    height: 100%;
    background: var(--line-strong);
    opacity: 0.5;
}

.logs-container::-webkit-scrollbar { width: 5px; }
.logs-container::-webkit-scrollbar-track { background: transparent; }
.logs-container::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 2px; }

.log-entry {
    margin-bottom: 20px;
    padding-bottom: 20px;
    border-bottom: 1px dotted var(--line-strong);
    position: relative;
}

.log-entry:last-child {
    margin-bottom: 0;
    padding-bottom: 0;
    border-bottom: none;
}

.log-entry.error {
    border-left: 2px solid var(--danger);
    padding-left: 16px;
}

.log-entry.success {
    border-left: 2px solid var(--success);
    padding-left: 16px;
}

.log-header {
    display: flex;
    justify-content: space-between;
    margin-bottom: 10px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    color: var(--ink-light);
    letter-spacing: 0.03em;
}

.log-user-input {
    font-family: 'Playfair Display', Georgia, serif;
    font-weight: 500;
    font-style: italic;
    color: var(--ink);
    margin-bottom: 12px;
    padding: 8px 0;
    font-size: 0.95rem;
    border-bottom: 1px solid var(--line);
}

.log-user-input::before {
    content: '"';
    font-family: 'Playfair Display', serif;
    font-size: 1.2em;
    color: var(--ink-faint);
    line-height: 0;
    vertical-align: -0.2em;
    margin-right: 2px;
}

.log-user-input::after {
    content: '"';
    font-family: 'Playfair Display', serif;
    font-size: 1.2em;
    color: var(--ink-faint);
    line-height: 0;
    vertical-align: -0.2em;
    margin-left: 2px;
}

.log-section {
    margin-top: 12px;
    padding: 12px 14px;
    background: var(--paper);
    border: 1px solid var(--line);
}

.log-section-title {
    font-family: 'DM Sans', sans-serif;
    font-size: 0.72rem;
    color: var(--ink-medium);
    margin-bottom: 8px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.12em;
}

.log-content {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 180px;
    overflow-y: auto;
    background: var(--paper-warm);
    padding: 10px 12px;
    color: var(--ink-soft);
    border: 1px solid var(--line);
    line-height: 1.6;
}

.log-json {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    color: var(--ink-medium);
}

/* Locked states */
.board-locked {
    pointer-events: none;
    opacity: 0.65;
    filter: grayscale(0.3) brightness(0.85);
}

.input-locked {
    pointer-events: none;
    opacity: 0.45;
    filter: grayscale(0.4);
}

/* Toast */
.toast-container {
    position: absolute;
    top: 80px;
    right: 32px;
    z-index: 300;
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.toast {
    padding: 14px 20px;
    background: var(--ink);
    color: var(--paper);
    font-family: 'DM Sans', sans-serif;
    font-size: 0.85rem;
    border-left: 3px solid var(--paper);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
    animation: toastIn 0.4s cubic-bezier(0.22, 1, 0.36, 1);
    max-width: 320px;
    position: relative;
}

.toast.success {
    border-left-color: var(--success);
}

.toast.error {
    border-left-color: var(--danger);
}

.toast.fade-out {
    animation: toastOut 0.3s ease forwards;
}

@keyframes toastIn {
    from {
        opacity: 0;
        transform: translateX(20px);
    }
    to {
        opacity: 1;
        transform: translateX(0);
    }
}

@keyframes toastOut {
    from {
        opacity: 1;
        transform: translateX(0);
    }
    to {
        opacity: 0;
        transform: translateX(20px);
    }
}

#board-container.coord-insert-mode {
    cursor: crosshair;
}

#board-container.coord-insert-mode .piece {
    cursor: crosshair;
}

#board-container.coord-insert-mode .valid-move-indicator {
    cursor: crosshair;
}

.btn.active {
    background: var(--ink);
    color: var(--paper);
}

.btn.active::before {
    height: 100%;
}

/* Coord dots */
.coord-dot {
    cursor: pointer;
    pointer-events: auto;
    transition: all 0.15s ease;
}

.coord-dot:hover {
    r: 0.35;
    filter: drop-shadow(0 0 4px rgba(34, 197, 94, 0.9));
}

.coord-dot.selected {
    filter: drop-shadow(0 0 8px rgba(34, 197, 94, 1));
}

.coord-dot.in-region {
    fill: #86efac;
}

/* Region rect */
.region-rect {
    pointer-events: none;
    fill: rgba(34, 197, 94, 0.15);
    stroke: #22c55e;
    stroke-width: 0.08;
    filter: drop-shadow(0 0 6px rgba(34, 197, 94, 0.3));
}

/* Coord select button */
.coord-select-btn-wrapper {
    position: relative;
    display: inline-flex;
}

.coord-select-btn-wrapper .btn {
    padding-right: 32px;
}

.btn-toggle-mode {
    position: absolute;
    right: 4px;
    top: 50%;
    transform: translateY(-50%);
    width: 22px;
    height: 22px;
    border: 1px solid var(--ink);
    background: transparent;
    color: var(--ink);
    cursor: pointer;
    font-size: 12px;
    border-radius: 3px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s ease;
    z-index: 2;
    line-height: 1;
    padding: 0;
}

.btn-toggle-mode:hover {
    background: var(--ink);
    color: var(--paper);
}

/* AI Personality Card */
.personality-card {
    position: relative;
    padding: 14px 12px 12px;
    background: var(--paper);
    border: 1px solid var(--line);
    transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.personality-card::before {
    content: '';
    position: absolute;
    top: 6px; left: 6px; right: 6px; bottom: 6px;
    border: 1px solid var(--line);
    pointer-events: none;
    opacity: 0.6;
}

.personality-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 10px;
    padding-bottom: 10px;
    border-bottom: 1px dashed var(--line);
}

.personality-icon {
    font-size: 1.6rem;
    width: 40px;
    height: 40px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--paper-warm);
    border: 1px solid var(--line);
    animation: float 3s ease-in-out infinite;
}

.personality-title {
    display: flex;
    flex-direction: column;
}

.personality-type {
    font-family: 'Playfair Display', Georgia, serif;
    font-size: 1rem;
    font-weight: 500;
    font-style: italic;
    color: var(--ink);
    letter-spacing: 0.02em;
}

.personality-subtitle {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.65rem;
    color: var(--ink-light);
    letter-spacing: 0.1em;
    text-transform: uppercase;
}

.personality-desc {
    font-family: 'DM Sans', sans-serif;
    font-size: 0.78rem;
    color: var(--ink-medium);
    line-height: 1.5;
    margin-bottom: 12px;
    font-style: italic;
    padding-left: 4px;
}

.personality-bars {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 0 4px;
}

.personality-bar {
    display: flex;
    align-items: center;
    gap: 8px;
}

.bar-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.65rem;
    color: var(--ink-light);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    width: 36px;
    flex-shrink: 0;
}

.bar-chars {
    flex: 1;
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 0.7rem;
    letter-spacing: 0.05em;
    transition: all 0.4s ease;
}

.bar-chars.aggressive {
    color: var(--neon-pink);
    text-shadow: 0 0 6px rgba(255, 45, 111, 0.4);
}

.bar-chars.defensive {
    color: var(--neon-cyan);
    text-shadow: 0 0 6px rgba(0, 240, 255, 0.4);
}

.bar-percent {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.65rem;
    color: var(--ink-soft);
    min-width: 32px;
    text-align: right;
    flex-shrink: 0;
}

.personality-card.type-aggressive {
    border-color: rgba(255, 45, 111, 0.3);
}
.personality-card.type-aggressive .personality-icon {
    border-color: rgba(255, 45, 111, 0.4);
    box-shadow: 0 0 12px rgba(255, 45, 111, 0.2);
}

.personality-card.type-defensive {
    border-color: rgba(0, 240, 255, 0.3);
}
.personality-card.type-defensive .personality-icon {
    border-color: rgba(0, 240, 255, 0.4);
    box-shadow: 0 0 12px rgba(0, 240, 255, 0.2);
}

.personality-card.type-random {
    border-color: rgba(255, 215, 0, 0.4);
}
.personality-card.type-random .personality-icon {
    border-color: rgba(255, 215, 0, 0.5);
    box-shadow: 0 0 12px rgba(255, 215, 0, 0.25);
    animation: spin-slow 4s linear infinite;
}

.personality-card.personality-changed {
    animation: personalityPulse 0.6s cubic-bezier(0.22, 1, 0.36, 1);
}

@keyframes personalityPulse {
    0% { transform: scale(1); }
    30% { transform: scale(1.03); box-shadow: 0 4px 20px rgba(0,0,0,0.1); }
    100% { transform: scale(1); }
}

@keyframes float {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-3px); }
}

@keyframes spin-slow {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
}

/* Mechanisms List */
.mechanisms-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 160px;
    overflow-y: auto;
    padding-right: 4px;
}

.mechanisms-list::-webkit-scrollbar { width: 4px; }
.mechanisms-list::-webkit-scrollbar-track { background: transparent; }
.mechanisms-list::-webkit-scrollbar-thumb {
    background: var(--line-strong);
    border-radius: 2px;
}

.mechanism-badge {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 10px;
    background: var(--paper);
    border: 1px solid var(--line);
    font-family: 'DM Sans', sans-serif;
    font-size: 0.78rem;
    color: var(--ink-soft);
    position: relative;
    overflow: hidden;
    animation: mechanismSlideIn 0.4s cubic-bezier(0.22, 1, 0.36, 1) both;
    transition: all 0.25s ease;
}

.mechanism-badge:hover {
    border-color: var(--ink-faint);
    background: var(--paper-warm);
}

.mechanism-badge.leaving {
    animation: mechanismSlideOut 0.3s ease forwards;
}

.mechanism-icon {
    font-size: 1rem;
    flex-shrink: 0;
}

.mechanism-text {
    flex: 1;
    line-height: 1.3;
}

.mechanism-count {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    font-weight: 500;
    padding: 2px 6px;
    background: var(--ink);
    color: var(--paper);
    flex-shrink: 0;
    min-width: 24px;
    text-align: center;
}

.mechanism-stop {
    font-family: 'DM Sans', sans-serif;
    font-size: 0.65rem;
    font-weight: 600;
    padding: 2px 8px;
    border: 1px solid var(--danger);
    background: transparent;
    color: var(--danger);
    cursor: pointer;
    flex-shrink: 0;
    transition: all 0.2s ease;
    letter-spacing: 0.05em;
}

.mechanism-stop:hover {
    background: var(--danger);
    color: var(--paper);
}

.mechanism-badge.type-skip {
    border-left: 3px solid var(--warning);
}
.mechanism-badge.type-skip .mechanism-count {
    background: var(--warning);
    color: var(--paper);
}

.mechanism-badge.type-ai {
    border-left: 3px solid var(--neon-cyan);
}
.mechanism-badge.type-ai .mechanism-count {
    background: var(--neon-cyan);
    color: var(--ink);
}

.mechanism-badge.type-random {
    border-left: 3px solid var(--neon-gold);
}
.mechanism-badge.type-random .mechanism-count {
    background: var(--neon-gold);
    color: var(--ink);
}

.mechanism-badge.type-extra {
    border-left: 3px solid var(--neon-green);
}
.mechanism-badge.type-extra .mechanism-count {
    background: var(--neon-green);
    color: var(--ink);
}

.mechanism-badge.type-limit {
    border-left: 3px solid var(--neon-magenta);
}
.mechanism-badge.type-limit .mechanism-count {
    background: var(--neon-magenta);
    color: var(--paper);
}

@keyframes mechanismSlideIn {
    from {
        opacity: 0;
        transform: translateX(16px);
    }
    to {
        opacity: 1;
        transform: translateX(0);
    }
}

@keyframes mechanismSlideOut {
    from {
        opacity: 1;
        transform: translateX(0);
        max-height: 60px;
        margin-bottom: 6px;
        padding-top: 8px;
        padding-bottom: 8px;
    }
    to {
        opacity: 0;
        transform: translateX(-16px);
        max-height: 0;
        margin-bottom: 0;
        padding-top: 0;
        padding-bottom: 0;
    }
}

/* Board mechanism effects */
#board-container.freeze-effect::before {
    content: '';
    position: absolute;
    top: 0; left: 0;
    width: 100%; height: 100%;
    background: linear-gradient(135deg,
        rgba(0, 240, 255, 0.08) 0%,
        rgba(0, 240, 255, 0.02) 50%,
        rgba(0, 240, 255, 0.08) 100%);
    pointer-events: none;
    z-index: 30;
    border-radius: 4px;
    animation: frostPulse 2s ease-in-out infinite;
}

#board-container.freeze-effect::after {
    content: '❄ 冻结中';
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    font-family: 'Playfair Display', Georgia, serif;
    font-size: 1.5rem;
    font-style: italic;
    color: var(--neon-cyan);
    text-shadow: 0 0 20px rgba(0, 240, 255, 0.5);
    z-index: 31;
    pointer-events: none;
    opacity: 0.8;
    animation: frostText 2s ease-in-out infinite;
}

@keyframes frostPulse {
    0%, 100% { opacity: 0.6; }
    50% { opacity: 1; }
}

@keyframes frostText {
    0%, 100% { opacity: 0.6; transform: translate(-50%, -50%) scale(1); }
    50% { opacity: 0.9; transform: translate(-50%, -50%) scale(1.05); }
}

.piece.random-move {
    animation: randomGlow 0.6s ease;
}

@keyframes randomGlow {
    0% { filter: brightness(1); }
    50% { filter: brightness(1.4) hue-rotate(30deg); }
    100% { filter: brightness(1); }
}

/* ── 内置走子动画（固化自 /chess1 的 AI 扩展，2026-11-01）── */
.piece.anim-moving {
    will-change: left, top;
    filter: drop-shadow(0 6px 10px rgba(0, 0, 0, 0.35));
}

.piece.anim-moving .piece-body {
    transform: scale(1.06);
    transition: transform 0.18s ease;
}

/* 落点轻微回弹，强调「落子」手感 */
.piece.anim-landed .piece-body {
    animation: piece-land-bounce 0.26s ease-out;
}

@keyframes piece-land-bounce {
    0%   { transform: scale(1.10); }
    55%  { transform: scale(0.96); }
    100% { transform: scale(1); }
}

/* 被吃棋子的残影（克隆原元素，淡出缩小） */
.piece.piece-ghost {
    pointer-events: none;
}

@media (prefers-reduced-motion: reduce) {
    .piece.anim-moving,
    .piece.anim-landed .piece-body,
    .piece.piece-ghost {
        animation: none !important;
        transition: none !important;
    }
}

#turn-indicator.ai-control {
    background: linear-gradient(90deg, var(--ink), #333);
}

#turn-indicator.ai-control::after {
    content: '🤖';
    display: inline-block;
    margin-left: 8px;
    font-size: 0.9em;
    animation: robotPulse 1s ease-in-out infinite;
}

@keyframes robotPulse {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.15); }
}

.panel-section:nth-child(3) { animation-delay: 0.32s; }
.panel-section:nth-child(4) { animation-delay: 0.39s; }
.panel-section:nth-child(5) { animation-delay: 0.46s; }
.panel-section:nth-child(6) { animation-delay: 0.53s; }
.panel-section:nth-child(7) { animation-delay: 0.60s; }
.panel-section:nth-child(8) { animation-delay: 0.67s; }

.coord-insert-mode {
    cursor: default;
}

/* Game Objectives Panel */
.objectives-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.objectives-list .empty {
    color: var(--muted-ink);
    font-style: italic;
    font-size: 0.85rem;
    opacity: 0.5;
}

.objective-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 12px;
    border: 1px solid var(--paper-dark);
    border-radius: 3px;
    background: var(--paper);
    transition: all 0.3s ease;
}

.objective-item.enabled {
    border-color: var(--ink);
}

.objective-item.achieved {
    border-color: #b8860b;
    background: linear-gradient(135deg,
        rgba(184, 134, 11, 0.06) 0%,
        rgba(184, 134, 11, 0.02) 100%);
}

.objective-item.disabled {
    opacity: 0.35;
    border-style: dashed;
}

.objective-icon {
    font-size: 1.4rem;
    line-height: 1;
    flex-shrink: 0;
    margin-top: 2px;
}

.objective-item.achieved .objective-icon {
    filter: drop-shadow(0 0 4px rgba(184, 134, 11, 0.5));
}

.objective-content {
    flex: 1;
    min-width: 0;
}

.objective-title {
    font-family: 'Playfair Display', Georgia, serif;
    font-size: 0.92rem;
    font-weight: 600;
    color: var(--ink);
    margin-bottom: 3px;
    letter-spacing: 0.02em;
}

.objective-item.achieved .objective-title {
    color: #8b6914;
}

.objective-item.disabled .objective-title {
    text-decoration: line-through;
    text-decoration-thickness: 1px;
}

.objective-desc {
    font-family: 'DM Sans', -apple-system, sans-serif;
    font-size: 0.78rem;
    color: var(--muted-ink);
    line-height: 1.4;
}

.objective-badge {
    display: inline-block;
    font-size: 0.65rem;
    font-family: 'DM Mono', monospace;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    padding: 2px 6px;
    border-radius: 2px;
    margin-bottom: 4px;
}

.objective-badge.victory {
    background: rgba(184, 134, 11, 0.1);
    color: #8b6914;
}

.objective-badge.draw {
    background: rgba(100, 100, 100, 0.1);
    color: #555;
}

.objective-badge.special {
    background: rgba(88, 166, 135, 0.1);
    color: #3a7a5e;
}

.objective-status {
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-top: 5px;
    padding-top: 5px;
    border-top: 1px solid var(--paper-dark);
}

.objective-item.enabled .objective-status {
    color: var(--accent-green);
}

.objective-item.achieved .objective-status {
    color: #b8860b;
}

.objective-item.disabled .objective-status {
    color: var(--muted-ink);
}

@keyframes objectiveAchieved {
    0% { transform: scale(1); }
    30% { transform: scale(1.05); }
    100% { transform: scale(1); }
}

.objective-item.achieved {
    animation: objectiveAchieved 0.6s ease;
}

/* ── v2.1 · 版本徽标（融入标题版式，无框） ── */
.version-badge {
    margin-left: 13px;
    font-family: 'Playfair Display', Georgia, serif;
    font-style: italic;
    font-weight: 400;
    font-size: 0.7rem;
    letter-spacing: 0.09em;
    color: var(--ink-light);
    vertical-align: middle;
    white-space: nowrap;
}
.version-badge::before {
    content: '';
    display: inline-block;
    width: 4px;
    height: 4px;
    border-radius: 50%;
    background: #b08d2e;
    margin-right: 9px;
    vertical-align: middle;
}
.version-badge b {
    font-weight: 600;
    color: var(--ink-medium);
    letter-spacing: 0.05em;
}

.agent-hud {
    margin-top: 10px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.04em;
    color: var(--ink-light);
    min-height: 15px;
}
.agent-progress {
    margin-top: 14px;
    width: 320px;
    max-width: 76vw;
    height: 3px;
    border-radius: 2px;
    background: var(--line);
    overflow: hidden;
}
.agent-progress-fill {
    height: 100%;
    width: 0%;
    border-radius: 2px;
    background: linear-gradient(90deg, var(--last-move), #c98d4f);
    box-shadow: 0 0 6px rgba(139, 90, 43, 0.35);
    will-change: width;
    transform: translateZ(0);
}
.agent-progress-fill.done { background: var(--success); }
.agent-steps {
    margin-top: 14px;
    max-height: 190px;
    overflow-y: auto;
    text-align: left;
    border-top: 1px solid var(--line);
    padding-top: 10px;
}
.agent-steps::-webkit-scrollbar { width: 3px; }
.agent-steps::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 3px; }
.step-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 3px 0;
    font-size: 12px;
    color: var(--ink-medium);
    animation: fadeInUp 0.25s ease both;
}
.step-row .step-icon { width: 14px; text-align: center; opacity: 0.75; flex: none; }
.step-row .step-text { flex: none; max-width: 46%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-row .step-detail {
    color: var(--ink-faint);
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.step-row.spawn .step-text, .step-row.publish .step-text, .step-row.done .step-text { font-weight: 600; color: var(--ink); }
.step-row.latest .step-text { color: var(--ink); }

.agent-cancel {
    margin-top: 16px;
    padding: 7px 18px;
    font-size: 11px;
    letter-spacing: 0.1em;
    color: var(--ink-light);
    background: transparent;
    border: 1px solid var(--line-strong);
    border-radius: 999px;
    cursor: pointer;
    transition: all 0.2s ease;
}
.agent-cancel:hover { color: var(--ink); border-color: var(--ink-faint); }

/* ── 通用棋子：五子棋/围棋的实心圆子 ── */
.piece.shape-stone .piece-body {
    border: none !important;
    box-shadow: 0 2px 5px rgba(0, 0, 0, 0.35), inset 0 -2px 5px rgba(0, 0, 0, 0.22);
    transition: box-shadow 0.15s ease;
}
.piece.shape-stone .piece-text { display: none; }
.piece.shape-stone:hover { transform: translate(-50%, -50%) scale(1.04); }
.piece.last-moved .piece-body { box-shadow: 0 0 0 2px var(--highlight), 0 2px 10px rgba(0,0,0,0.35); }
`;
    }

    // ═══════════════════════════════════════════════════════
    // 房间门禁：一张桌子一个座位
    // ═══════════════════════════════════════════════════════

    _cid() {
        if (this.__cid) return this.__cid;
        let v = '';
        try { v = localStorage.getItem('chess.cid') || ''; } catch (e) {}
        if (!v) {
            // localStorage 被清过：先认 cookie 里的旧指纹，别把自己变成「新访客」
            try {
                const m = document.cookie.match(/(?:^|;\s*)chess_cid=([^;]+)/);
                if (m) v = decodeURIComponent(m[1]);
            } catch (e) {}
        }
        if (!v) v = 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
        try { localStorage.setItem('chess.cid', v); } catch (e) {}
        this.__cid = v;
        // 同步一份到 cookie：整页请求（刷新、直接敲地址）带不上自定义请求头，
        // 服务端只能靠它认出「还是同一个人」，否则换网/换 IP 就会被自己的租约挡在门外。
        try {
            if (document.cookie.indexOf('chess_cid=' + v) < 0) {
                document.cookie = 'chess_cid=' + v + '; path=/; max-age=' + (15 * 86400) + '; SameSite=Lax';
            }
        } catch (e) {}
        return v;
    }

    _token() {
        try { return localStorage.getItem('chess.token') || ''; } catch (e) { return ''; }
    }

    _saveToken(v) {
        try {
            if (v) localStorage.setItem('chess.token', v);
            else localStorage.removeItem('chess.token');
        } catch (e) {}
    }

    _roomHeaders(extra) {
        return Object.assign({
            'Content-Type': 'application/json',
            'X-Chess-Cid': this._cid(),
        }, extra || {});
    }

    async _roomApi(path, body, timeoutMs = 12000) {
        try {
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), timeoutMs);
            const resp = await fetch(`${this.apiBase}${path}`, {
                method: 'POST',
                headers: this._roomHeaders(),
                body: JSON.stringify(body || {}),
                signal: ctrl.signal,
            });
            clearTimeout(timer);
            let data = null;
            try { data = await resp.json(); } catch (e) {}
            return { ok: resp.ok && !!(data && data.success), status: resp.status, data };
        } catch (e) {
            return { ok: false, status: 0, data: null, error: e };
        }
    }

    // 单用户发行版：无大厅、无令牌、无座位门禁。打开页面直接进入对局。
    // 保留此方法名以兼容 init() 调用链，恒返回 true，且不启动任何心跳/占座逻辑。
    async _roomGate() {
        this._gatePassed = true;
        return true;
    }

    // 单用户发行版无需心跳/离开钩子（原用于续租房间座位），置空即可。
    _afterEnter() {
        const st = this._roomStatus || {};
        if (st.title) document.title = `${st.title} · NL-FlexChineseChess`;
    }

    _startRoomHeartbeat() {
        if (this._hbTimer) return;
        // 页面在后台时停止心跳：10 分钟没人看，就该把座位让出来
        this._hbTimer = setInterval(async () => {
            if (document.visibilityState === 'hidden') return;
            try {
                const resp = await fetch(`${this.apiBase}/api/room/heartbeat`, {
                    method: 'POST', headers: this._roomHeaders(), body: '{}',
                });
                const d = await resp.json();
                if (d && d.held === false) this._onRoomLost();
            } catch (e) {}
        }, 30000);
    }

    _bindLeaveHooks() {
        if (this._leaveBound) return;
        this._leaveBound = true;
        const bye = () => {
            try {
                const url = `${this.apiBase}/api/room/release`;
                const payload = JSON.stringify({ cid: this._cid(), gen: this._leaseGen || null });
                if (navigator.sendBeacon) {
                    navigator.sendBeacon(url, new Blob([payload], { type: 'application/json' }));
                } else {
                    fetch(url, {
                        method: 'POST', headers: this._roomHeaders(),
                        body: payload, keepalive: true,
                    }).catch(() => {});
                }
            } catch (e) {}
        };
        // pagehide 是移动端唯一可靠的「要走了」信号；beforeunload 兜桌面端
        window.addEventListener('pagehide', bye);
        window.addEventListener('beforeunload', bye);
        // bfcache 回退：离开时已经释放，回来得重新占座
        window.addEventListener('pageshow', (e) => {
            if (e.persisted && this._initialized) this._reclaimRoom().then((ok) => {
                if (!ok) this._onRoomLost();
            });
        });
    }

    async _reclaimRoom() {
        const r = await this._roomApi('/api/room/enter', { token: this._token() });
        if (r.ok) {
            this._roomStatus = (r.data && r.data.status) || this._roomStatus;
            const st = this._roomStatus || {};
            if (st.holder && st.holder.gen) this._leaseGen = st.holder.gen;
            return true;
        }
        return false;
    }

    _onRoomLost() {
        if (this._gateEl) return;
        this._gateBusy({ status: { state: 'occupied', holder: { idle_left: 0 } } });
    }

    // ── 门禁界面 ──
    _gateCss() {
        return [
            '#room-gate{position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;',
            'padding:24px;overflow:auto;background:#060607;color:#e9e3d3;',
            "font-family:'DM Sans','Microsoft YaHei',system-ui,sans-serif;-webkit-font-smoothing:antialiased;}",
            '#room-gate::before{content:"";position:fixed;inset:0;pointer-events:none;',
            'background-image:linear-gradient(rgba(212,175,55,.055) 1px,transparent 1px),',
            'linear-gradient(90deg,rgba(212,175,55,.055) 1px,transparent 1px);background-size:48px 48px;',
            '-webkit-mask-image:radial-gradient(72% 62% at 50% 42%,#000 26%,transparent 100%);',
            'mask-image:radial-gradient(72% 62% at 50% 42%,#000 26%,transparent 100%);}',
            '.rg-box{position:relative;width:min(536px,100%);border:1px solid rgba(212,175,55,.26);',
            'background:rgba(9,9,11,.9);backdrop-filter:blur(8px);padding:34px 30px 28px;}',
            '.rg-box::after{content:"";position:absolute;top:-1px;left:0;width:56px;height:1px;background:#d4af37;}',
            '.rg-kicker{font-size:10px;letter-spacing:.34em;color:#7a705a;text-transform:uppercase;margin-bottom:20px;}',
            '.rg-title{font-size:clamp(26px,6.2vw,38px);font-weight:600;letter-spacing:-.012em;line-height:1.12;margin:0 0 12px;}',
            '.rg-sub{font-size:13px;line-height:1.75;color:#8d8474;margin:0 0 22px;}',
            '.rg-sub b{color:#c9b071;font-weight:500;}',
            '.rg-meta{display:flex;flex-wrap:wrap;gap:10px 22px;padding:14px 0;margin-bottom:22px;',
            'border-top:1px solid rgba(212,175,55,.13);border-bottom:1px solid rgba(212,175,55,.13);',
            "font-family:'JetBrains Mono',monospace;font-size:11px;color:#6b6455;letter-spacing:.02em;}",
            '.rg-meta b{color:#d4af37;font-weight:500;}',
            '.rg-form{display:flex;gap:10px;flex-wrap:wrap;}',
            '.rg-input{flex:1 1 200px;min-width:0;box-sizing:border-box;background:#0b0b0d;',
            'border:1px solid rgba(212,175,55,.28);color:#f4eeda;padding:13px 14px;',
            "font-family:'JetBrains Mono',monospace;font-size:14px;letter-spacing:.08em;outline:none;border-radius:0;}",
            '.rg-input:focus{border-color:#d4af37;box-shadow:0 0 0 1px rgba(212,175,55,.3);}',
            '.rg-input::placeholder{color:#57513f;letter-spacing:.04em;}',
            '.rg-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:13px 22px;',
            'border:1px solid rgba(212,175,55,.42);background:transparent;color:#d4af37;border-radius:0;',
            "font-family:inherit;font-size:13px;letter-spacing:.08em;cursor:pointer;transition:all .18s;text-decoration:none;}",
            '.rg-btn:hover{background:#d4af37;color:#0a0a0a;border-color:#d4af37;}',
            '.rg-btn[disabled]{opacity:.45;cursor:default;}',
            '.rg-btn-solid{background:rgba(212,175,55,.13);}',
            '.rg-btn-ghost{border-color:rgba(255,255,255,.14);color:#9c9484;}',
            '.rg-btn-ghost:hover{background:#e9e3d3;color:#0a0a0a;border-color:#e9e3d3;}',
            '.rg-err{margin-top:14px;padding:10px 12px;border-left:2px solid #b4453e;background:rgba(180,69,62,.1);',
            'color:#e0928c;font-size:12.5px;line-height:1.6;}',
            '.rg-foot{margin-top:20px;font-size:11px;color:#4d483a;letter-spacing:.03em;line-height:1.7;}',
            '@media(max-width:520px){.rg-box{padding:26px 20px 22px;} .rg-form{flex-direction:column;}',
            '.rg-btn{width:100%;}}',
        ].join('');
    }

    _gateShell(inner) {
        this._closeGate();
        // 引导层必须先让位：它盖在门禁之上，而且 10 秒超时会自作主张刷新页面
        try { document.dispatchEvent(new CustomEvent('ready')); } catch (e) {}
        const el = document.createElement('div');
        el.id = 'room-gate';
        el.innerHTML = `<style>${this._gateCss()}</style><div class="rg-box">${inner}</div>`;
        document.body.appendChild(el);
        this._gateEl = el;
        return el;
    }

    _closeGate() {
        if (this._gateTimer) { clearInterval(this._gateTimer); this._gateTimer = null; }
        if (this._gateEl && this._gateEl.parentNode) this._gateEl.parentNode.removeChild(this._gateEl);
        this._gateEl = null;
    }

    _gateToken() {
        const el = this._gateShell(`
            <div class="rg-kicker">restricted · visitor token</div>
            <h2 class="rg-title">内测通道</h2>
            <p class="rg-sub">这间房只对持有访客令牌的同学开放。验证一次会记住 <b>15 天</b>，
               期间刷新页面无需重复输入；你在房间里的一切操作会留下独立日志，便于复现问题。</p>
            <form class="rg-form" id="rg-form" autocomplete="off">
                <input class="rg-input" id="rg-token" type="password" spellcheck="false"
                       placeholder="输入访客令牌" maxlength="64">
                <button class="rg-btn rg-btn-solid" type="submit" id="rg-submit">验证并进入</button>
            </form>
            <div class="rg-err" id="rg-err" style="display:none"></div>
            <div class="rg-foot">令牌由维护者发放 · 遗失请重新索取</div>
        `);
        const form = el.querySelector('#rg-form');
        const input = el.querySelector('#rg-token');
        const err = el.querySelector('#rg-err');
        const submit = el.querySelector('#rg-submit');
        setTimeout(() => { try { input.focus(); } catch (e) {} }, 80);

        form.addEventListener('submit', async (ev) => {
            ev.preventDefault();
            const v = (input.value || '').trim();
            const show = (msg) => { err.style.display = 'block'; err.textContent = msg; };
            if (!v) { show('请输入令牌'); return; }
            submit.disabled = true; submit.textContent = '验证中…';
            const r = await this._roomApi('/api/room/enter', { token: v });
            submit.disabled = false; submit.textContent = '验证并进入';
            if (r.ok) {
                this._saveToken(v);
                this._roomStatus = r.data.status || null;
                this._closeGate();
                this._enterGame();
                return;
            }
            if (r.status === 409) {
                this._saveToken(v);
                this._gateBusy(r.data);
                return;
            }
            show(r.status === 0 ? '网络异常，请重试'
                : ((r.data && r.data.error) || '令牌无效'));
        });
    }

    /** 一人一桌：你在别的房间还占着座。给两个出口——回去那边，或把座位挪过来。 */
    _gateSeatConflict(data) {
        const held = String(data.held_room || '?');
        const el = this._gateShell(`
            <div class="rg-kicker">one seat per player</div>
            <h2 class="rg-title">你已占着另一张桌子</h2>
            <p class="rg-sub">每位玩家同一时刻只能坐一间房，你的座位目前在
               <b>ROOM_0${/^[0-9]$/.test(held) ? held : '?'}</b>。
               可以回那边继续，也可以把座位挪到这间——挪过去之后，旧桌会立即让出。</p>
            <div class="rg-form">
                <a class="rg-btn rg-btn-ghost" href="/chess${held}/">前往 ROOM_0${held}</a>
                <button class="rg-btn rg-btn-solid" id="rg-move">把座位挪到这间</button>
            </div>
            <div class="rg-err" id="rg-err" style="display:none"></div>
            <div class="rg-foot">旧桌若 10 分钟无操作，座位也会自动让出</div>
        `);
        const err = el.querySelector('#rg-err');
        el.querySelector('#rg-move').addEventListener('click', async (ev) => {
            const btn = ev.currentTarget;
            btn.disabled = true; btn.textContent = '挪动中…';
            const r = await this._roomApi('/api/room/enter',
                { token: this._token(), force: true });
            if (r.ok) {
                this._roomStatus = (r.data && r.data.status) || null;
                this._closeGate();
                this._enterGame();
                return;
            }
            btn.disabled = false; btn.textContent = '把座位挪到这间';
            err.style.display = 'block';
            err.textContent = r.status === 0 ? '网络异常，请重试'
                : ((r.data && r.data.error) || '挪动失败，请稍后再试');
        });
    }

    _gateBusy(data) {
        const st = (data && data.status) || {};
        const holder = st.holder || {};
        let left = Math.max(0, Number(holder.idle_left || 0));
        let held = Math.max(0, Number(holder.held_for || 0));
        const el = this._gateShell(`
            <div class="rg-kicker">occupied · seat taken</div>
            <h2 class="rg-title">这间房有人了</h2>
            <p class="rg-sub">一张桌子只有一个座位。里面的人离开、或 <b>10 分钟</b>没有任何操作后，
               房间会自动释放；本页每 5 秒替你试一次，空出来就自动进去。</p>
            <div class="rg-meta">
                <span>房间 <b>${st.title || ('ROOM_' + (st.room || '?'))}</b></span>
                <span>已占用 <b id="rg-held">—</b></span>
                <span>空闲倒计时 <b id="rg-left">—</b></span>
            </div>
            <div class="rg-form">
                <button class="rg-btn" id="rg-retry">立即重试</button>
                <a class="rg-btn rg-btn-ghost" href="/chess/">返回大厅</a>
            </div>
            <div class="rg-foot" id="rg-tip">正在等待房间空出来…</div>
        `);
        const $left = el.querySelector('#rg-left');
        const $held = el.querySelector('#rg-held');
        const $tip = el.querySelector('#rg-tip');

        const fmt = (s) => {
            s = Math.max(0, Math.floor(s));
            const m = Math.floor(s / 60);
            return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
        };
        const paint = () => { $left.textContent = fmt(left); $held.textContent = fmt(held); };
        paint();

        this._gateTimer = setInterval(() => {
            left = Math.max(0, left - 1);
            held += 1;
            paint();
        }, 1000);

        const retry = async () => {
            if (!this._gateEl) return;
            const r = await this._roomApi('/api/room/enter', { token: this._token() });
            if (r.ok) {
                this._roomStatus = r.data.status || null;
                this._closeGate();
                this._enterGame();
                return;
            }
            if (r.status === 401) { this._gateToken(); return; }
            if (r.data && r.data.reason === 'seat_held') { this._gateSeatConflict(r.data); return; }
            const h = (r.data && r.data.status && r.data.status.holder) || {};
            if (h.idle_left !== undefined) left = Math.max(0, Number(h.idle_left));
            if (h.held_for !== undefined) held = Math.max(0, Number(h.held_for));
            paint();
            $tip.textContent = r.status === 0 ? '网络异常，仍在重试…' : '仍在被占用，继续等待…';
        };
        el.querySelector('#rg-retry').addEventListener('click', retry);

        // 每 5 秒自动试一次（房间一空出就进去，不用用户盯着）
        let ticks = 0;
        const auto = setInterval(() => {
            if (!this._gateEl) { clearInterval(auto); return; }
            ticks += 1;
            if (ticks % 5 === 0) retry();
        }, 1000);
    }

    _gateError(title, hint) {
        const el = this._gateShell(`
            <div class="rg-kicker">connection error</div>
            <h2 class="rg-title">${title}</h2>
            <p class="rg-sub">${hint || ''}</p>
            <div class="rg-form">
                <button class="rg-btn" id="rg-retry">重新连接</button>
                <a class="rg-btn rg-btn-ghost" href="/chess/">返回大厅</a>
            </div>
        `);
        el.querySelector('#rg-retry').addEventListener('click', () => location.reload());
    }

    /** 门禁通过后的统一入口：首次初始化 / reload 重建 */
    _enterGame() {
        this._gatePassed = true;
        this._afterEnter();
        if (this._initialized) { location.reload(); return; }
        this.init();
    }

    async init() {
        if (this._initialized) return;

        // 进房先于一切：没座位就不该加载对局，避免「看得到棋盘却动不了」
        if (!this._gatePassed) {
            const passed = await this._roomGate();
            if (!passed) return;
            this._gatePassed = true;
        }

        await this._loadWithRetry();
        this.renderBoard();
        this.renderPieces();
        this.updateTurnIndicator();
        this.updateActiveRules();
        this.updateGameObjectives();
        this.updateAIPersonality();
        this.updateMechanisms();
        this._startStatePoll();     // 实时/活随机对局：跟随服务端推进

        // 载入棋种自带的前端扩展（视觉/交互）
        await this.loadGameExtension();
        this._notifyExtension();

        // 开局若轮到 AI（例如玩家执后手），自动应一手
        if (this._isCurrentTurnAITurn() && !this.aiThinking) {
            this.aiThinking = true;
            setTimeout(async () => {
                try { await this.makeAIMove(); } catch (e) {}
                this.aiThinking = false;
            }, 400);
        }

        // 载入时若对局已结束，补一个胜负弹窗
        if (this.boardState?.game_status?.state === 'ended') {
            setTimeout(() => this.showGameOver(), 500);
        }

        // === 六道 RPG（沙盒遮罩版，无关卡不推进） ===
        try {
            if (window.GameSharedRPG) {
                window.GameSharedRPG.install(this, {
                    isSandbox: true,
                    rerender: async (instance) => {
                    instance.clearSelection();
                    instance.renderBoard();
                    instance.renderPieces();
                    instance.updateTurnIndicator();
                    instance.updateActiveRules();
                    instance.updateGameObjectives();
                    instance.updateAIPersonality();
                    instance.updateMechanisms();
                    },
                });
                // 速览版无「大地图」页：移除胜负弹窗中的「返回大地图」按钮，避免跳转到不存在的 /overworld
                const _origShowGameOver = this.showRpgGameOver.bind(this);
                this.showRpgGameOver = (extras) => {
                    const p = _origShowGameOver(extras);
                    setTimeout(() => {
                        try {
                            const bc = this.shadowRoot && this.shadowRoot.getElementById('board-container');
                            const rb = bc && bc.querySelector('.game-over-overlay .rpg-returnbtn');
                            if (rb) rb.remove();
                        } catch (e) {}
                    }, 0);
                    return p;
                };
                // v2.0：页面刷新不再重置对局，保留玩家进度（换棋种时由新棋种的空局面接管）
                if (typeof this.initRpgEventListeners === 'function') this.initRpgEventListeners();
            }
        } catch (e) { console.warn('[Sandbox RPG] init failed', e); }

        this.bindEvents();
        this.checkApiKey();
        this.loadTokenStats();
        this._initialized = true;
        this.dispatchEvent(new CustomEvent('ready', { bubbles: true, composed: true }));

        // 改造任务跑在后端进程里，页面刷新不会打断它——但轮询随旧页面死了。
        // 启动时探一次状态，把还在跑（或刚结束）的改造重新接回来。
        this._resumeAgent();
    }

    /* 页面刷新后重新接管 AI 改造：
       - 还在跑：重开进度界面并继续轮询，结束后与 sendCommand 同路径收尾；
       - 刚结束（本页打开期间结束的）：补一条结果提示并就地应用新版本；
         结束早于本页加载的，新页面 boot 时已加载到发布后的配置，无需再动。 */
    async _resumeAgent() {
        if (this._agentResumed) return;
        this._agentResumed = true;
        try {
            const resp = await fetch(`${this.apiBase}/api/agent/status`, { cache: 'no-store' });
            const st = await resp.json();

            if (st.state === 'running') {
                this._agentJobId = st.job_id;
                this._agentSteps = [];
                this.showThinking('AI Agent 改造进行中（刷新后已重新接管）…', st.phase || '运行中', true);
                this._startAgentTimer(Math.max(20, st.remaining || 60));
                const result = await this._pollAgent(st.job_id);
                this.hideThinking();
                if (result && result.state === 'done') {
                    this.addMessage(`✅ ${result.summary || '改造完成'}`, 'success');
                    await this.sleep(700);
                    await this.softReload();
                } else if (result && result.state === 'cancelled') {
                    this.addMessage('已取消本次改造，游戏保持原样。', 'info');
                } else {
                    this.addMessage(`⚠️ ${(result && (result.error || result.phase)) || '改造未完成'}`, 'error');
                }
                return;
            }

            if (st.state === 'done' && st.summary && st.finished_at) {
                const finishedAgo = Date.now() / 1000 - st.finished_at;
                if (finishedAgo >= 0 && finishedAgo < 120) {
                    this.addMessage(`✅ ${st.summary}`, 'success');
                    if (st.finished_at * 1000 > this._bootTs - 1500) {
                        // 改造在本页加载前后几秒内才发布：就地应用，别让玩家停在旧版本上
                        await this.sleep(900);
                        await this.softReload();
                    }
                }
            }
        } catch (e) {
            console.warn('接管 AI 改造状态失败', e);
        }
    }

    async _loadWithRetry() {
        // 弱网/服务端短暂不可用时的首屏重试，避免移动端偶发「加载失败黑屏」
        for (let i = 0; i < 4; i++) {
            try {
                await this.loadGameMeta();
                await this.loadConfigs();
                return;
            } catch (e) {
                if (i === 3) throw e;
                await this.sleep(900 + i * 700);
            }
        }
    }

    async loadTokenStats() {
        try {
            const resp = await fetch(`${this.apiBase}/api/token_stats`);
            const data = await resp.json();
            this.shadowRoot.getElementById('token-total').textContent = data.total_tokens.toLocaleString();
            this.shadowRoot.getElementById('token-today').textContent = data.today_tokens.toLocaleString();
            this.shadowRoot.getElementById('token-calls').textContent = data.total_calls.toLocaleString();
            // DeepSeek-V4.1-Flash 官方价目（分高峰/空闲两档），单位为人民币元
            const cur = data.cost_currency === 'CNY' ? '¥' : '$';
            const val = data.estimated_cost_cny ?? data.estimated_cost_usd ?? 0;
            this.shadowRoot.getElementById('token-cost').textContent = `${cur}${Number(val).toFixed(4)}`;
        } catch (e) {
            console.error('Failed to load token stats:', e);
        }
    }

    async loadConfigs() {
        const resp = await fetch(`${this.apiBase}/api/config/all`, { cache: 'no-store' });
        this.configs = await resp.json();
        this.boardState = this.configs.board_state;
        this.uiConfig = this.configs.ui_config;
        // 能力与版本：老服务端没有这些字段，缺省即为「无随机/异步/实时」
        this.capabilities = this.configs.capabilities || {};
        this.actableSides = this.configs.actable_sides || null;
        this.aiSides = this.configs.ai_sides || null;
        this._stateRev = this.configs.rev || 0;
        if (window.AchievementChecker) {
            AchievementChecker.checkAfterConfigLoad(this.configs, this.boardState, 'xiangqi');
        }
    }

    async loadGameExtension() {
        try {
            const [js, css] = await Promise.all([
                fetch(`${this.apiBase}/api/game/web/extension.js`, { cache: 'no-store' }).then(r => r.ok ? r.text() : ''),
                fetch(`${this.apiBase}/api/game/web/extension.css`, { cache: 'no-store' }).then(r => r.ok ? r.text() : ''),
            ]);
            if (css && css.trim()) {
                const st = document.createElement('style');
                st.textContent = css;
                this.shadowRoot.appendChild(st);
            }
            const code = [js, this.uiConfig?.custom_js || ''].filter(x => x && x.trim()).join('\n');
            if (code) {
                const s = document.createElement('script');
                s.textContent = `(function(){var host=document.querySelector('xiangqi-board');\n${code}\n})();`;
                document.head.appendChild(s);
            }
            const ext = window.GameExtension;
            if (ext && typeof ext.onMount === 'function') {
                ext.onMount({
                    host: this,
                    root: this.shadowRoot,
                    api: this.apiBase,
                    get: () => ({ state: this.boardState, configs: this.configs, meta: this.gameMeta }),
                    send: (cmd) => this.sendCommand(cmd),
                });
            }
        } catch (e) {
            console.warn('加载棋种扩展失败', e);
        }
    }

    // ── 将军提示（2026-11-01）──
    // 服务端权威判定：被将军一方的将/帅标红呼吸，解除后自动恢复。
    // 自由度模式下走子不受「必须解将」限制，此提示只做告知、不做拦截。
    _kingTypes() {
        const types = new Set(['general', 'king']);
        for (const key of ['pieces_red', 'pieces_black']) {
            const cfg = this.configs?.[key] || {};
            for (const [t, def] of Object.entries(cfg.pieces || {})) {
                if (def && (def.is_king || t === 'general' || t === 'king')) types.add(t);
            }
            for (const cp of (cfg.custom_pieces || [])) {
                if (cp && cp.is_king && cp.type) types.add(cp.type);
            }
        }
        return types;
    }

    async _updateCheckHighlights() {
        try {
            const r = await fetch(`${this.apiBase}/api/check_status`, { cache: 'no-store' });
            if (!r.ok) return;
            const d = await r.json();
            const ic = (d && d.in_check) || {};
            const root = this.shadowRoot;
            root.querySelectorAll('.piece.in-check').forEach(el => el.classList.remove('in-check'));
            if ((this.boardState?.game_status || {}).state === 'ended') return;
            const kings = this._kingTypes();
            (this.boardState?.pieces || []).forEach(p => {
                if (p.is_alive === false || !ic[p.side] || !kings.has(p.type)) return;
                const el = root.querySelector(`[data-piece-id="${p.id}"]`);
                if (el) el.classList.add('in-check');
            });
        } catch (e) { /* 提示失败不影响对局 */ }
    }

    _notifyExtension() {
        const ext = window.GameExtension;
        if (ext && typeof ext.onState === 'function') {
            try {
                ext.onState(this.boardState, {
                    host: this, root: this.shadowRoot, api: this.apiBase,
                    meta: this.gameMeta, configs: this.configs,
                });
            } catch (e) { console.warn('扩展 onState 异常', e); }
        }
    }

    // ── 内置走子动画（2026-11-01 固化自 /chess1 的 AI 扩展）──
    // 棋子从起点平滑滑到终点（300ms 缓出），被吃棋子残影淡出缩小，落点轻微回弹。
    // 重新开局 / 回退不触发动画；系统「减少动态效果」时直接落位。
    // 若构建自带 GameExtension（onState），由扩展接管，内置动画整体跳过。

    _animReducedMotion() {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }

    _animSnapshot() {
        const cur = {};
        (this.boardState?.pieces || []).forEach(p => {
            if (p.is_alive === false) return;
            cur[p.id] = { x: p.position[0], y: p.position[1] };
        });
        return cur;
    }

    // renderPieces 删除旧棋子元素前调用：把本帧将被吃掉的棋子克隆成残影
    _stashCaptureGhosts(container) {
        this._animGhosts = [];
        if (this._animPrev === undefined) { this._animPrev = null; this._animPrevHistory = -1; }
        const state = this.boardState;
        if (!state || !this._animPrev) return;
        if (window.GameExtension && typeof window.GameExtension.onState === 'function') return;
        const historyLen = (state.move_history || []).length;
        if (historyLen <= this._animPrevHistory || historyLen === 0) return;
        const alive = new Set((state.pieces || []).filter(p => p.is_alive !== false).map(p => p.id));
        Object.keys(this._animPrev).forEach(id => {
            if (alive.has(id)) return;
            const el = container.querySelector(`[data-piece-id="${id}"]`);
            if (!el) return;
            const g = el.cloneNode(true);
            g.classList.add('piece-ghost');
            g.classList.remove('selected', 'capture-target', 'last-moved');
            this._animGhosts.push(g);
        });
    }

    // renderPieces 重建棋子元素后调用：残影淡出 + 移动棋子滑行
    _playMoveAnimation(container) {
        const state = this.boardState;
        if (!state) return;
        if (this._animPrev === undefined) { this._animPrev = null; this._animPrevHistory = -1; }
        const cur = this._animSnapshot();
        const historyLen = (state.move_history || []).length;

        // 首次渲染 / 重新开局 / 回退：不做动画，只记录快照
        if (!this._animPrev || historyLen <= this._animPrevHistory || historyLen === 0) {
            this._animPrev = cur;
            this._animPrevHistory = historyLen;
            this._animGhosts = [];
            return;
        }

        // 构建自带扩展时交出去（残影也不再生成）
        if (window.GameExtension && typeof window.GameExtension.onState === 'function') {
            this._animPrev = cur;
            this._animPrevHistory = historyLen;
            return;
        }

        const reduced = this._animReducedMotion();

        // 被吃棋子：残影在原位淡出缩小
        (this._animGhosts || []).forEach(g => {
            if (reduced) return;                       // 直接随重建消失，不出残影
            container.appendChild(g);
            g.style.zIndex = '5';
            requestAnimationFrame(() => {
                g.style.transition = 'opacity 0.22s ease, transform 0.22s ease';
                g.style.opacity = '0';
                g.style.transform += ' scale(0.55)';   // 保留原 translate / rotate
            });
            setTimeout(() => g.remove(), 280);
        });
        this._animGhosts = [];

        // 移动的棋子：先放回起点，再滑到终点
        Object.keys(cur).forEach(id => {
            const a = this._animPrev[id], b = cur[id];
            if (!a || (a.x === b.x && a.y === b.y)) return;
            const el = container.querySelector(`[data-piece-id="${id}"]`);
            if (!el) return;
            if (reduced) return;                       // 减少动态效果：直接落位
            const from = this._gridToPercent(a.x, a.y);
            const to = this._gridToPercent(b.x, b.y);
            el.style.transition = 'none';
            el.style.left = from.left + '%';
            el.style.top = from.top + '%';
            el.style.zIndex = '30';
            el.classList.add('anim-moving');
            let startTs = null;
            const step = (ts) => {
                if (startTs === null) startTs = ts;
                const t = Math.min(1, (ts - startTs) / 300);
                const k = 1 - Math.pow(1 - t, 3);
                el.style.left = (from.left + (to.left - from.left) * k) + '%';
                el.style.top = (from.top + (to.top - from.top) * k) + '%';
                if (t < 1) { requestAnimationFrame(step); return; }
                el.style.left = to.left + '%';
                el.style.top = to.top + '%';
                el.style.zIndex = '';
                el.classList.remove('anim-moving');
                el.classList.add('anim-landed');
                setTimeout(() => el.classList.remove('anim-landed'), 260);
            };
            requestAnimationFrame(step);
        });

        this._animPrev = cur;
        this._animPrevHistory = historyLen;
    }

    async loadGameMeta() {
        try {
            const resp = await fetch(`${this.apiBase}/api/game/meta`, { cache: 'no-store' });
            const data = await resp.json();
            this.gameMeta = data.meta || {};
            this.interaction = this.gameMeta.interaction || 'move';
            this._buildStamp = this.gameMeta.build || '';
            this._humanSide = this.gameMeta.human_side || '';
            if (data.capabilities) this.capabilities = data.capabilities;
            if (data.actable_sides) this.actableSides = data.actable_sides;
            if (data.ai_sides) this.aiSides = data.ai_sides;
            if (typeof data.rev === 'number') this._stateRev = data.rev;
            const title = this.gameMeta.name || '无限制棋局';
            document.title = title;
            const t = this.shadowRoot.getElementById('game-title');
            if (t) {
                const badge = t.querySelector('.version-badge');
                if (badge) {
                    // 只替换标题文本节点，保留版本徽标
                    if (t.firstChild && t.firstChild.nodeType === Node.TEXT_NODE) {
                        t.firstChild.textContent = title;
                    } else {
                        t.insertBefore(document.createTextNode(title), badge);
                    }
                } else {
                    t.textContent = title;
                }
            }
        } catch (e) {
            console.warn('加载棋局元信息失败', e);
        }
        try {
            const raw = sessionStorage.getItem('agent_result');
            if (raw) {
                sessionStorage.removeItem('agent_result');
                const r = JSON.parse(raw);
                if (Date.now() - r.ts < 90000 && r.summary) {
                    this.addMessage(`✅ ${r.summary}`, 'success');
                }
            }
        } catch (e) {}
    }
    _getBoardLayoutConfig() {
        const defaults = {
            grid: {
                line_thickness: 0.03,
                show_horizontal: true,
                show_vertical: true,
                river_gap: true
            },
            palace: {
                enabled: true,
                show_diagonals: true,
                line_thickness: 0.03
            },
            river: {
                enabled: true,
                text: '楚 河          漢 界',
                text_size: 0.5,
                gap_ratio: 1.0
            },
            appearance: {
                background_color: '#f0d9b5',
                line_color: '#5c3a1e',
                palace_line_color: null
            },
            layout: {
                viewbox_padding_left: 0.444,
                viewbox_padding_right: 0.444,
                viewbox_padding_top: 0.5,
                viewbox_padding_bottom: 0.5,
                board_size: '90vmin'
            },
            decorations: {
                border: {
                    enabled: false,
                    thickness: 0.1,
                    color: null
                },
                star_points: [],
                custom_lines: [],
                background_pattern: null
            }
        };

        const user = this.configs.board?.appearance || {};

        const merge = (def, usr) => {
            if (!usr || typeof usr !== 'object') return def;
            const result = {};
            for (const key in def) {
                if (def[key] && typeof def[key] === 'object' && !Array.isArray(def[key])) {
                    result[key] = merge(def[key], usr[key]);
                } else {
                    result[key] = (usr[key] !== undefined) ? usr[key] : def[key];
                }
            }
            // 保留默认表里没有的字段，便于扩展脚本/新棋种透传自定义配置
            for (const key in usr) {
                if (!(key in result)) result[key] = usr[key];
            }
            return result;
        };

        try {
            const merged = merge(defaults, user);
            merged.appearance = {
                background_color: user.background_color !== undefined
                    ? user.background_color : defaults.appearance.background_color,
                line_color: user.line_color !== undefined
                    ? user.line_color : defaults.appearance.line_color,
                palace_line_color: user.palace_line_color !== undefined
                    ? user.palace_line_color : defaults.appearance.palace_line_color,
            };
            return merged;
        } catch (e) {
            console.error('Failed to merge board layout config:', e);
            return defaults;
        }
    }

    renderBoard() {
        const container = this.shadowRoot.getElementById('board-container');
        container.innerHTML = '';

        const layoutConfig = this._getBoardLayoutConfig();

        const geometry = this.configs.board?.geometry || {};
        const width = geometry.width || 9;
        const height = geometry.height || 10;

        // 非象棋棋盘：按格数比例自适应尺寸，避免固定 9×10 比例拉伸
        if (!(width === 9 && height === 10)) {
            const bw = Math.max(1, width - 1);
            const bh = Math.max(1, height - 1);
            container.style.height = 'min(72vmin, 520px)';
            container.style.width = `calc(min(72vmin, 520px) * ${(bw / bh).toFixed(4)})`;
        } else {
            container.style.height = '';
            container.style.width = '';
        }
        const riverLine = geometry.river_line || 5;
        const palace = geometry.palace || {
            black: { top_left: [3, 0], bottom_right: [5, 2] },
            red: { top_left: [3, 7], bottom_right: [5, 9] }
        };

        let orientation = geometry.orientation;
        if (!orientation) {
            orientation = width > height ? 'vertical' : 'horizontal';
        }

        const bgColor = layoutConfig.appearance.background_color;
        container.style.backgroundColor = bgColor;
        this.style.setProperty('--board-bg', bgColor);

        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.classList.add('board-grid');
        const padLeft = layoutConfig.layout.viewbox_padding_left;
        const padRight = layoutConfig.layout.viewbox_padding_right;
        const padTop = layoutConfig.layout.viewbox_padding_top;
        const padBottom = layoutConfig.layout.viewbox_padding_bottom;
        const viewBoxWidth = (width - 1) + padLeft + padRight;
        const viewBoxHeight = (height - 1) + padTop + padBottom;
        svg.setAttribute('viewBox', `-${padLeft} -${padTop} ${viewBoxWidth} ${viewBoxHeight}`);
        svg.setAttribute('preserveAspectRatio', 'none');
        svg.style.width = '100%';
        svg.style.height = '100%';

        const lineColor = layoutConfig.appearance.line_color;
        const sw = String(layoutConfig.grid.line_thickness);
        const riverGap = layoutConfig.grid.river_gap;
        const showHorizontal = layoutConfig.grid.show_horizontal;
        const showVertical = layoutConfig.grid.show_vertical;

        if (orientation === 'horizontal') {
            if (showHorizontal) {
                for (let i = 0; i < height; i++) {
                    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                    line.setAttribute('x1', '0');
                    line.setAttribute('y1', i);
                    line.setAttribute('x2', width - 1);
                    line.setAttribute('y2', i);
                    line.setAttribute('stroke', lineColor);
                    line.setAttribute('stroke-width', sw);
                    svg.appendChild(line);
                }
            }

            if (showVertical) {
                for (let i = 0; i < width; i++) {
                    const x = i;
                    if (riverGap) {
                        const line1 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                        line1.setAttribute('x1', x);
                        line1.setAttribute('y1', '0');
                        line1.setAttribute('x2', x);
                        line1.setAttribute('y2', riverLine - 1);
                        line1.setAttribute('stroke', lineColor);
                        line1.setAttribute('stroke-width', sw);
                        svg.appendChild(line1);

                        const line2 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                        line2.setAttribute('x1', x);
                        line2.setAttribute('y1', riverLine);
                        line2.setAttribute('x2', x);
                        line2.setAttribute('y2', height - 1);
                        line2.setAttribute('stroke', lineColor);
                        line2.setAttribute('stroke-width', sw);
                        svg.appendChild(line2);
                    } else {
                        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                        line.setAttribute('x1', x);
                        line.setAttribute('y1', '0');
                        line.setAttribute('x2', x);
                        line.setAttribute('y2', height - 1);
                        line.setAttribute('stroke', lineColor);
                        line.setAttribute('stroke-width', sw);
                        svg.appendChild(line);
                    }
                }
            }
        } else {
            if (showVertical) {
                for (let i = 0; i < width; i++) {
                    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                    line.setAttribute('x1', i);
                    line.setAttribute('y1', '0');
                    line.setAttribute('x2', i);
                    line.setAttribute('y2', height - 1);
                    line.setAttribute('stroke', lineColor);
                    line.setAttribute('stroke-width', sw);
                    svg.appendChild(line);
                }
            }

            if (showHorizontal) {
                for (let i = 0; i < height; i++) {
                    const y = i;
                    if (riverGap) {
                        const line1 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                        line1.setAttribute('x1', '0');
                        line1.setAttribute('y1', y);
                        line1.setAttribute('x2', riverLine - 1);
                        line1.setAttribute('y2', y);
                        line1.setAttribute('stroke', lineColor);
                        line1.setAttribute('stroke-width', sw);
                        svg.appendChild(line1);

                        const line2 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                        line2.setAttribute('x1', riverLine);
                        line2.setAttribute('y1', y);
                        line2.setAttribute('x2', width - 1);
                        line2.setAttribute('y2', y);
                        line2.setAttribute('stroke', lineColor);
                        line2.setAttribute('stroke-width', sw);
                        svg.appendChild(line2);
                    } else {
                        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                        line.setAttribute('x1', '0');
                        line.setAttribute('y1', y);
                        line.setAttribute('x2', width - 1);
                        line.setAttribute('y2', y);
                        line.setAttribute('stroke', lineColor);
                        line.setAttribute('stroke-width', sw);
                        svg.appendChild(line);
                    }
                }
            }
        }

        if (layoutConfig.palace.enabled && layoutConfig.palace.show_diagonals) {
            const palaceLines = [];
            if (palace.black) {
                const tl = palace.black.top_left;
                const br = palace.black.bottom_right;
                palaceLines.push([tl, br]);
                palaceLines.push([[br[0], tl[1]], [tl[0], br[1]]]);
            }
            if (palace.red) {
                const tl = palace.red.top_left;
                const br = palace.red.bottom_right;
                palaceLines.push([tl, br]);
                palaceLines.push([[br[0], tl[1]], [tl[0], br[1]]]);
            }
            const palaceLineColor = layoutConfig.appearance.palace_line_color || lineColor;
            const palaceSw = String(layoutConfig.palace.line_thickness);
            palaceLines.forEach(([start, end]) => {
                const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                line.setAttribute('x1', start[0]);
                line.setAttribute('y1', start[1]);
                line.setAttribute('x2', end[0]);
                line.setAttribute('y2', end[1]);
                line.setAttribute('stroke', palaceLineColor);
                line.setAttribute('stroke-width', palaceSw);
                svg.appendChild(line);
            });
        }

        if (layoutConfig.decorations.border.enabled) {
            const borderThickness = layoutConfig.decorations.border.thickness;
            const borderColor = layoutConfig.decorations.border.color || lineColor;
            const borderRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            const halfThick = borderThickness / 2;
            borderRect.setAttribute('x', -halfThick);
            borderRect.setAttribute('y', -halfThick);
            borderRect.setAttribute('width', (width - 1) + borderThickness);
            borderRect.setAttribute('height', (height - 1) + borderThickness);
            borderRect.setAttribute('fill', 'none');
            borderRect.setAttribute('stroke', borderColor);
            borderRect.setAttribute('stroke-width', String(borderThickness));
            svg.appendChild(borderRect);
        }

        // 星位 / 装饰点：appearance.decorations.star_points
        // 支持 [{x,y,radius,color,glow}] 或 [[x,y], ...] 两种写法
        const starPoints = (layoutConfig.decorations && layoutConfig.decorations.star_points) || [];
        if (Array.isArray(starPoints) && starPoints.length) {
            const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            g.setAttribute('class', 'star-points');
            starPoints.forEach((sp) => {
                let sx, sy, radius, color, glow;
                if (Array.isArray(sp)) {
                    sx = sp[0]; sy = sp[1];
                } else if (sp && typeof sp === 'object') {
                    sx = sp.x; sy = sp.y;
                    radius = sp.radius; color = sp.color; glow = sp.glow;
                } else {
                    return;
                }
                sx = Number(sx); sy = Number(sy);
                if (!Number.isFinite(sx) || !Number.isFinite(sy)) return;
                const r = Number.isFinite(Number(radius)) && Number(radius) > 0 ? Number(radius) : 0.14;
                const fill = color || lineColor;
                if (glow) {
                    const halo = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                    halo.setAttribute('cx', sx);
                    halo.setAttribute('cy', sy);
                    halo.setAttribute('r', String(r * 2.6));
                    halo.setAttribute('fill', fill);
                    halo.setAttribute('opacity', '0.18');
                    g.appendChild(halo);
                }
                const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                dot.setAttribute('class', 'star-point');
                dot.setAttribute('cx', sx);
                dot.setAttribute('cy', sy);
                dot.setAttribute('r', String(r));
                dot.setAttribute('fill', fill);
                g.appendChild(dot);
            });
            if (g.childNodes.length) svg.appendChild(g);
        }

        container.appendChild(svg);

        if (layoutConfig.river.enabled) {
            const riverText = document.createElement('div');
            riverText.className = 'river-text';
            riverText.textContent = layoutConfig.river.text;
            riverText.style.color = lineColor;

            if (orientation === 'horizontal') {
                const riverTopPct = 5 + ((riverLine - 0.5) / (height - 1)) * 90;
                riverText.style.top = `${riverTopPct}%`;
                riverText.style.left = '50%';
                riverText.style.transform = 'translate(-50%, -50%)';
                riverText.style.writingMode = 'horizontal-tb';
            } else {
                const riverLeftPct = 5 + ((riverLine - 0.5) / (width - 1)) * 90;
                riverText.style.left = `${riverLeftPct}%`;
                riverText.style.top = '50%';
                riverText.style.transform = 'translate(-50%, -50%)';
                riverText.style.writingMode = 'vertical-rl';
            }

            container.appendChild(riverText);
        }

        this.applyUiConfig();
    }

    applyUiConfig() {
        const rotation = this.uiConfig?.layout?.rotation || 0;
        const container = this.shadowRoot.getElementById('board-container');
        if (container) {
            container.style.transform = `rotate(${rotation}deg)`;
            container.style.transition = 'transform 0.3s ease';
        }

        this.shadowRoot.querySelectorAll('.piece-text').forEach(el => {
            el.style.transform = `rotate(${-rotation}deg)`;
            el.style.transition = 'transform 0.3s ease';
        });

        const customStyleId = 'custom-ui-css';
        let styleEl = this.shadowRoot.getElementById(customStyleId);
        if (!styleEl) {
            styleEl = document.createElement('style');
            styleEl.id = customStyleId;
            this.shadowRoot.appendChild(styleEl);
        }
        styleEl.textContent = this.uiConfig?.custom_css || '';
    }

    renderPieces() {
        const container = this.shadowRoot.getElementById('board-container');
        this._stashCaptureGhosts(container);
        container.querySelectorAll('.piece').forEach(el => el.remove());

        const piecesTheme = this.uiConfig?.theme?.pieces || {};
        const fontFamily = piecesTheme.font_family || "'KaiTi', serif";

        const pieces = this.boardState?.pieces || [];
        pieces.forEach(piece => {
            if (!piece.is_alive) return;
            this.createPieceElement(piece, piecesTheme, fontFamily);
        });

        if (this.lastMove) {
            const el = container.querySelector(`[data-piece-id="${this.lastMove.piece_id}"]`);
            if (el) el.classList.add('last-moved');
        }

        this._playMoveAnimation(container);

        this._updateCheckHighlights();

        this._notifyExtension();

        if (this.interaction === 'place' && this._isCurrentTurnPlayerControlled() && !this.aiThinking) {
            this.showPlaceHints();
        }
    }

    async showPlaceHints() {
        if (this._hintBusy) return;
        this._hintBusy = true;
        try {
            const resp = await fetch(`${this.apiBase}/api/valid_moves`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ piece_id: null, to: [0, 0] }),
            });
            const data = await resp.json();
            this.validMoves = data.moves || [];
            this.validCaptures = null;   // 落子制棋种不存在「吃子落点」
            this.clearValidMoves();
            // 落点太多（如围棋/五子棋满盘可下）时不画提示，避免满屏绿点
            if (this.validMoves.length <= 80) this.showValidMoves();
        } catch (e) {
        } finally {
            this._hintBusy = false;
        }
    }

    // 网格坐标 → 容器百分比（与 SVG viewBox 的内边距严格一致）
    _gridMetrics() {
        const layout = this._getBoardLayoutConfig();
        const geometry = this.configs.board?.geometry || {};
        const width = geometry.width || 9;
        const height = geometry.height || 10;
        const L = layout.layout || {};
        const padL = L.viewbox_padding_left ?? 0.444;
        const padR = L.viewbox_padding_right ?? 0.444;
        const padT = L.viewbox_padding_top ?? 0.5;
        const padB = L.viewbox_padding_bottom ?? 0.5;
        return { width, height,
                 vbW: (width - 1) + padL + padR,
                 vbH: (height - 1) + padT + padB, padL, padT };
    }

    _gridToPercent(gx, gy) {
        const m = this._gridMetrics();
        return { left: ((gx + m.padL) / m.vbW) * 100, top: ((gy + m.padT) / m.vbH) * 100 };
    }

    createPieceElement(piece, theme, fontFamily) {
        const container = this.shadowRoot.getElementById('board-container');
        const el = document.createElement('div');
        el.className = `piece ${piece.side}`;
        el.dataset.pieceId = piece.id;

        // 外层只定位，内层承载视觉（背景/边框/阴影），规避 iOS Safari
        // 对 transform 定位元素绘制方形 box-shadow 的缺陷
        const body = document.createElement('div');
        body.className = 'piece-body';
        el.appendChild(body);

        const textSpan = document.createElement('span');
        textSpan.className = 'piece-text';
        textSpan.textContent = piece.name;
        body.appendChild(textSpan);

        const geometry = this.configs.board?.geometry || {};
        const width = geometry.width || 9;
        const height = geometry.height || 10;

        const [x, y] = piece.position;
        const _gp = this._gridToPercent(x, y);
        const leftPct = _gp.left;
        const topPct = _gp.top;

        const shape = theme.shape || 'disc';
        if (shape === 'stone') el.classList.add('shape-stone');
        if (theme.show_text === false || (shape === 'stone' && !piece.name)) {
            textSpan.style.display = 'none';
        }
        if (!(width === 9 && height === 10) || theme.size) {
            const scale = Number(theme.size) || 0.88;
            const _mm = this._gridMetrics();
            el.style.width = `${(100 / _mm.vbW) * scale}%`;
            el.style.height = `${(100 / _mm.vbH) * scale}%`;
        }
        if (shape === 'stone') {
            el.style.fontSize = '0';
            const t = this.uiConfig?.theme?.pieces || {};
            // 约定：black 侧恒为深色子，red 侧恒为浅色子（可用 stone_black / stone_red 覆盖）
            if (piece.side === 'red') {
                body.style.background = t.stone_red || '#f7f7f7';
            } else {
                body.style.background = t.stone_black || '#141414';
            }
            body.style.borderColor = 'transparent';
        } else if (piece.side === 'red') {
            body.style.color = theme.red_color || '#cc0000';
            body.style.background = theme.red_bg || '#fff5e6';
            body.style.borderColor = theme.red_color || '#cc0000';
        } else {
            body.style.color = theme.black_color || '#1a1a1a';
            body.style.background = theme.black_bg || '#e6e6e6';
            body.style.borderColor = theme.black_color || '#1a1a1a';
        }

        el.style.left = `${leftPct}%`;
        el.style.top = `${topPct}%`;
        el.style.transform = 'translate(-50%, -50%)';
        el.style.fontFamily = fontFamily;

        if (piece.custom_properties) {
            const cp = piece.custom_properties;
            if (cp.color) body.style.color = cp.color;
            if (cp.bg) body.style.background = cp.bg;
            if (cp.font_size) el.style.fontSize = cp.font_size;
        }

        el.addEventListener('click', (e) => {
            e.stopPropagation();
            if (this.coordInsertMode) {
                const [gx, gy] = piece.position;
                this.onCoordDotClick(gx, gy);
                return;
            }
            this.onPieceClick(piece);
        });

        container.appendChild(el);
    }

    async onPieceClick(piece) {
        if (this.aiThinking) return;
        if (this.boardState?.game_status?.state === 'ended') return;

        if (this.interaction === 'place') {
            // 落子制棋种（黑白棋/五子棋/围棋…）：棋子不可选中，落点提示已由
            // renderPieces → showPlaceHints 自动渲染，点击棋子只做清选
            if (this.selectedPiece) {
                this.clearSelection();
            }
            return;
        }

        if (this.selectedPiece && piece.side !== this.selectedPiece.side) {
            if (this.validMoves.some(m => m[0] === piece.position[0] && m[1] === piece.position[1])) {
                await this.executeMove(this.selectedPiece.id, piece.position);
                return;
            }
        }

        if (!this._isCurrentTurnPlayerControlled()) {
            if (this.selectedPiece) {
                this.clearSelection();
            }
            return;
        }

        if (piece.side !== this.boardState?.current_turn) {
            if (this.selectedPiece) {
                this.clearSelection();
            }
            return;
        }

        this.selectedPiece = piece;
        this.highlightPiece(piece);

        try {
            const resp = await fetch(`${this.apiBase}/api/valid_moves`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ piece_id: piece.id, to: piece.position })
            });
            const data = await resp.json();
            this.validMoves = data.moves || [];
            this.validCaptures = Array.isArray(data.captures) ? data.captures : null;
            this.showValidMoves();
        } catch (e) {
            console.error('获取合法移动失败:', e);
            this._dispatchError('获取合法移动失败', e);
        }
    }

    highlightPiece(piece) {
        this.shadowRoot.querySelectorAll('.piece').forEach(el => {
            el.classList.remove('selected');
        });
        const el = this.shadowRoot.querySelector(`[data-piece-id="${piece.id}"]`);
        if (el) el.classList.add('selected');
    }

    // 目标格是否为吃子：以服务端给的 captures 为准（那是引擎真实判定），
    // 拿不到时退回「目标上站着敌方活子」的本地判断。
    isCaptureTarget(x, y) {
        if (Array.isArray(this.validCaptures)) {
            return this.validCaptures.some(c => c[0] === x && c[1] === y);
        }
        const pieces = this.boardState?.pieces || [];
        const movingSide = this.selectedPiece?.side;
        return pieces.some(p => p.is_alive !== false &&
            p.position[0] === x && p.position[1] === y &&
            p.side !== movingSide);
    }

    // 给「能被吃掉」的棋子罩红光——红点落在深色棋子上不够醒目，
    // 棋子本体也给个信号，玩家一眼就能看到能吃谁。
    _markCaptureTargets() {
        const pieces = this.boardState?.pieces || [];
        const movingSide = this.selectedPiece?.side;
        if (!movingSide) return;
        pieces.forEach(p => {
            if (p.is_alive === false || p.side === movingSide) return;
            const [x, y] = p.position || [];
            if (!this.validMoves.some(m => m[0] === x && m[1] === y)) return;
            const el = this.shadowRoot.querySelector(`[data-piece-id="${p.id}"]`);
            if (el) el.classList.add('capture-target');
        });
    }

    showValidMoves() {
        this.clearValidMoves();
        const container = this.shadowRoot.getElementById('board-container');
        const geometry = this.configs.board?.geometry || {};
        const width = geometry.width || 9;
        const height = geometry.height || 10;
        this.validMoves.forEach(([x, y]) => {
            const isCapture = this.isCaptureTarget(x, y);
            const indicator = document.createElement('div');
            indicator.className = isCapture ? 'valid-move-indicator capture-move-indicator' : 'valid-move-indicator';
            // 同棋子：视觉放内层，规避 iOS Safari 方形阴影缺陷
            const dot = document.createElement('div');
            dot.className = 'valid-move-dot';
            indicator.appendChild(dot);
            const _gp = this._gridToPercent(x, y);
            const leftPct = _gp.left;
            const topPct = _gp.top;
            indicator.style.left = `${leftPct}%`;
            indicator.style.top = `${topPct}%`;
            indicator.style.transform = 'translate(-50%, -50%)';
            indicator.style.cursor = 'pointer';
            indicator.style.pointerEvents = 'auto';

            const highlight = this.uiConfig?.theme?.highlight;
            if (highlight?.valid_move && !isCapture) {
                dot.style.background = highlight.valid_move;
            }
            if (isCapture && highlight?.capture) {
                dot.style.background = highlight.capture;
            }

            indicator.addEventListener('click', (e) => {
                e.stopPropagation();
                if (this.coordInsertMode) {
                    this.onCoordDotClick(x, y);
                    return;
                }
                if (this.selectedPiece && this.interaction !== 'place') {
                    this.executeMove(this.selectedPiece.id, [x, y]);
                    return;
                }
                if (this.interaction === 'place') {
                    this.executeMove(null, [x, y], 'place');
                }
            });

            container.appendChild(indicator);
        });
        this._markCaptureTargets();
    }

    clearValidMoves() {
        this.shadowRoot.querySelectorAll('.valid-move-indicator').forEach(el => el.remove());
        this.shadowRoot.querySelectorAll('.piece.capture-target')
            .forEach(el => el.classList.remove('capture-target'));
    }

    clearSelection() {
        this.selectedPiece = null;
        this.validMoves = [];
        this.validCaptures = null;
        this.shadowRoot.querySelectorAll('.piece').forEach(el => {
            el.classList.remove('selected');
        });
        this.clearValidMoves();
    }

    // ── 时间型对局（实时 / 活随机）：局面可能在玩家没动的时候被服务端推进 ──
    _timeVarying() {
        const caps = this.capabilities || {};
        return Boolean(caps.realtime) || String(caps.randomness || '') === 'live';
    }

    // 拉一次最新局面并就地刷新（不整页重载，不打断正在进行的操作）
    async _resyncBoard(opts = {}) {
        try {
            const r = await fetch(`${this.apiBase}/api/state`, { cache: 'no-store' });
            if (!r.ok) return false;
            const d = await r.json();
            if (!d || !d.state) return false;
            this._stateRev = typeof d.rev === 'number' ? d.rev : this._stateRev;
            if (Array.isArray(d.actable_sides)) this.actableSides = d.actable_sides;
            if (Array.isArray(d.ai_sides)) this.aiSides = d.ai_sides;
            if (d.capabilities) this.capabilities = d.capabilities;
            this.boardState = d.state;
            const hist = this.boardState.move_history || [];
            this.lastMove = hist[hist.length - 1] || this.lastMove;
            if (opts.clearSelection !== false) this.clearSelection();
            this.renderPieces();
            this.updateTurnIndicator();
            this.updateActiveRules();
            this.updateMechanisms();
            this._notifyExtension();
            if (this.boardState.game_status?.state === 'ended') this.showGameOver();
            return true;
        } catch (e) {
            return false;
        }
    }

    // 实时/活随机对局的状态轮询：按 tick_hz 取间隔，只比版本号，变了才重绘
    _startStatePoll() {
        if (this._statePollTimer || !this._timeVarying()) return;
        const hz = Number(this.capabilities?.realtime?.tick_hz || 1);
        const ms = Math.max(800, Math.round(1000 / Math.max(0.2, Math.min(hz, 10))));
        this._statePollTimer = setInterval(async () => {
            if (document.hidden || this.aiThinking) return;
            if ((this.boardState?.game_status || {}).state === 'ended') return;
            try {
                const r = await fetch(`${this.apiBase}/api/state`, { cache: 'no-store' });
                if (!r.ok) return;
                const d = await r.json();
                if (!d || !d.state) return;
                if (typeof d.rev === 'number' && d.rev === this._stateRev) return;
                await this._resyncBoard({ clearSelection: false });
            } catch (e) {}
        }, ms);
    }

    async executeMove(pieceId, toPosition, actionType = 'move') {
        this.clearSelection();
        this.shadowRoot.querySelectorAll('.piece').forEach(el => {
            el.classList.remove('ai-moved');
        });
        this.aiThinking = true;

        try {
            const data = await this._fetchWithRetry(`${this.apiBase}/api/move`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ piece_id: pieceId, to: toPosition, type: actionType })
            }, 2);

            if (data.success) {
                this.boardState = data.board_state;
                if (typeof data.rev === 'number') this._stateRev = data.rev;
                if (Array.isArray(data.actable_sides)) this.actableSides = data.actable_sides;
                if (Array.isArray(data.ai_sides)) this.aiSides = data.ai_sides;
                this._playerActed = true;
                this.lastMove = this.boardState.move_history.slice(-1)[0];
                this.renderPieces();
                this.updateTurnIndicator();
                this.updateActiveRules();
                this.updateGameObjectives();
                this.updateMechanisms();
                this.loadTokenStats();
                if (window.AchievementChecker) {
                    AchievementChecker.checkAfterMove(this.boardState, this.configs, 'xiangqi');
                }

                this._dispatchMoveEvent();

                if (this.boardState.game_status.state === 'ended') {
                    this.showGameOver();
                    this._dispatchGameEndEvent();
                    this.aiThinking = false;
                    return;
                }

                await this.sleep(280);
                // 玩家本回合可能还有剩余行动点（随机 0~3 步），此时回合仍归
                // 玩家，不能无脑驱动 AI；服务端 ai_sides 为空 = 该玩家继续走。
                if (Array.isArray(this.aiSides) && this.aiSides.length) {
                    await this.makeAIMove();
                }
            } else {
                // 实时/活随机下「点了却被拒」多数不是作弊，是局面在查询与点击之间变了：
                // 静默重同步一次，别让玩家看到「非法移动」这种像是自己点错的话。
                const drift = Boolean(data.stale) || this._timeVarying();
                this.addMessage(drift ? '局面已变化，已刷新' : (data.message || '移动失败'), 'error');
                if (drift) await this._resyncBoard();
                if (data.ai_controlled && !this.aiThinking) {
                    this.aiThinking = true;
                    await this.sleep(500);
                    await this.makeAIMove();
                    this.aiThinking = false;
                }
            }
        } catch (e) {
            this.addMessage('网络连接失败，请重试', 'error');
            this._dispatchError('移动失败', e);
        }

        this.aiThinking = false;
    }

    async makeAIMove(depth = 0) {
        // 重入锁：看门狗/落子回调/循环可能并发触发，禁止重复请求。
        // 锁在整个「连走循环」期间持有——旧版在 finally 释放前就递归调用
        // 自己，递归一进来立刻被这把锁挡掉，于是 AI 每回合只走一步，
        // 只有刷新重建对象才能再走一步（2026-11-08 /chess1 实案）。
        if (this._aiMoveInFlight) return;
        this._aiMoveInFlight = true;

        if (typeof this.rpgShowThinking === 'function') this.rpgShowThinking();

        try {
          // 一回合可能有多步行动点：循环走完，锁不中途释放
          for (let _step = 0; _step < 12; _step++) {
            this.addMessage('AI思考中...', 'info');
            const data = await this._fetchWithRetry(`${this.apiBase}/api/ai_move`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            }, 2);
            if (typeof this.rpgHideThinking === 'function') this.rpgHideThinking();

            if (data.success) {
                this.boardState = data.board_state;
                if (typeof data.rev === 'number') this._stateRev = data.rev;
                if (Array.isArray(data.actable_sides)) this.actableSides = data.actable_sides;
                if (Array.isArray(data.ai_sides)) this.aiSides = data.ai_sides;
                this._playerActed = false;   // 异步棋种：AI 应过一次招，等玩家再动
                // place 类棋种（黑白棋等）动作无 piece_id，落子详情以棋谱末条为准
                this.lastMove = (this.boardState.move_history || []).slice(-1)[0] || data.ai_move;
                this.renderPieces();
                this.updateTurnIndicator();
                this.updateActiveRules();
                this.updateGameObjectives();
                this.updateMechanisms();
                    if (window.AchievementChecker) {
                        AchievementChecker.checkAfterMove(this.boardState, this.configs, 'xiangqi');
                    }

                if (this.lastMove?.piece_id) {
                    this.highlightAIMovedPiece(this.lastMove.piece_id);
                }

                const messages = this.shadowRoot.getElementById('ai-messages');
                const lastMsg = messages.lastElementChild;
                if (lastMsg && lastMsg.textContent.includes('思考中')) {
                    lastMsg.remove();
                }

                if (data.ai_move?.captured) {
                    this.addMessage(`AI走了${this.getPieceName(data.ai_move.piece_id)}`, 'info');
                } else if (this.lastMove?.flipped?.length) {
                    this.addMessage(`AI落子，翻转了 ${this.lastMove.flipped.length} 枚棋子`, 'info');
                }

                this._dispatchMoveEvent();

                if (this.boardState.game_status.state === 'ended') {
                    this.showGameOver();
                    this._dispatchGameEndEvent();
                    return;
                }

                // 服务端 ai_sides 是「接下来该谁走」的唯一真源：
                // 非空则继续本循环（锁仍持有），为空立即交还控制权给玩家。
                // 不再用前端本地推断，避免与 should_ai_move 口径失配。
                if (Array.isArray(this.aiSides) && this.aiSides.length) {
                    await this.sleep(600);
                    continue;
                }
                return;
            } else {
                const msg = data.message || 'AI移动失败';
                // 「不是AI回合 / 行动步数已用尽」是回合正常交接的信号，
                // 不是故障：别弹红色错误把界面钉死，按服务端真值对齐后交还玩家。
                const handedOff = msg.includes('不是AI回合') || msg.includes('行动步数已用尽');
                if (!handedOff) this.addMessage(msg, 'error');
                if (Array.isArray(data.ai_sides)) this.aiSides = data.ai_sides;
                if (Array.isArray(data.actable_sides)) this.actableSides = data.actable_sides;
                const _am = this.shadowRoot.getElementById('ai-messages');
                const _lm = _am?.lastElementChild;
                if (_lm && _lm.textContent.includes('思考中')) _lm.remove();
                // 「游戏已结束」不该只留一条错误提示就停住：重新对齐局面并
                // 弹出结算引导，否则玩家点任何棋子都没反应 = 卡住。
                if (msg.includes('结束') || this.boardState?.game_status?.state === 'ended') {
                    await this._resyncBoard({ clearSelection: false });
                }
                return;
            }
          }
        } catch (e) {
            // 移动端 Safari 网络抖动（Load failed）等瞬时失败已被重试吸收，
            // 走到这里说明彻底失败，去掉"思考中"再给友好提示
            const messages = this.shadowRoot.getElementById('ai-messages');
            const lastMsg = messages?.lastElementChild;
            if (lastMsg && lastMsg.textContent.includes('思考中')) lastMsg.remove();
            this.addMessage('AI 连接失败，请稍后重试', 'error');
            if (typeof this.rpgHideThinking === 'function') this.rpgHideThinking();
            this._dispatchError('AI移动失败', e);
        } finally {
            this._aiMoveInFlight = false;
        }
    }

    /**
     * 带自动重试的 fetch：网络层失败（Safari "Load failed"/断网切换）与
     * 5xx 网关错误按指数退避重试；4xx 业务错误不重试直接返回。
     */
    async _fetchWithRetry(url, opts, retries = 2) {
        let lastErr = null;
        // 统一带上门禁指纹：服务端据此判断「还是不是同一个人」
        const req = Object.assign({}, opts || {});
        req.headers = Object.assign({ 'X-Chess-Cid': this._cid() }, (opts && opts.headers) || {});
        for (let attempt = 0; attempt <= retries; attempt++) {
            if (attempt > 0) await this.sleep(800 * attempt);
            try {
                const resp = await fetch(url, req);
                if (resp.status === 409) {
                    const j = await resp.json().catch(() => null);
                    if (j && j.room_lost) {
                        // 座位被释放/被接管：先抢回来，再原样重试一次这次操作
                        if (attempt === 0 && await this._reclaimRoom()) continue;
                        this._onRoomLost();
                    }
                    return j || { success: false, message: '房间已释放' };
                }
                if (!resp.ok) {
                    if (resp.status >= 500 && attempt < retries) {
                        lastErr = new Error(`服务器错误 ${resp.status}`);
                        continue;
                    }
                    return await resp.json().catch(
                        () => ({ success: false, message: `服务器错误 ${resp.status}` }));
                }
                return await resp.json();
            } catch (e) {
                lastErr = e;
            }
        }
        throw lastErr;
    }

    _dispatchMoveEvent() {
        const lastMove = this.boardState?.move_history?.slice(-1)[0];
        const gameStatus = this.boardState?.game_status || {};
        const mover = this.boardState?.current_turn === 'red' ? 'black' : 'red';

        this.dispatchEvent(new CustomEvent('move', {
            bubbles: true,
            composed: true,
            detail: {
                captured: lastMove?.captured || null,
                mover: mover,
                is_check: gameStatus.is_check || false,
                game_ended: gameStatus.state === 'ended',
                winner: gameStatus.winner || null,
                is_five_in_a_row: false,
                go_captures: 0
            }
        }));
    }

    _dispatchGameEndEvent() {
        const gameStatus = this.boardState?.game_status || {};
        this.dispatchEvent(new CustomEvent('gameend', {
            bubbles: true,
            composed: true,
            detail: {
                winner: gameStatus.winner || null,
                win_condition: gameStatus.win_condition || null
            }
        }));
    }

    _dispatchError(message, error) {
        this.dispatchEvent(new CustomEvent('error', {
            bubbles: true,
            composed: true,
            detail: { message, error: error?.message || error }
        }));
    }

    _isCurrentTurnAITurn() {
        // 对局结束或本回合无人可行动（行动点用尽 / 0 步架空）时不要空转，
        // 否则看门狗会一直发 /api/ai_move，界面看起来就是「卡住」。
        if ((this.boardState?.game_status || {}).state === 'ended') return false;
        if (Array.isArray(this.actableSides) && this.actableSides.length === 0) return false;
        // 异步规则下可行动方可能不止「当前回合」：服务端给出 ai_sides，
        // 任一方可动且由 AI 控制，AI 就该接管（否则 AI 侧永远轮不到）。
        const multi = Array.isArray(this.actableSides) && this.actableSides.length > 1;
        if (Array.isArray(this.aiSides)) {
            // 服务端 ai_sides 是唯一真源：为空即「不该 AI 走」，绝不本地推断。
            // 旧版在空数组时回退到 playerSide 推断，与 should_ai_move 失配，
            // 导致玩家回合误发 /api/ai_move 拿到「不是AI回合」（2026-11-08 实案）。
            if (this.aiSides.length === 0) return false;
            // 多方同时可动时，AI 只在玩家动过之后应一次招，避免自走循环
            return multi ? this._playerActed : true;
        }
        const sides = multi ? this.actableSides : [this.boardState?.current_turn || 'red'];
        for (const s of sides) {
            if (this._isSideAIControlled(s)) return true;
        }
        if (multi) return sides.every(s => !this._isSidePlayerControlled(s));
        const currentTurn = this.boardState?.current_turn || 'red';
        if (!this._isSidePlayerControlled(currentTurn)) return true;
        return false;
    }

    _isCurrentTurnAIControlled() {
        return this._isSideAIControlled(this.boardState?.current_turn || 'red');
    }

    _isSideAIControlled(side) {
        const mechanisms = this.boardState?.mechanisms || {};
        const aiControl = mechanisms.ai_control || [];
        return aiControl.some(item => item.side === side && item.remaining !== 0);
    }

    _isCurrentTurnPlayerControlled() {
        return this._isSidePlayerControlled(this.boardState?.current_turn || 'red');
    }

    _isSidePlayerControlled(side) {
        const mechanisms = this.boardState?.mechanisms || {};
        const playerControl = mechanisms.player_control || [];

        if (!playerControl || playerControl.length === 0) {
            // 「控制双方」模式：meta.human_side === 'both' 时两侧都归玩家
            return this.playerSide === 'both' || side === this.playerSide;
        }

        for (const item of playerControl) {
            const ps = item.side;
            if (ps === 'both') return true;
            if (ps === side) return true;
        }

        return false;
    }

    highlightAIMovedPiece(pieceId) {
        this.shadowRoot.querySelectorAll('.piece').forEach(el => {
            el.classList.remove('ai-moved');
        });
        const el = this.shadowRoot.querySelector(`[data-piece-id="${pieceId}"]`);
        if (el) {
            el.classList.add('ai-moved');
        }
    }

    async sendCommand(command) {
        if (!command.trim()) return;

        this.addMessage(`📝 你: ${command}`, 'user');
        this._agentSteps = [];
        this._agentLastSeq = 0;
        this.showThinking('AI Agent 正在接管…', '启动', true);

        try {
            const resp = await fetch(`${this.apiBase}/api/agent/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command }),
            });
            const data = await resp.json();
            if (!data.success) {
                this.hideThinking();
                this.addMessage(`⚠️ ${data.message || '启动失败'}`, 'error');
                return;
            }
            this._addRuleFeedEntry(command);
            this._agentJobId = data.job_id;
            this._startAgentTimer(data.budget);
            const result = await this._pollAgent(data.job_id);
            this.hideThinking();
            this._markRuleFeedDone(
                result && result.state === 'done' ? 'done' :
                result && result.state === 'cancelled' ? 'cancelled' : 'failed');
            if (result && result.state === 'done') {
                const summary = result.summary || '改造完成';
                this.addMessage(`✅ ${summary}`, 'success');
                await this.sleep(700);
                // 就地应用改造结果；整页 reload 在移动端会退回加载界面且体验割裂
                await this.softReload();
            } else if (result && result.state === 'cancelled') {
                this.addMessage('已取消本次改造，游戏保持原样。', 'info');
            } else {
                const msg = (result && (result.error || result.phase)) || '改造未完成';
                this.addMessage(`⚠️ ${msg}`, 'error');
            }
        } catch (e) {
            this.hideThinking();
            this._markRuleFeedDone('failed');
            this.addMessage(`网络错误: ${e.message}`, 'error');
            this._dispatchError('发送指令失败', e);
        }
    }

    /* AI 改造发布后的就地刷新：规则/棋种变更可能触发服务端冷启动，
       先等服务恢复健康，再重拉配置与棋盘并重渲染，全程不整页刷新 */
    async softReload() {
        try {
            await this._waitForServer(15000);
            this.clearSelection();
            this._agentSteps = [];
            this._agentLastSeq = 0;
            await this.loadGameMeta();
            await this.loadConfigs();
            this.renderBoard();
            this.renderPieces();
            this.updateTurnIndicator();
            this.updateActiveRules();
            this.updateGameObjectives();
            this.updateAIPersonality();
            this.updateMechanisms();
            await this.loadGameExtension();
            this._notifyExtension();
            if (this.boardState?.game_status?.state === 'ended') {
                setTimeout(() => this.showGameOver(), 500);
            }
            if (this._isCurrentTurnAITurn() && !this.aiThinking) {
                this.aiThinking = true;
                setTimeout(async () => {
                    try { await this.makeAIMove(); } catch (e) {}
                    this.aiThinking = false;
                }, 400);
            }
        } catch (e) {
            console.warn('就地刷新失败，退回整页刷新', e);
            window.location.reload();
        }
    }

    async _waitForServer(timeoutMs = 15000) {
        const t0 = Date.now();
        while (Date.now() - t0 < timeoutMs) {
            try {
                const r = await fetch(`${this.apiBase}/api/game/meta`, { cache: 'no-store' });
                if (r.ok) return true;
            } catch (e) {}
            await this.sleep(800);
        }
        return false;
    }

    _agentIcon(kind) {
        return ({
            start: '▶', build: '📦', tool: '🔧', spawn: '🧑‍🏭', worker: '⚙️',
            check: '🧪', publish: '🚀', done: '✅', error: '⚠️', warn: '⏳', info: '💬',
        })[kind] || '·';
    }

    _renderAgentEvent(ev) {
        // 增量渲染：只追加新事件，绝不重建已有行 —— 重放 CSS 动画是卡顿的根源
        const box = this.shadowRoot.getElementById('agent-steps');
        if (!box) return;
        if (ev.seq <= (this._agentLastSeq || 0)) return;
        this._agentLastSeq = ev.seq;
        const row = document.createElement('div');
        row.className = `step-row ${ev.kind}`;
        const detail = ev.detail ? `<span class="step-detail">${this._esc(ev.detail).slice(0, 90)}</span>` : '';
        row.innerHTML = `<span class="step-icon">${this._agentIcon(ev.kind)}</span>` +
                        `<span class="step-text">${this._esc(ev.text)}</span>${detail}`;
        const last = box.lastElementChild;
        if (last) last.classList.remove('latest');
        row.classList.add('latest');
        box.appendChild(row);
        while (box.children.length > 8) box.removeChild(box.firstChild);
        box.scrollTop = box.scrollHeight;
    }

    _esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    _updateAgentHud(st) {
        const textEl = this.shadowRoot.getElementById('thinking-text');
        const hud = this.shadowRoot.getElementById('agent-hud');
        this._advanceAgentProgress(st.steps);
        if (textEl && st.phase) {
            const t = st.phase.length > 56 ? st.phase.slice(0, 56) + '…' : st.phase;
            if (textEl.textContent !== t) textEl.textContent = t;
        }
        if (hud) {
            const tk = st.token || {};
            const line = `第 ${st.steps || 1} 步 · Token ${tk.total_tokens || 0} · 缓存命中 ${Math.round((tk.cache_hit_rate || 0) * 100)}%`;
            if (hud.textContent !== line) hud.textContent = line;
        }
    }

    // ── 进度条（2026-11-01 重做）：步骤驱动 + 渐进时间基线 ──
    // 旧版按「最坏情况耗时」线性爬行，越等越绝望。新版：
    //  · 主推进力 = Agent 真实工具步数（每步跳一截，可见的推进感）；
    //  · 时间只做低姿态基线，渐进逼近 55% 且永不触顶（不再暗示「还剩 X 秒」）；
    //  · 预期步数自适应外扩，步数超预期时进度条不回退；
    //  · 完成时由 _pollAgent 置 100%（done 类）。
    _startAgentTimer(budget) {
        this._stopAgentTimer();
        this._agentBudget = budget || 360;
        this._agentT0 = performance.now();
        this._agentLastSec = -1;
        this._agentStepsSeen = 0;
        this._agentExpected = 8;      // 经验值：真实作业 6~13 步，超出自动外扩
        this._agentPct = 0;
        const fill = this.shadowRoot.getElementById('agent-progress-fill');
        if (fill) { fill.classList.remove('done'); fill.style.width = '0%'; }
        const tick = () => {
            if (!this._agentT0) return;
            const el = (performance.now() - this._agentT0) / 1000;
            const timePct = 55 * (1 - Math.exp(-el / (this._agentBudget * 0.5)));
            const pct = Math.min(96, Math.max(this._agentPct || 0, timePct));
            if (fill) fill.style.width = pct.toFixed(2) + '%';
            const sec = Math.floor(el);
            if (sec !== this._agentLastSec) {
                this._agentLastSec = sec;
                const stageEl = this.shadowRoot.getElementById('thinking-stage');
                if (stageEl) {
                    stageEl.textContent = this._agentStepsSeen > 0
                        ? `第 ${this._agentStepsSeen} 步 · 已进行 ${sec} 秒`
                        : `正在解析任务 · 已进行 ${sec} 秒`;
                }
            }
            this._agentRaf = requestAnimationFrame(tick);
        };
        this._agentRaf = requestAnimationFrame(tick);
    }

    _advanceAgentProgress(steps) {
        const n = Number(steps) || 0;
        if (n <= (this._agentStepsSeen || 0)) return;
        this._agentStepsSeen = n;
        while (n >= this._agentExpected) {
            this._agentExpected = Math.round(this._agentExpected * 1.35);
        }
        this._agentPct = Math.min(94, (n / this._agentExpected) * 94);
        const fill = this.shadowRoot.getElementById('agent-progress-fill');
        if (fill) fill.style.width = this._agentPct.toFixed(2) + '%';
    }

    _stopAgentTimer() {
        if (this._agentRaf) { cancelAnimationFrame(this._agentRaf); this._agentRaf = null; }
        this._agentT0 = 0;
    }

    async _pollAgent(jobId) {
        let since = 0;
        let last = null;
        const started = Date.now();
        while (Date.now() - started < 380000) {
            try {
                const r = await fetch(`${this.apiBase}/api/agent/status?since=${since}` +
                    (jobId ? `&job_id=${encodeURIComponent(jobId)}` : ''), { cache: 'no-store' });
                const st = await r.json();
                last = st;
                (st.events || []).forEach(ev => {
                    since = Math.max(since, ev.seq || 0);
                    this._renderAgentEvent(ev);
                });
                this._updateAgentHud(st);
                if (['done', 'error', 'cancelled'].includes(st.state)) {
                    const fill = this.shadowRoot.getElementById('agent-progress-fill');
                    if (fill) {
                        if (st.state === 'done') { fill.style.width = '100%'; fill.classList.add('done'); }
                        this._stopAgentTimer();
                    }
                    return st;
                }
            } catch (e) {
                // 发布瞬间服务可能短暂重启，容忍
                console.warn('轮询 Agent 状态失败', e);
            }
            await this.sleep(650);
        }
        return last;
    }

    // ── 井字棋 mini game（不保存数据）──
    _initTicTacToe() {
        const board = this.shadowRoot.getElementById('ttt-board');
        if (!board) return;
        this.ttt = { cells: Array(9).fill(null), over: false };
        this.ttt.playerFirst = Math.random() < 0.5; // 随机先手
        this.ttt.turn = 'X'; // 玩家 X，AI O
        board.querySelectorAll('.ttt-cell').forEach(cell => {
            cell.addEventListener('click', () => this._tttCellClick(cell));
        });
        this.shadowRoot.getElementById('ttt-restart').addEventListener('click', () => this._tttReset());
        this._tttRender();
        if (!this.ttt.playerFirst) {
            this.shadowRoot.getElementById('ttt-status').textContent = 'AI 先手（O）';
            setTimeout(() => this._tttAiMove(), 500);
        } else {
            this.shadowRoot.getElementById('ttt-status').textContent = '你先手（X）';
        }
    }

    _tttReset() {
        this.ttt = { cells: Array(9).fill(null), over: false };
        this.ttt.playerFirst = Math.random() < 0.5;
        this.ttt.turn = 'X';
        this.shadowRoot.getElementById('ttt-status').textContent = '新一局 · 随机先手';
        this._tttRender();
        if (!this.ttt.playerFirst) setTimeout(() => this._tttAiMove(), 400);
    }

    _tttRender() {
        const board = this.shadowRoot.getElementById('ttt-board');
        if (!board) return;
        board.querySelectorAll('.ttt-cell').forEach((cell, i) => {
            cell.textContent = this.ttt.cells[i] || '';
            cell.classList.toggle('x', this.ttt.cells[i] === 'X');
            cell.classList.toggle('o', this.ttt.cells[i] === 'O');
            cell.classList.toggle('taken', !!this.ttt.cells[i]);
            cell.classList.toggle('over', this.ttt.over);
            cell.classList.remove('win');
        });
        if (this.ttt.winLine) {
            this.ttt.winLine.forEach(i => board.querySelectorAll('.ttt-cell')[i].classList.add('win'));
        }
    }

    _tttCellClick(cell) {
        if (this.ttt.over || this.ttt.turn !== 'X') return;
        const idx = Array.prototype.indexOf.call(this.shadowRoot.querySelectorAll('#ttt-board .ttt-cell'), cell);
        if (this.ttt.cells[idx]) return;
        this.ttt.cells[idx] = 'X';
        this.shadowRoot.getElementById('ttt-status').textContent = 'AI 思考中…';
        this._tttRender();
        if (!this._tttCheckGame()) {
            this.ttt.turn = 'O';
            setTimeout(() => this._tttAiMove(), 450);
        }
    }

    _tttAiMove() {
        if (this.ttt.over) return;
        const empty = this.ttt.cells.map((v, i) => v ? null : i).filter(i => i !== null);
        if (!empty.length) { this._tttCheckGame(); return; }
        let move = null;
        for (const i of empty) {
            const c = this.ttt.cells.slice(); c[i] = 'O';
            if (this._tttWinner(c)) { move = i; break; }
        }
        if (move === null) {
            for (const i of empty) {
                const c = this.ttt.cells.slice(); c[i] = 'X';
                if (this._tttWinner(c)) { move = i; break; }
            }
        }
        if (move === null) move = empty[Math.floor(Math.random() * empty.length)];
        this.ttt.cells[move] = 'O';
        this.ttt.turn = 'X';
        this.shadowRoot.getElementById('ttt-status').textContent = '轮到你（X）';
        this._tttRender();
        this._tttCheckGame();
    }

    _tttWinner(cells) {
        const lines = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]];
        for (const [a,b,c] of lines) {
            if (cells[a] && cells[a] === cells[b] && cells[a] === cells[c]) return cells[a];
        }
        return null;
    }

    _tttCheckGame() {
        const w = this._tttWinner(this.ttt.cells);
        if (w) {
            this.ttt.over = true;
            const lines = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]];
            for (const [a,b,c] of lines) {
                if (this.ttt.cells[a] === w && this.ttt.cells[a] === this.ttt.cells[b] && this.ttt.cells[a] === this.ttt.cells[c]) {
                    this.ttt.winLine = [a,b,c];
                }
            }
            this._tttRender();
            this.shadowRoot.getElementById('ttt-status').textContent = w === 'X' ? '你赢了！点击重新开始再来一局' : 'AI 获胜，点击重新开始再来一局';
            return true;
        }
        if (this.ttt.cells.every(v => v)) {
            this.ttt.over = true;
            this.ttt.winLine = null;
            this._tttRender();
            this.shadowRoot.getElementById('ttt-status').textContent = '平局！点击重新开始再来一局';
            return true;
        }
        return false;
    }

    showThinking(text, stage, agentMode = false) {
        const overlay = this.shadowRoot.getElementById('thinking-overlay');
        const textEl = this.shadowRoot.getElementById('thinking-text');
        const stageEl = this.shadowRoot.getElementById('thinking-stage');

        textEl.textContent = text;
        stageEl.textContent = `阶段: ${stage}`;
        overlay.classList.add('show');

        const steps = this.shadowRoot.getElementById('agent-steps');
        if (steps) { steps.innerHTML = ''; }
        this._agentLastSeq = 0;
        const hud = this.shadowRoot.getElementById('agent-hud');
        if (hud) hud.textContent = '';
        const fill = this.shadowRoot.getElementById('agent-progress-fill');
        if (fill) { fill.style.width = '0%'; }
        if (!agentMode) this._stopAgentTimer();
        const cancelBtn = this.shadowRoot.getElementById('agent-cancel');
        if (cancelBtn) cancelBtn.style.display = agentMode ? '' : 'none';

        this.shadowRoot.getElementById('board-container').classList.add('board-locked');
        this.shadowRoot.getElementById('input-section')?.classList.add('input-locked');
        this.shadowRoot.getElementById('command-input').disabled = true;

        if (agentMode) this._bindAgentCancel();
        else this.pollThinkingStatus();
    }

    _bindAgentCancel() {
        const btn = this.shadowRoot.getElementById('agent-cancel');
        if (!btn || btn._bound) return;
        btn._bound = true;
        btn.addEventListener('click', async () => {
            btn.disabled = true;
            btn.textContent = '正在取消…';
            try { await fetch(`${this.apiBase}/api/agent/cancel`, { method: 'POST' }); } catch (e) {}
            setTimeout(() => { btn.disabled = false; btn.textContent = '取消本次改造'; }, 1500);
        });
    }

    hideThinking() {
        const overlay = this.shadowRoot.getElementById('thinking-overlay');
        this._stopAgentTimer();
        overlay.classList.remove('show');

        this.shadowRoot.getElementById('board-container').classList.remove('board-locked');
        this.shadowRoot.getElementById('input-section')?.classList.remove('input-locked');
        this.shadowRoot.getElementById('command-input').disabled = false;

        if (this.thinkingPollInterval) {
            clearInterval(this.thinkingPollInterval);
            this.thinkingPollInterval = null;
        }
    }

    async pollThinkingStatus() {
        this.thinkingPollInterval = setInterval(async () => {
            try {
                const resp = await fetch(`${this.apiBase}/api/thinking_status`);
                const data = await resp.json();

                if (data.thinking) {
                    const textEl = this.shadowRoot.getElementById('thinking-text');
                    const stageEl = this.shadowRoot.getElementById('thinking-stage');

                    if (data.stage === 'intent') {
                        textEl.textContent = 'ChatAI 正在理解您的意图...';
                        stageEl.textContent = '阶段: 意图解析';
                    } else if (data.stage === 'code') {
                        textEl.textContent = 'CodeAI 正在生成代码...';
                        stageEl.textContent = '阶段: 代码生成';
                    }
                }
            } catch (e) {
                console.error('轮询思考状态失败:', e);
            }
        }, 500);
    }

    async showLogs() {
        const modal = this.shadowRoot.getElementById('logs-modal');
        modal.classList.add('show');
        await this.refreshLogs();
    }

    hideLogs() {
        this.shadowRoot.getElementById('logs-modal').classList.remove('show');
    }

    async refreshLogs() {
        const container = this.shadowRoot.getElementById('logs-container');

        try {
            const resp = await fetch(`${this.apiBase}/api/logs?count=10`);
            const data = await resp.json();

            if (data.logs && data.logs.length > 0) {
                container.innerHTML = data.logs.map((log, index) => this.renderLogEntry(log, index)).join('');
            } else {
                container.innerHTML = '<div class="empty">暂无日志</div>';
            }
        } catch (e) {
            container.innerHTML = `<div class="error">加载日志失败: ${e.message}</div>`;
        }
    }

    renderLogEntry(log, index) {
        const t = log.ts ? new Date(log.ts * 1000).toLocaleTimeString('zh-CN', {hour12:false}) : '';
        const kind = log.kind || 'log';
        const color = kind === 'error' ? 'var(--danger)' : (kind === 'publish' || kind === 'done' ? 'var(--valid-move)' : 'inherit');
        return `
            <div class="log-entry" style="border-left:2px solid ${kind === 'error' ? 'var(--danger)' : 'var(--line-strong)'};padding-left:12px;margin-bottom:10px;">
                <div class="log-header"><span>#${index + 1} ${t}</span><span>${kind}</span></div>
                <div class="log-user-input" style="color:${color}">${this.escapeHtml(log.text || '')}</div>
                ${log.detail ? `<div class="log-content" style="color:var(--ink-light);font-size:11px;white-space:pre-wrap;word-break:break-all;">${this.escapeHtml(String(log.detail)).slice(0, 600)}</div>` : ''}
            </div>`;
    }

    renderCodeDiff(code) {
        if (code.patch_operations_detail && Array.isArray(code.patch_operations_detail) && code.patch_operations_detail.length > 0) {
            return code.patch_operations_detail.map(op => {
                const opStr = op.op || '?';
                const pathStr = op.path || '';
                let valStr = '';
                if (op.value !== undefined) {
                    valStr = JSON.stringify(op.value);
                    if (valStr.length > 200) valStr = valStr.substring(0, 200) + '...';
                }
                return `<span style="color: var(--neon-magenta)">${this.escapeHtml(opStr)}</span> <span style="color: var(--neon-cyan)">${this.escapeHtml(pathStr)}</span>${valStr ? ` → ${this.escapeHtml(valStr)}` : ''}`;
            }).join('<br>');
        }
        if (code.diff_operations_detail && Array.isArray(code.diff_operations_detail) && code.diff_operations_detail.length > 0) {
            return code.diff_operations_detail.map(op => {
                const opStr = op.op || '?';
                const pathStr = op.path || '';
                let valStr = '';
                if (op.value !== undefined) {
                    valStr = JSON.stringify(op.value);
                    if (valStr.length > 200) valStr = valStr.substring(0, 200) + '...';
                }
                return `<span style="color: var(--neon-magenta)">${this.escapeHtml(opStr)}</span> <span style="color: var(--neon-cyan)">${this.escapeHtml(pathStr)}</span>${valStr ? ` → ${this.escapeHtml(valStr)}` : ''}`;
            }).join('<br>');
        }
        if (code.modified_sections_detail && typeof code.modified_sections_detail === 'object' && Object.keys(code.modified_sections_detail).length > 0) {
            return Object.entries(code.modified_sections_detail).map(([section, content]) => {
                let contentStr = typeof content === 'string' ? content : JSON.stringify(content);
                if (contentStr.length > 300) contentStr = contentStr.substring(0, 300) + '...';
                return `<span style="color: var(--neon-cyan)">[${this.escapeHtml(section)}]</span><br>${this.escapeHtml(contentStr)}`;
            }).join('<br><br>');
        }
        if (code.patch_apply_error) return `<span style="color: var(--danger)">Patch应用失败: ${this.escapeHtml(code.patch_apply_error)}</span>`;
        if (code.diff_apply_error) return `<span style="color: var(--danger)">Diff应用失败: ${this.escapeHtml(code.diff_apply_error)}</span>`;
        if (code.parse_error) return `<span style="color: var(--danger)">${this.escapeHtml(code.parse_error)}</span>`;
        if (code.raw_output) {
            let raw = code.raw_output;
            if (raw.length > 500) raw = raw.substring(0, 500) + '...';
            return `<span style="color: var(--text-light); opacity: 0.7">${this.escapeHtml(raw)}</span>`;
        }
        return '<span style="color: var(--text-light); opacity: 0.5">无代码修改片段</span>';
    }

    escapeHtml(text) {
        if (!text) return '';
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    async clearLogs() {
        if (!confirm('确定要清空所有日志吗？')) return;

        try {
            const resp = await fetch(`${this.apiBase}/api/clear_logs`, { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                await this.refreshLogs();
            }
        } catch (e) {
            console.error('清空日志失败:', e);
        }
    }

    addMessage(text, type = 'info') {
        const container = this.shadowRoot.getElementById('ai-messages');
        const msg = document.createElement('div');
        msg.className = `message ${type}`;
        msg.textContent = text;
        container.appendChild(msg);
        container.scrollTop = container.scrollHeight;

        while (container.children.length > 50) {
            container.removeChild(container.firstChild);
        }
    }

    // 把指令返回的「最终结果」写入 AI 面板（含成功 / 拒绝 / 拦截 / 校验等多种状态）
    _appendCommandResult(data) {
        if (!data) return;
        let text = '';
        const fr = data.final_result;
        if (typeof fr === 'string' && fr) {
            text = fr;
        } else if (fr && typeof fr === 'object') {
            text = fr.message || fr.response || fr.reason || '';
            if (!text) text = JSON.stringify(fr);
        }
        if (!text && data.message) text = data.message;
        if (!text) return;
        const type = (data.type === 'applied' || data.type === 'fun' || data.success) ? 'success' : 'error';
        this.addMessage(`🔚 最终结果: ${text}`, type);
    }

    // 重新拉取最新配置并重绘棋盘与棋子，确保 codeAI 等命令最新写入的棋子 type/name 生效
    async refreshPiecesFromLatest() {
        await this.loadConfigs();
        this.renderBoard();
        this.renderPieces();
    }

    updateTurnIndicator() {
        const indicator = this.shadowRoot.getElementById('turn-indicator');
        const turn = this.boardState?.current_turn;
        const state = this.boardState?.game_status;
        const sideName = (s) => (s === 'red' ? '红方' : s === 'black' ? '黑方' : s);

        if (!indicator) {
            // 个别皮肤没有回合条：不能因此跳过 AI 看门狗
            this._maybeAutoAIMove();
            return;
        }
        if (state?.state === 'ended') {
            const winner = state.winner === 'red' ? '红方' : '黑方';
            indicator.textContent = `${winner}获胜!`;
            indicator.style.background = 'var(--success)';
        } else if (Array.isArray(this.actableSides) && this.actableSides.length > 1) {
            // 异步规则：多方同时可动（连动 / 反应着）
            indicator.textContent = `${this.actableSides.map(sideName).join(' · ')}均可行动`;
            indicator.style.background = '#4a3b12';
        } else {
            const ts = this.boardState?.turn_steps;
            let budget = '';
            if (ts && typeof ts === 'object' && ts.side === turn) {
                const left = Math.max(0, (Number(ts.allowed) || 0) - (Number(ts.used) || 0));
                budget = ` · 剩 ${left} 步`;
            }
            const noActor = Array.isArray(this.actableSides) && this.actableSides.length === 0;
            indicator.textContent = (turn === 'red' ? '红方回合' : '黑方回合')
                + (noActor ? ' · 本回合步数已用尽' : budget);
            indicator.style.background = turn === 'red' ? '#8B0000' : '#333';
        }
        this._maybeAutoAIMove();
    }

    // ── AI 回合看门狗：轮到 AI 却无人触发时（请求失败/页面刷新/改造后的
    //    新棋种首手）自动接管，1.2s 后发起 AI 落子；按「局面是否推进」退避，防止打爆接口 ──
    _maybeAutoAIMove() {
        if (this.aiThinking || this._aiAutoTimer) return;
        if ((this.boardState?.game_status || {}).state === 'ended') return;
        if (!this._isCurrentTurnAITurn()) return;
        if ((this._aiAutoFails || 0) >= 3) return;
        this._aiAutoTimer = setTimeout(async () => {
            this._aiAutoTimer = null;
            if (this.aiThinking) return;
            if ((this.boardState?.game_status || {}).state === 'ended') return;
            if (!this._isCurrentTurnAITurn()) { this._aiAutoFails = 0; return; }
            const historyBefore = this.boardState?.move_history?.length || 0;
            this.aiThinking = true;
            try {
                await this.makeAIMove();
            } catch (e) {}
            this.aiThinking = false;
            const advanced = (this.boardState?.move_history?.length || 0) > historyBefore;
            this._aiAutoFails = advanced ? 0 : (this._aiAutoFails || 0) + 1;
            this._maybeAutoAIMove();
        }, 1200);
    }

    updateActiveRules() {
        this._customRules = this.boardState?.game_status?.custom_rules_active || [];
        this._renderActiveRules();
    }

    // ── 玩家指令流（合并进「已激活规则」面板，localStorage 持久化）──
    _feedKey() {
        const room = this.apiBase || location.pathname;
        return 'chess.rulefeed.' + room;
    }

    _loadRuleFeed() {
        if (this._ruleFeed) return this._ruleFeed;
        try { this._ruleFeed = JSON.parse(localStorage.getItem(this._feedKey()) || '[]'); }
        catch (e) { this._ruleFeed = []; }
        if (!Array.isArray(this._ruleFeed)) this._ruleFeed = [];
        return this._ruleFeed;
    }

    _saveRuleFeed() {
        try { localStorage.setItem(this._feedKey(), JSON.stringify(this._ruleFeed.slice(-20))); }
        catch (e) {}
    }

    _addRuleFeedEntry(command) {
        const feed = this._loadRuleFeed();
        const now = new Date();
        const hhmm = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
        feed.push({ text: command, t: hhmm, state: 'running' });
        this._saveRuleFeed();
        this._renderActiveRules();
    }

    _markRuleFeedDone(state) {
        const feed = this._loadRuleFeed();
        let touched = false;
        for (let i = feed.length - 1; i >= 0; i--) {
            if (feed[i].state === 'running') { feed[i].state = state; touched = true; break; }
        }
        if (touched) { this._saveRuleFeed(); this._renderActiveRules(); }
    }

    _renderActiveRules() {
        const container = this.shadowRoot.getElementById('active-rules');
        if (!container) return;

        // ① 玩家指令（最新在后，最多显示 6 条）
        const feed = this._loadRuleFeed().slice(-6);
        const feedHtml = feed.length ? `
            <div class="rules-subhead">你的指令</div>
            ${feed.map(f => `
                <div class="rule-item feed-item state-${f.state || 'done'}">
                    <span class="feed-state" title="${{running: '执行中', done: '已生效', failed: '未完成', cancelled: '已取消'}[f.state] || ''}"></span>
                    <span class="feed-text">${this._esc(f.text)}</span>
                    <span class="feed-time">${this._esc(f.t)}</span>
                </div>
            `).join('')}` : '';

        // ② 机制条目（原「游戏机制」面板，含截停）
        const mechItems = this._mechItems || [];
        const mechHtml = mechItems.length ? `
            <div class="rules-subhead">机制</div>
            ${mechItems.map(item => `
                <div class="mechanism-badge type-${item.type}">
                    <span class="mechanism-icon">${item.icon}</span>
                    <span class="mechanism-text">${item.text}</span>
                    <span class="mechanism-count">${item.count}${item.unit}</span>
                    ${item.canStop ? `<button class="mechanism-stop" data-mechanism="${item.mechanismKey}" data-side="${item.side}">截停</button>` : ''}
                </div>
            `).join('')}` : '';

        // ③ 自定义规则（AI 改造沉淀的规则）
        const rules = this._customRules || [];
        const rulesHtml = rules.length ? `
            <div class="rules-subhead">自定义</div>
            ${rules.map(r => `<div class="rule-item">✨ ${this._esc(r)}</div>`).join('')}` : '';

        // ④ 本回合行动点：回合制解耦后的一等公民状态，不再藏在 mechanisms 里。
        //    AI 自造顶层字段时这里也能显示，避免「规则生效了但侧栏看不见」。
        const ts = this.boardState?.turn_steps;
        let turnHtml = '';
        if (ts && typeof ts === 'object') {
            const left = Math.max(0, (Number(ts.allowed) || 0) - (Number(ts.used) || 0));
            const sideLabel = ts.side === 'red' ? '红方' : ts.side === 'black' ? '黑方' : (ts.side || '?');
            const hist = this.boardState?.move_history || [];
            const lastSkip = [...hist].reverse().find(m => m && m.note && m.note.includes('掷出 0 步'));
            turnHtml = `
                <div class="rules-subhead">本回合</div>
                <div class="rule-item">${this._esc(sideLabel)} · 掷出 ${Number(ts.allowed) || 0} 步 · 剩 ${left} 步</div>
                ${lastSkip ? `<div class="rule-item">⏭️ ${this._esc(lastSkip.note)}</div>` : ''}`;
        }

        if (!feedHtml && !mechHtml && !rulesHtml && !turnHtml) {
            container.innerHTML = '<span class="empty">暂无激活规则 —— 发送指令让 AI 改造游戏</span>';
        } else {
            container.innerHTML = turnHtml + feedHtml + mechHtml + rulesHtml;
        }

        container.querySelectorAll('.mechanism-stop').forEach(btn => {
            btn.addEventListener('click', () => {
                this.stopMechanism(btn.dataset.mechanism, btn.dataset.side);
            });
        });
    }

    updateGameObjectives() {
        const container = this.shadowRoot.getElementById('game-objectives');
        if (!container) return;

        let winConditions = this.configs?.rules?.win_conditions || {};
        // 兼容 Agent 常用的 rules.objectives[] 写法
        if (!Object.keys(winConditions).length) {
            const objs = this.configs?.rules?.objectives || [];
            if (objs.length) {
                winConditions = {};
                objs.forEach((o, i) => {
                    winConditions['obj_' + i] = {
                        display_name: o.title || o.display_name || ('目标 ' + (i + 1)),
                        description: o.description || '',
                        icon: o.icon || '🎯',
                        enabled: o.status !== 'disabled',
                        priority: i,
                    };
                });
            }
        }
        const gameStatus = this.boardState?.game_status;
        const achievedCondition = gameStatus?.win_condition;
        const isGameEnded = gameStatus?.state === 'ended';

        const entries = Object.entries(winConditions);
        if (entries.length === 0) {
            container.innerHTML = '<span class="empty">无游戏目标</span>';
            return;
        }

        const sorted = entries.sort((a, b) => {
            const pa = a[1].priority ?? 999;
            const pb = b[1].priority ?? 999;
            return pa - pb;
        });

        container.innerHTML = sorted.map(([key, cond]) => {
            const enabled = cond.enabled !== false;
            const achieved = isGameEnded && achievedCondition === key;
            const name = cond.display_name || key;
            const icon = cond.icon || '🎯';
            const desc = cond.description || '';
            const category = cond.category || 'victory';

            const statusText = achieved ? '已达成' : (enabled ? '进行中' : '未启用');
            const classes = ['objective-item'];
            if (enabled) classes.push('enabled');
            if (achieved) classes.push('achieved');
            if (!enabled) classes.push('disabled');

            return `
                <div class="${classes.join(' ')}">
                    <div class="objective-icon">${icon}</div>
                    <div class="objective-content">
                        ${category ? `<span class="objective-badge ${category}">${category === 'victory' ? '胜利' : category === 'draw' ? '平局' : '特殊'}</span>` : ''}
                        <div class="objective-title">${name}</div>
                        <div class="objective-desc">${desc}</div>
                        <div class="objective-status">${statusText}</div>
                    </div>
                </div>
            `;
        }).join('');
    }

    updateAIPersonality() {
        const card = this.shadowRoot.getElementById('ai-personality');
        if (!card) return;

        const personality = this.configs?.rules?.ai_difficulty?.personality || {};
        const type = personality.type || 'normal';
        const info = this._personalityInfo[type] || this._personalityInfo.custom;

        card.className = `personality-card type-${type}`;

        this.shadowRoot.getElementById('personality-icon').textContent = info.icon;
        this.shadowRoot.getElementById('personality-type').textContent = info.name;
        this.shadowRoot.getElementById('personality-subtitle').textContent = info.subtitle;
        this.shadowRoot.getElementById('personality-desc').textContent = info.desc;

        let agg = personality.aggressiveness ?? info.agg;
        let def = personality.conservatism ?? info.def;
        if (type === 'custom') {
            agg = personality.aggressiveness ?? 0.5;
            def = personality.conservatism ?? 0.5;
        }

        const totalBars = 10;
        const aggBars = Math.round(agg * totalBars);
        const defBars = Math.round(def * totalBars);

        this.shadowRoot.getElementById('bar-agg').textContent = '█'.repeat(aggBars) + '░'.repeat(totalBars - aggBars);
        this.shadowRoot.getElementById('bar-def').textContent = '█'.repeat(defBars) + '░'.repeat(totalBars - defBars);
        this.shadowRoot.getElementById('bar-agg-pct').textContent = `${Math.round(agg * 100)}%`;
        this.shadowRoot.getElementById('bar-def-pct').textContent = `${Math.round(def * 100)}%`;
    }

    triggerPersonalityChangeAnimation() {
        const card = this.shadowRoot.getElementById('ai-personality');
        if (!card) return;
        card.classList.remove('personality-changed');
        void card.offsetWidth;
        card.classList.add('personality-changed');
    }

    updateMechanisms() {
        // 2026-11-01 合并进「已激活规则」面板：只负责算条目 + 盘面副作用，渲染统一走 _renderActiveRules
        const mechanisms = this.boardState?.mechanisms || {};
        const items = [];

        for (const [key, meta] of Object.entries(this._mechanismMeta)) {
            const list = mechanisms[key] || [];
            for (const item of list) {
                let sideLabel = item.side === 'red' ? '红方' : '黑方';
                if (item.side === 'both') sideLabel = '双方';

                const remaining = item.remaining ?? item.limit ?? 0;
                const reason = item.reason ? ` — ${item.reason}` : '';
                const isInfinite = remaining < 0;

                let isActive = remaining !== 0;
                if (key === 'player_control') {
                    isActive = true;
                }

                items.push({
                    type: meta.type,
                    icon: meta.icon,
                    text: `${sideLabel}${meta.label}${reason}`,
                    count: isInfinite ? '∞' : remaining,
                    unit: isInfinite ? '' : meta.unit,
                    mechanismKey: key,
                    side: item.side,
                    canStop: isActive,
                });
            }
        }

        this._mechItems = items;
        this._renderActiveRules();

        const boardContainer = this.shadowRoot.getElementById('board-container');
        const hasFreeze = (mechanisms.skip_turns || []).length > 0;
        boardContainer.classList.toggle('freeze-effect', hasFreeze);

        const turnIndicator = this.shadowRoot.getElementById('turn-indicator');
        const currentTurn = this.boardState?.current_turn;
        const hasAIControl = (mechanisms.ai_control || []).some(m => m.side === currentTurn);
        turnIndicator.classList.toggle('ai-control', hasAIControl);
    }

    async stopMechanism(mechanismType, side) {
        try {
            const resp = await fetch(`${this.apiBase}/api/stop_mechanism`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mechanism_type: mechanismType, side: side })
            });
            const data = await resp.json();
            if (data.success) {
                this.boardState = data.board_state;
                this.aiThinking = false;
                this.updateMechanisms();
                this.updateTurnIndicator();
                this.addMessage('⏹️ 机制已截停', 'success');
            }
        } catch (e) {
            this.addMessage(`截停失败: ${e.message}`, 'error');
        }
    }

    async refreshMechanisms() {
        try {
            const resp = await fetch(`${this.apiBase}/api/mechanisms`);
            const data = await resp.json();
            if (data.success && data.raw) {
                if (!this.boardState.mechanisms) {
                    this.boardState.mechanisms = {};
                }
                Object.assign(this.boardState.mechanisms, data.raw);
                this.updateMechanisms();
            }
        } catch (e) {
            console.error('Failed to refresh mechanisms:', e);
        }
    }
    async showGameOver() {
        // 沙盒版：通用胜负弹窗 + 两按钮（无下一关）
        return this.showRpgGameOver();
    }

    
    /* ---- 速览版：补齐被移除的 RPG 胶水层方法（纯沙盒精简实现） ---- */
    async rpgRestart() {
        try {
            const resp = await fetch(`${this.apiBase}/api/restart`, { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                this.boardState = data.board_state;
                await this.loadConfigs();
                this.renderBoard();
                this.addMessage(data.message || '对局已重新开始', 'success');
            }
            return data;
        } catch (e) {
            this.addMessage(`重开失败: ${e.message}`, 'error');
            return { success: false };
        }
    }

    async rpgResetConfigsHandler(mode) {
        try {
            const resp = await fetch(`${this.apiBase}/api/reset_configs`, { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                this.addMessage(data.message || '所有配置已重置为默认', 'success');
                // 出厂重置会撤销全部 AI 改造，走完整软刷新保证棋盘/规则/棋子全部同步
                await this.softReload();
            }
            return data;
        } catch (e) {
            this.addMessage(`重置失败: ${e.message}`, 'error');
            return { success: false };
        }
    }

    async rpgResetBattleAndApply(opts) {
        try {
            const resp = await fetch(`${this.apiBase}/api/restart`, { method: 'POST' });
            const data = await resp.json();
            if (data.success) this.boardState = data.board_state;
            return data;
        } catch (e) {
            return { success: false };
        }
    }

    rpgShowThinking(msg, stage) { this.showThinking(msg || 'AI 正在思考...', stage || ''); }
    rpgHideThinking() { this.hideThinking(); }

    /* ---- 指令请求：120s 超时保护，避免长 LLM 调用悬空时无限等待 ---- */
    async _commandFetch(command) {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 120000);
        try {
            return await fetch(`${this.apiBase}/api/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command }),
                signal: ctrl.signal,
            });
        } finally {
            clearTimeout(timer);
        }
    }

    async restart() {
        // 沙盒：RPG 重玩（fallback /api/restart + 全重绘）
        return this.rpgRestart();
    }


    getPieceName(pieceId) {
        const piece = this.boardState?.pieces?.find(p => p.id === pieceId);
        return piece ? piece.name : pieceId;
    }

    sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    async checkApiKey() {
        const resp = await fetch(`${this.apiBase}/api/apikey/status`);
        const data = await resp.json();
        if (!data.has_key) {
            this.showSettings();
            this.addMessage('⚠️ 请先设置DeepSeek API Key', 'error');
        }
    }

    showSettings() {
        this.shadowRoot.getElementById('settings-modal').classList.add('show');
    }

    hideSettings() {
        this.shadowRoot.getElementById('settings-modal').classList.remove('show');
    }

    async saveSettings() {
        const apiKey = this.shadowRoot.getElementById('api-key-input').value;
        const difficulty = this.shadowRoot.getElementById('difficulty-select').value;

        if (apiKey) {
            const resp = await fetch(`${this.apiBase}/api/apikey`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_key: apiKey })
            });
            const data = await resp.json();
            if (data.success) {
                this.addMessage('✅ API Key已设置', 'success');
            }
        }

        const resp2 = await fetch(`${this.apiBase}/api/difficulty`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ difficulty: difficulty })
        });
        const data2 = await resp2.json();
        if (data2.success) {
            this.addMessage(`✅ 难度已设置为: ${difficulty}`, 'success');
        }

        this.hideSettings();
    }

    bindEvents() {
        const input = this.shadowRoot.getElementById('command-input');
        const sendBtn = this.shadowRoot.getElementById('btn-send');

        const sendCommand = () => {
            const cmd = input.value;
            if (cmd.trim()) {
                this.sendCommand(cmd);
                input.value = '';
            }
        };

        sendBtn.addEventListener('click', sendCommand);
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendCommand();
        });

        this.shadowRoot.getElementById('btn-settings').addEventListener('click', () => {
            this.showSettings();
        });
        this.shadowRoot.getElementById('save-settings').addEventListener('click', () => {
            this.saveSettings();
        });
        this.shadowRoot.getElementById('close-settings').addEventListener('click', () => {
            this.hideSettings();
        });

        this.shadowRoot.getElementById('btn-undo').addEventListener('click', async () => {
            const resp = await fetch(`${this.apiBase}/api/undo`, { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                this.boardState = data.board_state;
                this.lastMove = this.boardState.move_history.slice(-1)[0] || null;
                this.clearSelection();
                this.renderPieces();
                this.updateTurnIndicator();
                this.updateGameObjectives();
                this.addMessage(data.message, 'success');
            } else {
                this.addMessage(data.message, 'error');
            }
        });

        this.shadowRoot.getElementById('btn-undo-config').addEventListener('click', async () => {
            const resp = await fetch(`${this.apiBase}/api/undo_config`, { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                this.addMessage(data.message || '已撤回AI修改', 'success');
                await this.softReload();
            } else {
                this.addMessage(data.message || '没有可撤回的修改', 'error');
            }
        });

        this.shadowRoot.getElementById('btn-restart').addEventListener('click', () => {
            this.restart();
        });
        this.shadowRoot.getElementById('btn-reset-configs').addEventListener('click', async () => {
            if (!confirm('确定要重置当前沙盒所有配置为默认值吗？')) return;
            await this.rpgResetConfigsHandler('soft');
        });

        this.shadowRoot.getElementById('btn-logs').addEventListener('click', () => {
            this.showLogs();
        });
        this.shadowRoot.getElementById('close-logs').addEventListener('click', () => {
            this.hideLogs();
        });
        this.shadowRoot.getElementById('btn-refresh-logs').addEventListener('click', () => {
            this.refreshLogs();
        });
        this.shadowRoot.getElementById('btn-clear-logs').addEventListener('click', () => {
            this.clearLogs();
        });

        this.shadowRoot.getElementById('btn-insert-coord').addEventListener('click', () => {
            this.toggleCoordInsertMode();
        });

        this.shadowRoot.getElementById('btn-toggle-coord-mode').addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleSelectMode();
        });

        this._keydownHandler = (e) => {
            if (e.key === 'Escape' && this.coordInsertMode) {
                this.exitCoordInsertMode();
            }
        };
        document.addEventListener('keydown', this._keydownHandler);

        this.shadowRoot.getElementById('board-container').addEventListener('click', (e) => {
            if (this.coordInsertMode) {
                return;
            }

            if (this.selectedPiece && this.interaction !== 'place' && this.validMoves.length > 0) {
                const [gridX, gridY] = this._getGridCoordsFromEvent(e);
                if (gridX === null) return;

                const isValidMove = this.validMoves.some(m => m[0] === gridX && m[1] === gridY);
                if (isValidMove) {
                    this.executeMove(this.selectedPiece.id, [gridX, gridY]);
                    return;
                }
            }

            // 落子类棋种（五子棋/围棋/黑白棋…）：点空格直接落子，
            // 即使残留 selectedPiece 也走 place，避免误发 move 动作
            if (this.interaction === 'place') {
                const [gx, gy] = this._getGridCoordsFromEvent(e);
                if (gx === null) return;
                const occupied = (this.boardState?.pieces || []).some(
                    p => p.is_alive !== false && p.position[0] === gx && p.position[1] === gy);
                if (!occupied) {
                    this.executeMove(null, [gx, gy], 'place');
                }
                return;
            }
            this.clearSelection();
        });
    }

    _getGridCoordsFromEvent(e) {
        const container = this.shadowRoot.getElementById('board-container');
        const rect = container.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const geometry = this.configs.board?.geometry || {};
        const width = geometry.width || 9;
        const height = geometry.height || 10;

        const _m = this._gridMetrics();
        const boardX = (x / rect.width) * 100 / 100 * _m.vbW - _m.padL;
        const boardY = (y / rect.height) * 100 / 100 * _m.vbH - _m.padT;

        const gridX = Math.round(boardX);
        const gridY = Math.round(boardY);

        if (gridX < 0 || gridX >= width || gridY < 0 || gridY >= height) {
            return [null, null];
        }
        return [gridX, gridY];
    }

    toggleCoordInsertMode() {
        if (this.coordInsertMode) {
            this.exitCoordInsertMode();
        } else {
            this.enterCoordInsertMode();
        }
    }

    enterCoordInsertMode() {
        this.coordInsertMode = true;
        this.regionPoints = [];
        this.shadowRoot.getElementById('btn-insert-coord').classList.add('active');
        this.shadowRoot.getElementById('board-container').classList.add('coord-insert-mode');
        this.updateCoordButtonText();
        this.showCoordDots();
    }

    exitCoordInsertMode() {
        this.coordInsertMode = false;
        this.regionPoints = [];
        const btn = this.shadowRoot.getElementById('btn-insert-coord');
        if (btn) btn.classList.remove('active');
        const board = this.shadowRoot.getElementById('board-container');
        if (board) board.classList.remove('coord-insert-mode');
        this.hideCoordDots();
        this.clearRegionSelection();
    }

    updateCoordButtonText() {
        const btn = this.shadowRoot.getElementById('btn-insert-coord');
        if (!btn) return;
        if (this.selectMode === 'region') {
            btn.innerHTML = '🔲 选区域';
        } else {
            btn.innerHTML = '📍 选坐标';
        }
    }

    toggleSelectMode() {
        if (this.selectMode === 'coord') {
            this.selectMode = 'region';
        } else {
            this.selectMode = 'coord';
        }
        this.regionPoints = [];
        this.clearRegionSelection();
        this.updateCoordButtonText();
        if (this.coordInsertMode) {
            this.showCoordDots();
        }
    }

    showCoordDots() {
        this.hideCoordDots();
        const svg = this.shadowRoot.querySelector('#board-container svg.board-grid');
        if (!svg) return;

        const geometry = this.configs.board?.geometry || {};
        const width = geometry.width || 9;
        const height = geometry.height || 10;

        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('id', 'coord-dots-group');
        g.style.pointerEvents = 'all';

        for (let x = 0; x < width; x++) {
            for (let y = 0; y < height; y++) {
                const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                dot.setAttribute('class', 'coord-dot');
                dot.setAttribute('cx', x);
                dot.setAttribute('cy', y);
                dot.setAttribute('r', 0.2);
                dot.setAttribute('fill', '#22c55e');
                dot.setAttribute('data-x', x);
                dot.setAttribute('data-y', y);
                dot.style.cursor = 'pointer';
                dot.style.pointerEvents = 'auto';

                dot.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const gx = parseInt(dot.getAttribute('data-x'));
                    const gy = parseInt(dot.getAttribute('data-y'));
                    this.onCoordDotClick(gx, gy);
                });

                g.appendChild(dot);
                this.coordDots.push(dot);
            }
        }

        svg.appendChild(g);
    }

    hideCoordDots() {
        this.coordDots.forEach(dot => dot.remove());
        this.coordDots = [];
    }

    onCoordDotClick(x, y) {
        if (this.selectMode === 'coord') {
            this.insertCoordToInput(x, y);
            this.exitCoordInsertMode();
        } else if (this.selectMode === 'region') {
            this.onRegionPointClick(x, y);
        }
    }

    onRegionPointClick(x, y) {
        if (this.regionPoints.length === 0) {
            this.regionPoints.push([x, y]);
            this.updateRegionVisual();
        } else if (this.regionPoints.length === 1) {
            const [px, py] = this.regionPoints[0];
            if (px === x && py === y) {
                return;
            }
            this.regionPoints.push([x, y]);
            this.updateRegionVisual();
            this.insertRegionToInput();
            setTimeout(() => {
                this.exitCoordInsertMode();
            }, 500);
        } else {
            this.regionPoints = [[x, y]];
            this.clearRegionSelection();
            this.updateRegionVisual();
        }
    }

    updateRegionVisual() {
        this.clearRegionVisual();
        if (this.regionPoints.length === 0) return;

        const svg = this.shadowRoot.querySelector('#board-container svg.board-grid');
        if (!svg) return;

        this.coordDots.forEach(dot => {
            const dx = parseInt(dot.getAttribute('data-x'));
            const dy = parseInt(dot.getAttribute('data-y'));
            const isSelected = this.regionPoints.some(p => p[0] === dx && p[1] === dy);
            if (isSelected) {
                dot.classList.add('selected');
                dot.setAttribute('r', 0.28);
                dot.setAttribute('fill', '#16a34a');
            } else {
                dot.classList.remove('selected');
                dot.setAttribute('r', 0.2);
                dot.setAttribute('fill', '#22c55e');
            }

            if (this.regionPoints.length === 2) {
                const [p1, p2] = this.regionPoints;
                const minX = Math.min(p1[0], p2[0]);
                const maxX = Math.max(p1[0], p2[0]);
                const minY = Math.min(p1[1], p2[1]);
                const maxY = Math.max(p1[1], p2[1]);
                if (dx >= minX && dx <= maxX && dy >= minY && dy <= maxY) {
                    dot.classList.add('in-region');
                }
            }
        });

        if (this.regionPoints.length === 2) {
            const [p1, p2] = this.regionPoints;
            const minX = Math.min(p1[0], p2[0]);
            const maxX = Math.max(p1[0], p2[0]);
            const minY = Math.min(p1[1], p2[1]);
            const maxY = Math.max(p1[1], p2[1]);

            const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            rect.setAttribute('class', 'region-rect');
            rect.setAttribute('x', minX - 0.4);
            rect.setAttribute('y', minY - 0.4);
            rect.setAttribute('width', (maxX - minX) + 0.8);
            rect.setAttribute('height', (maxY - minY) + 0.8);
            rect.setAttribute('rx', 0.1);
            svg.appendChild(rect);
            this._regionRectEl = rect;
        }
    }

    clearRegionVisual() {
        if (this._regionRectEl) {
            this._regionRectEl.remove();
            this._regionRectEl = null;
        }
        this.coordDots.forEach(dot => {
            dot.classList.remove('selected', 'in-region');
            dot.setAttribute('r', 0.2);
            dot.setAttribute('fill', '#22c55e');
        });
    }

    clearRegionSelection() {
        this.clearRegionVisual();
        this.regionPoints = [];
    }

    insertCoordToInput(x, y) {
        const input = this.shadowRoot.getElementById('command-input');
        const coordStr = `[${x}, ${y}]`;

        const start = input.selectionStart;
        const end = input.selectionEnd;
        const value = input.value;

        input.value = value.substring(0, start) + coordStr + value.substring(end);
        const newPos = start + coordStr.length;
        input.setSelectionRange(newPos, newPos);
        input.focus();
    }

    insertRegionToInput() {
        if (this.regionPoints.length < 2) return;
        const [p1, p2] = this.regionPoints;
        const minX = Math.min(p1[0], p2[0]);
        const maxX = Math.max(p1[0], p2[0]);
        const minY = Math.min(p1[1], p2[1]);
        const maxY = Math.max(p1[1], p2[1]);

        const input = this.shadowRoot.getElementById('command-input');
        const regionStr = `[${minX}, ${minY}]-[${maxX}, ${maxY}]`;

        const start = input.selectionStart;
        const end = input.selectionEnd;
        const value = input.value;

        input.value = value.substring(0, start) + regionStr + value.substring(end);
        const newPos = start + regionStr.length;
        input.setSelectionRange(newPos, newPos);
        input.focus();
    }

    async applyCheatPatch(modifiedConfigs) {
        try {
            // RPG 后端 cheat_use 已通过 /api/command 将配置应用到棋类后端
            // 这里只需重新拉取最新配置并重渲染
            await this.loadConfigs();
            this.renderBoard();
            this.renderPieces();
            this.updateTurnIndicator();
            this.updateActiveRules();
            this.updateGameObjectives();
            this.updateAIPersonality();
            this.updateMechanisms();
            return { success: true };
        } catch (e) {
            this._dispatchError('应用补丁失败', e);
            throw e;
        }
    }

    getBoardSnapshot() {
        return {
            boardState: this.boardState,
            configs: this.configs
        };
    }

    async resetBoard() {
        const resp = await fetch(`${this.apiBase}/api/reset_configs`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            await this.loadConfigs();
            this.lastMove = null;
            this.clearSelection();
            this.renderBoard();
            this.renderPieces();
            this.updateTurnIndicator();
            this.updateActiveRules();
            this.updateGameObjectives();
            this.updateAIPersonality();
            this.updateMechanisms();
        }
        return data;
    }

    destroy() {
        if (this._gameOverTimer) {
            clearTimeout(this._gameOverTimer);
            this._gameOverTimer = null;
        }
        if (this.thinkingPollInterval) {
            clearInterval(this.thinkingPollInterval);
            this.thinkingPollInterval = null;
        }
        if (this._keydownHandler) {
            document.removeEventListener('keydown', this._keydownHandler);
            this._keydownHandler = null;
        }
        this._initialized = false;
    }
}

// ═══════════════════════════════════════════════════════════
// 前端异常上报：内测通道要靠它复现 bug，所以连同上下文一起送
// ═══════════════════════════════════════════════════════════
(function () {
    var base = window.__API_BASE__ || '';
    var seen = {};
    var sent = 0;
    var MAX = 40;          // 单次页面生命周期最多 40 条，防止异常风暴打爆日志

    function cid() {
        try { return localStorage.getItem('chess.cid') || ''; } catch (e) { return ''; }
    }

    function boardSnap() {
        try {
            var el = document.querySelector('xiangqi-board');
            var s = el && el.boardState;
            if (!s) return null;
            return {
                turn: s.current_turn,
                status: (s.game_status || {}).state,
                pieces: (s.pieces || []).length,
                moves: (s.move_history || []).length,
            };
        } catch (e) { return null; }
    }

    function report(payload) {
        if (sent >= MAX) return;
        var key = (payload.event || '') + '|' + String(payload.msg || '').slice(0, 120);
        var now = Date.now();
        if (seen[key] && now - seen[key] < 60000) return;   // 同类错误 60s 内只报一次
        seen[key] = now;
        sent++;
        var body = JSON.stringify(Object.assign({
            event: 'client',
            url: location.href,
            title: document.title,
            viewport: window.innerWidth + 'x' + window.innerHeight,
            online: navigator.onLine,
            board: boardSnap(),
            cid: cid(),
        }, payload));
        try {
            if (navigator.sendBeacon) {
                navigator.sendBeacon(base + '/api/client_log',
                    new Blob([body], { type: 'application/json' }));
                return;
            }
        } catch (e) {}
        try {
            fetch(base + '/api/client_log', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: body,
                keepalive: true,
            }).catch(function () {});
        } catch (e) {}
    }

    window.__chessReport = report;
    window.addEventListener('error', function (e) {
        report({
            event: 'js_error',
            msg: String(e.message || '').slice(0, 600),
            src: (e.filename || '') + ':' + (e.lineno || 0),
            stack: String((e.error && e.error.stack) || '').slice(0, 2000),
        });
    });
    window.addEventListener('unhandledrejection', function (e) {
        var r = e.reason || {};
        report({
            event: 'unhandled_rejection',
            msg: String(r.message || r).slice(0, 600),
            stack: String(r.stack || '').slice(0, 2000),
        });
    });
})();

if (!customElements.get('xiangqi-board')) customElements.define('xiangqi-board', XiangqiBoard);
