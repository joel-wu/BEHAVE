module control_foc_transform_park_transform_alphabeta_to_dq (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_alpha,
    input  logic [15:0]  in_beta,
    input  logic [15:0]  in_theta,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_d,
    output logic [15:0]  out_q
);

logic busy;
wire  input_accepted;
wire  output_consumed;

logic [15:0] out_d_next;
logic [15:0] out_q_next;

logic [1:0] quad;
logic [5:0] idx;
logic [6:0] idx_mirror7;

logic signed [15:0] alpha_s;
logic signed [15:0] beta_s;
logic signed [15:0] sin_mag_s;
logic signed [15:0] cos_mag_s;
logic signed [15:0] sin_v;
logic signed [15:0] cos_v;

logic signed [31:0] p_ca;
logic signed [31:0] p_sb;
logic signed [31:0] p_cb;
logic signed [31:0] p_sa;

logic signed [31:0] t_ca;
logic signed [31:0] t_sb;
logic signed [31:0] t_cb;
logic signed [31:0] t_sa;
logic signed [31:0] d_pre;
logic signed [31:0] q_pre;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [15:0] sin_table(input logic [6:0] table_idx);
    begin
        sin_table = 16'd0;
        unique case (table_idx)
            7'd0:  sin_table = 16'd0;
            7'd1:  sin_table = 16'd804;
            7'd2:  sin_table = 16'd1608;
            7'd3:  sin_table = 16'd2411;
            7'd4:  sin_table = 16'd3212;
            7'd5:  sin_table = 16'd4011;
            7'd6:  sin_table = 16'd4808;
            7'd7:  sin_table = 16'd5602;
            7'd8:  sin_table = 16'd6393;
            7'd9:  sin_table = 16'd7180;
            7'd10: sin_table = 16'd7962;
            7'd11: sin_table = 16'd8740;
            7'd12: sin_table = 16'd9512;
            7'd13: sin_table = 16'd10279;
            7'd14: sin_table = 16'd11039;
            7'd15: sin_table = 16'd11793;
            7'd16: sin_table = 16'd12540;
            7'd17: sin_table = 16'd13279;
            7'd18: sin_table = 16'd14010;
            7'd19: sin_table = 16'd14733;
            7'd20: sin_table = 16'd15447;
            7'd21: sin_table = 16'd16151;
            7'd22: sin_table = 16'd16846;
            7'd23: sin_table = 16'd17531;
            7'd24: sin_table = 16'd18205;
            7'd25: sin_table = 16'd18868;
            7'd26: sin_table = 16'd19520;
            7'd27: sin_table = 16'd20160;
            7'd28: sin_table = 16'd20788;
            7'd29: sin_table = 16'd21403;
            7'd30: sin_table = 16'd22006;
            7'd31: sin_table = 16'd22595;
            7'd32: sin_table = 16'd23170;
            7'd33: sin_table = 16'd23732;
            7'd34: sin_table = 16'd24279;
            7'd35: sin_table = 16'd24812;
            7'd36: sin_table = 16'd25330;
            7'd37: sin_table = 16'd25833;
            7'd38: sin_table = 16'd26320;
            7'd39: sin_table = 16'd26791;
            7'd40: sin_table = 16'd27246;
            7'd41: sin_table = 16'd27684;
            7'd42: sin_table = 16'd28106;
            7'd43: sin_table = 16'd28511;
            7'd44: sin_table = 16'd28899;
            7'd45: sin_table = 16'd29269;
            7'd46: sin_table = 16'd29622;
            7'd47: sin_table = 16'd29957;
            7'd48: sin_table = 16'd30274;
            7'd49: sin_table = 16'd30572;
            7'd50: sin_table = 16'd30853;
            7'd51: sin_table = 16'd31114;
            7'd52: sin_table = 16'd31357;
            7'd53: sin_table = 16'd31581;
            7'd54: sin_table = 16'd31786;
            7'd55: sin_table = 16'd31972;
            7'd56: sin_table = 16'd32138;
            7'd57: sin_table = 16'd32286;
            7'd58: sin_table = 16'd32413;
            7'd59: sin_table = 16'd32522;
            7'd60: sin_table = 16'd32610;
            7'd61: sin_table = 16'd32679;
            7'd62: sin_table = 16'd32729;
            7'd63: sin_table = 16'd32758;
            7'd64: sin_table = 16'd32767;
            default: sin_table = 16'd0;
        endcase
    end
endfunction

function automatic logic signed [15:0] sat16(input logic signed [31:0] value);
    begin
        if (value > 32'sd32767)
            sat16 = 16'sd32767;
        else if (value < -32'sd32768)
            sat16 = -16'sd32768;
        else
            sat16 = value[15:0];
    end
endfunction

always_comb begin
    alpha_s = $signed(in_alpha);
    beta_s  = $signed(in_beta);
    quad    = in_theta[15:14];
    idx     = in_theta[13:8];

    idx_mirror7 = 7'd64 - {1'b0, idx};

    sin_mag_s = 16'sd0;
    cos_mag_s = 16'sd0;
    sin_v     = 16'sd0;
    cos_v     = 16'sd0;

    p_ca = 32'sd0;
    p_sb = 32'sd0;
    p_cb = 32'sd0;
    p_sa = 32'sd0;

    t_ca = 32'sd0;
    t_sb = 32'sd0;
    t_cb = 32'sd0;
    t_sa = 32'sd0;
    d_pre = 32'sd0;
    q_pre = 32'sd0;

    out_d_next = 16'h0000;
    out_q_next = 16'h0000;

    unique case (quad)
        2'd0: begin
            sin_mag_s = $signed(sin_table({1'b0, idx}));
            cos_mag_s = $signed(sin_table(idx_mirror7));
            sin_v     = sin_mag_s;
            cos_v     = cos_mag_s;
        end
        2'd1: begin
            sin_mag_s = $signed(sin_table(idx_mirror7));
            cos_mag_s = $signed(sin_table({1'b0, idx}));
            sin_v     = sin_mag_s;
            cos_v     = -cos_mag_s;
        end
        2'd2: begin
            sin_mag_s = $signed(sin_table({1'b0, idx}));
            cos_mag_s = $signed(sin_table(idx_mirror7));
            sin_v     = -sin_mag_s;
            cos_v     = -cos_mag_s;
        end
        2'd3: begin
            sin_mag_s = $signed(sin_table(idx_mirror7));
            cos_mag_s = $signed(sin_table({1'b0, idx}));
            sin_v     = -sin_mag_s;
            cos_v     = cos_mag_s;
        end
        default: begin
            sin_mag_s = 16'sd0;
            cos_mag_s = 16'sd0;
            sin_v     = 16'sd0;
            cos_v     = 16'sd0;
        end
    endcase

    p_ca = cos_v * alpha_s;
    p_sb = sin_v * beta_s;
    p_cb = cos_v * beta_s;
    p_sa = sin_v * alpha_s;

    t_ca = p_ca >>> 15;
    t_sb = p_sb >>> 15;
    t_cb = p_cb >>> 15;
    t_sa = p_sa >>> 15;

    d_pre = t_ca + t_sb;
    q_pre = t_cb - t_sa;

    out_d_next = sat16(d_pre);
    out_q_next = sat16(q_pre);
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy  <= 1'b0;
        out_d <= 16'h0000;
        out_q <= 16'h0000;
    end else if (input_accepted) begin
        out_d <= out_d_next;
        out_q <= out_q_next;
        busy  <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
