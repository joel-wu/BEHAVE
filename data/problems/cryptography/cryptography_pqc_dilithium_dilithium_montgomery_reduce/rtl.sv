module cryptography_pqc_dilithium_dilithium_montgomery_reduce (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a,

    output logic         out_valid,
    output logic out_error,
    input  logic         out_ready,
    output logic [31:0]  out_r
);

    localparam logic [31:0] QINV = 32'd58728449;
    localparam logic signed [31:0] Q = 32'sd8380417;

    logic busy;
    wire invalid_input = $signed(in_a) <= -64'sd17996808470921216 || $signed(in_a) >= 64'sd17996808470921216;
    logic [31:0] out_r_next;

    wire input_accepted;
    wire output_consumed;

    logic [63:0] mul_u64;
    logic signed [31:0] t_signed;
    logic signed [95:0] a_signed_96;
    logic signed [95:0] tq_signed_96;
    logic signed [95:0] r_pre_96;
    logic signed [95:0] r_shifted_96;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        mul_u64       = in_a[31:0] * QINV;
        t_signed      = mul_u64[31:0];
        a_signed_96   = {{32{in_a[63]}}, in_a};
        tq_signed_96  = t_signed * Q;
        r_pre_96      = a_signed_96 - tq_signed_96;
        r_shifted_96  = r_pre_96 >>> 32;
        out_r_next    = r_shifted_96[31:0];
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy  <= 1'b0;
            out_error <= 1'b0;
            out_r <= 32'h00000000;
        end else if (input_accepted) begin
            busy  <= 1'b1;
            out_error <= invalid_input;
            out_r <= invalid_input ? '0 : (out_r_next);
        end else if (output_consumed) begin
            busy <= 1'b0;
            out_error <= 1'b0;
        end
    end

endmodule
