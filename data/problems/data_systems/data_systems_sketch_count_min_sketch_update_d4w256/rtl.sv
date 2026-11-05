module data_systems_sketch_count_min_sketch_update_d4w256 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [7:0] in_h1,
    input logic [7:0] in_h2,
    input logic [15:0] in_delta,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_counts,
    output logic [15:0] out_estimate
);
    logic [15:0] counter [0:1023];
    logic [7:0] column [0:3];
    logic [15:0] next_count [0:3];
    logic [15:0] estimate;
    logic [16:0] sum;
    assign in_ready = !rst && (!out_valid || out_ready);
    always_comb begin
        estimate = 16'hffff;
        sum = 0;
        for (integer row=0; row<4; row=row+1) begin
            column[row] = in_h1 + row * in_h2;
            sum = {1'b0,counter[row*256+column[row]]} + {1'b0,in_delta};
            next_count[row] = sum[16] ? 16'hffff : sum[15:0];
            if (next_count[row] < estimate) estimate = next_count[row];
        end
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 0; out_counts <= 0; out_estimate <= 0;
            for (integer index=0; index<1024; index=index+1) counter[index] <= 0;
        end else begin
            if (out_valid && out_ready) out_valid <= 0;
            if (in_valid && in_ready) begin
                out_valid <= 1;
                out_estimate <= estimate;
                for (integer row=0; row<4; row=row+1) begin
                    counter[row*256+column[row]] <= next_count[row];
                    out_counts[16*row +: 16] <= next_count[row];
                end
            end
        end
    end
endmodule
