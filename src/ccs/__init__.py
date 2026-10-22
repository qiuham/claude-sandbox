"""ccs（claude-sandbox）—— 手工/自动两用的注册沙盒。

包内按职责分层：
errors（输出）、config（配置）、paths（路径）、sandbox（元数据和生命周期）、
network（出口、预检、DoH、DNS 中继）、browser（偏好、进程、启动、任务）、
report（终端输出）、cli（参数解析）。
"""

from __future__ import annotations

__version__ = "0.1.0"
