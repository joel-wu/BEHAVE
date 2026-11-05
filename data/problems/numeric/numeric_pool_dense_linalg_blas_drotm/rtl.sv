module modified_givens_rotm_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_x,
    input  logic [63:0]  in_y,
    input  logic [63:0]  in_dflag,
    input  logic [63:0]  in_dh11,
    input  logic [63:0]  in_dh21,
    input  logic [63:0]  in_dh12,
    input  logic [63:0]  in_dh22,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_x,
    output logic [63:0]  out_y
);

logic busy;

wire input_accepted;
wire output_consumed;

logic signed [63:0] sx;
logic signed [63:0] sy;
logic signed [63:0] sdflag;
logic signed [63:0] sdh11;
logic signed [63:0] sdh21;
logic signed [63:0] sdh12;
logic signed [63:0] sdh22;

logic signed [63:0] calc_out_x;
logic signed [63:0] calc_out_y;

logic signed [127:0] mul_full;
logic signed [63:0]  p0;
logic signed [63:0]  p1;
logic signed [63:0]  p2;
logic signed [63:0]  p3;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    sx     = $signed(in_x);
    sy     = $signed(in_y);
    sdflag = $signed(in_dflag);
    sdh11  = $signed(in_dh11);
    sdh21  = $signed(in_dh21);
    sdh12  = $signed(in_dh12);
    sdh22  = $signed(in_dh22);

    p0 = 64'sd0;
    p1 = 64'sd0;
    p2 = 64'sd0;
    p3 = 64'sd0;
    calc_out_x = 64'sd0;
    calc_out_y = 64'sd0;
    mul_full = 128'sd0;

    if (sdflag == -64'sd2) begin
        calc_out_x = sx;
        calc_out_y = sy;
    end else if (sdflag < 64'sd0) begin
        mul_full = sx * sdh11;
        p0 = mul_full[63:0];
        mul_full = sy * sdh12;
        p1 = mul_full[63:0];
        mul_full = sx * sdh21;
        p2 = mul_full[63:0];
        mul_full = sy * sdh22;
        p3 = mul_full[63:0];
        calc_out_x = p0 + p1;
        calc_out_y = p2 + p3;
    end else if (sdflag == 64'sd0) begin
        mul_full = sy * sdh12;
        p0 = mul_full[63:0];
        mul_full = sx * sdh21;
        p1 = mul_full[63:0];
        calc_out_x = sx + p0;
        calc_out_y = p1 + sy;
    end else begin
        mul_full = sx * sdh11;
        p0 = mul_full[63:0];
        mul_full = sdh22 * sy;
        p1 = mul_full[63:0];
        calc_out_x = p0 + sy;
        calc_out_y = (-sx) + p1;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy  <= 1'b0;
        out_x <= 64'h0000000000000000;
        out_y <= 64'h0000000000000000;
    end else if (input_accepted) begin
        busy  <= 1'b1;
        out_x <= calc_out_x;
        out_y <= calc_out_y;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule