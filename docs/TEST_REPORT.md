# 测试报告 · QuizMaster 在线知识竞答游戏

- **项目**：QuizMaster（综合实践选题第 17 题「在线知识竞答游戏」）
- **提交人**：2413979 刘珂
- **测试日期**：2026-09-22（第四轮改动后复测，覆盖首页横幅重新构图 / 窄屏画幅对齐 / 交付包组装）
- **测试环境**：Windows 11 / Python 3.13.12 / uv 0.11.27 / SQLite（WAL）
- **被测版本**：本地主分支最新提交

---

## 1. 测试目标

本次测试要回答三个问题：

1. **核心闭环是否真的能跑通** —— 登录 → 抽题 → 逐题作答 → 提交 → 判分 → 结果 → 历史/排行榜，不允许只有静态页面。
2. **关键业务规则是否真实生效** —— 服务端判分（单选 / 判断按键位、填空按关键字）、超时处理、重复提交幂等、题库编辑不改历史成绩、权限隔离、标准答案与判分关键字不泄露。
3. **异常场景是否可控** —— 空题库、非法选项、越权访问、RAG 不可用等情况下，系统应给出明确错误码而不是 500 崩溃。

---

## 2. 测试策略

采用三层测试，越往上层越接近真实演示场景：

| 层级 | 工具 | 覆盖对象 | 用例数 | 是否依赖模型 |
|---|---|---|---|---|
| 单元测试 | pytest | 判分纯函数（含单选/判断/填空三题型分派与填空题宽松判分边界）、奖励纯函数（SP/等级/成就） | 28 | 否 |
| API 集成测试 | pytest + FastAPI TestClient | 状态机、快照、权限、幂等、按题型校验、题库按题型增改、降级 | 39 | 否 |
| 交付包组装 | pytest | 打包排除规则（视频 / 密钥 / 数据库不进 `代码/`）与包内 README 材料清单 | 20 | 否 |
| 端到端冒烟 | httpx 脚本（真实 HTTP） | 完整闭环 + 知识库 + 新手指引 + 视觉与可访问性 + RAG 真实调用 | 87（不带 `--with-rag` 为 84） | 可选（`--with-rag`） |
| 浏览器行为探针 | CDP（Chrome 无头） | 高亮对齐、跳过路径、已读持久化、无 JS 报错 | 51 | 否（不写库） |

四层互补：单元测试锁定算分公式，集成测试锁定状态机，**打包规则测试守住交付物结构**，冒烟测试保证真实部署路径可用。

执行命令：

```bash
# 单元 + 集成 + 打包规则（104 项）
uv run python -m pytest -q

# 端到端冒烟（87 项，含真实模型调用）
uv run python scripts/smoke_test.py --with-rag
```

---

## 3. 执行结果总览

```
$ uv run python -m pytest -q
.......................................................................................      [100%]
104 passed, 2 warnings in 30.27s
```

```
$ uv run python scripts/smoke_test.py --with-rag
...
结果：通过 87 项，失败 0 项
P0 核心闭环全部通过。
```

| 指标 | 结果 |
|---|---|
| pytest 用例 | 104 / 104 通过 |
| 冒烟用例 | 87 / 87 通过（不带 `--with-rag` 84 / 84） |
| CDP 浏览器探针 | 106 / 106 通过（4 个脚本：知识库 16、人物卡响应式 10 视口、新手指引 51、离线页面 29） |
| 离线交付物 | 单文件页面与 APK 各验一遍，均 29 / 29 通过（见 §4.6） |
| 失败用例 | 0 |
| 阻断性缺陷 | 0 |
| 已知限制 | 4 项（见第 9 节） |

2 条 warning 均为 Starlette/FastAPI 内部关于 `httpx` TestClient 的弃用提示，与业务代码无关。

---

## 4. 用例清单

### 4.1 判分与评级（`tests/test_scoring.py`，18 项）

**计分口径与评级（6）**

| 用例 | 验证点 |
|---|---|
| `test_accuracy_score_is_percentage` | A-1 口径：得分 = 正确数 ÷ 总题数 × 100 |
| `test_unanswered_counts_as_wrong` | 未答题计入分母且记为错误 |
| `test_score_never_exceeds_boundaries` | 得分恒在 0–100 区间 |
| `test_grade_thresholds` | 100→优秀、85–99→良好、60–84→合格、<60→待提升 |
| `test_weighted_mode_uses_difficulty_weights` | B-1 加权口径：简单1.0/中等1.5/困难2.0 |
| `test_breakdown_is_reproducible` | 同一输入两次计算得分与逐题拆解完全一致 |

**判断题判分（2）**

| 用例 | 验证点 |
|---|---|
| `test_judge_uses_ab_keys` | 判断题 A=正确 / B=错误，键位比对与单选同构 |
| `test_judge_cannot_be_answered_with_cd` | 越界键位 C/D 既不算答对，也不算「已作答」 |

**填空题宽松判分（10）**

| 用例 | 验证点 |
|---|---|
| `test_normalize_text_folds_noise` | 归一化：全角转半角、中文数字转阿拉伯、去标点与大小写 |
| `test_blank_accepts_keyword_hit` | 命中关键字即判对 |
| `test_blank_accepts_any_of_several_keywords` | 多关键字命中任意一个即判对 |
| `test_blank_rejects_missing_keyword` | 未命中任何关键字且与参考答案不同 → 判错 |
| `test_blank_empty_is_unanswered_not_wrong_answer` | 空答案记为未作答，而非答错 |
| `test_blank_rejects_profanity_even_with_keyword` | 含污言秽语即使命中关键字也判错 |
| `test_blank_rejects_absurd_input` | 超长粘贴 / 单字重复灌水 / 无意义字符 → 判错 |
| `test_blank_falls_back_to_reference_answer` | 未配关键字时，与参考答案归一化后一致也算对 |
| `test_hit_keywords_reports_which_ones` | 正确回报命中了哪些关键字（供结果页展示） |
| `test_evaluate_mixes_all_three_types` | 三种题型混排一局，整体判分与逐题拆解正确 |

