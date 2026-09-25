"""混样批次的原始样本占比计算。

全程使用 fractions.Fraction 做精确有理数运算，避免浮点误差；
Fraction 会自动约分，分子/分母即为最简形式。
"""
from __future__ import annotations

import math
import sqlite3
from fractions import Fraction

Composition = dict[int, Fraction]  # {原始样本 id: 占比}
Recipes = dict[int, list[tuple[int, int]]]  # {混样批次 id: [(原料批次 id, 份数)]}


class PoolPlanError(ValueError):
    """混样规划在业务上无解（目标不可达或最小解超过总份数上限），对应 HTTP 422。"""


def _load_graph(conn: sqlite3.Connection) -> tuple[dict[int, str], Recipes]:
    """读取全部批次类型与配方，构成内存中的配方引用图。"""
    kinds = {row["id"]: row["kind"] for row in conn.execute("SELECT id, kind FROM batches")}

    recipes: Recipes = {}
    for row in conn.execute("SELECT pool_id, ingredient_id, portions FROM pool_recipes"):
        recipes.setdefault(row["pool_id"], []).append((row["ingredient_id"], row["portions"]))
    return kinds, recipes


def _expand(
    kinds: dict[int, str],
    recipes: Recipes,
    batch_id: int,
    memo: dict[int, Composition],
) -> Composition:
    """递归展开批次；memo 让多路径汇合的批次只精确计算一次。"""
    if batch_id in memo:
        return memo[batch_id]
    if kinds[batch_id] == "sample":
        memo[batch_id] = {batch_id: Fraction(1)}
        return memo[batch_id]
    items = recipes.get(batch_id, [])
    total = sum(portions for _, portions in items)
    result: Composition = {}
    for ingredient_id, portions in items:
        share = Fraction(portions, total)
        for sample_id, frac in _expand(kinds, recipes, ingredient_id, memo).items():
            result[sample_id] = result.get(sample_id, Fraction(0)) + share * frac
    memo[batch_id] = result
    return result


def reachable_samples(conn: sqlite3.Connection, ingredient_ids: list[int]) -> set[int]:
    """沿配方图递归收集若干原料批次可达的全部原始样本 id。

    包含嵌套混样与多路径汇合：只要某原始样本经任意路径可达即被收集。
    配方图是有向无环图（新批次只能引用更早创建的批次），遍历必然终止。
    份数不影响可达性，故此处只需图结构，不做分数运算。
    """
    kinds = {row["id"]: row["kind"] for row in conn.execute("SELECT id, kind FROM batches")}
    recipes: dict[int, list[int]] = {}
    for row in conn.execute("SELECT pool_id, ingredient_id FROM pool_recipes"):
        recipes.setdefault(row["pool_id"], []).append(row["ingredient_id"])

    samples: set[int] = set()
    seen: set[int] = set()
    stack = list(ingredient_ids)
    while stack:
        node = stack.pop()
        if node in seen:  # 多路径汇合：同一批次只展开一次
            continue
        seen.add(node)
        if kinds.get(node) == "sample":
            samples.add(node)
        else:
            stack.extend(recipes.get(node, []))
    return samples


def batch_composition(conn: sqlite3.Connection, batch_id: int) -> Composition:
    """递归展开批次，返回 {原始样本 id: 占比}。

    - 每一层按“投入份数 / 该层总份数”分摊占比；
    - 同一原始样本经多条路径到达时，占比相加合并；
    - 新批次只能引用已创建的批次，引用关系天然是有向无环图，递归必然终止。
    """
    kinds, recipes = _load_graph(conn)
    return _expand(kinds, recipes, batch_id, {})


def downstream_batches(recipes: Recipes, batch_id: int) -> list[int]:
    """沿配方引用关系找出全部下游批次（含目标自身）。

    下游 = 直接或间接把目标批次当作原料的混样批次；
    多路径汇合时同一批次只收集一次，结果按 id 升序返回。
    """
    consumers: dict[int, list[int]] = {}
    for pool_id, items in recipes.items():
        for ingredient_id, _ in items:
            consumers.setdefault(ingredient_id, []).append(pool_id)

    seen = {batch_id}
    stack = [batch_id]
    while stack:
        for consumer in consumers.get(stack.pop(), []):
            if consumer not in seen:
                seen.add(consumer)
                stack.append(consumer)
    return sorted(seen)


