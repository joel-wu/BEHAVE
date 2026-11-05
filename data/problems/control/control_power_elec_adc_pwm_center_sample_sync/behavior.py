class AdcPwmSamplePlanner:
    def __init__(self):
        self.reset()

    def reset(self):
        self.curr1 = 0
        self.curr2 = 0

    def process(self, in_top, in_duty, in_max_duty_uq16, in_motor_type_dc, in_pwm_mode_bipolar, in_comm_step, in_direction, in_is_detecting, in_has_3_shunts, in_curr1_double_sample, in_curr2_double_sample):
        top = in_top & 0xffffffff
        gain = min(in_max_duty_uq16 & 0x1ffff, 65536)
        duty = min(in_duty & 0xffffffff, (top * gain) >> 16)
        step = in_comm_step & 7
        direction = in_direction & 1
        three = in_has_3_shunts & 1
        val, c1, c2, c3, mask = 0, self.curr1, self.curr2, 0, 0
        p = duty + ((top - duty) >> 1)
        z = (top - 2) & 0xffffffff
        if in_motor_type_dc & 1:
            c1 = (top - 10) & 0xffffffff
            c2 = c1
            c3 = c1 if three else 0
            if duty > 1000:
                val = duty >> 1
            else:
                val = duty + 800
                mask = 7
        elif in_is_detecting & 1:
            val = duty >> 1
            c1, c2 = p, p
            c3 = p if three else 0
        elif in_pwm_mode_bipolar & 1:
            val = top >> 2
            c3 = z if three else 0
            if not direction:
                if step == 1:
                    c1, c2, mask = z, p, 0
                elif step == 2:
                    c1, c2, mask = p, z, 0
                elif step == 3:
                    c1, c2, mask = p, z, 2
                elif step == 4:
                    c1, c2, mask = z, z, 2
                elif step == 5:
                    c1, c2, mask = z, z, 1
                elif step == 6:
                    c1, c2, mask = z, p, 1
            else:
                if step == 1:
                    c1, c2, mask = z, z, 2
                elif step == 2:
                    c1, c2, mask = p, z, 2
                elif step == 3:
                    c1, c2, mask = p, z, 0
                elif step == 4:
                    c1, c2, mask = z, p, 0
                elif step == 5:
                    c1, c2, mask = z, p, 1
                elif step == 6:
                    c1, c2, mask = z, z, 1
        else:
            val = duty >> 1
            center = min(p, (top - 70) & 0xffffffff)
            c1, c2 = center, center
            c3 = center if three else 0
            if duty > (top >> 1):
                double1 = (in_curr1_double_sample & 1) and (step == 2 or step == 3)
                double2 = (in_curr2_double_sample & 1) and (
                    (direction and (step == 4 or step == 5)) or
                    (not direction and (step == 1 or step == 6)))
                if double1 or double2:
                    c1 = (duty + 90) & 0xffffffff
                    c2 = (top - 230) & 0xffffffff
                if not three:
                    if not direction:
                        if step == 1:
                            mask = 1
                        elif step == 2:
                            mask = 2
                        elif step == 3:
                            mask = 2
                        elif step == 4:
                            mask = 3
                        elif step == 5:
                            mask = 3
                        elif step == 6:
                            mask = 1
                    else:
                        if step == 1:
                            mask = 3
                        elif step == 2:
                            mask = 2
                        elif step == 3:
                            mask = 2
                        elif step == 4:
                            mask = 1
                        elif step == 5:
                            mask = 1
                        elif step == 6:
                            mask = 3
                else:
                    if not direction:
                        if step == 1:
                            mask = 3
                        elif step == 2:
                            mask = 6
                        elif step == 3:
                            mask = 6
                        elif step == 4:
                            mask = 5
                        elif step == 5:
                            mask = 5
                        elif step == 6:
                            mask = 3
                    else:
                        if step == 1:
                            mask = 5
                        elif step == 2:
                            mask = 6
                        elif step == 3:
                            mask = 6
                        elif step == 4:
                            mask = 3
                        elif step == 5:
                            mask = 3
                        elif step == 6:
                            mask = 5
        self.curr1, self.curr2 = c1, c2
        return {"out_val_sample": val, "out_curr1_sample": c1,
                "out_curr2_sample": c2, "out_curr3_sample": c3, "out_curr_samp_volt": mask}