### 4.2 积分与成就（`tests/test_rewards.py`，10 项）

| 用例 | 验证点 |
|---|---|
| `test_level_table_boundaries` | L1–L5 等级区间边界正确 |
| `test_level_progress_points_to_next` | 距下一级所需 SP 计算正确 |
| `test_sp_formula_matches_documentation` | SP = 20 + 得分×0.5（上限50）+ 难度 + 连续天数 与文档一致 |
| `test_sp_caps_are_enforced` | 单局 SP 上限 105、连续奖励上限 25 生效 |
| `test_streak_progression` | 连续研习天数递增/中断归零 |
| `test_first_and_count_achievements` | 首战、10 局、50 局成就按阈值解锁 |
| `test_perfect_and_speed_and_hard_master` | 满分、速通、困难达人成就 |
| `test_speed_requires_min_score` | 速通成就要求得分达标，避免乱点刷成就 |
| `test_all_round_requires_four_categories` | 全能成就要求覆盖 4 个分类 |
| `test_streak_seven` | 连续 7 天成就 |

### 4.3 业务闭环与规则（`tests/test_flow.py`，39 项）

**认证与权限（4）**

| 用例 | 验证点 |
|---|---|
| `test_requires_login` | 未登录访问受保护 API 返回 401 + `AUTH_REQUIRED` |
| `test_login_rejects_wrong_password` | 错误密码被拒绝 |
| `test_login_sets_httponly_cookie` | 会话 Cookie 为 HttpOnly |
| `test_me_returns_current_user` | 当前用户信息正确 |

**题目与组卷（5）**

| 用例 | 验证点 |
|---|---|
| `test_answer_payload_never_leaks_correct_answer` | 答题阶段绝不返回 `correctAnswer` / `explanation` |
| `test_questions_are_unique_in_one_session` | 同一局内题目不重复 |
| `test_question_not_enough_raises` | 可用题量不足返回 `QUESTION_NOT_ENOUGH` |
| `test_invalid_question_count` | 非法题量（非 5/10/20）被拒绝 |
| `test_answer_from_other_session_rejected` | 不能把答案提交到他人的会话 |

**作答与判分（12）**

| 用例 | 验证点 |
|---|---|
| `test_answer_upsert_keeps_single_record` | 改答案只保留一条记录，不重复累计 |
| `test_invalid_option_rejected` | 非法选项（如 `E`）被拒绝 |
| `test_judge_rejects_option_outside_ab` | 判断题只接受 A/B，越界键位被拒绝 |
| `test_blank_answer_is_saved_as_free_text` | 填空题自由文本原样落库（不被截断或改写） |
| `test_blank_rejects_overlong_answer` | 填空题超过 60 字被拒绝 |
| `test_blank_grading_is_lenient_but_not_lawless` | 三题型混排一局：宽松判分下得分仍然可复算 |
| `test_result_page_renders_blank_review` | 结果页填空题展示「你的作答 / 参考答案 / 判分要点」 |
| `test_score_is_computed_server_side_and_reproducible` | 得分由服务端按标准答案计算，可复算 |
| `test_unanswered_counted_as_wrong` | 未答题按错误计入 |
| `test_submit_is_idempotent` | 重复提交返回原结果，不重复写入 |
| `test_answering_after_submit_is_rejected` | 已提交后不能继续作答 |
| `test_cannot_access_other_users_session` | 越权读取他人会话被拒绝 |

**管理员与题库（9）**

| 用例 | 验证点 |
|---|---|
| `test_student_cannot_use_admin_api` | 普通用户访问管理 API 返回 403（服务端校验，非前端隐藏） |
| `test_admin_can_list_questions` | 管理员可读取含标准答案的题库 |
| `test_admin_can_filter_questions_by_type` | 题库可按题型筛选 |
| `test_admin_can_create_blank_question` | 可新增填空题（需带关键字） |
| `test_admin_can_create_judge_question` | 可新增判断题（答案固定 A/B，自动补「正确 / 错误」选项） |
| `test_admin_blank_without_keywords_is_rejected` | 填空题未配关键字被拒绝（否则无法判分） |
| `test_admin_single_question_still_requires_four_options` | 单选题仍强制四个非空选项 |
| `test_admin_create_and_edit_question` | 题目新增与编辑可用 |
| `test_editing_question_does_not_change_history` | **编辑题目不改动历史成绩**（快照机制） |

**统计与超时（5）**

| 用例 | 验证点 |
|---|---|
| `test_dashboard_and_history_and_leaderboard` | 看板、历史、排行榜数据一致 |
| `test_in_progress_session_excluded_from_stats` | 进行中的会话不计入统计 |
| `test_timeout_settlement` | 超时后按预设规则结算 |
| `test_lazy_timeout_on_read` | 用户不再访问时，任意读取触发惰性超时结算 |
| `test_rag_rejects_unfinished_session` | 未结算的会话不能请求增强解析 |

**奖励与 RAG（4）**

| 用例 | 验证点 |
|---|---|
| `test_rewards_summary_and_achievements` | 奖励汇总与成就墙数据正确 |
| `test_reward_client_cannot_report_points` | 客户端无法上报分数/积分 |
| `test_recompute_matches_online_value` | 离线重算结果与在线值完全一致（奖励可复现） |
| `test_rag_returns_graceful_fallback` | RAG 不可用时优雅降级，不影响结果页 |

