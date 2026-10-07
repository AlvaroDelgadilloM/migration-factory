"""Line-aware, entity-free XML parsing (stdlib expat). DTDs are rejected."""
import re
import xml.parsers.expat
from dataclasses import dataclass, field

DTD = re.compile(r'<!DOCTYPE|<!ENTITY', re.I)


class XmlRejected(ValueError):
    pass


@dataclass
class Node:
    tag: str            # local name, namespace stripped
    ns: str
    attrib: dict
    line: int
    parent: 'Node | None' = None
    children: list = field(default_factory=list)
    text: str = ''

    def find(self, path):
        """Simple '/'-separated child path lookup."""
        nodes = [self]
        for part in path.split('/'):
            nodes = [c for n in nodes for c in n.children if c.tag == part]
        return nodes

    def first(self, path):
        found = self.find(path)
        return found[0] if found else None

    def findtext(self, path, default=None):
        n = self.first(path)
        return n.text.strip() if n is not None else default

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()


def parse(content: str) -> Node:
    if DTD.search(content):
        raise XmlRejected('XML_DTD_REJECTED')
    p = xml.parsers.expat.ParserCreate(namespace_separator='}')
    # No external entity resolution: expat only resolves them if a handler is set.
    p.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack, root = [], [None]

    def start(name, attrs):
        ns, _, local = name.rpartition('}')
        node = Node(local, ns, {k.rpartition('}')[2]: v for k, v in attrs.items()}, p.CurrentLineNumber,
                    stack[-1] if stack else None)
        if stack:
            stack[-1].children.append(node)
        else:
            root[0] = node
        stack.append(node)

    def end(_):
        stack.pop()

    def chars(data):
        if stack:
            stack[-1].text += data

    p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, chars
    try:
        p.Parse(content, True)
    except xml.parsers.expat.ExpatError as e:
        raise ValueError(f'INVALID_XML: {e}') from None
    return root[0]
