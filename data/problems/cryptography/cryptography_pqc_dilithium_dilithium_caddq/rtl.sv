module cryptography_pqc_dilithium_dilithium_caddq (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a,

    output logic        out_valid,
    output logic out_error,
    input  logic        out_ready,
    output logic [31:0] out_r
);

    localparam logic [31:0] Q = 32'd8380417;

    logic busy;
    wire invalid_input = $signed(in_a) <= -32'sd8380417 || $signed(in_a) >= 32'sd8380417;
    logic signed [31:0] a_signed;
    logic [31:0] result_next;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        a_signed = $signed(in_a);
        if (a_signed >= 32'sd0) begin
            result_next = in_a;
        end else begin
            result_next = in_a + Q;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy  <= 1'b0;
            out_error <= 1'b0;
            out_r <= 32'd0;
        end else if (input_accepted) begin
            busy  <= 1'b1;
            out_error <= invalid_input;
            out_r <= invalid_input ? '0 : (result_next);
        end else if (output_consumed) begin
            busy <= 1'b0;
            out_error <= 1'b0;
        end
    end

endmodule
