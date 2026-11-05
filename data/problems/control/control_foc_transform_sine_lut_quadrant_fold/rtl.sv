module control_foc_transform_sine_lut_quadrant_fold (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_angle,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [16:0] out_sin,
    output logic signed [16:0] out_cos
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    function automatic logic signed [16:0] lut_val(input logic [6:0] idx);
        begin
            lut_val = 17'sd0;
            unique case (idx)
                7'd0:  lut_val = 17'sd0;
                7'd1:  lut_val = 17'sd804;
                7'd2:  lut_val = 17'sd1608;
                7'd3:  lut_val = 17'sd2411;
                7'd4:  lut_val = 17'sd3212;
                7'd5:  lut_val = 17'sd4011;
                7'd6:  lut_val = 17'sd4808;
                7'd7:  lut_val = 17'sd5602;
                7'd8:  lut_val = 17'sd6393;
                7'd9:  lut_val = 17'sd7180;
                7'd10: lut_val = 17'sd7962;
                7'd11: lut_val = 17'sd8740;
                7'd12: lut_val = 17'sd9512;
                7'd13: lut_val = 17'sd10279;
                7'd14: lut_val = 17'sd11039;
                7'd15: lut_val = 17'sd11793;
                7'd16: lut_val = 17'sd12540;
                7'd17: lut_val = 17'sd13279;
                7'd18: lut_val = 17'sd14010;
                7'd19: lut_val = 17'sd14733;
                7'd20: lut_val = 17'sd15447;
                7'd21: lut_val = 17'sd16151;
                7'd22: lut_val = 17'sd16846;
                7'd23: lut_val = 17'sd17531;
                7'd24: lut_val = 17'sd18205;
                7'd25: lut_val = 17'sd18868;
                7'd26: lut_val = 17'sd19520;
                7'd27: lut_val = 17'sd20160;
                7'd28: lut_val = 17'sd20788;
                7'd29: lut_val = 17'sd21403;
                7'd30: lut_val = 17'sd22006;
                7'd31: lut_val = 17'sd22595;
                7'd32: lut_val = 17'sd23170;
                7'd33: lut_val = 17'sd23732;
                7'd34: lut_val = 17'sd24279;
                7'd35: lut_val = 17'sd24812;
                7'd36: lut_val = 17'sd25330;
                7'd37: lut_val = 17'sd25833;
                7'd38: lut_val = 17'sd26320;
                7'd39: lut_val = 17'sd26791;
                7'd40: lut_val = 17'sd27246;
                7'd41: lut_val = 17'sd27684;
                7'd42: lut_val = 17'sd28106;
                7'd43: lut_val = 17'sd28511;
                7'd44: lut_val = 17'sd28899;
                7'd45: lut_val = 17'sd29269;
                7'd46: lut_val = 17'sd29622;
                7'd47: lut_val = 17'sd29957;
                7'd48: lut_val = 17'sd30274;
                7'd49: lut_val = 17'sd30572;
                7'd50: lut_val = 17'sd30853;
                7'd51: lut_val = 17'sd31114;
                7'd52: lut_val = 17'sd31357;
                7'd53: lut_val = 17'sd31581;
                7'd54: lut_val = 17'sd31786;
                7'd55: lut_val = 17'sd31972;
                7'd56: lut_val = 17'sd32138;
                7'd57: lut_val = 17'sd32286;
                7'd58: lut_val = 17'sd32413;
                7'd59: lut_val = 17'sd32522;
                7'd60: lut_val = 17'sd32610;
                7'd61: lut_val = 17'sd32679;
                7'd62: lut_val = 17'sd32729;
                7'd63: lut_val = 17'sd32758;
                7'd64: lut_val = 17'sd32768;
            endcase
        end
    endfunction

    function automatic logic signed [16:0] sine_lut_fold(input logic [15:0] p);
        logic [1:0] quadrant;
        logic [13:0] offset;
        logic [5:0] index;
        logic [7:0] frac;
        logic signed [16:0] t1;
        logic signed [16:0] t2;
        logic signed [17:0] delta;
        logic signed [25:0] product;
        logic signed [25:0] interp;
        logic signed [26:0] sin_raw;
        begin
            quadrant = p[15:14];
            offset   = p[13:0];
            index    = offset[13:8];
            frac     = offset[7:0];
            t1       = 17'sd0;
            t2       = 17'sd0;
            delta    = 18'sd0;
            product  = 26'sd0;
            interp   = 26'sd0;
            sin_raw  = 27'sd0;

            unique case (quadrant)
                2'd0: begin
                    t1 = lut_val({1'b0, index});
                    t2 = lut_val({1'b0, index} + 7'd1);
                end
                2'd1: begin
                    t1 = lut_val(7'd64 - {1'b0, index});
                    t2 = lut_val(7'd63 - {1'b0, index});
                end
                2'd2: begin
                    t1 = -lut_val({1'b0, index});
                    t2 = -lut_val({1'b0, index} + 7'd1);
                end
                2'd3: begin
                    t1 = -lut_val(7'd64 - {1'b0, index});
                    t2 = -lut_val(7'd63 - {1'b0, index});
                end
            endcase

            delta   = t2 - t1;
            product = delta * $signed({1'b0, frac});
            interp  = product >>> 8;
            sin_raw = t1 + interp;
            sine_lut_fold = sin_raw[16:0];
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy    <= 1'b0;
            out_sin <= 17'sd0;
            out_cos <= 17'sd0;
        end else if (input_accepted) begin
            out_sin <= sine_lut_fold(in_angle);
            out_cos <= sine_lut_fold(in_angle + 16'h4000);
            busy    <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
