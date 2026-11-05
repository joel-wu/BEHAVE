class StaticBranchPredict:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, fetch_rdata_i, fetch_pc_i, fetch_valid_i):
        opcode=fetch_rdata_i&127
        offset=0
        if opcode==111:
            offset=((fetch_rdata_i>>31)<<20)|(((fetch_rdata_i>>12)&255)<<12)|(((fetch_rdata_i>>20)&1)<<11)|(((fetch_rdata_i>>21)&1023)<<1)
            if offset&(1<<20):
                offset-=1<<21
        elif opcode==99:
            offset=((fetch_rdata_i>>31)<<12)|(((fetch_rdata_i>>7)&1)<<11)|(((fetch_rdata_i>>25)&63)<<5)|(((fetch_rdata_i>>8)&15)<<1)
            if offset&(1<<12):
                offset-=1<<13
        elif opcode==103:
            offset=fetch_rdata_i>>20
            if offset&2048:
                offset-=4096
        taken=int(bool(fetch_valid_i) and (opcode==111 or opcode==103 or (opcode==99 and offset<0)))
        return {"predict_branch_taken_o":taken,"predict_branch_pc_o":(fetch_pc_i+offset)&4294967295}
