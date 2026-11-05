module onset_strength_multi (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [127:0] in_S_flat,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_onset_env
);

logic busy;
logic have_ref;
logic signed [15:0] ref0, ref1, ref2, ref3, ref4, ref5, ref6, ref7;

wire output_consumed;
wire input_accepted;

logic signed [15:0] s0, s1, s2, s3, s4, s5, s6, s7;
logic signed [16:0] diff0, diff1, diff2, diff3, diff4, diff5, diff6, diff7;
logic [16:0] pos0, pos1, pos2, pos3, pos4, pos5, pos6, pos7;
logic [19:0] sum_pos;
logic [15:0] onset_env_calc;

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    s0 = in_S_flat[15:0];
    s1 = in_S_flat[31:16];
    s2 = in_S_flat[47:32];
    s3 = in_S_flat[63:48];
    s4 = in_S_flat[79:64];
    s5 = in_S_flat[95:80];
    s6 = in_S_flat[111:96];
    s7 = in_S_flat[127:112];

    diff0 = s0 - ref0;
    diff1 = s1 - ref1;
    diff2 = s2 - ref2;
    diff3 = s3 - ref3;
    diff4 = s4 - ref4;
    diff5 = s5 - ref5;
    diff6 = s6 - ref6;
    diff7 = s7 - ref7;

    if (diff0 > 0) pos0 = diff0[16:0]; else pos0 = 17'd0;
    if (diff1 > 0) pos1 = diff1[16:0]; else pos1 = 17'd0;
    if (diff2 > 0) pos2 = diff2[16:0]; else pos2 = 17'd0;
    if (diff3 > 0) pos3 = diff3[16:0]; else pos3 = 17'd0;
    if (diff4 > 0) pos4 = diff4[16:0]; else pos4 = 17'd0;
    if (diff5 > 0) pos5 = diff5[16:0]; else pos5 = 17'd0;
    if (diff6 > 0) pos6 = diff6[16:0]; else pos6 = 17'd0;
    if (diff7 > 0) pos7 = diff7[16:0]; else pos7 = 17'd0;

    sum_pos = pos0 + pos1 + pos2 + pos3 + pos4 + pos5 + pos6 + pos7;
    onset_env_calc = have_ref ? sum_pos[18:3] : 16'd0;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        have_ref <= 1'b0;
        out_onset_env <= 16'h0000;
        ref0 <= 16'sd0;
        ref1 <= 16'sd0;
        ref2 <= 16'sd0;
        ref3 <= 16'sd0;
        ref4 <= 16'sd0;
        ref5 <= 16'sd0;
        ref6 <= 16'sd0;
        ref7 <= 16'sd0;
    end else if (input_accepted) begin
        have_ref <= 1'b1;
        out_onset_env <= onset_env_calc;
        busy <= 1'b1;
        ref0 <= s0;
        ref1 <= s1;
        ref2 <= s2;
        ref3 <= s3;
        ref4 <= s4;
        ref5 <= s5;
        ref6 <= s6;
        ref7 <= s7;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
