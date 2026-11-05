"""Persistent exact dependency sets; nearby histories share unchanged subtrees.

Leaf words pack 4096 adjacent atom positions. Patricia branches skip empty
regions, so neither a late singleton nor an appended history copies its prefix.
The word width is a storage layout, not a semantic or execution limit.
"""

from dataclasses import dataclass, field


_WORD_BITS = 4096
_WORD_MASK = (1 << _WORD_BITS) - 1


@dataclass(frozen=True, slots=True)
class _Node:
    base: int
    level: int
    word: int = 0
    left: object = None
    right: object = None
    _hash: int = field(init=False, compare=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, '_hash', hash((self.base, self.level, self.word,
                                              self.left, self.right)))

    def __hash__(self):
        return self._hash


def _branch(base, level, left, right):
    if left is None:
        return right
    if right is None:
        return left
    return _Node(base, level, left=left, right=right)


def _union(left, right):
    if left is right or right is None:
        return left
    if left is None:
        return right
    if left.level < right.level:
        left, right = right, left
    outside = (left.base ^ right.base).bit_length()
    if outside > left.level:
        low, high = (left, right) if left.base < right.base else (right, left)
        return _branch((left.base >> outside) << outside, outside, low, high)
    if left.level == right.level:
        if left.level == 0:
            word = left.word | right.word
            return left if word == left.word else right if word == right.word else _Node(left.base, 0, word)
        low, high = _union(left.left, right.left), _union(left.right, right.right)
        if low is right.left and high is right.right:
            return right
    elif right.base & (1 << (left.level - 1)):
        low, high = left.left, _union(left.right, right)
    else:
        low, high = _union(left.left, right), left.right
    if low is left.left and high is left.right:
        return left
    return _branch(left.base, left.level, low, high)


def _intersection(left, right, *, exists=False):
    if left is None or right is None:
        return None
    if left is right:
        return left
    if left.level < right.level:
        left, right = right, left
    if (left.base ^ right.base).bit_length() > left.level:
        return None
    if left.level > right.level:
        child = left.right if right.base & (1 << (left.level - 1)) else left.left
        return _intersection(child, right, exists=exists)
    if left.level == 0:
        word = left.word & right.word
        return (None if not word else left if word == left.word or exists
                else right if word == right.word else _Node(left.base, 0, word))
    low = _intersection(left.left, right.left, exists=exists)
    if exists and low is not None:
        return low
    high = _intersection(left.right, right.right, exists=exists)
    if low is left.left and high is left.right:
        return left
    if low is right.left and high is right.right:
        return right
    return _branch(left.base, left.level, low, high)


@dataclass(frozen=True, slots=True, weakref_slot=True, init=False)
class Support:
    _root: _Node | None

    def __init__(self, first=0, bits=0):
        root = None
        block, shift = divmod(first, _WORD_BITS)
        bits <<= shift
        while bits:
            word = bits & _WORD_MASK
            if word:
                root = _union(root, _Node(block, 0, word))
            block += 1
            bits >>= _WORD_BITS
        object.__setattr__(self, '_root', root)

    @classmethod
    def _of(cls, root):
        value = object.__new__(cls)
        object.__setattr__(value, '_root', root)
        return value

    @classmethod
    def atom(cls, index):
        block, offset = divmod(index, _WORD_BITS)
        return cls._of(_Node(block, 0, 1 << offset))

    @classmethod
    def from_atoms(cls, atoms):
        """Pack only occupied words, without materializing the gaps between them."""
        words = {}
        for atom in atoms:
            block, offset = divmod(atom, _WORD_BITS)
            words[block] = words.get(block, 0) | (1 << offset)
        root = None
        for block, word in words.items():
            root = _union(root, _Node(block, 0, word))
        return cls._of(root)

    def __or__(self, other):
        root = _union(self._root, other._root)
        return self if root is self._root else other if root is other._root else self._of(root)

    def __and__(self, other):
        root = _intersection(self._root, other._root)
        return self if root is self._root else other if root is other._root else self._of(root)

    def __bool__(self):
        return self._root is not None

    def __contains__(self, atom):
        block, offset = divmod(atom, _WORD_BITS)
        node = self._root
        while node is not None:
            if (node.base ^ block).bit_length() > node.level:
                return False
            if node.level == 0:
                return bool(node.word & (1 << offset))
            node = node.right if block & (1 << (node.level - 1)) else node.left
        return False

    def __iter__(self):
        pending = [] if self._root is None else [self._root]
        while pending:
            node = pending.pop()
            if node.level:
                pending.extend((node.right, node.left))
                continue
            word = node.word
            while word:
                bit = word & -word
                yield node.base * _WORD_BITS + bit.bit_length() - 1
                word ^= bit

    def project(self, facts):
        retained = facts.projections.get(self._root)
        if retained is not None:
            return retained
        root = _intersection(self._root, facts.mask._root)
        result = self if root is self._root else facts.mask if root is facts.mask._root else self._of(root)
        facts.projections[self._root] = result
        return result

    def intersects(self, facts):
        retained = facts.projections.get(self._root)
        if retained is not None:
            return retained._root is not None
        return _intersection(self._root, facts.mask._root, exists=True) is not None
