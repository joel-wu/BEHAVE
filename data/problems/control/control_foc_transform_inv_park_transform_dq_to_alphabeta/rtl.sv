module control_foc_transform_inv_park_transform_dq_to_alphabeta (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_d,
    input  logic [15:0] in_q,
    input  logic [15:0] in_theta,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_alpha,
    output logic [15:0] out_beta
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [15:0] sat16(input logic signed [31:0] value);
    begin
        if (value > 32'sd32767) begin
            sat16 = 16'sd32767;
        end else if (value < -32'sd32768) begin
            sat16 = -16'sd32768;
        end else begin
            sat16 = value[15:0];
        end
    end
endfunction

function automatic logic signed [15:0] lut_q15(input logic [6:0] idx);
    begin
        unique case (idx)
            7'd0:  lut_q15 = 16'sd0;
            7'd1:  lut_q15 = 16'sd804;
            7'd2:  lut_q15 = 16'sd1608;
            7'd3:  lut_q15 = 16'sd2411;
            7'd4:  lut_q15 = 16'sd3212;
            7'd5:  lut_q15 = 16'sd4011;
            7'd6:  lut_q15 = 16'sd4808;
            7'd7:  lut_q15 = 16'sd5602;
            7'd8:  lut_q15 = 16'sd6393;
            7'd9:  lut_q15 = 16'sd7180;
            7'd10: lut_q15 = 16'sd7962;
            7'd11: lut_q15 = 16'sd8740;
            7'd12: lut_q15 = 16'sd9512;
            7'd13: lut_q15 = 16'sd10279;
            7'd14: lut_q15 = 16'sd11039;
            7'd15: lut_q15 = 16'sd11793;
            7'd16: lut_q15 = 16'sd12540;
            7'd17: lut_q15 = 16'sd13279;
            7'd18: lut_q15 = 16'sd14010;
            7'd19: lut_q15 = 16'sd14733;
            7'd20: lut_q15 = 16'sd15447;
            7'd21: lut_q15 = 16'sd16151;
            7'd22: lut_q15 = 16'sd16846;
            7'd23: lut_q15 = 16'sd17531;
            7'd24: lut_q15 = 16'sd18205;
            7'd25: lut_q15 = 16'sd18868;
            7'd26: lut_q15 = 16'sd19520;
            7'd27: lut_q15 = 16'sd20160;
            7'd28: lut_q15 = 16'sd20788;
            7'd29: lut_q15 = 16'sd21403;
            7'd30: lut_q15 = 16'sd22006;
            7'd31: lut_q15 = 16'sd22595;
            7'd32: lut_q15 = 16'sd23170;
            7'd33: lut_q15 = 16'sd23732;
            7'd34: lut_q15 = 16'sd24279;
            7'd35: lut_q15 = 16'sd24812;
            7'd36: lut_q15 = 16'sd25330;
            7'd37: lut_q15 = 16'sd25833;
            7'd38: lut_q15 = 16'sd26320;
            7'd39: lut_q15 = 16'sd26791;
            7'd40: lut_q15 = 16'sd27246;
            7'd41: lut_q15 = 16'sd27684;
            7'd42: lut_q15 = 16'sd28106;
            7'd43: lut_q15 = 16'sd28511;
            7'd44: lut_q15 = 16'sd28899;
            7'd45: lut_q15 = 16'sd29269;
            7'd46: lut_q15 = 16'sd29622;
            7'd47: lut_q15 = 16'sd29957;
            7'd48: lut_q15 = 16'sd30274;
            7'd49: lut_q15 = 16'sd30572;
            7'd50: lut_q15 = 16'sd30853;
            7'd51: lut_q15 = 16'sd31114;
            7'd52: lut_q15 = 16'sd31357;
            7'd53: lut_q15 = 16'sd31581;
            7'd54: lut_q15 = 16'sd31786;
            7'd55: lut_q15 = 16'sd31972;
            7'd56: lut_q15 = 16'sd32138;
            7'd57: lut_q15 = 16'sd32286;
            7'd58: lut_q15 = 16'sd32413;
            7'd59: lut_q15 = 16'sd32522;
            7'd60: lut_q15 = 16'sd32610;
            7'd61: lut_q15 = 16'sd32679;
            7'd62: lut_q15 = 16'sd32729;
            7'd63: lut_q15 = 16'sd32758;
            7'd64: lut_q15 = 16'sd32767;
        endcase
    end
endfunction

logic [1:0] quadrant;
logic [5:0] offset;
logic [6:0] mirror_idx;

logic signed [15:0] d_s;
logic signed [15:0] q_s;
logic signed [15:0] sin_theta_s;
logic signed [15:0] cos_theta_s;
logic signed [15:0] sin_lut_val;
logic signed [15:0] cos_lut_val;

logic signed [31:0] prod_cos_d;
logic signed [31:0] prod_sin_q;
logic signed [31:0] prod_cos_q;
logic signed [31:0] prod_sin_d;

logic signed [31:0] shifted_cos_d;
logic signed [31:0] shifted_sin_q;
logic signed [31:0] shifted_cos_q;
logic signed [31:0] shifted_sin_d;

logic signed [15:0] mul_cos_d;
logic signed [15:0] mul_sin_q;
logic signed [15:0] mul_cos_q;
logic signed [15:0] mul_sin_d;

logic signed [31:0] alpha_tmp;
logic signed [31:0] beta_tmp;
logic signed [15:0] alpha_sat;
logic signed [15:0] beta_sat;

always_comb begin
    quadrant   = in_theta[15:14];
    offset     = in_theta[13:8];
    mirror_idx = 7'd64 - {1'b0, offset};

    d_s = in_d;
    q_s = in_q;

    sin_lut_val = 16'sd0;
    cos_lut_val = 16'sd0;
    sin_theta_s = 16'sd0;
    cos_theta_s = 16'sd0;

    unique case (quadrant)
        2'd0: begin
            sin_lut_val = lut_q15({1'b0, offset});
            cos_lut_val = lut_q15(mirror_idx);
            sin_theta_s = sin_lut_val;
            cos_theta_s = cos_lut_val;
        end
        2'd1: begin
            sin_lut_val = lut_q15(mirror_idx);
            cos_lut_val = lut_q15({1'b0, offset});
            sin_theta_s = sin_lut_val;
            cos_theta_s = -cos_lut_val;
        end
        2'd2: begin
            sin_lut_val = lut_q15({1'b0, offset});
            cos_lut_val = lut_q15(mirror_idx);
            sin_theta_s = -sin_lut_val;
            cos_theta_s = -cos_lut_val;
        end
        2'd3: begin
            sin_lut_val = lut_q15(mirror_idx);
            cos_lut_val = lut_q15({1'b0, offset});
            sin_theta_s = -sin_lut_val;
            cos_theta_s = cos_lut_val;
        end
    endcase

    prod_cos_d = cos_theta_s * d_s;
    prod_sin_q = sin_theta_s * q_s;
    prod_cos_q = cos_theta_s * q_s;
    prod_sin_d = sin_theta_s * d_s;

    shifted_cos_d = prod_cos_d >>> 15;
    shifted_sin_q = prod_sin_q >>> 15;
    shifted_cos_q = prod_cos_q >>> 15;
    shifted_sin_d = prod_sin_d >>> 15;

    mul_cos_d = sat16(shifted_cos_d);
    mul_sin_q = sat16(shifted_sin_q);
    mul_cos_q = sat16(shifted_cos_q);
    mul_sin_d = sat16(shifted_sin_d);

    alpha_tmp = mul_cos_d - mul_sin_q;
    beta_tmp  = mul_cos_q + mul_sin_d;

    alpha_sat = sat16(alpha_tmp);
    beta_sat  = sat16(beta_tmp);
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy      <= 1'b0;
        out_alpha <= 16'h0000;
        out_beta  <= 16'h0000;
    end else if (input_accepted) begin
        busy      <= 1'b1;
        out_alpha <= alpha_sat[15:0];
        out_beta  <= beta_sat[15:0];
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
