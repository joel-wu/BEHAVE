module KyberPolyToBytesPair (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_coeff0,
    input  logic [15:0] in_coeff1,

    output logic        out_valid,
    output logic out_error,
    input  logic        out_ready,
    output logic [7:0]  out_byte0,
    output logic [7:0]  out_byte1,
    output logic [7:0]  out_byte2
);

logic busy;
    wire invalid_input = ($signed(in_coeff0) <= -16'sd3329 || $signed(in_coeff0) >= 16'sd3329) ||
        ($signed(in_coeff1) <= -16'sd3329 || $signed(in_coeff1) >= 16'sd3329);

wire input_accepted;
wire output_consumed;

logic signed [15:0] c0_s;
logic signed [15:0] c1_s;
logic signed [15:0] t0_s;
logic signed [15:0] t1_s;
logic [7:0] next_byte0;
logic [7:0] next_byte1;
logic [7:0] next_byte2;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    c0_s = $signed(in_coeff0);
    c1_s = $signed(in_coeff1);

    if (c0_s < 0)
        t0_s = c0_s + 16'sd3329;
    else
        t0_s = c0_s;

    if (c1_s < 0)
        t1_s = c1_s + 16'sd3329;
    else
        t1_s = c1_s;

    next_byte0 = t0_s[7:0];
    next_byte1 = (t0_s[15:8] | {t1_s[3:0], 4'b0000});
    next_byte2 = t1_s[11:4];
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy      <= 1'b0;
            out_error <= 1'b0;
        out_byte0 <= 8'h00;
        out_byte1 <= 8'h00;
        out_byte2 <= 8'h00;
    end else if (input_accepted) begin
        out_byte0 <= invalid_input ? '0 : (next_byte0);
        out_byte1 <= invalid_input ? '0 : (next_byte1);
        out_byte2 <= invalid_input ? '0 : (next_byte2);
        busy      <= 1'b1;
            out_error <= invalid_input;
    end else if (output_consumed) begin
        busy <= 1'b0;
            out_error <= 1'b0;
    end
end

endmodule
