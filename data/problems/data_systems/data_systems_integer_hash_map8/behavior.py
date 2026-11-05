# SPDX-License-Identifier: MIT
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class HashMap8:
    def __init__(self):
        self.reset()

    def reset(self):
        self.keys=0
        self.values=0
        self.flags=0

    def process(self, in_op, in_key, in_value):
        keys=self.keys
        values=self.values
        flags=self.flags
        hit=-1
        site=-1
        done=0
        slot=in_key & 7
        for step in range(8):
            if not done:
                flag=(flags >> (2*slot)) & 3
                if flag == 0:
                    if site < 0:
                        site=slot
                    done=1
                elif flag == 2:
                    site=slot
                elif ((keys >> (16*slot)) & 65535) == in_key:
                    hit=slot
                    done=1
            slot=(slot+step+1) & 7
        payload=0
        status=0
        if in_op == 3:
            keys=0
            values=0
            flags=0
            status=6
        elif in_op == 1:
            if hit >= 0:
                site=hit
                status=3
            elif site >= 0:
                status=2
            else:
                status=5
            if site >= 0:
                keys=(keys & ~(65535 << (16*site))) | (in_key << (16*site))
                values=(values & ~(65535 << (16*site))) | (in_value << (16*site))
                flags=(flags & ~(3 << (2*site))) | (1 << (2*site))
                payload=in_value
        elif hit >= 0:
            payload=(values >> (16*hit)) & 65535
            status=1
            if in_op == 2:
                flags=(flags & ~(3 << (2*hit))) | (2 << (2*hit))
                status=4
        count=0
        for i in range(8):
            if ((flags >> (2*i)) & 3) == 1:
                count += 1
        self.keys=keys
        self.values=values
        self.flags=flags
        result=payload | (status << 16) | (count << 19)
        return {"out_values": result}
