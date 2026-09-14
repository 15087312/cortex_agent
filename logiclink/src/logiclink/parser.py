"""Parsing for the Link language (v1): terms + declarations.

Grammar (precedence high -> low): postfix ``* + ?`` -> ``.``/``>>`` ->
``||`` -> ``|``. ``|`` / ``||`` / ``.`` are order-sensitive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import ast_terms as T

_IDENT_RE = re.compile(r"[^\W\d]\w*", re.UNICODE)
_DECL_NAME_RE = re.compile(r"[^\W\d]\w*(?:\.[^\W\d]\w*)*", re.UNICODE)

_MID_DOT = "\u00b7"  # ·
_DOT_ALIASES = (_MID_DOT, "·")


class ParseError(Exception):
    pass


# ---------------------------------------------------------------- tokens

@dataclass(frozen=True)
class Token:
    kind: str  # IDENT STRING DOT PIPE PAR STAR PLUS QMARK LPAREN RPAREN LBRACK RBRACK COMMA EQ EOF
    text: str
    pos: int


def tokenize(s: str) -> list[Token]:
    toks: list[Token] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c in _DOT_ALIASES:
            toks.append(Token("DOT", c, i))
            i += 1
            continue
        if c == ">" and s[i : i + 2] == ">>":
            toks.append(Token("DOT", ">>", i))
            i += 2
            continue
        if c == "|":
            if s[i : i + 2] == "||":
                toks.append(Token("PAR", "||", i))
                i += 2
            else:
                toks.append(Token("PIPE", "|", i))
                i += 1
            continue
        if c == "*":
            toks.append(Token("STAR", "*", i))
            i += 1
            continue
        if c == "+":
            toks.append(Token("PLUS", "+", i))
            i += 1
            continue
        if c == "?":
            toks.append(Token("QMARK", "?", i))
            i += 1
            continue
        if c == "(":
            toks.append(Token("LPAREN", "(", i))
            i += 1
            continue
        if c == ")":
            toks.append(Token("RPAREN", ")", i))
            i += 1
            continue
        if c == "[":
            toks.append(Token("LBRACK", "[", i))
            i += 1
            continue
        if c == "]":
            toks.append(Token("RBRACK", "]", i))
            i += 1
            continue
        if c == ",":
            toks.append(Token("COMMA", ",", i))
            i += 1
            continue
        if c == "=":
            toks.append(Token("EQ", "=", i))
            i += 1
            continue
        if c == "@":
            toks.append(Token("AT", "@", i))
            i += 1
            continue
        if c == ":":
            toks.append(Token("COLON", ":", i))
            i += 1
            continue
        if c in ("'", '"'):
            j = i + 1
            buf = []
            while j < n and s[j] != c:
                if s[j] == "\\" and j + 1 < n:
                    buf.append(s[j + 1])
                    j += 2
                else:
                    buf.append(s[j])
                    j += 1
            if j >= n:
                raise ParseError(f"unterminated string at column {i}")
            toks.append(Token("STRING", "".join(buf), i))
            i = j + 1
            continue
        m = _IDENT_RE.match(s, i)
        if m:
            toks.append(Token("IDENT", m.group(0), i))
            i = m.end()
            continue
        raise ParseError(f"unexpected character {c!r} at column {i}")
    toks.append(Token("EOF", "", n))
    return toks


def _splice_lexicon(toks: list[Token], lexicon: dict[str, str]) -> list[Token]:
    """Token-level lexicon substitution (supports operator aliases like 循环->Loop)."""
    if not lexicon:
        return toks
    out: list[Token] = []
    for tk in toks:
        if tk.kind == "IDENT" and tk.text in lexicon:
            replacement = tokenize(lexicon[tk.text])
            # Recursively resolve chained aliases with a depth guard.
            depth = 0
            while (
                len(replacement) == 2
                and replacement[0].kind == "IDENT"
                and replacement[0].text in lexicon
                and depth < 10
            ):
                replacement = tokenize(lexicon[replacement[0].text])
                depth += 1
            out.extend(t for t in replacement if t.kind != "EOF")
        else:
            out.append(tk)
    return out


# ---------------------------------------------------------------- term parser

class _Parser:
    def __init__(self, toks: list[Token]):
        self.toks = toks
        self.pos = 0

    def peek(self) -> Token:
        return self.toks[self.pos]

    def next(self) -> Token:
        tk = self.toks[self.pos]
        self.pos += 1
        return tk

    def expect(self, kind: str) -> Token:
        tk = self.next()
        if tk.kind != kind:
            raise ParseError(f"expected {kind} but found {tk.kind} ({tk.text!r}) at column {tk.pos}")
        return tk

    def parse(self) -> T.Term:
        if self.peek().kind == "EOF":
            raise ParseError("empty term")
        t = self.parse_alt()
        if self.peek().kind != "EOF":
            tk = self.peek()
            raise ParseError(f"unexpected {tk.text!r} at column {tk.pos}")
        return t

    def parse_alt(self) -> T.Term:
        left = self.parse_par()
        while self.peek().kind == "PIPE":
            self.next()
            right = self.parse_par()
            left = T.Alt(left, right)
        return left

    def parse_par(self) -> T.Term:
        left = self.parse_seq()
        while self.peek().kind == "PAR":
            self.next()
            right = self.parse_seq()
            left = T.Par(left, right)
        return left

    def parse_seq(self) -> T.Term:
        left = self.parse_postfix()
        while self.peek().kind == "DOT":
            self.next()
            right = self.parse_postfix()
            left = T.Seq(left, right)
        return left

    def parse_postfix(self) -> T.Term:
        node = self.parse_primary()
        while self.peek().kind in ("STAR", "PLUS", "QMARK"):
            kind = self.next().kind
            if kind == "STAR":
                node = T.Star(node)
            elif kind == "PLUS":
                node = T.Plus(node)
            else:
                node = T.Opt(node)
        return node

    def parse_primary(self) -> T.Term:
        tk = self.peek()
        if tk.kind == "LPAREN":
            self.next()
            t = self.parse_alt()
            self.expect("RPAREN")
            return t
        if tk.kind != "IDENT":
            raise ParseError(f"expected term at column {tk.pos}, found {tk.text!r}")
        self.next()
        name = tk.text
        if name == "Loop" and self.peek().kind == "LPAREN":
            self.next()
            body = self.parse_alt()
            self.expect("RPAREN")
            return T.Loop(body)
        if name == "id" and self.peek().kind != "LBRACK":
            return T.Id()
        # v2 dataflow binding: Atom(var) / Atom(v@"channel") / Atom(v:Type)
        if self.peek().kind == "LPAREN":
            self.next()
            v = self.peek()
            if v.kind != "IDENT":
                raise ParseError(f"expected variable name at column {v.pos}")
            self.next()
            args: tuple = (("var", v.text),)
            if self.peek().kind == "AT":
                self.next()
                ch = self.next()
                if ch.kind != "STRING":
                    raise ParseError(f"expected channel string after '@' at column {ch.pos}")
                args += (("chan", ch.text),)
            elif self.peek().kind == "COLON":
                self.next()
                ty = self.next()
                if ty.kind != "IDENT":
                    raise ParseError(f"expected type name after ':' at column {ty.pos}")
                args += (("type", ty.text),)
            self.expect("RPAREN")
            return T.Atom(name, args)
        args: tuple = ()
        if self.peek().kind == "LBRACK":
            self.next()
            args = self.parse_arglist()
            self.expect("RBRACK")
        return T.Atom(name, args)

    def parse_arglist(self) -> tuple:
        args: list[tuple] = []
        if self.peek().kind == "RBRACK":
            return ()
        while True:
            tk = self.peek()
            if tk.kind == "STRING":
                self.next()
                args.append(("", tk.text))
            elif tk.kind == "IDENT":
                self.next()
                if self.peek().kind == "EQ":
                    self.next()
                    v = self.peek()
                    if v.kind not in ("IDENT", "STRING"):
                        raise ParseError(f"expected value at column {v.pos}")
                    self.next()
                    args.append((tk.text, v.text))
                else:
                    args.append(("", tk.text))
            else:
                raise ParseError(f"expected argument at column {tk.pos}")
            if self.peek().kind == "COMMA":
                self.next()
                continue
            break
        return tuple(args)


def parse_term(text: str, lexicon: dict[str, str] | None = None) -> T.Term:
    toks = _splice_lexicon(tokenize(text), lexicon or {})
    return _Parser(toks).parse()


# ---------------------------------------------------------------- declarations

@dataclass
class Decl:
    kind: str  # atom alias eq export use block
    name: str
    term_str: str | None  # raw term text for alias/eq/block-with-term
    path: str
    line: int  # 1-based
    col: int = 1
    hook_file: str | None = None
    hook_fn: str | None = None
    op: str | None = None  # ':' or '=' for block


_BLOCK_HEAD_RE = re.compile(
    r"^block\s+([^\W\d]\w*(?:\.[^\W\d]\w*)*)(?:\s+(@.*)|\s*(?:([:=])\s*(.*))?)?$",
    re.UNICODE | re.DOTALL,
)
_HOOK_RE = re.compile(r"^(.*?)\s*@\s*file:\s*(\S+)(?:\s+fn:(\S+))?\s*$", re.DOTALL)
_ATOM_RE = re.compile(r"^atom\s+([^\W\d]\w*)\s*(\[.*\])?\s*$", re.UNICODE | re.DOTALL)
_ALIAS_EQ_RE = re.compile(
    r"^(alias|eq)\s+([^\W\d]\w*(?:\.[^\W\d]\w*)*)\s*=\s*(.+)$", re.UNICODE | re.DOTALL
)
_EXPORT_RE = re.compile(r"^export\s+([^\W\d]\w*(?:\.[^\W\d]\w*)*)\s*$", re.UNICODE)
_USE_RE = re.compile(r"^use\s+([^\W\d]\w*(?:\.[^\W\d]\w*)*)\s*$", re.UNICODE)
# v2: use name := module.Symbol  (assembly / wiring)
_USE_BIND_RE = re.compile(r"^use\s+([^\W\d]\w*)\s*:=\s*([^\W\d]\w*(?:\.[^\W\d]\w*)*)\s*$", re.UNICODE)
# v2: port contracts -- input [Atom, ...] / output [Atom, ...]
#     optional NAME binds to that symbol; bare form binds to the nearest `export`.
_PORT_RE = re.compile(
    r"^(input|output)\s*(?:([^\W\d]\w*)\s*)?:\s*\[(.*)\]\s*$", re.UNICODE | re.DOTALL
)
# v2: state machine declarations
_STATE_RE = re.compile(r"^state\s+([^\W\d]\w*)\s*$", re.UNICODE)
_STATE_INIT_RE = re.compile(r"^init\s*=\s*(\S+)\s*$", re.UNICODE)
_STATE_TRANS_RE = re.compile(r'^trans\s*=\s*"(.+)"\s*$', re.UNICODE)


def strip_trailing_comment(s: str) -> str:
    """Remove a trailing `` #...`` comment, respecting quoted strings."""
    in_str: str | None = None
    i = 0
    while i < len(s):
        c = s[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ("'", '"'):
            in_str = c
            i += 1
            continue
        if c == "#" and i > 0 and s[i - 1].isspace():
            return s[:i].rstrip()
        i += 1
    return s


def parse_decl(text: str, path: str, line: int, col: int = 1) -> Decl:
    s = strip_trailing_comment(text.strip())
    if not s:
        raise ParseError("empty declaration")
    m = _ATOM_RE.match(s)
    if m:
        return Decl("atom", m.group(1), None, path, line, col)
    m = _ALIAS_EQ_RE.match(s)
    if m:
        kind, name, term = m.group(1), m.group(2), m.group(3).strip()
        if not term:
            raise ParseError(f"{kind} {name} is missing a right-hand term")
        return Decl(kind, name, term, path, line, col)
    m = _EXPORT_RE.match(s)
    if m:
        return Decl("export", m.group(1), None, path, line, col)
    m = _USE_BIND_RE.match(s)
    if m:
        return Decl("use", m.group(1), m.group(2), path, line, col, op=":=")
    m = _USE_RE.match(s)
    if m:
        return Decl("use", m.group(1), None, path, line, col)
    m = _PORT_RE.match(s)
    if m:
        kind, pname, plist = m.group(1), (m.group(2) or ""), ("[" + m.group(3) + "]")
        return Decl(kind, pname, plist, path, line, col)
    m = _STATE_RE.match(s)
    if m:
        return Decl("state", m.group(1), None, path, line, col)
    m = _STATE_INIT_RE.match(s)
    if m:
        return Decl("state_init", "", m.group(1), path, line, col)
    m = _STATE_TRANS_RE.match(s)
    if m:
        return Decl("state_trans", "", m.group(1), path, line, col)
    m = _BLOCK_HEAD_RE.match(s)
    if m:
        name, at_rest, op, rest = m.group(1), m.group(2), m.group(3), (m.group(4) or "")
        if at_rest is not None:
            rest = at_rest
        rest = rest.strip()
        hook_file = hook_fn = None
        term_str: str | None = None
        if rest:
            hm = _HOOK_RE.match(rest)
            if hm:
                term_part, hook_file, hook_fn = hm.group(1).strip(), hm.group(2), hm.group(3)
                term_str = term_part or None
            elif rest.startswith("@"):
                hm2 = re.match(r"^@\s*file:\s*(\S+)(?:\s+fn:(\S+))?\s*$", rest)
                if not hm2:
                    raise ParseError(f"bad hook syntax in block {name}: {rest!r}")
                hook_file, hook_fn = hm2.group(1), hm2.group(2)
            else:
                term_str = rest
        if op is None and term_str is not None:
            raise ParseError(f"block {name}: term needs ':' or '='")
        return Decl("block", name, term_str, path, line, col, hook_file, hook_fn, op)
    raise ParseError(f"unknown declaration: {s!r}")
