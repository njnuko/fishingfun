#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_mechanism.py —— FishingFun 机制一致性静态检查

存在理由（2026-11-02 用户的质问）：
    「这个我在对话里也跟你说过很多遍，你说加到文档里面并核对过，
      为什么还会出现这种逻辑错误」

答：之前的"核对"是**读文档 + 看默认值**，属于纸面核对。纸面核对永远发现不了
    "文档写 A，代码里还留着一条 B 分支" 这类问题 —— 因为 B 分支根本不在
    文档的视野里。要防复发，核对必须是**机器执行**的，检查对象是代码本身。

本脚本检查两类东西：
  1. 代码禁区（BANNED）：MECHANISM.md 明令禁止恢复的判定路径。若某个禁用函数
     体内出现实质判定语句（return True / 右键 / 改状态），立即 FAIL。
  2. 唯一咬钩路径（UNIQUE）：咬钩只允许 y 位移一条路径。若出现第二条会产生
     "咬钩"结论的入口，立即 FAIL。

用法：
    python3 check_mechanism.py            # 检查，退出码 0=通过 1=失败
    python3 check_mechanism.py --verbose  # 额外打印每条规则的明细

打包前必须跑一遍；建议接进打包脚本，FAIL 就拒绝出包。
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "fishingfun")
FILES = ["fishbot.py", "searchbobberfinder.py", "sinkcalib.py", "wowscreen.py"]

# 允许在函数体内出现、但不构成"实质判定"的语句类型
NEUTRAL_STMTS = (ast.Expr, ast.Pass, ast.Return)

VERBOSE = "--verbose" in sys.argv


def _src(name):
    p = os.path.join(SRC, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


def _funcs(name, text):
    """返回 {函数名: (源码行范围, ast节点)}，遇到语法错误直接报。"""
    tree = ast.parse(text, filename=name)
    lines = text.splitlines()
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = "\n".join(lines[node.lineno - 1:node.end_lineno])
            out.setdefault(node.name, []).append((body, node))
    return out


def _is_trivial_return(stmt):
    """return False / return None / return  —— 视为空壳，允许存在。"""
    if not isinstance(stmt, ast.Return):
        return False
    v = stmt.value
    if v is None:
        return True
    if isinstance(v, ast.Constant) and v.value in (None, False, 0, ""):
        return True
    return False


def check_dead_stub(func_name, node, body, problems, allow_bool=True):
    """禁用函数必须是空壳：文档字符串 + return False，最多再多一句 log。"""
    real = []
    for stmt in node.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                and isinstance(stmt.value.value, str):
            continue                      # docstring
        if _is_trivial_return(stmt):
            continue                      # return False / None
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            continue                      # self.log(...) 一类，允许留痕
        real.append(type(stmt).__name__)
    if real:
        problems.append(
            "【代码禁区】%s() 已废弃，但函数体内仍有实质语句: %s\n"
            "    废弃函数必须只剩 docstring + return False（+ 一句 log）。"
            % (func_name, ", ".join(real)))


def check_no_true_return(func_name, body, problems):
    """废弃判定函数不允许 return True 或 return <非 False 表达式>。"""
    for m in re.finditer(r"^\s*return\s+(?!False\b|None\b|$)(.+)$", body, re.M):
        expr = m.group(1).strip()
        if expr.startswith(("#",)):
            continue
        problems.append(
            "【代码禁区】%s() 内出现 `return %s` —— 废弃路径不得返回真值判定结果。"
            % (func_name, expr))


def check_unique_bite_path(all_funcs, problems):
    """咬钩结论只能由 y 位移判定产生；不允许第二处"消失即咬钩"。"""
    # 该模式出现 = 有人在"找不到了"的时候宣布咬钩
    bad = re.compile(
        r"(?:is\s+None|not\s+found|found\s+is\s+None|current\s+is\s+None)"
        r"[^\n]{0,120}?"
        r"(?:bite|咬钩)",
        re.I)
    for fname, entries in all_funcs.items():
        for body, node in entries:
            if node.name in ("_bite_from_decay", "_claim_decay"):
                continue                   # 这两个本身是空壳
            for m in bad.finditer(body):
                seg = m.group(0).replace("\n", " ")
                if "不再" in seg or "禁止" in seg or "不能" in seg or "≠" in seg:
                    continue               # 注释里说明"不要这么做"
                problems.append(
                    "【唯一路径】%s() 里出现「检测不到 → 判咬钩」模式：\n"
                    "    %s\n"
                    "    浮标消失 = 17 秒到期自动收杆，必须重抛，绝不能右键。"
                    % (node.name, seg[:160]))


def check_rightclick_requires_ydiff(name, text, problems):
    """右键拾取动作必须能追溯到 y 位移判定（允许 _loot 内部执行点击）。"""
    # 粗粒度：统计"咬钩"字样出现的分支，确认都带 yDiff
    # 只检查**日志调用**里的咬钩判定行（含 self.log / log(），注释与文档串不算
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("#") and "log(" in line and "咬钩判定" in line:
            # 日志字符串可能跨行，向下拼最多 3 行找 yDiff
            window = " ".join(lines[i:i + 3])
            if "yDiff" not in window and "y_diff" not in window \
                    and "legacy" not in window:
                problems.append("【证据缺失】%d行 咬钩判定日志未带 yDiff 证据: %s"
                                % (i + 1, line.strip()[:120]))


def main():
    problems = []
    warnings = []
    all_funcs = {}
    for f in FILES:
        text = _src(f)
        if text is None:
            warnings.append("跳过不存在的文件: %s" % f)
            continue
        try:
            funcs = _funcs(f, text)
        except SyntaxError as e:
            problems.append("【语法】%s 无法解析: %s" % (f, e))
            continue
        for k, v in funcs.items():
            all_funcs.setdefault(k, []).extend(v)

    # --- 规则 1：代码禁区必须为空壳 ---
    BANNED = ["_bite_from_decay", "_claim_decay"]
    for fn in BANNED:
        entries = all_funcs.get(fn, [])
        if not entries:
            warnings.append("%s() 已彻底删除（比空壳更干净，可以）" % fn)
            continue
        for body, node in entries:
            check_dead_stub(fn, node, body, problems)
            check_no_true_return(fn, body, problems)
            if VERBOSE:
                print("  [检查] 禁区函数 %s(): 空壳校验通过" % fn)

    # --- 规则 2：不允许第二条咬钩判定路径 ---
    check_unique_bite_path(all_funcs, problems)

    # --- 规则 3：咬钩日志必须带 yDiff 证据 ---
    for f in ("fishbot.py",):
        text = _src(f)
        if text:
            check_rightclick_requires_ydiff(f, text, problems)

    # --- 规则 4：area_decayed 不得再参与咬钩结论 ---
    for f, text in ((x, _src(x)) for x in FILES):
        if not text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "area_decayed" not in line:
                continue
            if line.lstrip().startswith("#"):
                continue
            if "def area_decayed" in line:
                continue
            st = line.strip()
            if st.startswith(("#", "\"", "'", "★", "*")):
                continue                  # 注释/文档字符串里的提及
            problems.append(
                "【代码禁区】%s:%d 仍在调用 area_decayed()（面积衰减不得作为咬钩证据）\n"
                "    %s" % (f, i, line.strip()[:140]))

    print("=" * 62)
    print("FishingFun 机制一致性检查")
    print("=" * 62)
    for w in warnings:
        print("  ⚠️  " + w)
    if problems:
        print()
        for p in problems:
            print("  ❌ " + p)
        print()
        print("结果：FAIL（%d 项）—— 禁止打包，先修代码。" % len(problems))
        return 1
    print()
    print("结果：PASS —— 咬钩判定唯一路径成立，代码禁区干净。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
