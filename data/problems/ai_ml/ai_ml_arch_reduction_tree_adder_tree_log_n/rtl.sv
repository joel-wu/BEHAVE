module eight_lane_sum (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic [127:0] in_values,
    output logic out_valid, input logic out_ready,
    output logic [18:0] out_sum
);
    logic busy;
    assign in_ready = !busy || out_ready;
    assign out_valid = busy;
    logic signed [18:0] tree [0:14];
    always_comb begin
        for (integer i = 0; i < 8; i = i + 1)
            tree[7+i] = {{3{in_values[16*i+15]}}, in_values[16*i +: 16]};
        for (integer i = 6; i >= 0; i = i - 1)
            tree[i] = tree[2*i+1] + tree[2*i+2];
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            out_sum <= 0;
        end else if (in_valid && in_ready) begin
            busy <= 1;
            out_sum <= tree[0];
        end else if (out_ready) busy <= 0;
    end
endmodule
