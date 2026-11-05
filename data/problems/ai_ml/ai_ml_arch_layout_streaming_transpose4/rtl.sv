module streaming_transpose4 (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [63:0] in_row,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_col
);
    logic [63:0] rows [0:3];
    logic [1:0] row_index, column_index;
    logic draining;

    assign in_ready = !rst && !draining;
    assign out_valid = !rst && draining;
    always_comb begin
        out_col = 64'b0;
        if (out_valid) begin
            for (int r = 0; r < 4; r++)
                out_col[16*r +: 16] = rows[r][16*column_index +: 16];
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            for (int r = 0; r < 4; r++) rows[r] <= 64'b0;
            row_index <= 2'b0;
            column_index <= 2'b0;
            draining <= 1'b0;
        end else if (in_valid && in_ready) begin
            rows[row_index] <= in_row;
            row_index <= row_index + 2'd1;
            if (row_index == 2'd3) begin
                draining <= 1'b1;
                column_index <= 2'b0;
            end
        end else if (out_valid && out_ready) begin
            column_index <= column_index + 2'd1;
            if (column_index == 2'd3) draining <= 1'b0;
        end
    end
endmodule
