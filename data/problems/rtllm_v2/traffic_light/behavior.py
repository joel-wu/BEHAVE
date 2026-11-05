class TrafficLight:
    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 10
        self.phase = 0
        self.p_red = 0
        self.p_yellow = 0
        self.p_green = 0
        self.red = 0
        self.yellow = 0
        self.green = 0

    def process(self, pass_request):
        count = self.count
        red = self.red
        yellow = self.yellow
        green = self.green
        p_red = self.p_red
        p_yellow = self.p_yellow
        p_green = self.p_green

        # All three register groups use the same pre-edge state.
        if pass_request and green and count > 10:
            self.count = 10
        elif not green and p_green:
            self.count = 60
        elif not yellow and p_yellow:
            self.count = 5
        elif not red and p_red:
            self.count = 10
        else:
            self.count = (count - 1) & 255

        self.red = p_red
        self.yellow = p_yellow
        self.green = p_green
        if self.phase == 0:
            self.p_red = 0
            self.p_yellow = 0
            self.p_green = 0
            self.phase = 1
        elif self.phase == 1:
            self.p_red = 1
            self.p_yellow = 0
            self.p_green = 0
            if count == 3:
                self.phase = 3
        elif self.phase == 2:
            self.p_red = 0
            self.p_yellow = 1
            self.p_green = 0
            if count == 3:
                self.phase = 1
        else:
            self.p_red = 0
            self.p_yellow = 0
            self.p_green = 1
            if count == 3:
                self.phase = 2
        return {"clock": count, "red": red, "yellow": yellow, "green": green}
