module cryptography_sym_aead_ghash_gf128_mul (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_h,
    input  logic [127:0] in_y,
    input  logic [127:0] in_block,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_y
);

logic        busy;
logic [127:0] out_y_reg;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;
assign out_y     = out_y_reg;

function automatic logic [127:0] ghash_mul(
    input logic [127:0] h,
    input logic [127:0] y,
    input logic [127:0] block
);
    logic [127:0] x;
    logic [127:0] v;
    logic [127:0] z;
    logic [127:0] rconst;
    int i;
    begin
        x      = y ^ block;
        v      = h;
        z      = 128'b0;
        rconst = 128'hE1000000000000000000000000000000;

        for (i = 0; i < 128; i = i + 1) begin
            if (x[127 - i]) begin
                z = z ^ v;
            end
            if (i != 127) begin
                if (v[0]) begin
                    v = (v >> 1) ^ rconst;
                end else begin
                    v = (v >> 1);
                end
            end
        end

        ghash_mul = z;
    end
endfunction

always_ff @(posedge clk) begin
    if (rst) begin
        busy      <= 1'b0;
        out_y_reg <= 128'b0;
    end else if (input_accepted) begin
        out_y_reg <= ghash_mul(in_h, in_y, in_block);
        busy      <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule