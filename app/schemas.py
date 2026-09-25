"""Pydantic 请求/响应模型。"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

# 严格的正整数：拒绝 0、负数、浮点数（如 1.5 / 2.0）与字符串（如 "3"）
StrictPositiveInt = Annotated[int, Field(strict=True, gt=0)]

# 严格的非负整数：允许 0，其余规则同 StrictPositiveInt
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]

BatchName = Annotated[str, Field(min_length=1, max_length=200)]


class SampleCreate(BaseModel):
    name: BatchName


class ExclusionRuleCreate(BaseModel):
    """登记一对不同的原始样本禁配对；顺序无关，服务端按 id 归一化。"""

    sample_a_id: StrictPositiveInt
    sample_b_id: StrictPositiveInt


class ExclusionRuleOut(BaseModel):
    # 无序对归一后按 id 升序返回
    low_sample_id: int
    high_sample_id: int
    created_at: str


class RecipeItem(BaseModel):
    batch_id: StrictPositiveInt
    portions: StrictPositiveInt


class PoolCreate(BaseModel):
    name: BatchName
    recipe: Annotated[list[RecipeItem], Field(min_length=1)]  # 拒绝空配方


class RecipeItemOut(BaseModel):
    batch_id: int
    batch_name: str
    portions: int


class BatchOut(BaseModel):
    id: int
    name: BatchName
    kind: Literal["sample", "pool"]
    created_at: str
    recipe: list[RecipeItemOut] | None = None  # 仅混样批次返回配方


class ComponentOut(BaseModel):
    sample_id: int
    sample_name: str
    numerator: int    # 约分后的分子
    denominator: int  # 约分后的分母


class CompositionOut(BaseModel):
    batch_id: int
    batch_name: BatchName
    kind: Literal["sample", "pool"]
    components: list[ComponentOut]


class RecipePreviewRequest(BaseModel):
    """完整替代配方（与创建混样相同：非空、严格正整数份数、重复原料合并）。"""

    recipe: Annotated[list[RecipeItem], Field(min_length=1)]  # 拒绝空配方


class FractionOut(BaseModel):
    numerator: int    # 约分后的分子（有符号）
    denominator: int  # 约分后的分母（恒为正）


class PreviewComponentOut(BaseModel):
    sample_id: int
    sample_name: str
    original: FractionOut
    preview: FractionOut
    delta: FractionOut  # 有符号差值 = preview - original


class AffectedBatchOut(BaseModel):
    batch_id: int
    batch_name: BatchName
    kind: Literal["sample", "pool"]
    components: list[PreviewComponentOut]


class RecipePreviewOut(BaseModel):
    batch_id: int
    batch_name: BatchName
    recipe: list[RecipeItemOut]  # 合并重复原料后的替代配方
    affected_batches: list[AffectedBatchOut]  # 目标批次 + 全部下游批次，各只出现一次


class PoolPlanRequest(BaseModel):
    """混样规划：给定两个已有原料与目标样本占比，反算正整数投入份数。"""

    ingredient_a_batch_id: StrictPositiveInt
    ingredient_b_batch_id: StrictPositiveInt
    sample_id: StrictPositiveInt
    numerator: StrictNonNegativeInt
    denominator: StrictPositiveInt
    max_total_portions: StrictPositiveInt

    @model_validator(mode="after")
    def _validate(self) -> PoolPlanRequest:
        if self.ingredient_a_batch_id == self.ingredient_b_batch_id:
            raise ValueError("两个原料批次必须不同")
        if self.numerator > self.denominator:
            raise ValueError("目标占比必须满足 0 ≤ 分子 ≤ 分母")
        return self


class PoolPlanRecipeItemOut(BaseModel):
    batch_id: int
    portions: int  # 正整数，可直接作为 /pools 的 recipe 项提交


class PoolPlanOut(BaseModel):
    sample_id: int
    target: FractionOut  # 约分后的目标占比
    total_portions: int  # 最小总份数 = recipe 中份数之和，且 ≤ max_total_portions
    recipe: list[PoolPlanRecipeItemOut]  # 顺序与请求中两个原料一致，可直接提交 /pools
    components: list[ComponentOut]  # 新混样的全部原始样本占比（约分后的分子/分母）
