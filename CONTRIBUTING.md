# 参与贡献

感谢你愿意折腾这个工具。这份文档只讲三件事：**怎么跑起来、怎么跑测试、什么不能碰**。

## 环境

Windows 10/11 + Python 3.10+。不需要管理员权限。

```bat
git clone https://github.com/FireKeymanzz/wordgrab.git
cd wordgrab
start.bat
```

`start.bat` 会建 `.venv` 并装依赖。只想改代码跑测试的话：

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\playwright install chromium   :: 只有跑 test_extension.py 才需要
```

## 跑测试

```bat
run_tests.bat
```

**加任何功能/修任何 bug，都得先跑一遍再提 PR。** `run_tests.bat` 会在跑之前自动给 `data/` 存一份快照。

10 个套件里有 4 个（`test_capture_win` / `test_e2e_dblclick` / `test_regression_thread` / `test_ui`）
需要真实鼠标和前台焦点：跑之前请把桌面切到前台、别让别的程序抢焦点。
`test_e2e_dblclick` 抢不到焦点时会偶发失败，重试一次通常就好。

CI 只跑不依赖桌面的那几组（core / export / concurrency / backup / sandbox_guard / extension）。

### 铁律：测试绝对不能碰 `data/`

`data/` 里是**用户真实的生词库**——读的什么书、记了什么笔记，都是隐私，而且丢过一次。

规矩是硬性的：

1. 每个测试文件**第一件事**就是 `from sandbox import activate, verify`，
   在 import `wordgrab` **之前**调 `activate()`（它把 `WORDGRAB_DATA` 指向临时目录）。
2. 测试**最后**调 `verify()`——它会对真实目录做指纹（大小 + mtime + sha256）比对，
   任何改动都会抛 `SandboxViolation`。
3. 想给 `data/` 做「有意的」改动（比如首次写哨兵文件）才用 `sandbox.rebase()`，
   其它情况一律当作测试失败。

照抄 `tests/test_core.py` 的头尾就行：

```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sandbox import activate, verify
TMP = activate("wordgrab_core_")
from wordgrab import db          # 必须在 activate() 之后
...
verify()
```

`tests/test_sandbox_guard.py` 会主动往真实目录写一次、确认 `verify()` 一定报错——
所以这道防线是被测过的，不是摆设。

## 代码约定

- **Python 3.10+**，只用标准库 + `requirements.txt` 里的依赖，别再加新依赖（保持「clone 下来就能跑」）。
- 风格跟现有代码走：标准库、`from __future__ import annotations`、中文注释解释**为什么**而不是复述代码。
- **Tk 不是线程安全的**。后台线程（取词钩子、词典查询）要碰界面，一律走
  `panel.post_task(fn, *args)`，别在子线程里直接调 `after()` / `createcommand()`。
  （这个坑已经踩过一次，`test_regression_thread.py` 就是它的守门员。）
- **SQLite 一线程一连接**。用 `db.get_conn()`，别自己 `sqlite3.connect()`；
  写操作用 `db._lock`（或 `db` 模块里已有的函数）串起来。
- 改数据库结构：只加列/表，别删改已有列——用户库是**长期演进**的，删列等于毁数据。
  加完记得在 `tests/test_core.py` 里补一条兼容性断言。
- 界面配色/间距集中改 `wordgrab/ui.py` 顶部的常量，别在控件里散落魔法数字。

## 提 PR

- 一个 PR 只做一件事，描述里写清**为什么**改（不是「改了什么」）。
- 附上跑测试的结果；涉及界面的改动最好附截图。
- 新功能顺手补测试；`README.md` 里对应章节一起更新（README 是给人看的第一入口）。

## 想找活干？

- [open issues](https://github.com/FireKeymanzz/wordgrab/issues) 里带 `good first issue` 的
- 词典源（现在只有有道 + dictionaryapi.dev + 可选 ECDICT）——接一个 Wiktionary / 金山 / Bing
- 复习算法升级到 FSRS
- macOS / Linux 支持（工程量巨大，但重构空间也大）
- 界面本地化（现在只有中文）

## 许可

贡献即表示你同意你的代码以 [MIT](LICENSE) 协议发布。
