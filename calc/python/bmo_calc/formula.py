"""Formula tokenizer + recursive-descent parser (Excel-compatible subset).
Grammar (low to high precedence):
  cmp   := concat (('='|'<>'|'<='|'>='|'<'|'>') concat)*
  concat:= add (('&') add)*
  add   := mul (('+'|'-') mul)*
  mul   := pow (('*'|'/') pow)*
  pow   := unary ('^' unary)*
  unary := ('+'|'-')* postfix
  postfix:= primary ('%')*
  primary:= number | string | TRUE() | FALSE() | PI() | ref | range | func | '(' expr ')'
Ranges are only valid as function arguments (else #VALUE!).
Sheet-qualified refs: Sheet1!A1 and 'My Sheet'!$B$2.
"""
import re

ADDR = r"\$?[A-Za-z]{1,3}\$?[0-9]{1,7}"
SHEET = r"(?:'[^']+'|[A-Za-z_][A-Za-z0-9_.]*)"

class FErr(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code

TOKEN_RE = re.compile(rf"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<ref>(?P<sheet>{SHEET}!)?(?P<addr>{ADDR}))
  | (?P<op><>|<=|>=|[=+\-*/^&<>()%,!:])
  | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
""", re.VERBOSE)

class Tok:
    __slots__ = ("kind", "text")
    def __init__(self, kind, text):
        self.kind = kind
        self.text = text

def tokenize(src: str):
    toks, i = [], 0
    while i < len(src):
        m = TOKEN_RE.match(src, i)
        if not m or m.end() == i:
            raise FErr("#NAME?")
        i = m.end()
        g = m.groupdict()
        if g["ws"]:
            continue
        if g["num"] is not None:
            toks.append(Tok("num", m.group(0)))
        elif g["str"] is not None:
            toks.append(Tok("str", m.group(0)))
        elif g["ref"] is not None:
            toks.append(Tok("ref", (g["sheet"], g["addr"])))
        elif g["op"] is not None:
            toks.append(Tok("op", m.group(0)))
        elif g["name"] is not None:
            toks.append(Tok("name", m.group(0)))
        else:
            raise FErr("#NAME?")
    toks.append(Tok("eof", ""))
    return toks

# AST nodes: tuples ("num",v) ("str",v) ("bool",v) ("ref",sheet,addr)
# ("range",sheet,a,b) ("call",name,args) ("bin",op,l,r) ("un",op,x) ("pct",x)

class Parser:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self):
        return self.t[self.i]

    def eat(self, kind=None, text=None):
        tok = self.t[self.i]
        if kind and tok.kind != kind:
            raise FErr("#NAME?")
        if text is not None and tok.text != text:
            raise FErr("#NAME?")
        self.i += 1
        return tok

    def parse(self):
        node = self.cmp()
        if self.peek().kind != "eof":
            raise FErr("#NAME?")
        return node

    def cmp(self):
        node = self.concat()
        while self.peek().kind == "op" and self.peek().text in ("=", "<>", "<=", ">=", "<", ">"):
            op = self.eat().text
            node = ("bin", op, node, self.concat())
        return node

    def concat(self):
        node = self.add()
        while self.peek().kind == "op" and self.peek().text == "&":
            self.eat()
            node = ("bin", "&", node, self.add())
        return node

    def add(self):
        node = self.mul()
        while self.peek().kind == "op" and self.peek().text in ("+", "-"):
            op = self.eat().text
            node = ("bin", op, node, self.mul())
        return node

    def mul(self):
        node = self.pow()
        while self.peek().kind == "op" and self.peek().text in ("*", "/"):
            op = self.eat().text
            node = ("bin", op, node, self.pow())
        return node

    def pow(self):
        node = self.unary()
        while self.peek().kind == "op" and self.peek().text == "^":
            self.eat()
            node = ("bin", "^", node, self.unary())
        return node

    def unary(self):
        if self.peek().kind == "op" and self.peek().text in ("+", "-"):
            op = self.eat().text
            return ("un", op, self.unary())
        node = self.primary()
        while self.peek().kind == "op" and self.peek().text == "%":
            self.eat()
            node = ("pct", node)
        return node

    def primary(self):
        tok = self.peek()
        if tok.kind == "num":
            self.eat()
            return ("num", float(tok.text) if re.search(r"[.eE]", tok.text) else int(tok.text))
        if tok.kind == "str":
            self.eat()
            return ("str", tok.text[1:-1].replace('""', '"'))
        if tok.kind == "name":
            name = tok.text.upper()
            self.eat()
            if self.peek().kind == "op" and self.peek().text == "(":
                self.eat()
                args = []
                if not (self.peek().kind == "op" and self.peek().text == ")"):
                    args.append(self.cmp())
                    while self.peek().kind == "op" and self.peek().text == ",":
                        self.eat()
                        args.append(self.cmp())
                self.eat("op", ")")
                if name in ("TRUE", "FALSE"):
                    if args:
                        raise FErr("#NAME?")
                    return ("bool", name == "TRUE")
                if name == "PI":
                    if args:
                        raise FErr("#NAME?")
                    return ("num", 3.141592653589793)
                return ("call", name, args)
            if name in ("TRUE", "FALSE"):
                return ("bool", name == "TRUE")
            raise FErr("#NAME?")
        if tok.kind == "op" and tok.text == "(":
            self.eat()
            node = self.cmp()
            self.eat("op", ")")
            return node
        if tok.kind == "ref":
            return self.ref_or_range()
        raise FErr("#NAME?")

    @staticmethod
    def _sheet_name(raw):
        if not raw:
            return None
        s = raw[:-1]  # strip trailing !
        if s.startswith("'"):
            s = s[1:-1].replace("''", "'")
        return s

    def ref_or_range(self):
        sheet, addr = self.eat("ref").text
        sheet = self._sheet_name(sheet)
        if self.peek().kind == "op" and self.peek().text == ":":
            self.eat()
            tok2 = self.peek()
            if tok2.kind != "ref":
                raise FErr("#NAME?")
            sheet2, addr2 = self.eat("ref").text
            sheet2 = self._sheet_name(sheet2)
            if sheet2 is None:
                sheet2 = sheet
            if sheet is not None and sheet2 is not None and sheet != sheet2:
                raise FErr("#VALUE?")  # cross-sheet endpoints: not a range
            return ("range", sheet or sheet2, addr, addr2)
        return ("ref", sheet, addr)

def parse(src: str):
    return Parser(tokenize(src)).parse()
