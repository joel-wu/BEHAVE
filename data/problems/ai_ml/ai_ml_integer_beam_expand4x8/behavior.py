# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

class BeamExpand4x8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_beam_scores, in_token_scores, in_beam_mask, in_eos):
        used=0
        result=0
        count=0
        for rank in range(4):
            found=0
            best=-65537
            index=0
            for i in range(32):
                parent=i//8
                token=i%8
                if ((in_beam_mask >> parent)&1) and token!=in_eos and not ((used >> i)&1):
                    score=s16(in_beam_scores >> (16*parent))+s16(in_token_scores >> (16*i))
                    if score>best:
                        best=score
                        index=i
                        found=1
            if found:
                used |= 1 << index
                slot=(index//8) | ((index%8)<<2) | ((best&131071)<<5)
                result |= slot << (22*rank)
                count+=1
        result |= count << 88
        found=0
        best=-65537
        index=0
        for parent in range(4):
            if (in_beam_mask >> parent)&1:
                score=s16(in_beam_scores >> (16*parent))+s16(in_token_scores >> (16*(8*parent+in_eos)))
                if score>best:
                    best=score
                    index=parent
                    found=1
        if found:
            result |= index << 91
            result |= (best&131071) << 93
            result |= 1 << 110
        return {"out_values": result}