def recipe_preview(
    conn: sqlite3.Connection, batch_id: int, new_recipe: dict[int, int]
) -> dict[int, tuple[Composition, Composition]]:
    """在内存中把 batch_id 的配方替换为 new_recipe，评估影响，不修改数据库。

    返回 {受影响批次 id: (原占比, 预演占比)}；新配方只允许引用比目标批次
    更早创建的批次（由调用方校验），替换后引用图仍是无环图，递归必然终止。
    """
    kinds, recipes = _load_graph(conn)
    affected = downstream_batches(recipes, batch_id)

    preview_recipes = dict(recipes)
    preview_recipes[batch_id] = sorted(new_recipe.items())

    original_memo: dict[int, Composition] = {}
    preview_memo: dict[int, Composition] = {}
    return {
        bid: (
            _expand(kinds, recipes, bid, original_memo),
            _expand(kinds, preview_recipes, bid, preview_memo),
        )
        for bid in affected
    }


def pool_plan(
    conn: sqlite3.Connection,
    ingredient_a: int,
    ingredient_b: int,
    sample_id: int,
    target: Fraction,
    max_total_portions: int,
) -> tuple[int, int, Composition]:
    """反算两个原料的正整数投入份数 (portions_a, portions_b)，使 sample_id 在
    新混样（仅由这两个原料按份数混合）中的占比恰好等于 target。

    设两原料中该样本的精确占比分别为 a、b（沿用 _expand 的分数展开），新混样
    占比 = (a·x + b·y) / (x + y) = target（x、y 为正整数份数）。一般情况下该
    方程的正整数解构成一族 x:y 固定的等比解，把比值约到最简即得到总份数最小
    的唯一解；当 a == b == target 时任意份数都满足，按约定取 1:1。无正整数解
    或最小总份数超过上限时抛 PoolPlanError。

    返回 (portions_a, portions_b, 新混样全部原始样本占比)。
    """
    kinds, recipes = _load_graph(conn)
    memo: dict[int, Composition] = {}
    comp_a = _expand(kinds, recipes, ingredient_a, memo)
    comp_b = _expand(kinds, recipes, ingredient_b, memo)

    a = comp_a.get(sample_id, Fraction(0))
    b = comp_b.get(sample_id, Fraction(0))

    if a == b:
        # 两原料中该样本占比相同：只有目标也相等时才有解，且任意正整数份数都满足，
        # 按要求取总份数最小的 1:1；否则目标在区间之外。
        if a != target:
            raise PoolPlanError(
                f"目标占比 {target.numerator}/{target.denominator} 无法用正整数份数达到: "
                f"两原料中该样本占比均为 {a.numerator}/{a.denominator}"
            )
        portions_a, portions_b = 1, 1
    else:
        # 目标必须严格落在两原料占比之间：区间之外（含只与一端相等）时，
        # 正份数加权平均不可能达到——相等端点会迫使另一原料投入 0 份。
        if not (min(a, b) < target < max(a, b)):
            raise PoolPlanError(
                f"目标占比 {target.numerator}/{target.denominator} 无法用正整数份数达到: "
                f"两原料中该样本占比为 {a.numerator}/{a.denominator}、{b.numerator}/{b.denominator}"
            )
        # (a - target)·x = (target - b)·y，两侧同号非零；交叉相乘后约到最简
        left = a - target
        right = target - b
        x_num = abs(right.numerator * left.denominator)
        y_num = abs(left.numerator * right.denominator)
        g = math.gcd(x_num, y_num)
        portions_a = x_num // g
        portions_b = y_num // g

    total = portions_a + portions_b
    if total > max_total_portions:
        raise PoolPlanError(
            f"最小整数解总份数为 {total}，超过上限 {max_total_portions}"
        )

    share_a = Fraction(portions_a, total)
    share_b = Fraction(portions_b, total)
    composition: Composition = {}
    for sid, frac in comp_a.items():
        composition[sid] = composition.get(sid, Fraction(0)) + share_a * frac
    for sid, frac in comp_b.items():
        composition[sid] = composition.get(sid, Fraction(0)) + share_b * frac
    return portions_a, portions_b, composition