### 4.4 交付包组装（`tests/test_packaging.py`，20 项）

`scripts/build_submission.py` 跑一次要打 PDF、压几十 MB 的包，不适合进单测；这里测的是从它拆出来的**纯函数级规则**：

| 用例组 | 验证点 |
|---|---|
| 敏感与运行时文件被排除（7 项） | `.env` / `data/*.db*` / `.venv` / `__pycache__` / `backups/` / make_pdf 中间产物都不进 `代码/` |
| 视频永不进入工程树（4 项） | `演示视频.mp4` / `.mov` / `.MKV`（大小写混写）/ `.webm` 一律排除 |
| 源码与模板被保留（7 项） | `app/` 源码、种子数据、`docs/`、`.env.example` 正常入包 |
| `copy_code_tree` 端到端复制（1 项） | 在临时工程里放 `.env` / `.db` / `.mp4`，复制后 `代码/` 只剩两个真实源码文件 |
| 包内 README 材料清单（1 项） | 5 项清单齐全、写明截止时间与投递邮箱、视频条目留占位符按实情填写 |

> 这组用例的由来：演示视频曾被放进工程根目录，而 `代码/` 是整个工程的复制 —— 视频先被复制进
> `代码/`、再由 `--video` 在顶层放一份，zip 从 12.6 MB 涨到约 94 MB 且内容重复。
> **症状是「包变大」而不是报错**，所以规则必须有断言守着（见 `AI_ASSISTED_DEVELOPMENT.md` 轮次 15）。

### 4.4b 新手指引（`tests/test_tour.py`，17 项）

| 分组 | 项数 | 关键断言 |
|---|---|---|
| 步骤定义自洽 | 3 | key 唯一、标题/正文非空、姿势与位置在允许集合内、**每个锚点都登记在 `TOUR_ANCHORS`**、首尾两步无准星、署名含原型出处 |
| 渲染契约 | 5 | 覆盖层默认 `hidden`、重开入口默认 `hidden`（无脚本不留死控件）、`#tour-data` 可解析且与定义逐步一致、**荷宝 4 姿势且 SVG 内无 `<rect>` / `<image>`**、步骤按本页锚点过滤（首页恰好少 1 步） |
| 页面差异 | 2 | 知识库页带 `kb-tools` 锚点、登录页只露荷宝不放覆盖层 |
| 账号级持久化 | 5 | 未登录 401、**首次 autoStart=True → 标记后 autoStart=False**、标记幂等、**已读不跨账号继承**、非首页页面永不自动开场 |
| 署名可见 | 2 | 首页与知识库页面上都能读到原型出处「紫菡」 |

> 「高亮框对不对齐、Esc 能不能关」测不了 —— 那属于浏览器行为，交给
> `.workbuddy/tmp/verify-tour.mjs`（CDP 探针，51 项断言，见 §4.6）。

### 4.5 端到端冒烟（12 组 87 项）

| 组 | 项数 | 关键断言 |
|---|---|---|
| 1. 健康检查 | 1 | 服务信息含环境、开关状态、模型配置状态 |
| 2. 页面与权限 | 4 | 未登录跳转、API 401、错误码正确 |
| 3. 登录 | 4 | Cookie HttpOnly、错误密码拒绝、页面渲染 |
| 4. 抽题与作答 | 4 | 答题阶段不泄露答案、返回服务端截止时间 |
| 5. 提交与判分 | 5 | 得分等于服务端算式、评级生成、终态返回答案与解析、幂等 |
| 6. 统计与排行榜 | 6 | 看板/历史/排行榜数据与页面一致 |
| 7. 奖励 | 2 | SP 累计、成就墙 8 项 |
| 8. 管理员端 | 5 | 403 隔离、题库页与资料库页渲染、索引存在 |
| 9. 知识库与交互动效 | 21 | 时间轴/人物/题卡数量、**八格数字看板已移除**、**章节目录 5 项齐全且只在知识库页出现**、**姓氏小章已移除且人物数据不再带 initial 字段**、内容服务端直出、放映模式（翻面 + `is-present`）、未登录跳转、动效底座与降级兜底、锚点补检、站点图标 |
| 10. 新手指引（荷宝导览） | 14 | 数据段可解析、步骤数与服务端定义一致、autoStart 与 seen 互斥、首页 6 处锚点齐全、覆盖层与入口默认 hidden、**荷宝 4 姿势且背景透明**、出处署名「紫菡」可见、`tour.js` 关键实现齐备、窄屏抽屉样式、知识库锚点、未登录不得标记已读、标记后不再自动开场 |
| 11. 视觉一致性与可访问性 | 18 | 登录页分栏与公共 head、文案与知识库同源、侧栏图标为内联 SVG 且无 Unicode 残留、`:focus-visible`、答题页快捷键与 `aria-live` 播报、静态目录无临时免登录页、插画**全部为 WebP 且共 6 张**、体积门禁（6 张合计 < 680KB、单张 < 150KB）、静态目录不留 JPG 母版、六张插画**按槽位对齐画幅**（首页横幅 4 帧 1152×512，登录/结果 2 张 960×594，即已裁去生成平台水印带且横幅按框重构过） |
| 12. RAG 增强解析 | 3 | 结构合法、生成解析、**引用 chunkId 必须来自本次 Top-3** |

第 12 组真实调用模型的结果（引用条数与命中来源取决于本局抽到的题目，此处为一次实际运行的样例）：

```
[PASS] RAG 接口返回 200/409 且结构合法
[PASS] 生成了增强解析
[PASS] 引用可验证（chunkId 来自本次 Top-3）
       模型：deepseek-chat · 引用 2 条
       来源：《南开校史讲义》· 第一章 创校与早期发展
```

### 4.6 离线交付物（单文件页面与安卓 APK，各 29 项）

`dist/QuizMaster_在线知识竞答_预览版.html` 与 `dist/QuizMaster_安卓版.apk` 都不是提交材料，
但它们是「现场只有手机」时的演示入口，所以按同一套可断言的探针验过。**关键是这两份是同源产物**：
APK 的 `assets/app.html` 与 `dist/` 里那份单文件页面字节完全相同（打包脚本比对 sha256，不一致即退出），
因此探针跑两遍 —— 一遍验产物，一遍验**从 APK 里解出来的那一份**。

| 段 | 项数 | 关键断言 |
|---|---|---|
| A. 自包含与运行健康 | 5 | 标题正确、**`performance` 里 0 条外部资源请求**（真离线）、题目数据已内联 10 题、无 JS 运行时异常、无资源加载错误 |
| B. 新手指引（荷宝导览） | 19 | 首次打开自动开场、第 1 步欢迎步无高亮、顶栏入口由脚本放出、四种姿势齐全且**同一时刻只显示一种**、高亮框与目标对齐 ±2px、卡片不压高亮框、姿势随步骤切换、**Esc / 跳过 / 点遮罩 / 完成 四条关闭路径**、顶栏可重开、首步「上一步」禁用、末步文案变「完成」且计数 5/5、窄屏卡片贴底满宽且无横向滚动 |
| C. 本地判分与后端口径一致 | 5 | `#tour=0` 不弹指引、**全部答对 = 100 分**、逐题明细条数 = 题目数、**全部答错 = 0 分**、全程无 JS 异常 |

判分那两条是这套离线版最要紧的断言：页面内的判分脚本与 `app/services/scoring.py` 逐条对齐，
全对必须 100、全错必须 0 —— 否则「离线版只是长得像」这句话就站不住。

APK 本体另有一层构建期自检（`build_apk.py` 内，任一项不符即报错退出）：

| 检查 | 结果 |
|---|---|
| 包名 / 版本 | `cn.edu.nankai.quizmaster` · 1.0(1) |
| SDK | `minSdkVersion 21` → `targetSdkVersion 34` |
| 可启动 Activity | `cn.edu.nankai.quizmaster.MainActivity` |
| 权限 | **空** —— 连 `INTERNET` 都没有，飞行模式可运行 |
| 签名 | v1 与 v2 方案 `apksigner verify -v` 均为 `true`（debug 证书，仅可侧载） |
| 包内页面 | `assets/app.html` sha256 与源文件一致 |
| dex | 仅 `classes.dex`，`dexdump` 含 `MainActivity` |
| 体积 | 0.49 MB，包内 18 项 |

---

## 5. 验收标准映射（第 17 题）

对照《综合实践项目选题与验收标准》第 17 题逐项给出证据。

### 5.0 结论

| 类别 | 结果 |
|---|---|
| 必做功能（6 项） | **6/6 满足** |
| 关键业务规则（2 项） | **2/2 满足**，且都能现场操作验证 |
| 统计 / 可视化（三项满足其一即可） | **3 项全做**（排行榜 + 正确率 + 历史成绩） |
| 异常场景（4 项） | **4/4 满足**，每项对应独立错误码 |
| 可选 AI 扩展（三项满足其一即可） | **1/3 实现**（AI 解释答案）。「自动生成题目」「按错题调整难度」**未实现** —— 题面第 4 条明确「可选 AI 扩展不属于必做内容」 |

题面对 AI 的硬性要求落在**开发过程**（能说明 AI 如何参与需求分析、代码生成、调试、测试或代码审查），由 `docs/AI_ASSISTED_DEVELOPMENT.md` 按 18 个轮次逐条记录。

### 5.1 所有项目统一完成要求（标准第二部分）

| 统一要求 | 落实情况 | 证据 |
|---|---|---|
| 项目可正常启动，无阻断性错误 | 一条命令启动（`start.bat` / `start.sh`）；首次运行自动建库并播种题库 | README §7.1；启动日志 `Application startup complete` |
| 完成核心功能，不能只有静态页面或固定演示 | 全站服务端渲染真实数据；开考 → 作答 → 提交 → 成绩全走真实接口与数据库 | 冒烟 84 项，其中 12 组为真实 HTTP 流程 |
| 关键业务规则真实生效，可现场操作验证 | 分数只由服务端按题库快照计算，客户端任何分数字段都被忽略 | `test_score_is_computed_server_side_and_reproducible`；§6 现场手册 |
| 关键业务数据保存，前后操作形成完整闭环 | SQLite（WAL）持久化：题库 → 会话 → 答题 → 成绩 → 排行 / 成就 / 历史 | `test_dashboard_and_history_and_leaderboard` |
| 典型非法输入、重复操作、越界操作有基本异常处理 | 12 个业务错误码 + 统一响应结构；越权、非法选项、重复提交、超时、题量不足均明确返回 | 见 §5.5；§3 缺陷表 / §4 修复表 |
| 提交 README（目标 / 技术栈 / 启动 / 核心功能 / AI 使用情况）+ 演示视频 | README 含全部要求章节；**演示视频随包提交**（`build_submission.py --video`），未录制时包内 README 如实标注缺项 | README §1–§7；`docs/DEMO_SCRIPT.md` |

### 5.2 必做功能

| 验收要求 | 实现位置 | 验证证据 |
|---|---|---|
| 题库管理 | `routers/api_admin.py` + `admin_questions.html` | `test_admin_create_and_edit_question`；截图 07 |
| 多题型支持（单选 / 判断 / 填空，题面未要求，属加分） | `models.QuestionType` + `scoring.judge_blank` + `quiz.html` | `test_evaluate_mixes_all_three_types`、`test_blank_grading_is_lenient_but_not_lawless`、`test_admin_can_create_judge_question` |
| 随机或规则出题 | `services/question_service.draw_questions`（按题量 / 分类 / 难度筛池后 `random.sample`，不重复） | `test_questions_are_unique_in_one_session` |
| 答题交互 | `templates/quiz.html` + `api_quiz.py`（进度格 / 答题卡 / 键盘 A–D 与方向键 / 无障碍播报） | 冒烟第 4 组；截图 03 |
| 单题或整局计时 | `core/config.per_question_seconds`（单题 60s，整局 = 单题 × 题数）+ `quiz_service.GRACE_SECONDS` | `test_timeout_settlement`；结果页显示用时 |
| 自动计分 | `services/scoring.py`（纯函数，服务端执行） | 18 项判分单测；冒烟「得分等于服务端算式」 |
| 成绩历史记录 | `api_stats.history` + `history.html`（分页 + 状态筛选） | `test_dashboard_and_history_and_leaderboard`；截图 05 |

### 5.3 关键业务规则

| 验收要求 | 实现方式 | 验证证据 |
|---|---|---|
| 按标准答案计算 | 服务端比对**快照**中的 `correct_answer`；改题库不影响历史成绩 | `test_score_is_computed_server_side_and_reproducible`、`test_editing_question_does_not_change_history` |
| 超时按预设规则处理 | 预设规则：① 到期即惰性结算为 `TIMEOUT`；② 已保存答案照常判分，未作答按错（计入分母、不计分子）；③ 用时按截止时间封顶；④ 超时**不额外扣分**；⑤ 请求卡在截止瞬间留 3 秒网络宽限 | `test_timeout_settlement`、`test_lazy_timeout_on_read`、`test_unanswered_counted_as_wrong` |

### 5.4 统计与可视化

| 验收要求 | 实现方式 | 验证证据 |
|---|---|---|
| 排行榜 | `stats_service.leaderboard`：最佳分数 ↓ → 平均正确率 ↓ → 最佳用时 ↑（第三键保证顺序稳定）；含「我的名次」 | `test_dashboard_and_history_and_leaderboard`；截图 05 |
| 正确率 | 得分口径 A-1（`正确数 × 100 ÷ 题数`），结果页展示可复核算式 | 截图 04（`正确率口径: 4 × 100 ÷ 10 = 40`） |
| 历史成绩 | 分页历史 + 状态筛选 + 汇总统计（完赛局数 / 最佳分 / 平均正确率 / 我的名次） | 同上；截图 05 |

### 5.5 异常场景

| 验收要求 | 触发方式 | 错误码 / 行为 | 验证证据 |
|---|---|---|---|
| 超时 | 整局时间耗尽后继续作答或提交 | `SESSION_EXPIRED`（409）；本局按已保存内容结算为 `TIMEOUT` | `test_timeout_settlement`、`test_lazy_timeout_on_read` |
| 重复答题 | ① 同一题改选 ② 提交后继续作答 ③ 重复提交 | ① 原地 upsert，库中恒为一条记录，**不新增**；② `SESSION_FINISHED`（409）；③ 幂等，返回同一结果、不重复计分与发 SP | `test_answer_upsert_keeps_single_record`、`test_answering_after_submit_is_rejected`、`test_submit_is_idempotent` |
| 空题库 | 题库为空，或筛选后可用题数 < 所选题量 | `QUESTION_NOT_ENOUGH`（400），提示剩余可用题数，**绝不缩水成局** | `test_question_not_enough_raises` |
| 非法选项 | 单选 / 判断题传 `E`、判断题传 `C/D`、题量传 5/10/20 之外、填空题答案超 60 字 | `INVALID_OPTION`（400）/ `VALIDATION_ERROR`（400） | `test_invalid_option_rejected`、`test_judge_rejects_option_outside_ab`、`test_invalid_question_count`、`test_blank_rejects_overlong_answer` |
| 越权访问（题面未要求，属加分） | 访问他人会话 / 学生调用管理端接口 | `FORBIDDEN`（403）/ `AUTH_REQUIRED`（401）；不区分「不存在」与「无权」以防越权探测 | `test_cannot_access_other_users_session`、`test_student_cannot_use_admin_api` |

### 5.6 可选 AI 扩展

| 扩展项 | 是否实现 | 说明 |
|---|---|---|
| AI 自动生成知识题目 | **未实现** | 出题走题库随机抽取；题库由课程讲义人工整理后 import，不由模型现场生成 |
| AI 解释答案 | **已实现** | RAG 增强解析：检索校史讲义（BM25 稀疏检索）→ DeepSeek 生成 → **引用强校验**（`chunkId` 必须来自本次 Top-3，否则丢弃输出）→ 落库缓存（同题同答不重复调模型） |
| AI 根据错题调整难度 | **未实现** | 难度由用户开考时自选（简单 / 中等 / 困难），非由错题自适应 |

AI 链路的降级是**分级**的，且不影响成绩：未配模型 → `RAG_MODEL_ERROR`；未导入资料 → `RAG_NO_DOCUMENT`；检索不到相关段落 → `RAG_NO_RELEVANT_CHUNK`；输出不可靠 → `RAG_INVALID_OUTPUT`。以上任一情况都回落到**题库标准解析**并附一句原因说明，成绩与 SP 照常结算（`test_rag_returns_graceful_fallback`）。

额外实现了**得分口径版本化**（`rule_version` + `score_breakdown_json` 逐题拆解），使成绩可现场纸笔复核。

---

## 6. 关键规则现场验证手册

