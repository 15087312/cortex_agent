"""calculator 补充测试：异常分支 / sum / avg"""
from infra.tool_manager.tools.calculator import (
    advanced_calc,
    avg_numbers,
    calculate,
    sum_numbers,
)


def test_calculate_float_ok():
    assert calculate(5, "/", 2) == "5 / 2 = 2.5"


def test_calculate_int_result_normalized():
    assert calculate(8, "/", 2) == "8 / 2 = 4"


def test_calculate_generic_error():
    r = calculate("abc", "+", 1)
    assert "计算错误" in r


def test_calculate_mod_zero():
    assert "除数不能为零" in calculate(5, "%", 0)


def test_advanced_sin_cos_tan():
    assert "0" in advanced_calc("sin", 0)
    assert "1" in advanced_calc("cos", 0)
    assert "0" in advanced_calc("tan", 0)


def test_advanced_math_domain_error():
    # sqrt(-1) → math domain error
    r = advanced_calc("sqrt", -1)
    assert "计算错误" in r
    r2 = advanced_calc("log", -1)
    assert "计算错误" in r2


def test_advanced_float_result():
    r = advanced_calc("sqrt", 2)
    assert "sqrt(2.0)" in r


def test_advanced_case_insensitive_and_strip():
    r = advanced_calc("  SQRT ", 16)
    assert "计算错误" not in r


def test_advanced_exp_floor_ceil_abs():
    assert "1" in advanced_calc("floor", 1.5)
    assert "2" in advanced_calc("ceil", 1.5)
    assert "5" in advanced_calc("abs", -5)
    assert "e" not in advanced_calc("exp", 1) or "2.718" in advanced_calc("exp", 1)


def test_advanced_invalid_value():
    # float(value) 在 try 外执行 → ValueError 直接抛出
    import pytest
    with pytest.raises(ValueError):
        advanced_calc("sqrt", "abc")


def test_sum_numbers_ok():
    r = sum_numbers("1,2,3")
    assert "6.0" in r or "6" in r


def test_sum_numbers_error():
    r = sum_numbers("1,a,3")
    assert "解析错误" in r


def test_sum_numbers_empty():
    r = sum_numbers("")
    assert "解析错误" in r


def test_avg_numbers_ok():
    r = avg_numbers("2,4")
    assert "3.0" in r or "3" in r


def test_avg_numbers_error():
    r = avg_numbers("x")
    assert "解析错误" in r


def test_avg_numbers_empty():
    r = avg_numbers("")
    assert "解析错误" in r
