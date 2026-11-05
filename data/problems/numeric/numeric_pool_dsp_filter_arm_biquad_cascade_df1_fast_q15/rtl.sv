module biquad_cascade_df1_fast_q15 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_x,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_y
);

logic busy;

logic signed [15:0] s0_x1, s0_x2, s0_y1, s0_y2;
logic signed [15:0] s1_x1, s1_x2, s1_y1, s1_y2;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [15:0] sat16_from_shifted(input logic signed [31:0] v);
    logic signed [31:0] upper;
    logic signed [15:0] low16;
    begin
        upper = v >>> 15;
        low16 = v[15:0];
        sat16_from_shifted = low16;
        if ((upper != 32'sd0) && (upper != -32'sd1)) begin
            sat16_from_shifted = {16{~v[31]}};
            sat16_from_shifted[15] = v[31];
        end
    end
endfunction

logic signed [15:0] sample_0;
logic signed [31:0] acc_0;
logic signed [31:0] shifted_0;
logic signed [15:0] stage_out_0;

logic signed [31:0] acc_1;
logic signed [31:0] shifted_1;
logic signed [15:0] stage_out_1;

always_comb begin
    sample_0 = in_x;

    acc_0 =
        (32'sd8192  * sample_0) +
        (32'sd4096  * s0_x1)    +
        (32'sd2048  * s0_x2)    +
        (-32'sd4096 * s0_y1)    +
        (32'sd1024  * s0_y2);
    shifted_0 = acc_0 >>> 14;
    stage_out_0 = sat16_from_shifted(shifted_0);

    acc_1 =
        (32'sd8192  * stage_out_0) +
        (-32'sd2048 * s1_x1)       +
        (32'sd1024  * s1_x2)       +
        (-32'sd3072 * s1_y1)       +
        (32'sd512   * s1_y2);
    shifted_1 = acc_1 >>> 14;
    stage_out_1 = sat16_from_shifted(shifted_1);
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy  <= 1'b0;
        out_y <= 16'h0000;

        s0_x1 <= 16'sd0;
        s0_x2 <= 16'sd0;
        s0_y1 <= 16'sd0;
        s0_y2 <= 16'sd0;

        s1_x1 <= 16'sd0;
        s1_x2 <= 16'sd0;
        s1_y1 <= 16'sd0;
        s1_y2 <= 16'sd0;
    end else if (input_accepted) begin
        out_y <= stage_out_1[15:0];
        busy  <= 1'b1;

        s0_x2 <= s0_x1;
        s0_x1 <= sample_0;
        s0_y2 <= s0_y1;
        s0_y1 <= stage_out_0;

        s1_x2 <= s1_x1;
        s1_x1 <= stage_out_0;
        s1_y2 <= s1_y1;
        s1_y1 <= stage_out_1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule