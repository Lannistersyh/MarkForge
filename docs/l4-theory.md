# L4 视觉控制理论 — 调研、定位与 MarkForge 设计论证

> 调研日期：2026-10-13。GitHub 数据经 REST API 实时核实；论文经 arXiv 摘要页核实。

## 1. 问题的形式化

**GUI grounding**（SeeClick, arXiv:2401.10935 的定义）：
给定一段自然语言指令和一张界面截图，输出指令所指元素的可操作坐标。

这是纯视觉 GUI agent（不读 DOM/无障碍树）的核心能力。
四层实验室的 L4 层正是这个问题的实例。

## 2. 现有三条技术路线

### 路线 A：结构化数据（L3 的领地）
读 DOM/AX tree → 元素定位。可靠、可审计，但桌面应用/Canvas/远程桌面不可用。

### 路线 B：VLM 全权（browser-use、cua 等）
截图整张丢给 VLM，直接要坐标/动作。
- 优点：零工程、泛化强；
- 缺点：**坐标不可审计**（模型说 (640,512) 无法知道为什么）、
  幻觉坐标风险、每次调用花钱、结果不可重放。

### 路线 C：专用检测模型（OmniParser、SeeClick、OS-Atlas、ShowUI）
微调过的检测/grounding 模型解析截图。
- 优点：精度高；
- 缺点：需下载 1–9B 权重、多为研究代码（SeeClick/OS-Atlas 已停更）、
  推理仍是黑箱。

### 缺口（MarkForge 的位置）
**没有人做"零模型下载、确定性、可审计"的轻量 grounding 引擎**：
经典 CV（连通域 + 颜色 + 几何 + 可选 OCR）完全可以在常见 UI 场景
（实心按钮、色块图标、文本链接）上给出可复核的坐标，
并把"不确定"显式暴露为 AMBIGUOUS 而不是硬猜。

## 3. 理论支柱

### 3.1 Set-of-Mark（arXiv:2310.11441，Microsoft）
用交互式分割把图切成区域，叠加字母数字/掩码/框标记，
让 GPT-4V 通过**引用标记**回答 grounding 问题——
零样本超过完全微调的 referring expression 模型。

**MarkForge 的继承**：`marking.py` 给候选元素渲染编号徽章；
`ground.py` 支持 `#N` 查询直接反查坐标。
上层（VLM 或人）只需说"点 3 号"，坐标换算是确定性的——
SoM 论文"标记把 grounding 变成引用"的收益，落到本地零模型实现。

### 3.2 SeeClick（arXiv:2401.10935，南京大学）
GUI grounding 是视觉 agent 的核心挑战；grounding 预训练直接提升下游任务。
提出 ScreenSpot 基准。

**MarkForge 的继承**：grounding 被明确建模为独立步骤（`ground.py`），
与检测（`detect.py`）解耦；查询语法可单测、结果可解释
（每个候选给出得分与原因，亚军一并返回）。

### 3.3 LLM-Brained GUI Agents 综述（arXiv:2411.18279，Microsoft）
系统梳理感知（结构化 vs 视觉）→ 规划 → 执行的框架与评测。

**MarkForge 的继承**：只做"感知中的视觉定位 + 执行前安全门"，
明确不抢 LLM 的规划职责——可嵌入任何上层 agent（含 browser-use）。

## 4. 工程对照（2026-10-13 GitHub API 实测）

| 项目 | ⭐ | 最近推送 | 状态 | 路线 | 对 MarkForge 的启示 |
|---|---|---|---|---|---|
| browser-use/browser-use | 117,766 | 2026-10-09 | 活跃 | B | VLM 全权的天花板；其坐标不可审计是我们的差异化 |
| trycua/cua | 29,528 | 2026-10-13 | 活跃 | B+容器 | 桌面场景基础设施太重；我们是零基建 |
| microsoft/OmniParser | 25,517 | 2026-07-20 | 维护 | C | 检测模型路线代表；需下载模型 |
| anthropics/claude-quickstarts | 17,938 | 2026-10-08 | 活跃 | B | 官方 computer-use 范式：VLM 输出坐标 |
| showlab/ShowUI | 1,917 | 2026-04-24 | 维护 | C | GUI 视觉-文本对齐；2B 权重 |
| microsoft/SoM | 1,569 | 2024-08-19 | 停更2年 | 理论 | SoM 官方实现基于 SAM/SEEM（重）；我们轻量重实现其思想 |
| xlang-ai/OpenCUA | 852 | 2026-05-25 | 维护 | B+C | 基础+评测路线 |
| njucckevin/SeeClick | 495 | 2025-07-13 | 停更 | C | GUI grounding 理论来源；9.6B 权重 |
| OS-Copilot/OS-Atlas | 455 | 2025-04-20 | 停更 | C | 基础动作模型；7B 权重 |

**结论**：高 star 的活跃项目全部押在路线 B/C；
"确定性轻量 grounding"这一格是空的——不是没人想过，
而是社区默认"要么上模型要么上 VLM"。
对**可审计性要求高的场景**（科研软件操作、危险动作把关、离线环境），
这个空格有真实价值。

## 5. MarkForge 设计

### 5.1 流水线（五模块）

```
截图 ─→ ①detect ─→ ②ocr(可选) ─→ ③marking ─→ ④ground ─→ ⑤vlm复核(可选) ─→ click
        连通域      三后端降级     SoM编号徽章    查询评分       布尔判定        安全门
```

### 5.2 查询语法（`ground.py`）

| 语法 | 语义 | 适用 |
|---|---|---|
| `#3` | Set-of-Mark 编号引用 | 上层 VLM/人看过 marked 图后下指令 |
| `text:提交` | OCR 文本包含匹配 | 有 OCR 后端时 |
| `color:#1769e0` | 主色最近邻 | 色块按钮/图标 |
| `max_area` | 面积最大元素 | 兜底 |

评分全部确定性：文本完全匹配 1.0 / 部分 0.6-0.8；
颜色欧氏距离归一；**前两名得分差 < 10% 判 AMBIGUOUS**，交上层裁决。

### 5.3 安全门（`engine.click`）

1. `status != GROUNDED` → REFUSED
2. 命中文本含危险词（删除/提交/购买/登录/上传/发送…）→ REFUSED
   （需显式 `override_danger=True` 才放行，且记录在审计里）
3. VLM 复核（若配置）失败 → REFUSED
4. 实际点击前重新截图，绝不复用陈旧坐标
5. 每次决策写入 `eng.last_audit`

### 5.4 渐进降级

- OCR：tesseract → easyocr → none（无 OCR 时 text: 查询返回可读的 NO_MATCH）
- 截图后端：playwright → CDP proxy → 明确报错
- VLM 复核：未配置即跳过（确定性引擎结果直接用）

## 6. 局限（诚实声明）

- **非实心/非彩色控件**（扁平线框、纯图标字体）检出率下降——
  边缘检测分支只部分缓解；这正是 OmniParser 用模型解决的问题
- **无 OCR 时文本查询不可用**（颜色/编号/几何仍可用）
- **复杂背景图片**（照片级 UI）会有误检——目标场景是软件界面不是自然图像
- **不生成自然语言描述**——它只做"查询→坐标"，描述该元素是什么仍需上层
- 未在 ScreenSpot 基准上系统评测（那需要下载基准数据 + 模型对比，
  超出本次"本地确定性引擎"的范围；作为后续工作）
- **max_area 上限 25%**：占屏幕超过 1/4 的超大控件（全屏横幅、模态遮罩）
  会被过滤掉——设计取舍：这类"元素"几乎总是背景而非点击目标

## 6b. 2026-10-13 复核修复记录（五个 bug，全部附回归测试）

| 编号 | 问题 | 修复 |
|---|---|---|
| B1 | 颜色查询无距离上限：查白色会硬凑最近邻返回 GROUNDED（假阳性，打破核心承诺） | `ground()` 新增 `max_color_dist=80`；超限返回 NO_MATCH 并在 reason/extra 里给出距离证据 |
| B2 | "点击前重新截图"只拍照不验证——宣称的防陈旧坐标是口号不是行为 | `engine._validate_fresh()`：新截图重新 detect，原质心必须落在某候选 bbox±20px 内且非巨型背景元素，否则拒签 |
| B3 | 显式 `ocr_backend="tesseract"` 但未安装 → detect 时 FileNotFoundError 崩溃 | `Engine.__init__` 快速失败：显式指定的后端不可用直接 ValueError（含安装指引），auto/none 不受影响 |
| B4 | `attach_text_to_elements` 用 `list.index()` 按值查 OCR 词块：重复词块错位归属 → 同一位置升格幽灵元素 → 假 AMBIGUOUS | 先按 (text, bbox) 去重，再用枚举索引 `used`；回归测试验证单一"确定"按钮正确 GROUNDED |
| B5 | CLI `click` 强制 `--url required`：只想 dry-run 安全检查也过不去 | `--url` 可选；缺省自动 dry-run 并在输出里注明 |

复核同时清理：`detect.py` 删除死代码（未用的 `_hex_from_bgr`、占位变量 `sample`）。
调试中发现测试画布若按钮占比 > 25% 会被 max_area 过滤——这是设计行为（见局限节），
测试用例画布已改为 600×400（按钮占 8.3%）。

## 7. 后续路线

1. tesseract 装上后补 text: 查询的真实页面 e2e
2. 接入 ScreenSpot 子集做定量评测（与 SeeClick 论文报告的数字对照）
3. VLM 复核命令模板给一个 Claude CLI 的实例
4. 模板匹配（cv2.matchTemplate）作为第三种检测分支，覆盖图标型控件
5. B2 的 `_validate_fresh` 目前按 bbox 包含判定；可加颜色一致性检查
   （新帧该 bbox 的主色 vs 原元素主色距离 < 阈值）进一步收紧
