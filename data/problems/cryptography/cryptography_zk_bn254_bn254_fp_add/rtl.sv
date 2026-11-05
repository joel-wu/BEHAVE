module cryptography_zk_bn254_bn254_fp_add (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [253:0] in_a,
    input  logic [253:0] in_b,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [253:0] out_sum
);

    localparam logic [253:0] P = 254'h30644E72E131A029B85045B68181585D97816A916871CA8D3C208C16D87CFD47;

    logic busy;
    logic [254:0] raw_sum_ext;
    logic [254:0] p_ext;
    logic [254:0] reduced_sum_ext;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        raw_sum_ext = {1'b0, in_a} + {1'b0, in_b};
        p_ext = {1'b0, P};
        if (raw_sum_ext >= p_ext) begin
            reduced_sum_ext = raw_sum_ext - p_ext;
        end else begin
            reduced_sum_ext = raw_sum_ext;
        end
        if (reduced_sum_ext >= p_ext) reduced_sum_ext = reduced_sum_ext - p_ext;
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy    <= 1'b0;
            out_sum <= 254'b0;
        end else if (input_accepted) begin
            out_sum <= reduced_sum_ext[253:0];
            busy    <= 1'b1;
        end else if (output_consumed) begin
            busy    <= 1'b0;
        end
    end

endmodule
