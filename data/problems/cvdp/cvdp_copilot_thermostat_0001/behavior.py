class Thermostat:
    def __init__(self):
        self.reset()

    def reset(self):
        self.fault_latched = 0
        self.state = 3
        self.fan = 0

    def process(self, i_temp_feedback, i_fan_on, i_enable, i_fault, i_clr):
        state = self.state
        fan = self.fan
        if i_fault:
            self.fault_latched = 1
        elif i_clr:
            self.fault_latched = 0
        next_state = 3
        if i_enable and not self.fault_latched and not i_clr:
            if i_temp_feedback & 32:
                next_state = 2
            elif i_temp_feedback & 16:
                next_state = 1
            elif i_temp_feedback & 8:
                next_state = 0
            elif i_temp_feedback & 1:
                next_state = 6
            elif i_temp_feedback & 2:
                next_state = 5
            elif i_temp_feedback & 4:
                next_state = 4
        self.state = next_state
        self.fan = int(i_enable and not self.fault_latched and (next_state != 3 or i_fan_on))
        return {"o_heater_full": int(state == 2), "o_heater_medium": int(state == 1),
                "o_heater_low": int(state == 0), "o_aircon_full": int(state == 6),
                "o_aircon_medium": int(state == 5), "o_aircon_low": int(state == 4),
                "o_fan": fan, "o_state": state}
