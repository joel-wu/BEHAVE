class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, cpu_overheated, arrived, gas_tank_empty):
        return {"shut_off_computer": cpu_overheated, "keep_driving": (arrived ^ 1) & (gas_tank_empty ^ 1)}
