module semi_restrict_3tap_1d (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_constant_coefficient,
    input  logic signed [31:0] in_r_center,
    input  logic signed [31:0] in_r_left,
    input  logic signed [31:0] in_r_right,
    input  logic signed [31:0] in_Rp0,
    input  logic signed [31:0] in_Rp1,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [31:0] out_rc
);

logic busy;
wire  output_consumed;
wire  input_accepted;

logic signed [63:0] sum64;
logic signed [31:0] result32;

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    sum64    = $signed(in_r_center) + ($signed(in_Rp0) * $signed(in_r_left)) + ($signed(in_Rp1) * $signed(in_r_right));
    result32 = sum64[31:0];
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy   <= 1'b0;
        out_rc <= 32'sd0;
    end else if (input_accepted) begin
        out_rc <= result32;
        busy   <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule