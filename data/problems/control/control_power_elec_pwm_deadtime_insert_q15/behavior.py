class ControlPowerElecPwmDeadtimeInsertQ15:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_deadtime_ns, in_core_clock_hz):
        product = in_deadtime_ns * in_core_clock_hz
        saturated = 0
        if product <= 127000000000:
            code = product // 1000000000
        elif product <= 254000000000:
            payload = (product - 128000000000) // 2000000000 if product >= 128000000000 else 0
            code = 128 | payload
        elif product <= 504000000000:
            payload = (product - 256000000000) // 8000000000 if product >= 256000000000 else 0
            code = 192 | payload
        elif product <= 1008000000000:
            payload = (product - 512000000000) // 16000000000 if product >= 512000000000 else 0
            code = 224 | payload
        else:
            code = 255
            saturated = 1
        if code < 128:
            ticks = code
        elif code < 192:
            ticks = (64 + (code & 63)) * 2
        elif code < 224:
            ticks = (32 + (code & 31)) * 8
        else:
            ticks = (32 + (code & 31)) * 16
        bad = in_core_clock_hz == 0
        return {"out_dtg":0 if bad else code, "out_ticks":0 if bad else ticks,
                "out_saturated":0 if bad else saturated, "out_error":1 if bad else 0}
