module cryptography_pqc_kyber_kyber_barrett_reduce (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_a,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_r
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    logic signed [15:0] a_s;
    logic signed [31:0] p;
    logic signed [31:0] p_round;
    logic signed [31:0] t;
    logic signed [31:0] tq;
    logic signed [31:0] r;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = rst ? 1'b1 : (~busy | output_consumed);
    assign out_valid = rst ? 1'b0 : busy;

    always_comb begin
        a_s     = $signed(in_a);
        p       = 32'sd20159 * a_s;
        p_round = p + 32'sd33554432;
        t       = p_round >>> 26;
        tq      = t * 32'sd3329;
        r       = a_s - tq;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy  <= 1'b0;
            out_r <= 16'h0000;
        end else if (input_accepted) begin
            out_r <= r[15:0];
            busy  <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule