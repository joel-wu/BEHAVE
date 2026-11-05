class Calendar:
    def __init__(self):
        self.reset()

    def reset(self):
        self.hours = 0
        self.mins = 0
        self.secs = 0

    def process(self):
        hours = self.hours
        mins = self.mins
        secs = self.secs
        if secs == 59:
            self.secs = 0
            if mins == 59:
                self.mins = 0
                if hours == 23:
                    self.hours = 0
                else:
                    self.hours = hours + 1
            else:
                self.mins = mins + 1
        else:
            self.secs = secs + 1
        return {"Hours": hours, "Mins": mins, "Secs": secs}
