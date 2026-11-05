module biquad_cascade_df1_q15_sample (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_y
);

logic busy;

logic signed [15:0] xn1_0, xn2_0, yn1_0, yn2_0;
logic signed [15:0] xn1_1, xn2_1, yn1_1, yn2_1;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

localparam logic signed [15:0] B0_0 = 16'sh2000;
localparam logic signed [15:0] B1_0 = 16'sh1000;
localparam logic signed [15:0] B2_0 = 16'sh0800;
localparam logic signed [15:0] A1_0 = 16'sh0400;
localparam logic signed [15:0] A2_0 = 16'sh0200;

localparam logic signed [15:0] B0_1 = 16'sh1800;
localparam logic signed [15:0] B1_1 = 16'sh0C00;
localparam logic signed [15:0] B2_1 = 16'sh0600;
localparam logic signed [15:0] A1_1 = 16'sh0300;
localparam logic signed [15:0] A2_1 = 16'sh0100;

logic signed [15:0] x0_s;
logic signed [63:0] p0_b0, p0_b1, p0_b2, p0_a1, p0_a2;
logic signed [63:0] acc0;
logic signed [63:0] shifted0;
logic signed [15:0] y0;

logic signed [15:0] x1_s;
logic signed [63:0] p1_b0, p1_b1, p1_b2, p1_a1, p1_a2;
logic signed [63:0] acc1;
logic signed [63:0] shifted1;
logic signed [15:0] y1;

always_comb begin
    x0_s = $signed(in_x);

    p0_b0 = $signed(B0_0) * $signed(x0_s);
    p0_b1 = $signed(B1_0) * $signed(xn1_0);
    p0_b2 = $signed(B2_0) * $signed(xn2_0);
    p0_a1 = $signed(A1_0) * $signed(yn1_0);
    p0_a2 = $signed(A2_0) * $signed(yn2_0);
    acc0 = p0_b0 + p0_b1 + p0_b2 + p0_a1 + p0_a2;
    shifted0 = acc0 >>> 15;
    if (shifted0[63:15] == {49{1'b0}} || shifted0[63:15] == {49{1'b1}}) begin
        y0 = shifted0[15:0];
    end else begin
        y0 = shifted0[63] ? 16'sh8000 : 16'sh7FFF;
    end

    x1_s = y0;

    p1_b0 = $signed(B0_1) * $signed(x1_s);
    p1_b1 = $signed(B1_1) * $signed(xn1_1);
    p1_b2 = $signed(B2_1) * $signed(xn2_1);
    p1_a1 = $signed(A1_1) * $signed(yn1_1);
    p1_a2 = $signed(A2_1) * $signed(yn2_1);
    acc1 = p1_b0 + p1_b1 + p1_b2 + p1_a1 + p1_a2;
    shifted1 = acc1 >>> 15;
    if (shifted1[63:15] == {49{1'b0}} || shifted1[63:15] == {49{1'b1}}) begin
        y1 = shifted1[15:0];
    end else begin
        y1 = shifted1[63] ? 16'sh8000 : 16'sh7FFF;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy  <= 1'b0;
        out_y <= 16'h0000;

        xn1_0 <= 16'sd0;
        xn2_0 <= 16'sd0;
        yn1_0 <= 16'sd0;
        yn2_0 <= 16'sd0;

        xn1_1 <= 16'sd0;
        xn2_1 <= 16'sd0;
        yn1_1 <= 16'sd0;
        yn2_1 <= 16'sd0;
    end else if (input_accepted) begin
        out_y <= y1[15:0];
        busy  <= 1'b1;

        xn2_0 <= xn1_0;
        xn1_0 <= x0_s;
        yn2_0 <= yn1_0;
        yn1_0 <= y0;

        xn2_1 <= xn1_1;
        xn1_1 <= x1_s;
        yn2_1 <= yn1_1;
        yn1_1 <= y1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule