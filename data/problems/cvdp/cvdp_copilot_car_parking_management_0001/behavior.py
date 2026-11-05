class CarParkingSystem:
    def __init__(self, TOTAL_SPACES=12):
        self.TOTAL_SPACES = TOTAL_SPACES
        self.reset()

    def reset(self):
        self.count = 0
        self.state = 0
        self.previous_entry = 0
        self.previous_exit = 0

    def process(self, vehicle_entry_sensor, vehicle_exit_sensor):
        count = self.count
        available = self.TOTAL_SPACES-count
        segments = (126,48,109,121,51,91,95,112,127,123)
        output = {"available_spaces":available, "count_car":count,
                  "led_status":int(count<self.TOTAL_SPACES),
                  "seven_seg_display_available_tens":segments[available//10],
                  "seven_seg_display_available_units":segments[available%10],
                  "seven_seg_display_count_tens":segments[count//10],
                  "seven_seg_display_count_units":segments[count%10]}
        if self.state == 1:
            self.count = count+1
            self.state = 3 if count+1 == self.TOTAL_SPACES else 0
        elif self.state == 2:
            self.count = count-1
            self.state = 0
        elif vehicle_exit_sensor and not self.previous_exit and count>0:
            self.state = 2
        elif vehicle_entry_sensor and not self.previous_entry and count<self.TOTAL_SPACES:
            self.state = 1
        self.previous_entry = vehicle_entry_sensor
        self.previous_exit = vehicle_exit_sensor
        return output
