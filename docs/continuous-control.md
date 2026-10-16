# 连续电脑操作

MarkForge 现在提供持续的观察/执行接口。原来的离线 grounding 命令保持兼容。

## 宿主已有 Computer Use 时

先通过工具入口获取授权的 App 或支持原生输入的 Tab，再在 `cua_repl` 加载。将示例中的 `/absolute/path/to/MarkForge` 替换成实际检出目录的绝对路径：

```javascript
var {createController} = await import('file:///absolute/path/to/MarkForge/scripts/cua-control.mjs');
var control = createController(app); // Tab 则传第二参数 {kind:'tab'}
var observed = await control.observe({visual:true});
var result = await control.act([
  {type:'keypress', key:'1'},
  {type:'keypress', key:'7'},
  {type:'keypress', key:'shift+equal'},
  {type:'keypress', key:'2'},
  {type:'keypress', key:'5'},
  {type:'keypress', key:'Return'}
], {frame_id:observed.frame_id});
```

`observe()` 默认返回并显示无障碍状态；`visual:true` 同时取得截图。支持 `click`（`target` 为当前索引或截图坐标）、`keypress`（`key`）、`paste`（`text`）、`scroll`（`target,direction,pages`）。DOM-only 标签用宿主提供的 Playwright 语义接口，不使用原生输入适配器。

截图坐标只接受视觉帧；多次复用坐标须声明 `stableLayout:true`。批次末尾自动读取新无障碍状态，需要截图加 `visualAfter:true`。下一批直接使用返回 `observation` 的新帧和控件索引。

## 独立 Python 控制器

启动器选择 `MARKFORGE_PYTHON`，否则优先用本机 Anaconda。客户端仅用标准库；CV、Quartz、浏览器对象在后台进程中保持加载。

```bash
bash scripts/control.sh start --session calc --app com.apple.calculator
bash scripts/control.sh observe --session calc --out /tmp/calc-before.png
bash scripts/control.sh status --session calc
```

多个可见窗口时，启动列出候选，需用 `--window-id` 选择。Mac 宿主需要屏幕录制与辅助功能权限，控制器不修改系统授权。

把观察返回的 `frame_id` 放进下一条命令：

```bash
bash scripts/control.sh act --session calc --frame-id FRAME_ID \
  --actions '[{"type":"keypress","keys":["1"]},{"type":"keypress","keys":["7"]},{"type":"keypress","keys":["SHIFT","="]},{"type":"keypress","keys":["2"]},{"type":"keypress","keys":["5"]},{"type":"keypress","keys":["ENTER"]}]' \
  --expect-text 42 --out /tmp/calc-after.png
bash scripts/control.sh stop --session calc
```

其他动作：

- `{"type":"click","label":"7"}`：按唯一无障碍标签点按钮；浏览器可指定 `role`。
- `{"type":"click","x":100,"y":200}`：截图像素坐标；Mac 自动映射 Retina 像素到屏幕点。
- `{"type":"click","query":"color:#1769e0"}`：复用 CV 结果，支持 `#N`；已有 tesseract 时支持 `text:`。
- `{"type":"type","text":"Hello，中文"}`：原样 Unicode 输入，不更改剪贴板。
- `{"type":"scroll","dy":300}`：滚动，须放在批次末尾。
- `{"type":"fill","label":"搜索","text":"关键词"}`：浏览器语义填充，须放在批次末尾。

CV 候选用 `observe --detect`，编号图按需用 `--marked-out PATH`，OCR 按需用 `--ocr`，不下载模型。同一帧多次查询共享检测。连续坐标操作加 `--stable-layout`；`--dry-run` 校验但不注入输入。每批最多 16 动作，帧有效期 60 秒。

浏览器模式启动一次自己的页面，后续一直复用：

```bash
bash scripts/control.sh start --session web --backend browser --url 'https://example.com'
```

仅对用户授权目标使用，不绕过宿主浏览器访问限制。结束时关闭自己的浏览器，不关闭用户原有浏览器或退出 Mac 应用。

## 会话与失败处理

控制器通过仅当前用户可访问的 Unix socket 通信，不监听 TCP；空闲十分钟退出。截图保留在内存，指定输出路径才保存，标注/兼容后端可能短暂使用临时文件。动作日志不记录输入文本。

执行前校验整批结构和坐标；每步快速检查焦点、窗口几何或页面 URL。布局稳定由调用方根据当前界面判断，控制器不会自动发现所有页面内部变化。跳转、滚动、弹窗等作为批次边界。

`OK` 表示动作已派发，仍检查返回界面。`--expect-text` 短时轮询结果文字，失败返回 `VERIFY_FAILED`。`PARTIAL` 表示部分动作已执行，不得直接重放整批。失焦、旧帧或中途异常后重新观察。

## 本机验收

2026-10-16，通过新 `cua-control.mjs` 在 macOS 计算器上一次执行六次按键；界面显示 `17 + 25`，结果 `42`。适配器计时 627 ms，包括执行与最终无障碍状态读取，不包含外层模型思考及工具调度；未做同任务旧版对照。

Python 离线测试覆盖：四次点击只取一张批次后截图、CV 结果复用、错误坐标、失焦、窗口变化、旧帧、中途失败和结果校验。当前 Python 宿主辅助功能检查为未授权，因此独立原生输入尚未实机验收。浏览器适配器尚未做真实页面端到端验收。

```bash
python3 -m pytest tests/markforge/test_session.py tests/markforge/test_core.py -q
node --test tests/cua-control.test.mjs
```
