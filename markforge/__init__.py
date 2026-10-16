"""MarkForge — 确定性、可审计的 L4 视觉 grounding 引擎。

理论出处：
- Set-of-Mark prompting (arXiv:2310.11441)：把候选区域叠加编号标记，
  让上层模型通过引用标记实现精准 grounding。
- SeeClick / GUI grounding (arXiv:2401.10935)：GUI grounding =
  "给定指令，在截图上定位元素" 的能力，是视觉 GUI agent 的核心挑战。

设计原则（与重模型派/VLM 全权派的差异）：
1. 零模型下载：经典 CV（连通域 + 颜色 + 几何），OpenCV/Pillow 即可运行。
2. 确定性：同输入同输出，坐标可复核、可重放、可审计。
3. 渐进增强：OCR 缺席时自动降级为颜色/几何 grounding。
4. 连续会话复用稳定界面的观察帧；布局改变后重新观察并校验结果。
"""

__version__ = "0.1.0"

from .types import Element, GroundResult, MarkedImage  # noqa: F401
