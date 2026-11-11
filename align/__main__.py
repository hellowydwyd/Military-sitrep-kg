"""align：规范名与消歧。

每个子模块都是独立命令，没有统一入口。常用：

    python3 -m align.zh build          # 汉语规范名（查维基标题）
    python3 -m align.zh localize       # 把规范名写回抽取结果
    python3 -m align.types             # 同一名称的多种类型归并
    python3 -m align.disamb            # 实体消歧与事件融合
    python3 -m align.edges             # 边的语义细分
    python3 -m align.apply --graph ... # 把 aliases 写回图谱

实验类（不参与主流程）：

    python3 -m align.compare           # 对比几种对齐方法
    python3 -m align.loc               # 地点对齐候选
    python3 -m align.verify            # 验证写回质量
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
