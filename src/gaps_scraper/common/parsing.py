"""Structures HTML et valeurs partagées par les parseurs."""
from dataclasses import dataclass,field
from decimal import Decimal,InvalidOperation
from html.parser import HTMLParser
import re

VOID_TAGS={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

class ParseError(ValueError):
    pass

@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def text(self):
        return "".join(c.text() if isinstance(c, Node) else c for c in self.children)

    def descendants(self, tag):
        for child in self.children:
            if isinstance(child, Node):
                if child.tag == tag:
                    yield child
                yield from child.descendants(tag)

class Tree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag == "br":
            node.children.append("\n")
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                return

    def handle_data(self, text):
        self.stack[-1].children.append(text)

def clean(text):
    return " ".join(text.split())

def number(text, *, nullable=False):
    value = clean(text)
    if nullable and value == "-":
        return None
    if not re.fullmatch(r"\d+(?:[.,]\d+)?", value):
        raise ParseError("Valeur numérique inconnue.")
    try:
        return format(Decimal(value.replace(",", ".")).normalize(), "f")
    except InvalidOperation:
        raise ParseError("Valeur numérique invalide.") from None
