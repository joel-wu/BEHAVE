class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, sel, data0, data1, data2, data3, data4, data5):
        out = 0
        if sel == 0:
            out = data0
        elif sel == 1:
            out = data1
        elif sel == 2:
            out = data2
        elif sel == 3:
            out = data3
        elif sel == 4:
            out = data4
        elif sel == 5:
            out = data5
        return {"out": out}
