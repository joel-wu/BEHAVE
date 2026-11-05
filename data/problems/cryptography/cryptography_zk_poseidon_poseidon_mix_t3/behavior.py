P = 21888242871839275222246405745257275088548364400416034343698204186575808495617
M = [
    [0x109b7f411ba0e4c9b2b70caf5c36a7b194be7c11ad24378bfedb68592ba8118b, 0x2969f27eed31a480b9c36c764379dbca2cc8fdd1415c3dded62940bcde0bd771, 0x143021ec686a3f330d5f9e654638065ce6cd79e28c5b3753326244ee65a1b1a7],
    [0x16ed41e13bb9c0c66ae119424fddbcbc9314dc9fdbdeea55d6c64543dc4903e0, 0x2e2419f9ec02ec394c9871c832963dc1b89d743c8c7b964029b2311687b1fe23, 0x176cc029695ad02582a70eff08a6fd99d057e12e58e7d7b6b16cdfabc8ee2911],
    [0x2b90bba00fca0589f617e7dcbfe82e0df706ab640ceb247b791a93b74e36736d, 0x101071f0032379b697315876690f053d148d4e109f5fb065c8aacc55a0f89bfa, 0x19a3fc0a56702bf417ba7fee3802593fa644470307043f7773279cd71d25d5e0],
]

class PoseidonMixT3:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_0, in_1, in_2, in_s):
        s = in_s & 3
        if s == 3:
            return {"out": 0, "out_error": 1}
        x0, x1, x2 = in_0 % P, in_1 % P, in_2 % P
        return {"out": (M[0][s]*x0 + M[1][s]*x1 + M[2][s]*x2) % P, "out_error": 0}
