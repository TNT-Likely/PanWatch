"""因子评估相关系数(Phase 2)单元测试 —— 纯函数,不触发 DB。"""

from src.modules.strategy.factor_eval import pearson, spearman


def test_pearson_perfect_positive():
    """完全正线性相关 = +1。"""
    assert abs(pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) - 1.0) < 1e-9


def test_pearson_perfect_negative():
    """完全负线性相关 = -1。"""
    assert abs(pearson([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) + 1.0) < 1e-9


def test_pearson_too_few_returns_none():
    """样本不足 3 返回 None。"""
    assert pearson([1, 2], [1, 2]) is None


def test_pearson_zero_variance_none():
    """零方差(常量序列)返回 None。"""
    assert pearson([1, 1, 1, 1], [1, 2, 3, 4]) is None


def test_spearman_monotonic_nonlinear():
    """单调非线性关系下 Spearman = +1(秩相关)。"""
    assert abs(spearman([1, 2, 3, 4, 5], [1, 4, 9, 16, 25]) - 1.0) < 1e-9


def test_spearman_handles_ties():
    """含并列值时仍返回合法相关系数(平均秩)。"""
    r = spearman([1, 1, 2, 3], [1, 2, 2, 3])
    assert r is not None and -1.0 <= r <= 1.0
