module recoded_float_resize (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic [32:0] in_in,
    input logic [2:0] in_roundingMode,
    input logic in_detectTininess,
    output logic out_valid, input logic out_ready,
    output logic [16:0] out_out,
    output logic [4:0] out_exceptionFlags
);
    logic busy;
    logic [16:0] value;
    logic [4:0] flags;
    logic sign, inexact, up, tiny, carry_up;
    integer exponent, e, shift, top;
    logic [23:0] significand, fraction;
    logic [31:0] quotient, remainder, half, unbounded, extra;
    logic [15:0] magnitude;
    assign in_ready = !busy || out_ready;
    assign out_valid = busy;

    always_comb begin
        sign = in_in[32];
        exponent = {23'b0, in_in[31:23]};
        fraction = {1'b0, in_in[22:0]};
        e = exponent - 256;
        shift = 13;
        top = 0;
        significand = 24'h800000 | fraction;
        quotient = 0;
        remainder = 0;
        half = 0;
        unbounded = 0;
        extra = 0;
        magnitude = 0;
        up = 0;
        carry_up = 0;
        inexact = 0;
        tiny = 0;
        value = {sign, 16'b0};
        flags = 0;
        case (in_in[31:29])
            3'd7: begin
                value = 17'h0E200;
                flags = in_in[22] ? 5'd0 : 5'd16;
            end
            3'd6: value = {sign, 16'hC000};
            3'd0: value = {sign, 16'b0};
            default: begin
                if (e < -14) shift = 255 - exponent;
                quotient = 0;
                remainder = {8'b0, significand};
                half = 32'h1000000;
                if (shift <= 24) begin
                    quotient = significand >> shift;
                    remainder = significand & ((32'd1 << shift) - 1);
                    half = 32'd1 << (shift - 1);
                end
                inexact = remainder != 0;
                case (in_roundingMode)
                    0: up = remainder > half || (remainder == half && quotient[0]);
                    2: up = sign && inexact;
                    3: up = !sign && inexact;
                    4: up = remainder >= half;
                    default: up = 0;
                endcase
                if (up) quotient = quotient + 1;
                if (in_roundingMode == 6 && inexact) quotient = quotient | 1;
                unbounded = significand >> 13;
                extra = significand & 8191;
                case (in_roundingMode)
                    0: carry_up = extra > 4096 || (extra == 4096 && unbounded[0]);
                    2: carry_up = sign && extra != 0;
                    3: carry_up = !sign && extra != 0;
                    4: carry_up = extra >= 4096;
                    default: carry_up = 0;
                endcase
                if (carry_up) unbounded = unbounded + 1;
                tiny = e < -14;
                if (in_detectTininess && e == -15 && unbounded == 2048) tiny = 0;
                if (inexact) flags = tiny ? 5'd3 : 5'd1;
                if (e >= -14) begin
                    if (quotient == 2048) begin
                        quotient = 1024;
                        e = e + 1;
                    end
                    if (e > 15) begin
                        flags = 5;
                        magnitude = 16'hBFFF;
                        if (in_roundingMode == 0 || in_roundingMode == 4 ||
                            (in_roundingMode == 2 && sign) ||
                            (in_roundingMode == 3 && !sign)) magnitude = 16'hC000;
                        value = {sign, magnitude};
                    end else value = (sign ? 17'h10000 : 17'd0) |
                                     ((e + 32) << 10) | (quotient & 1023);
                end else if (quotient != 0) begin
                    for (integer bitno = 0; bitno < 11; bitno = bitno + 1)
                        if (quotient >= (32'd1 << bitno)) top = bitno;
                    value = (sign ? 17'h10000 : 17'd0) |
                            ((top + 8) << 10) | ((quotient << (10 - top)) & 1023);
                end
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            out_out <= 0;
            out_exceptionFlags <= 0;
        end else if (in_valid && in_ready) begin
            busy <= 1;
            out_out <= value;
            out_exceptionFlags <= flags;
        end else if (out_ready) busy <= 0;
    end
endmodule
