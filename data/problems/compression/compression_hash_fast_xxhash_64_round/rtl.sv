module compression_hash_fast_xxhash_64_round (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_acc,
    input  logic [63:0]  in_input,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_acc
);

    localparam logic [63:0] PRIME64_1 = 64'h9E3779B185EBCA87;
    localparam logic [63:0] PRIME64_2 = 64'hC2B2AE3D27D4EB4F;

    logic        busy;
    logic [63:0] product64;
    logic [63:0] sum64;
    logic [63:0] rot64;
    logic [63:0] result64;
    logic [127:0] mul_tmp0;
    logic [127:0] mul_tmp1;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        mul_tmp0  = in_input * PRIME64_2;
        product64 = mul_tmp0[63:0];
        sum64     = in_acc + product64;
        rot64     = (sum64 << 31) | (sum64 >> 33);
        mul_tmp1  = rot64 * PRIME64_1;
        result64  = mul_tmp1[63:0];
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy    <= 1'b0;
            out_acc <= 64'h0000000000000000;
        end else if (input_accepted) begin
            busy    <= 1'b1;
            out_acc <= result64;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule