class BitonicSortU32x16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_data_0, in_data_1, in_data_2, in_data_3, in_data_4, in_data_5, in_data_6, in_data_7, in_data_8, in_data_9, in_data_10, in_data_11, in_data_12, in_data_13, in_data_14, in_data_15):
        lanes = [in_data_0 & 0xFFFFFFFF, in_data_1 & 0xFFFFFFFF, in_data_2 & 0xFFFFFFFF, in_data_3 & 0xFFFFFFFF, in_data_4 & 0xFFFFFFFF, in_data_5 & 0xFFFFFFFF, in_data_6 & 0xFFFFFFFF, in_data_7 & 0xFFFFFFFF, in_data_8 & 0xFFFFFFFF, in_data_9 & 0xFFFFFFFF, in_data_10 & 0xFFFFFFFF, in_data_11 & 0xFFFFFFFF, in_data_12 & 0xFFFFFFFF, in_data_13 & 0xFFFFFFFF, in_data_14 & 0xFFFFFFFF, in_data_15 & 0xFFFFFFFF]
        for level in range(1, 5):
            for stage in range(level - 1, -1, -1):
                distance = 1 << stage
                for lane in range(16):
                    partner = lane ^ distance
                    if partner > lane:
                        ascending = (lane & (1 << level)) == 0
                        if (lanes[lane] > lanes[partner]) == ascending:
                            lanes[lane], lanes[partner] = lanes[partner], lanes[lane]
        return {"out_data_0": lanes[0], "out_data_1": lanes[1], "out_data_2": lanes[2], "out_data_3": lanes[3], "out_data_4": lanes[4], "out_data_5": lanes[5], "out_data_6": lanes[6], "out_data_7": lanes[7], "out_data_8": lanes[8], "out_data_9": lanes[9], "out_data_10": lanes[10], "out_data_11": lanes[11], "out_data_12": lanes[12], "out_data_13": lanes[13], "out_data_14": lanes[14], "out_data_15": lanes[15]}
