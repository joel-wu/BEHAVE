module DiagScaleElementwiseDivider (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_diag,
    input  logic [31:0]  in_y,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_x
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [31:0] trunc_div32(
    input logic [31:0] y_bits,
    input logic [31:0] diag_bits
);
    logic signed [31:0] y_s;
    logic signed [31:0] diag_s;
    logic [31:0] abs_y;
    logic [31:0] abs_diag;
    logic [31:0] q_mag;
    logic        neg_result;
begin
    trunc_div32 = 32'h00000000;

    y_s    = $signed(y_bits);
    diag_s = $signed(diag_bits);

    if (diag_bits == 32'h00000000) begin
        trunc_div32 = 32'h00000000;
    end else if (y_bits == 32'h00000000) begin
        trunc_div32 = 32'h00000000;
    end else begin
        if (y_s < 0)
            abs_y = (~y_bits) + 32'd1;
        else
            abs_y = y_bits;

        if (diag_s < 0)
            abs_diag = (~diag_bits) + 32'd1;
        else
            abs_diag = diag_bits;

        q_mag = abs_y / abs_diag;
        neg_result = y_s[31] ^ diag_s[31];

        if (neg_result)
            trunc_div32 = (~q_mag) + 32'd1;
        else
            trunc_div32 = q_mag;
    end
end
endfunction

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy  <= 1'b0;
        out_x <= 32'h00000000;
    end else if (input_accepted) begin
        out_x <= trunc_div32(in_y, in_diag);
        busy  <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule