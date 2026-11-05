module cryptography_pqc_dilithium_dilithium_reduce32 (
    input  logic               clk,
    input  logic               rst,

    input  logic               in_valid,
    output logic               in_ready,
    input  logic signed [31:0] in_a,

    output logic               out_valid,
    output logic out_error,
    input  logic               out_ready,
    output logic signed [31:0] out_r
);

    logic busy;
    wire invalid_input = $signed(in_a) > 32'sd2143289343;

    wire input_accepted;
    wire output_consumed;

    logic signed [32:0] s_ext;
    logic signed [32:0] q_ext;
    logic signed [63:0] prod_ext;
    logic signed [63:0] a_ext;
    logic signed [63:0] r_ext;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        s_ext    = $signed({in_a[31], in_a}) + 33'sd4194304;
        q_ext    = s_ext >>> 23;
        prod_ext = q_ext * 64'sd8380417;
        a_ext    = $signed({{32{in_a[31]}}, in_a});
        r_ext    = a_ext - prod_ext;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy  <= 1'b0;
            out_error <= 1'b0;
            out_r <= 32'sd0;
        end else if (input_accepted) begin
            out_r <= invalid_input ? '0 : (r_ext[31:0]);
            busy  <= 1'b1;
            out_error <= invalid_input;
        end else if (output_consumed) begin
            out_r <= 32'sd0;
            busy  <= 1'b0;
            out_error <= 1'b0;
        end
    end

endmodule
