module semi_interp_1d_fine_kernel (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_p0,
    input  logic [31:0] in_p1,
    input  logic [31:0] in_e_left,
    input  logic [31:0] in_e_right,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_e
);

logic busy;
wire  output_consumed;
wire  input_accepted;

logic signed [31:0] p0_s;
logic signed [31:0] p1_s;
logic signed [31:0] e_left_s;
logic signed [31:0] e_right_s;
logic signed [63:0] prod0_s;
logic signed [63:0] prod1_s;
logic signed [63:0] sum64_s;

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

assign p0_s      = $signed(in_p0);
assign p1_s      = $signed(in_p1);
assign e_left_s  = $signed(in_e_left);
assign e_right_s = $signed(in_e_right);
assign prod0_s   = p0_s * e_left_s;
assign prod1_s   = p1_s * e_right_s;
assign sum64_s   = prod0_s + prod1_s;

always_ff @(posedge clk) begin
    if (rst) begin
        busy  <= 1'b0;
        out_e <= 32'h00000000;
    end else if (input_accepted) begin
        busy  <= 1'b1;
        out_e <= sum64_s[31:0];
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule