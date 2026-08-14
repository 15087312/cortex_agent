"""modules/perception/difference/sources/file_source — 文件差异源防御分支（unit）"""
from unittest.mock import patch

from modules.perception.difference.sources.file_source import FileDifferenceSource, _IGNORE


def _make(tmp_path):
    return FileDifferenceSource(root=str(tmp_path))


def test_source_type():
    assert _make(None).source_type == "file"


def test_detect_first_scan_baseline(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    fs = _make(tmp_path)
    assert fs.detect() == []
    assert fs._first is False


def test_detect_modified_and_deleted(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    fs = _make(tmp_path)
    fs.detect()  # 基线
    (tmp_path / "a.txt").write_text("2")
    (tmp_path / "b.txt").write_text("3")
    diffs = fs.detect()
    cats = {d.category for d in diffs}
    assert "file_modified" in cats  # a.txt 修改
    assert "file_deleted" not in cats  # b.txt 新增不视为删除

    (tmp_path / "a.txt").unlink()
    diffs2 = fs.detect()
    cats2 = {d.category for d in diffs2}
    assert "file_deleted" in cats2  # a.txt 被删除


def test_detect_subdir_file_modified(tmp_path):
    """一级子目录文件变化也应被追踪（dir 分支 32->27）"""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "y.txt").write_text("1")
    fs = _make(tmp_path)
    fs.detect()
    (sub / "y.txt").write_text("2")
    diffs = fs.detect()
    assert any(d.category == "file_modified" for d in diffs)


def test_detect_broken_symlink_skipped(tmp_path):
    """既非文件也非目录（断链符号链接）→ 跳过（32->27）"""
    import os
    os.symlink(str(tmp_path / "ghost"), str(tmp_path / "link"))
    fs = _make(tmp_path)
    assert fs.detect() == []


def test_ignores_dotdirs_and_ignorelist(tmp_path):
    """跳过隐藏目录/文件与 _IGNORE 清单（28 行过滤分支）"""
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "x.txt").write_text("1")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    for name in _IGNORE:
        (tmp_path / name).mkdir(exist_ok=True)
    fs = _make(tmp_path)
    assert fs.detect() == []  # 全部被过滤


def test_iter_files_exception_returns_empty(tmp_path):
    """_iter_files 抛异常 → 静默返回空（36-37）"""
    fs = _make(tmp_path)
    with patch.object(type(fs._root), "iterdir", side_effect=OSError("denied")):
        assert fs.detect() == []


def test_detect_stat_exception_skips_file(tmp_path):
    """stat 失败的文件被跳过（46-47）"""
    (tmp_path / "bad.txt").write_text("1")
    fs = _make(tmp_path)
    real_stat = type(tmp_path).stat
    with patch("modules.perception.difference.sources.file_source.Path.is_file",
               side_effect=lambda: True), \
         patch("modules.perception.difference.sources.file_source.Path.is_dir",
               side_effect=lambda: False), \
         patch("modules.perception.difference.sources.file_source.Path.stat",
               side_effect=OSError("gone")):
        assert fs.detect() == []  # stat 异常 → 跳过该文件
    assert fs._known == {}
