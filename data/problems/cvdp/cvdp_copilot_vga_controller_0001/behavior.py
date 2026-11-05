class Behavior:
    def __init__(self, H_ACTIVE=640, H_FRONT=16, H_PULSE=96, H_BACK=48, V_ACTIVE=480, V_FRONT=10, V_PULSE=2, V_BACK=33):
        self.H_ACTIVE = H_ACTIVE
        self.H_FRONT = H_FRONT
        self.H_PULSE = H_PULSE
        self.H_BACK = H_BACK
        self.V_ACTIVE = V_ACTIVE
        self.V_FRONT = V_FRONT
        self.V_PULSE = V_PULSE
        self.V_BACK = V_BACK
        self.reset()

    def reset(self):
        self.previous_clock = 0
        self.x = 0
        self.y = 0

    def process(self, clock_level, reset_level, color_in):
        if reset_level:
            self.x = 0
            self.y = 0
        elif clock_level and not self.previous_clock:
            if self.x == self.H_ACTIVE+self.H_FRONT+self.H_PULSE+self.H_BACK-1:
                self.x = 0
                if self.y == self.V_ACTIVE+self.V_FRONT+self.V_PULSE+self.V_BACK-1:
                    self.y = 0
                else:
                    self.y += 1
            else:
                self.x += 1
        self.previous_clock = clock_level
        active = self.x < self.H_ACTIVE and self.y < self.V_ACTIVE and not reset_level
        return {"hsync": int(not (self.H_ACTIVE+self.H_FRONT <= self.x < self.H_ACTIVE+self.H_FRONT+self.H_PULSE)),
                "vsync": int(not (self.V_ACTIVE+self.V_FRONT <= self.y < self.V_ACTIVE+self.V_FRONT+self.V_PULSE)),
                "red": ((color_in >> 5) << 5) if active else 0,
                "green": (((color_in >> 2) & 7) << 5) if active else 0,
                "blue": ((color_in & 3) << 6) if active else 0,
                "next_x": self.x if active else 0, "next_y": self.y if active else 0,
                "sync": 0, "clk": clock_level, "blank": int(not active)}
