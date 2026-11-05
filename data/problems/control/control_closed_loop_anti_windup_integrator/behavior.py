def signed16(value):
    value &= 65535
    return value - 65536 if value >= 32768 else value


def trunc_div(numerator, denominator):
    return numerator // denominator if numerator >= 0 else -((-numerator) // denominator)


def bounded(value, limit):
    # A negative limit disables the configured clamp, not the port's range.
    low = -limit if limit >= 0 else -32768
    high = limit if limit >= 0 else 32767
    return max(low, min(high, value))


class MicrosecondSamplePidQ15:
    def __init__(self):
        self.reset()

    def reset(self):
        self.error_prev = 0
        self.integral_prev = 0
        self.output_prev = 0

    def process(self, in_error, in_p_gain, in_i_gain, in_d_gain, in_output_ramp, in_limit, in_dt_us):
        error = signed16(in_error)
        p, i, d = signed16(in_p_gain), signed16(in_i_gain), signed16(in_d_gain)
        ramp, limit = signed16(in_output_ramp), signed16(in_limit)
        dt_num = in_dt_us & 0xFFFFFFFF
        if dt_num == 0 or dt_num > 500000:
            dt_num = 1000
        dt_den = 1000000

        proportional = trunc_div(p * error, 32768)
        increment = trunc_div(i * (error + self.error_prev) * dt_num, 65536 * dt_den)
        integral = bounded(self.integral_prev + increment, limit)
        derivative = trunc_div(d * (error - self.error_prev) * dt_den, 32768 * dt_num)
        output = bounded(proportional + integral + derivative, limit)
        if ramp > 0:
            step = (ramp * dt_num) // dt_den
            output = max(self.output_prev - step, min(self.output_prev + step, output))
        self.error_prev = error
        self.integral_prev = integral
        self.output_prev = output
        return {"out_output": output & 65535}
