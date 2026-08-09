"""文件差异源测试（基线/修改/删除）"""
from modules.perception.difference.sources.file_source import FileDifferenceSource


def test_file_source_detect(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    fs = FileDifferenceSource(root=str(tmp_path))
    assert fs.detect() == []  # 首次基线

    (tmp_path / "a.txt").write_text("2")  # 修改
    (tmp_path / "b.txt").write_text("3")  # 新增（首次出现不算变化）
    diffs = fs.detect()
    cats = [d.category for d in diffs]
    assert "file_modified" in cats
    assert all(d.source_type == "file" for d in diffs)

    (tmp_path / "a.txt").unlink()  # 删除
    diffs2 = fs.detect()
    cats2 = [d.category for d in diffs2]
    assert "file_deleted" in cats2


def test_ignores_directories_and_known(tmp_path):
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "x.txt").write_text("1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "y.txt").write_text("1")
    fs = FileDifferenceSource(root=str(tmp_path))
    assert fs.detect() == []  # 基线
    (tmp_path / "sub" / "y.txt").write_text("2")  # 一级子目录文件修改
    diffs = fs.detect()
    assert any("file_modified" == d.category for d in diffs)
