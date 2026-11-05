module greedy_argmax (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic [255:0] in_logits,
    output logic out_valid, input logic out_ready,
    output logic [2:0] out_index, output logic out_has_nan
);
    logic busy;
    assign in_ready = !busy || out_ready;
    assign out_valid = busy;
    logic [31:0] best, value, key_best, key_value;
    logic [2:0] index;
    logic has_nan, nan, take;
    always_comb begin
        best = in_logits[31:0];
        value = 0;
        key_best = 0;
        key_value = 0;
        index = 0;
        has_nan = (best & 32'h7FFFFFFF) > 32'h7F800000;
        nan = 0;
        take = 0;
        for (integer i = 1; i < 8; i = i + 1) begin
            value = in_logits[32*i +: 32];
            nan = (value & 32'h7FFFFFFF) > 32'h7F800000;
            key_best = best[31] ? ~best : best ^ 32'h80000000;
            key_value = value[31] ? ~value : value ^ 32'h80000000;
            take = 0;
            if (!has_nan) begin
                if (nan) take = 1;
                else if ((best & 32'h7FFFFFFF) != 0 || (value & 32'h7FFFFFFF) != 0)
                    take = key_value > key_best;
            end
            if (take) begin
                best = value;
                index = i[2:0];
                has_nan = nan;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            out_index <= 0; out_has_nan <= 0;
        end else if (in_valid && in_ready) begin
            busy <= 1;
            out_index <= index; out_has_nan <= has_nan;
        end else if (out_ready) busy <= 0;
    end
endmodule