以下步骤供教师现场操作，每步都有明确的可观察结果。

### 6.1 服务端判分不可篡改

1. 用任一普通账号登录（自注册即可，交付版不预置学生账号），开一局 5 题。
2. 打开浏览器开发者工具，在作答请求中把选项改成 `E` 或直接构造提交请求。
3. **预期**：返回 `INVALID_OPTION` 错误，不产生成绩。
4. 正常答完提交，结果页显示 `正确率口径: 正确数 × 100 ÷ 总题数 = 得分`，可纸笔验算。

### 6.2 题库编辑不影响历史成绩

1. 完成一局，记下成绩。
2. 用 `admin / QuizAdmin@2026` 登录，编辑该局做过的某道题，改掉正确答案与解析。
3. 回到历史成绩页。
4. **预期**：历史成绩与逐题拆解**完全不变**（题目以快照形式存于 `session_questions`）。

### 6.3 重复提交与超时

1. 提交一局后，再次点击提交。
2. **预期**：返回原结果，积分与成就**不重复累计**。
3. 开一局后放置超过时限，再刷新页面。
4. **预期**：会话自动结算为超时，未答题按错误计入。

### 6.4 权限隔离

1. 用任一普通账号（自注册）直接访问 `GET /api/admin/questions`。
2. **预期**：403，且标准答案不在响应中（服务端校验，非前端隐藏）。
3. 用他人会话 ID 请求 `GET /api/quiz/sessions/{id}`。
4. **预期**：拒绝访问。

### 6.5 RAG 引用可追溯

1. 在结果页点击任意题目「生成资料增强解析」。
2. **预期**：返回增强解析，来源标注形如《南开校史讲义》· 第一章 创校与早期发展。
3. 断网或清空 `AI_API_KEY` 后重试。
4. **预期**：优雅降级为题库自带解析，结果页正常显示，不报 500。

---

## 7. 降级路径验证

RAG 与奖励均为旁路增强，任一环节失败都不得影响成绩落库。已覆盖的降级路径：

| 触发条件 | 降级行为 | 验证 |
|---|---|---|
| 未配置 API Key | 返回题库自带解析，`status=DISABLED` | `test_rag_returns_graceful_fallback` |
| 模型超时（>8s） | 返回题库自带解析，`status=FALLBACK` | 超时保护代码路径 |
| 模型输出非法 JSON | 丢弃输出，降级 | `parse_model_output` 结构校验 |
| 引用不在本次 Top-3 | 丢弃该引用 | 冒烟第 11 组「引用可验证」 |
| 检索无命中（低于阈值 1.2） | 不调用模型，直接降级 | 阈值判断 |
| 会话未结算 | 拒绝请求（409） | `test_rag_rejects_unfinished_session` |
| 奖励结算异常 | 吞异常，标记 `REWARD_UNAVAILABLE` | `settle_rewards_safe` |
| `REWARD_ENABLED=false` | 关闭积分/等级/成就，成绩与排行榜完全正常 | 开关配置验证 |

---

## 8. 开发期缺陷与修复记录

以下为开发过程中实际发现并修复的问题，按「发现 → 定位 → 修复 → 回归」记录。最终交付版本中这些缺陷均已修复，回归测试全绿。

| # | 现象 | 根因 | 修复 | 回归证据 |
|---|---|---|---|---|
| 1 | 所有页面 500：`TypeError: unhashable type: 'dict'` | Starlette 1.6 起 `TemplateResponse` 首个参数改为 `request` | 在 `pages.py` 封装 `_render(name, context)` 并替换 10 处调用 | 冒烟第 3、5、6、8 组页面渲染全通过 |
| 2 | 列表页 500：`object of type 'builtin_function_or_method' has no len()` | Jinja2 中 `data.items` 命中了 dict 的 `items()` 方法而非同名 key | 4 个模板统一改为 `data['items']` 下标访问 | 排行榜 / 历史 / 题库 / 成就页渲染通过 |
| 3 | 新开进程建表报 `NameError: QuizSession is not defined` | 并行编辑种子脚本时后一次写入覆盖了前一次的导入修改 | 改为顺序编辑并复验导入块 | `init_db.py --reset` 全流程成功 |
| 4 | `TypeError: fail() missing 1 required positional argument` | 兜底异常处理器漏传 message | 从 `ERROR_META` 取默认提示补全参数 | 故意触发 500 场景返回规范错误体 |
| 5 | 模板报 `No filter named 'difficulty_label'` | 只在 Jinja `globals` 注册，模板中按 `\|` filter 使用 | 同时注册 `filters` 与 `globals` | 答题页与结果页渲染通过 |
| 6 | 首页 / 历史页 500：`'str' object has no attribute 'tzinfo'` | 时间格式化函数只接受 `datetime`，服务层传的是 ISO 字符串 | 兼容 `str` 与 `datetime` 两种输入 | `test_dashboard_and_history_and_leaderboard` |
| 7 | 冒烟脚本 `AttributeError: 'dict' object has no attribute 'headers'` | 辅助函数已解包 `.json()`，后续误用 `.headers` | 改为直接调用 `client.post` 取响应头 | 冒烟第 3 组 Cookie 断言通过 |
| 8 | 重置数据库报 `PermissionError: [WinError 32]` | 运行中的 uvicorn 占用 `data/quizmaster.db` | 先停服务再重置（同时实现自动备份到 `backups/`） | `init_db.py --reset` 可重复执行 |
| 9 | pytest 报 `ModuleNotFoundError: No module named 'app'` | 项目未声明包根目录 | `pyproject.toml` 增加 `pythonpath = ["."]` | 45 项测试正常收集 |
| 10 | 测试报 `NameError: name 'login' is not defined` | 辅助函数被误删且定义位置在 fixture 之后 | 移回 fixture 之前的模块级位置 | 全部 API 用例通过 |
| 11 | 语料切片质量差（74 字碎片块、章节归属错位、整份变一块） | 切片器未处理短引言与跨章节合并的标题归属 | 短引言并入首节；跨章节合并记「第一章 · 第二章」；过短尾巴合并加上限判断 | 11 个切片均落在 262–599 字，章节标注正确 |
| 12 | 知识库页面区块停在半透明状态、数字看板只剩第一个格子 | 滚动揭示最初用 `IntersectionObserver`，它在 headless 浏览器（截图）与部分嵌入式 WebView 里不触发回调，元素永远停在 `opacity: 0` | 改为 `scroll` + 时间戳节流 + 同步 `getBoundingClientRect` 的视口检测；计数、得分环、进度条再补一层 `setTimeout` 兜底写入终值 | 冒烟第 9 组相应项通过；14 张截图全部正常渲染（当时张数，现为 15 张） |
| 13 | 打开 `/knowledge#people` 这类带锚点的链接，画面里一片空白 | 浏览器把视口定位到 URL 锚点（以及恢复上次滚动位置）发生在 `load` 之后，**这个过程不派发 `scroll` 事件**；而视口检测只在 `start()` 里查一次，于是锚点所在区块从未被检查，一直保持 `opacity: 0` | 在 `load` / `hashchange` 上补检，并在 `start()` 后于 0 / 80 / 240 / 600ms 各补查一次 | 冒烟新增「锚点定位后补检视口（load / hashchange）」；截图脚本改为按元素矩形裁切，不再依赖锚点滚动 |
| 14 | 加了放映模式后整页 JS 静默失效，没有报错但所有交互都不响应 | 用 `replace_all` 批量替换动效判断时，把 `const NO_MOTION = !MOTION_OK \|\| prefersReducedMotion \|\| PRESENT_MODE;` 里的表达式也一起替换成了 `NO_MOTION`，造成自引用 → 常量在自身初始化时被读取，抛 TDZ `ReferenceError`，脚本从第一行起就没执行 | 改为正确的表达式；并在截图脚本里挂上 `Runtime.exceptionThrown` 与 `Log.entryAdded` 监听，把「页面无 JS 异常、无资源错误」变成可断言的验收项 | `node --check` 只能查语法、查不出这类运行时错误，因此补了运行时监听；冒烟新增「动效退化开关定义完整（防自引用）」 |

其中 #12、#13 是这次新增交互动效时暴露的问题，也说明「动效必须能和内容解耦」—— 最终的实现方式保证：即使整套动效脚本完全不执行，内容也是完整可读的。#14 则说明批量替换需要复核，且**语法检查通过不等于脚本能跑起来**，所以验证手段必须覆盖运行时异常。

| 15 | 登录页实景铺满全屏后既模糊，右下角第三方水印（人民画报 / China.com.cn）也被顶进画面 | 用 900×600 的图去铺 1440 宽的视口，放大倍率约 1.6；原图自带媒体水印，铺满后必然可见 | 改为「左栏实景 + 右栏表单」分栏，实景收进按原比例裁切的有边界容器；同时裁掉最下 72px 水印区并重压 | 冒烟第 10 组「登录页分栏布局」通过；截图 01 目视复核 |
| 16 | 键盘用户 Tab 到按钮/链接上完全看不出焦点在哪 | 全站只有 `input:focus` 定义了样式，其余元素沿用了浏览器默认或 `outline: none` | 加 `*:focus-visible { outline: 2px solid var(--gold) }`；列表项/选项/进度格用负 `outline-offset` 以免焦点框被容器裁掉 | 冒烟第 10 组「全站键盘焦点可见」通过 |
| 18 | `mainhall.jpg` 底部仍残留第三方水印（结果页 Hero 右下角有一小条红色残迹） | 首轮按「水印在最下 72px」的目视估算裁切，实际水印顶在 y=528，裁到 528 恰好留下水印顶部约 23px | 逐行统计红色主导像素，定位水印带为 y=528–575；改为从原图裁到 y=505，并用同一检测复检（红像素 2000+ → 21，且全部落在 y≤411 的主楼立面） | 冒烟新增图片尺寸断言；截图 04 目视复核 |
| 17 | 答题页右栏下方大片空白，键盘快捷键完全不可发现 | 右栏只有一张进度卡；快捷键此前只存在于脚本里，没有任何界面提示 | 补「键盘快捷键」卡（`kbd` 键帽样式）并为每个选项标 `aria-keyshortcuts`；侧栏 200px → 248px，进度格从 26px 回到 36px 可点面积 | 冒烟第 10 组两项通过；截图 03 目视复核 |
| 19 | 新增判断题后，后台「新增题目」报 `KeyError: 'C'` | `_options_from_payload()` 对判断题只返回 `{'A':'正确','B':'错误'}` 两个键，而 `create_question` 要写 `option_a..option_d` 四个列 | 改为**恒定返回 A–D 四个键**：判断题只填 A/B、填空题四项留空；空的选项由 `option_map()`（按非空过滤）在序列化时自然消失，不会渲染出空按钮 | `test_admin_can_create_judge_question`、`test_admin_can_create_blank_question` |
| 20 | 判断题在判分层面能被 `C` 越界作答（`is_answered("JUDGE","C")` 返回 `True`） | `is_answered` 对非填空题一律按 `{A,B,C,D}` 判断，没有按题型收窄合法键位；而 `answer_is_correct` 已经正确返回 `False`，两个函数的口径不一致 | `is_answered` 按题型分派：判断题只认 A/B，单选认 A–D，填空题认任意非空串。越界键位一律算「未作答」而非「答错」，避免老数据在结果页被算成糊涂账 | `test_judge_cannot_be_answered_with_cd`；`save_answer` 侧也因 `option_map()` 只含 A/B 而直接拒绝 C 键 |
| 21 | 首页横幅图注是压在水彩上的白字：四帧里「新开湖」那帧几乎全白，图注基本看不见；窄屏下插画底部渐隐到纸色，白字更是压在近白底上。登录页插画同样用白字，靠一层深色渐变压底才勉强读清 | 图注一直被当成「图片上的装饰文字」，只加了 `text-shadow` 而没有稳定底色；但轮播四帧的明度差很大，任何固定字色都不可能帧帧可读 | 图注与轮播圆点统一改成**纸标签**（墨字 + 近白纸底 + 左侧一道金边），并把这层写法同时用到登录页 —— 顺带**删掉登录页插画上 `rgba(44,55,66,0.62)` 的渐变压底**，它正是 §5.4 明确否掉的「深色遮罩压白字」，属于自家文档和自家样式打架 | 1440×900 与 1000×1100 两个断点各裁一条 120px 的横幅底带放大复核（`verify-caption.mjs`），两种布局下图注均清晰可读 |

其中 #1、#2、#6 是典型的「AI 生成代码在真实运行时才暴露」的问题，属于本项目 AI 协作记录中人工复核发挥作用的直接证据。

> **题型扩展（v2.0）说明**：#19、#20 来自「把题库从 40 道扩充到 80 道、并新增判断 / 填空两种题型」这一轮。两者是同源问题的两面 —— 判断题复用 A/B 键位以最小化改动，但代码里凡是**默认「选项就是 A–D 四项」**的地方都必须按题型重新审视：#19 出在「写库时凑不齐四个键」，#20 出在「判分时没按题型收窄合法键」。修复方式统一为「按题型分派」，并把这两条边界写成了独立单测（`test_admin_can_create_judge_question`、`test_judge_cannot_be_answered_with_cd`），防止回归。这也再次印证：**新增枚举值的成本不在新增本身，而在所有隐含假设旧枚举完备的角落。**

> **第一轮实景素材的失效说明**：上表中 **#15（登录页实景铺满全屏 / 第三方水印）** 与 **#18（`mainhall.jpg` 底部残留水印）** 所描述的载体均为第一轮的**实景照片素材**。第二轮整体重设计（见 `docs/DESIGN.md` §5.1 与 §5.4）已将全站图片替换为**手绘水彩插画**，`libr.jpg`、`mainhall.jpg` 等实景素材**已从项目删除**，相关模板引用、冒烟断言与文档描述均已同步更新。因此这两条**不再适用于当前交付版本**，仅作为开发过程记录保留。同理，本表中凡涉及实景照片遮罩/裁切的表述，均已由 §17 的手绘插画方案取代。
>
> **当前生效的图片类验收项**（冒烟第 10 组）：六张插画全部为 WebP、体积门禁（6 张合计 < 680KB 且单张 < 150KB）、静态目录不留 JPG 母版、六张按槽位对齐画幅（首页横幅四帧 1152×512、登录 / 结果两张 960×594，即已裁去生成平台水印带）。第三轮把插画从 3 张扩到 6 张并统一转 WebP 后，总量从 1255KB（1200 宽 JPG）降到 633KB；第四轮把首页四帧按 2.25:1 重新构图，总量 642KB —— 只多 9KB，但横幅不再被 `object-fit: cover` 裁掉三成高度。

---

## 9. 已知限制

1. **重复提交的 HTTP 语义**：幂等提交返回 200 与原结果（而非 409），以便前端重复点击时无感。已提交后继续作答才返回 409。
2. **BM25 检索规模**：当前语料 11 个切片，采用内存稀疏索引，全量加载。语料超过约 1 万切片时需换成倒排文件或向量索引。
3. **单机 SQLite**：并发写入受 SQLite 锁限制，适合课程演示与单机验收；生产多实例部署需替换为 PostgreSQL。
4. **填空题判分不做语义理解**：刻意采用「命中任一关键字 + 无污言秽语 + 非离谱输入」的宽松口径，衡量的是「有没有记住这个知识点」，无法区分表述上的细微语义差异；80 道题的参考答案与填空题关键字都需要人工复核后定稿。

---

## 10. 复现步骤

**有 uv（推荐）：**

```bash
cd quizmaster
uv sync
uv run python scripts/init_db.py --reset
uv run python run.py
```

**没有 uv、只有 Python 3.11+（等价）：**

```bash
cd quizmaster
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
.venv/bin/python -m pip install -r requirements.txt           # macOS / Linux
.venv\Scripts\python.exe scripts/init_db.py --reset
.venv\Scripts\python.exe run.py
```

另开终端：

```bash
cd quizmaster
uv run python -m pytest -q                     # 104 项
uv run python scripts/smoke_test.py            # 84 项（不调模型）
uv run python scripts/smoke_test.py --with-rag # 87 项（含真实模型调用）
```

> 没有 uv 时，把上面的 `uv run python` 换成 `.venv\Scripts\python.exe`（Windows）
> 或 `.venv/bin/python`（macOS / Linux），参数不变。`requirements.txt` 锁定的版本与
> `uv.lock` 一致，两条路径装出的环境等价；网络慢时可加
> `-i https://mirrors.aliyun.com/pypi/simple/`。
> 统一入口 `start.bat` / `start.sh` 会自动判断走哪条路，见 `README.md` §7.1。

验收截图见 `docs/screenshots/`（15 张，含知识库 4 张、首页横幅第二帧 1 张、新手指引 1 张）。