def portion_diagnosis(
    conn: sqlite3.Connection,
    batch_id: int,
    measured: dict[int, Fraction],
    max_portions: int,
) -> tuple[list[tuple[int, int, int]], list[tuple[int, int]]]:
    """假设混样 batch_id 的配方中仅一项投入份数记错，反推该项的正整数份数。

    measured 为 {原始样本 id: 实测占比}（未列出的样本视为 0，总和已为 1，
    由调用方校验）。设被记错的项为 j（记录份数 p_j，实际份数 x），其余项
    份数之和 R 与其贡献 B_s 固定，则实际组成为 (B_s + x·A_s)/(R + x)，
    其中 A_s 为 j 沿嵌套配方精确展开的样本 s 占比。对每个样本得到有理数
    方程 x·(A_s − m_s) = m_s·R − B_s，任取 A_s ≠ m_s 的样本即可解出唯一
    候选 x，再用全部样本占比逐一复核。

    返回 (solutions, undetermined)，均按原料 id 升序：
    - solutions: [(原料批次 id, 原份数, 纠正份数)]，只含与原值不同且不超过
      max_portions 的解；
    - undetermined: [(原料批次 id, 原份数)]，份数变化不改变组成（B_s = A_s·R）
      且组成恰与实测吻合（A_s = m_s）的项——任意正整数份数都成立，无法判定，
      不枚举份数。
    不写库。
    """
    kinds, recipes = _load_graph(conn)
    recipe = recipes.get(batch_id, [])
    memo: dict[int, Composition] = {}
    comps = {ingredient_id: _expand(kinds, recipes, ingredient_id, memo)
             for ingredient_id, _ in recipe}

    total = sum(portions for _, portions in recipe)
    # 全部相关样本：实测列出的 ∪ 任一原料展开含有的（未列出的实测占比视为 0）
    all_samples = set(measured)
    for comp in comps.values():
        all_samples.update(comp)

    # 当前配方下各样本的未归一化分子 N_s = Σ_i p_i·comp_i(s)，
    # 去掉项 j 后即其余项的贡献 B_s = N_s − p_j·A_s
    numerator_sum = {
        s: sum((portions * comps[i].get(s, Fraction(0)) for i, portions in recipe),
               Fraction(0))
        for s in all_samples
    }

    solutions: list[tuple[int, int, int]] = []
    undetermined: list[tuple[int, int]] = []
    for ingredient_id, original in sorted(recipe):
        rest = total - original  # R：其余项份数之和
        comp_j = comps[ingredient_id]

        def others_contrib(s: int) -> Fraction:
            return numerator_sum[s] - original * comp_j.get(s, Fraction(0))

        # 份数变化不改变组成且与实测吻合：任意正整数份数都成立，无法判定
        if all(
            comp_j.get(s, Fraction(0)) == measured.get(s, Fraction(0))
            and others_contrib(s) == measured.get(s, Fraction(0)) * rest
            for s in all_samples
        ):
            undetermined.append((ingredient_id, original))
            continue

        # 任取一个 A_s ≠ m_s 的样本解出唯一候选 x；若不存在这样的样本
        # （A_s 恒等于 m_s 但某项 B_s ≠ m_s·R），则任何 x 都不满足，无解
        candidate: Fraction | None = None
        for s in all_samples:
            a_s = comp_j.get(s, Fraction(0))
            m_s = measured.get(s, Fraction(0))
            if a_s != m_s:
                candidate = (m_s * rest - others_contrib(s)) / (a_s - m_s)
                break
        if candidate is None or candidate.denominator != 1 or candidate < 1:
            continue
        corrected = candidate.numerator
        # 只保留不同于原值且不超过上限的解
        if corrected == original or corrected > max_portions:
            continue
        # 用全部样本占比复核（未列出的样本实测占比视为 0）
        if all(
            (others_contrib(s) + corrected * comp_j.get(s, Fraction(0)))
            / (rest + corrected)
            == measured.get(s, Fraction(0))
            for s in all_samples
        ):
            solutions.append((ingredient_id, original, corrected))
    return solutions, undetermined
