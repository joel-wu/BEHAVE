module multicycle_mul4 (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [3:0] in_a,
    input  logic [3:0] in_b,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_p
);

    // 4-cycle multiply: computes result immediately but holds it
    // for 4 cycles with in_ready=0 before presenting on out_valid.
    logic       busy;
    logic [7:0] result_reg;
    logic [2:0] cnt;

    wire done            = (cnt == 3'd4);
    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy;
    assign out_valid = busy & done;
    assign out_p     = result_reg;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy       <= 1'b0;
            result_reg <= 8'd0;
            cnt        <= 3'd0;
        end else if (input_accepted) begin
            result_reg <= in_a * in_b;
            cnt        <= 3'd1;
            busy       <= 1'b1;
        end else if (busy && !done) begin
            cnt <= cnt + 3'd1;
        end else if (output_consumed) begin
            busy <= 1'b0;
            cnt  <= 3'd0;
        end
    end

endmodule
