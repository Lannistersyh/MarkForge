# MarkForge

[English](README.md) | [简体中文](README.zh-CN.md)

**为 GUI Agent 提供确定性、可审计的视觉定位与连续电脑操作，无需下载检测模型。**

MarkForge 把“点击那个按钮”拆成可解释的计算：从截图中用传统计算机视觉检测候选控件，叠加 Set-of-Mark 编号，再把颜色、文字或编号查询转换为像素坐标。相同输入得到相同定位结果，每个决策都有机器可读的理由。

现在它也提供持续的 **观察 → 批量操作 → 验证** 会话：复用同一个应用窗口或浏览器页面，优先使用语义控件，必要时再使用截图定位，不必每次按键都重新截图。

## 能做什么

- **离线视觉定位**：连通域、颜色与几何特征检测，Set-of-Mark 编号，以及可选 OCR。
- **宿主 Computer Use 适配**：在已有的 `cua_repl` 中加载控制器，默认读取无障碍状态，按需截图。
- **独立 macOS 控制**：通过 Python、Quartz 与无障碍接口截图、点击、滚动和输入文字。
- **独立浏览器控制**：通过 Playwright 复用一个自有页面，支持已安装的 Edge、Chrome，或 Playwright 自带的 Chromium。
- **连续执行**：每批最多 16 个动作，同一观察帧复用 CV 检测结果，支持 Retina 坐标转换、结果校验与部分失败报告。

它是供上层 Agent 调用的定位与执行组件；任务理解、操作授权和是否继续执行，仍由上层 Agent 与宿主环境负责。

## 快速开始：截图定位

```bash
git clone https://github.com/Lannistersyh/MarkForge.git
cd MarkForge
python3 -m pip install -r requirements.txt

# 为已有截图检测候选控件并添加编号
python3 -m markforge.cli --ocr none annotate your-shot.png --out marked.png

# 按颜色、文字或编号定位
python3 -m markforge.cli ground your-shot.png "color:#1769e0"
python3 -m markforge.cli ground your-shot.png "text:Submit"
python3 -m markforge.cli ground your-shot.png "#2"
```

核心依赖是 OpenCV 与 Pillow，不下载检测模型。`text:` 查询需要可用的 OCR 后端；例如，安装 Tesseract 与 Python 适配包：

```bash
brew install tesseract
python3 -m pip install pytesseract
```

没有 OCR 时，颜色、编号与几何定位仍可使用。

## 连续电脑操作

### 方式一：使用已有的 Computer Use 宿主

先按照宿主工具的文档获取用户授权的应用对象 `app`，再在持久化的 `cua_repl` 中加载适配器。把示例路径替换为本仓库的真实绝对路径：

```javascript
var {createController} = await import('file:///absolute/path/to/MarkForge/scripts/cua-control.mjs');
var control = createController(app);
var observed = await control.observe();
```

默认观察返回无障碍状态；需要截图时使用 `observe({visual:true})`。随后通过 `control.act(actions, {frame_id:observed.frame_id})` 连续执行点击、按键或粘贴等动作，并读取返回的新观察结果。

截图坐标需要来自视觉观察；同一批次复用多个坐标时，须先判断布局稳定并声明 `stableLayout:true`。仅支持 DOM 的浏览器标签页应使用宿主提供的 Playwright 接口，不使用这个原生输入适配器。

完整动作示例见[连续控制指南](docs/continuous-control.md)。

### 方式二：独立 Python / macOS 通道

安装原生截图与输入依赖：

```bash
python3 -m pip install -r requirements-macos.txt
```

在 macOS“系统设置 → 隐私与安全性”中，为实际运行控制器的宿主应用授予**屏幕录制**与**辅助功能**权限。控制器不会自动修改系统授权。

以下示例假定计算器已打开，处于基础计算模式且输入区为空：

```bash
bash scripts/control.sh start --session calc --app com.apple.calculator
bash scripts/control.sh observe --session calc --out /tmp/calc-before.png
```

把观察结果中的 `frame_id` 替换到下面的 `FRAME_ID`，在 60 秒内执行同一批次的 `17 + 25`：

```bash
bash scripts/control.sh act --session calc --frame-id FRAME_ID \
  --actions '[{"type":"keypress","keys":["1"]},{"type":"keypress","keys":["7"]},{"type":"keypress","keys":["SHIFT","="]},{"type":"keypress","keys":["2"]},{"type":"keypress","keys":["5"]},{"type":"keypress","keys":["ENTER"]}]' \
  --expect-text 42 --out /tmp/calc-after.png

bash scripts/control.sh stop --session calc
```

有多个可见窗口时，启动器会列出候选，需要通过 `--window-id` 选择。可用 `MARKFORGE_PYTHON` 指定解释器；启动器也会识别当前 Conda 环境及用户目录中的 Anaconda。

客户端仅使用 Python 标准库，CV 与系统接口保留在后台进程中，避免每次操作重复加载。

### 方式三：独立浏览器通道

安装 Playwright：

```bash
python3 -m pip install playwright
```

使用已安装的 Edge 启动一个自有测试页面：

```bash
bash scripts/control.sh start --session web --backend browser \
  --channel msedge --url 'https://www.selenium.dev/selenium/web/web-form.html'
bash scripts/control.sh observe --session web --out /tmp/web-before.png
```

后续 `act` 操作始终复用这一页面；动作格式与示例见[连续控制指南](docs/continuous-control.md)。使用完毕后关闭自有会话：

```bash
bash scripts/control.sh stop --session web
```

- 已安装的 Edge：`--channel msedge`。
- 已安装的 Chrome：`--channel chrome`。
- 不指定 `--channel`：使用 Playwright 自带的 Chromium，需要先执行 `python3 -m playwright install chromium`。

使用已安装的 Edge 或 Chrome 时，无需另外下载 Chromium。这仍是独立浏览器会话，**不会自动继承用户日常浏览器的登录状态**。

仅操作用户授权的网站，不绕过宿主的访问限制；结束时关闭自己的浏览器，不关闭用户原有标签页。

## 会话、安全与失败处理

连续控制器采用以下约束：

- 每批最多 16 个动作，观察帧有效期为 60 秒。
- 执行前检查整批动作结构与坐标；执行中快速检查焦点、窗口几何或页面身份。
- 滚动、跳转、弹窗等作为批次边界，之后重新观察。页面内部是否仍保持布局稳定，需要调用方判断。
- `OK` 只表示动作派发完成，仍需检查最终界面；`--expect-text` 可校验结果文字，不匹配时返回 `VERIFY_FAILED`。
- `PARTIAL` 表示已有部分动作生效，不能直接重放整批；应重新观察并确定剩余操作。
- 支持 `--dry-run`，只校验、不注入输入。
- 使用仅当前用户可访问的 Unix socket，不监听 TCP；后台进程空闲十分钟后退出。
- 停止会话不会退出用户的原生应用；浏览器模式会关闭本控制器创建的浏览器。

提交、发送、购买、删除等敏感操作仍须遵守宿主的授权规则。批量执行不扩大原有授权范围。

## 视觉定位原理与查询方式

离线视觉管线为：

```text
截图 → 候选检测 → 可选 OCR → 编号标注 → 查询定位 → 校验 → 点击
```

候选检测使用连通域、HSV 颜色与 Canny 边缘等传统方法，不依赖专用检测模型。

- `#3`：选择 Set-of-Mark 图上的第 3 个候选，使用前应查看编号图。
- `text:Submit`：按 OCR 文字匹配，需要可用的 OCR 后端。
- `color:#1769e0`：按颜色距离选择候选，距离上限为 80；不存在合适颜色时返回 `NO_MATCH`，不强行选点。
- `max_area`：选择面积最大的候选，作为显式兜底查询。

前两名评分差距不足 10% 时，返回 `AMBIGUOUS` 与次优候选，由调用者决定，不默默猜测。

原有 `Engine.click` 接口还包含独立的安全门禁：

- 结果不是 `GROUNDED` 时拒绝点击。
- 目标文字包含 delete、submit、purchase、login、upload、send 等敏感词时，默认拒绝；显式覆盖会留下记录。
- 可选 VLM 复核失败时拒绝。
- 真实点击前重新截图检测；旧坐标不再落在候选区域内时拒绝。
- 决策及理由记录在 `eng.last_audit` 中。

这些逐次点击门禁属于原有 `Engine.click` API；连续控制器使用前述帧检查与批次验证策略，两者不要混为一谈。

### Python 库调用

```python
from markforge.engine import Engine

eng = Engine(ocr_backend="auto")  # 不需要 OCR 时使用 "none"
els = eng.detect("shot.png")
marked = eng.annotate("shot.png", els, "marked.png")
result = eng.ground("color:#1769e0", els)

if result.status == "GROUNDED":
    verdict = eng.click(result, marked_image=marked["path"], dry_run=True)
    assert verdict["decision"] == "DRY_RUN"
```

## 本机实测

2026-10-16 在开发用 Mac 上完成以下冒烟测试：

- **Computer Use 适配器**：一次执行六次按键，计算器显示 `17 + 25 = 42`，约 627 ms。
- **独立 Python / macOS**：五次语义点击完成 `8 × 9 = 72`，约 278 ms；七步原生键盘相关操作完成 `13 + 29 = 42`，约 223 ms。
- **独立 Edge**：在 Selenium 官方公开测试表单中连续执行五步输入与点击，最终显示 `Form submitted / Received!`，约 599 ms；观察次数从 1 变为 2，始终只有一个自有页面。

以上计时包含动作执行与结果读取，**不包含模型推理、外层工具调度及初始浏览器启动**。未进行同任务旧版对照，不应据此宣称固定倍数的加速。实测只覆盖上述应用与页面，不代表所有软件和网站均已验收。

验收后已关闭自有浏览器、后台进程与 socket，用户原有浏览器标签保持不变。详情见[实测与控制器说明](docs/continuous-control.md#本机验收)。

## 自动化测试

```bash
python3 -m pip install pytest
python3 -m pytest tests/markforge/test_core.py tests/markforge/test_session.py -q
node --test tests/cua-control.test.mjs
```

本轮本地验证通过 **45 项 Python 检查与 4 项 JavaScript 检查**。这些自动化检查不需要真实桌面输入或浏览器，覆盖 CV 结果复用、批次后截图、Retina 坐标转换、错误坐标、失焦、窗口变化、旧帧拒绝、部分失败和结果校验等。

## 示例与设计背景

原始截图：

![原始截图](assets/demo-shot.png)

Set-of-Mark 编号结果：

![候选控件编号图](assets/demo-marked.png)

运行 `bash scripts/demo.sh` 可复现示例；需要本地 HTTP 服务与截图后端，脚本提供原始 CDP 截图兜底。

相关研究与设计参考：

- [Set-of-Mark](https://arxiv.org/abs/2310.11441)：叠加编号，使定位转化为按编号引用。
- [SeeClick](https://arxiv.org/abs/2401.10935)：将 GUI grounding 作为可单独测试的能力。
- [LLM-Brained GUI Agents 综述](https://arxiv.org/abs/2411.18279)：GUI Agent 领域背景。
- [设计分析与方案比较](docs/l4-theory.md)：实现思路、回归问题与局限。

## 已知局限

- 扁平、仅轮廓控件及图标字体可能难以检测。
- 没有 OCR 后端时，无法使用 `text:` 查询。
- 照片式背景可能产生较多误检，主要目标是软件界面，不是自然图像。
- 尚未运行 ScreenSpot 基准测试。
- 面积超过整张截图 25% 的元素会被当作背景过滤，可能影响大型弹窗等目标。
- 连续操作依赖对当前界面及批次边界的判断，不能自动发现所有页面内部变化。

## 作为 Agent Skill 使用

可复用的 Agent 指令位于 [SKILL.md](SKILL.md)。**整个仓库才是完整的 skill 包**，安装或复制时需要保留 `scripts/`、`markforge/` 等配套资源，不能只复制一份 `SKILL.md`。

## 许可证

[MIT License](LICENSE)。
