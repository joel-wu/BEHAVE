module cryptography_sym_sha_sha256_sigma_caps (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_sigma0,
    output logic [31:0] out_sigma1
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = (~busy) & (~rst);
    assign out_valid = busy & (~rst);

    function automatic logic [31:0] rotr32(input logic [31:0] x, input integer n);
        begin
            rotr32 = (x >> n) | (x << (32 - n));
        end
    endfunction

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy       <= 1'b0;
            out_sigma0 <= 32'h00000000;
            out_sigma1 <= 32'h00000000;
        end else if (input_accepted) begin
            out_sigma0 <= rotr32(in_x, 2) ^ rotr32(in_x, 13) ^ rotr32(in_x, 22);
            out_sigma1 <= rotr32(in_x, 6) ^ rotr32(in_x, 11) ^ rotr32(in_x, 25);
            busy       <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule