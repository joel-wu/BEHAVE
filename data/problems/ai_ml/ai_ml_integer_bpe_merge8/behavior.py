# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class BPEMerge8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_tokens, in_length, in_rules, in_rule_mask):
        tokens=[0]*8
        length=min(8,in_length)
        for i in range(8):
            tokens[i]=(in_tokens >> (4*i))&15
        merges=0
        for iteration in range(7):
            best_rule=8
            best_pos=0
            replacement=0
            for r in range(8):
                if (in_rule_mask >> r)&1:
                    rule=(in_rules >> (12*r))&4095
                    for j in range(7):
                        if j+1<length and tokens[j]==(rule&15) and tokens[j+1]==((rule>>4)&15) and r<best_rule:
                            best_rule=r
                            best_pos=j
                            replacement=(rule>>8)&15
            if best_rule<8:
                tokens[best_pos]=replacement
                for j in range(7):
                    if j>best_pos:
                        tokens[j]=tokens[j+1]
                length-=1
                merges+=1
        result=(length<<32) | (merges<<36)
        for i in range(8):
            if i<length:
                result |= tokens[i] << (4*i)
        return {"out_values": result}
