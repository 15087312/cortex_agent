"""v2 L2 dataflow: variable bindings (E201/E202/E203/E204)."""

import textwrap

from logiclink.check import run


def write_proj(tmp_path, files: dict) -> str:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(tmp_path)


def codes(diags):
    return sorted(f"{d.level}[{d.code}]" for d in diags)


def ok_proj():
    return {
        "link.toml": """
            [atoms.Think]
            calls = ["think"]
            [atoms.Retrieve]
            calls = ["retrieve"]
            [atoms.Write]
            calls = ["write"]
            """,
    }


def test_datav_ok(tmp_path):
    files = ok_proj()
    files["a.link"] = "eq Pipe = Think(answer) · Write(answer)\n"
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]


def test_e201_use_before_define(tmp_path):
    files = ok_proj()
    # Write consumes answer before Think produced it.
    files["a.link"] = "eq Pipe = Write(answer) · Think(answer)\n"
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e201 = [d for d in diags if d.code == "E201"]
    assert e201 and "answer" in e201[0].message, [(d.code, d.message) for d in diags]


def test_e202_produced_never_consumed(tmp_path):
    files = ok_proj()
    # q is produced by Retrieve but nobody consumes it.
    files["a.link"] = "eq Pipe = Retrieve(q) · Think(answer) · Write(answer)\n"
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e202 = [d for d in diags if d.code == "E202"]
    assert e202 and "q" in e202[0].message, [(d.code, d.message) for d in diags]


def test_e203_type_mismatch(tmp_path):
    files = ok_proj()
    files["a.link"] = "eq Pipe = Think(answer:str) · Write(answer:int)\n"
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e203 = [d for d in diags if d.code == "E203"]
    assert e203, [(d.code, d.message) for d in diags]


def test_e204_channel_not_published(tmp_path):
    files = ok_proj()
    # Write subscribes to a channel nobody publishes.
    files["a.link"] = 'eq Pipe = Write(q@"thinker.question")\n'
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 1
    e204 = [d for d in diags if d.code == "E204"]
    assert e204 and "thinker.question" in e204[0].message, [(d.code, d.message) for d in diags]


def test_channel_published_and_subscribed_ok(tmp_path):
    files = ok_proj()
    files["a.link"] = 'eq Pipe = Think(q@"thinker.question") · Write(q)\n'
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]


def test_variable_binding_does_not_break_evidence(tmp_path):
    # Atom(var) parses fine and still carries its atom name for v1 checks.
    files = ok_proj()
    files["a.py"] = """
        # @link:block Pipe = Think(answer) · Write(answer)
        def pipe():
            think()
            write()
        """
    root = write_proj(tmp_path, files)
    diags, code = run(root)
    assert code == 0, [(d.code, d.message) for d in diags]
