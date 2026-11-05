class EncoderObserverHysteresis:
    def __init__(self):
        self.reset()

    def reset(self):
        self.using_encoder = 0

    def process(self, in_obs_angle, in_enc_angle, in_speed_rad_q16, in_threshold_rpm_q16):
        speed = in_speed_rad_q16 & 0xffffffff
        if speed >= 0x80000000:
            speed -= 0x100000000
        if speed < 0:
            speed = -speed
        threshold = in_threshold_rpm_q16 & 0xffffffff
        rpm_scaled = 20 * speed * 10013163
        if self.using_encoder:
            if rpm_scaled > 21 * threshold * 1048576:
                self.using_encoder = 0
        elif rpm_scaled < 19 * threshold * 1048576:
            self.using_encoder = 1
        angle = in_enc_angle if self.using_encoder else in_obs_angle
        return {"out_angle": angle & 0xffffffff,
                "out_using_encoder": self.using_encoder}
