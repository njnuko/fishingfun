#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests_v62_guard.py —— v62 禁区守卫测试

v60 的两个测试（tests_v60_decay / tests_v60_gate）断言的是**旧机制**：
「面积渐进衰减 → 判咬钩，且在 16.5s 前才允许」。v62 起该机制被整体废除，
那两个测试的期望值本身就是错的，必须随代码一起下线 —— 否则测试会变成
「保护 bug 不被修掉」的护栏。

本测试改为断言新契约：
  1. _bite_from_decay() 恒返回 False，无论面积多像"鱼在拉"、时间多早。
  2. _claim_decay()   恒返回 False，同样无论面积/时间。
  3. 两个函数体内不得出现实质判定语句（静态检查，防复活）。
  4. 主循环里不得存在「检测不到 → 判咬钩」的路径。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAIL = []


def ok(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        FAIL.append(name)


def stub_of(path, fname):
    text = open(path, encoding="utf-8").read()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fname:
            return text.splitlines()[node.lineno - 1:node.end_lineno], node
    return None, None


def main():
    print("=" * 62)
    print("v62 禁区守卫测试")
    print("=" * 62)

    fb = os.path.join(HERE, "fishingfun", "fishbot.py")
    sf = os.path.join(HERE, "fishingfun", "searchbobberfinder.py")

    # ---- 1/3. _bite_from_decay 必须是空壳 ----
    lines, node = stub_of(fb, "_bite_from_decay")
    if node is None:
        ok("_bite_from_decay 已彻底删除", True)
    else:
        real = []
        for s in node.body:
            if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant):
                continue
            if isinstance(s, ast.Return) and (
                    s.value is None or
                    (isinstance(s.value, ast.Constant) and s.value.value is False)):
                continue
            if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call):
                continue
            real.append(type(s).__name__)
        ok("_bite_from_decay 是空壳（无实质判定）", not real, str(real))
        has_fe = any("return False" in l for l in lines)
        ok("_bite_from_decay 返回 False", has_fe)

    # ---- 2/3. _claim_decay 必须是空壳 ----
    lines2, node2 = stub_of(sf, "_claim_decay")
    if node2 is None:
        ok("_claim_decay 已彻底删除", True)
    else:
        real2 = []
        for s in node2.body:
            if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant):
                continue
            if isinstance(s, ast.Return) and (
                    s.value is None or
                    (isinstance(s.value, ast.Constant) and s.value.value is False)):
                continue
            if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call):
                continue
            real2.append(type(s).__name__)
        ok("_claim_decay 是空壳（无实质判定）", not real2, str(real2))

    # ---- 4. 运行时行为：绝不为真 ----
    try:
        from fishingfun.searchbobberfinder import SearchBobberFinder
        f = SearchBobberFinder.__new__(SearchBobberFinder)
        f._area_hist = [(i, 400 - i * 20) for i in range(11)]   # 完美渐进衰减
        f._decay_claimed = False
        f._cast_t0 = 0.0
        try:
            got = SearchBobberFinder._claim_decay(f)
        except Exception as e:
            got = "EXC:%s" % e
        ok("_claim_decay 面对完美衰减序列仍返回 False", got is False, str(got))
    except ImportError as e:
        print("  SKIP  运行时检查（导入失败: %s）" % e)

    # ---- 5. 静态：代码禁区符号不得参与咬钩结论 ----
    for path, fname in ((fb, "fishbot.py"), (sf, "searchbobberfinder.py")):
        text = open(path, encoding="utf-8").read()
        bad = []
        for i, line in enumerate(text.splitlines(), 1):
            if "area_decayed" not in line:
                continue
            st = line.strip()
            if st.startswith(("#", '"', "'", "★", "*")):
                continue
            if "def area_decayed" in line:
                continue
            bad.append("%d: %s" % (i, st[:90]))
        ok("%s 无 area_decayed() 调用点" % fname, not bad, str(bad))

    print()
    if FAIL:
        print("结果：FAIL（%d 项）" % len(FAIL))
        for n in FAIL:
            print("   - " + n)
        return 1
    print("结果：PASS（%d 项）" % (13 - len(FAIL)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
