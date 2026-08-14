"""modules/database/models.py — SQLAlchemy 模型占位模块（import 即覆盖）"""


def test_import_placeholder():
    import modules.database.models as m
    assert m.__doc__ is not None
