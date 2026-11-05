module ai_ml_quant_mx_mx_e8m0_scale_extract (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_hp_data_M,
    input  logic [15:0] in_hp_data_K,
    input  logic [31:0] in_scale_index,
    input  logic [7:0]  in_scale_value,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_scale_rows,
    output logic [15:0] out_scale_cols,
    output logic        out_in_range,
    output logic [7:0]  out_scale_value
);

logic busy;

wire input_accepted;
wire output_consumed;

logic [15:0] n_row_blocks;
logic [15:0] n_col_blocks;
logic [15:0] scale_rows_calc;
logic [15:0] scale_cols_calc;
logic [31:0] scale_count_calc;
logic        in_range_calc;
logic [7:0]  scale_value_calc;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    if (in_hp_data_M == 16'h0000) begin
        n_row_blocks = 16'h0000;
    end else begin
        n_row_blocks = (in_hp_data_M - 16'h0001) >> 7;
        n_row_blocks = n_row_blocks + 16'h0001;
    end

    if (in_hp_data_K == 16'h0000) begin
        n_col_blocks = 16'h0000;
    end else begin
        n_col_blocks = (in_hp_data_K - 16'h0001) >> 7;
        n_col_blocks = n_col_blocks + 16'h0001;
    end

    scale_rows_calc = {n_row_blocks[10:0], 5'b00000};
    scale_cols_calc = {n_col_blocks[11:0], 4'b0000};
    scale_count_calc = scale_rows_calc * scale_cols_calc;
    in_range_calc = (in_scale_index < scale_count_calc);
    if (in_range_calc) begin
        scale_value_calc = in_scale_value;
    end else begin
        scale_value_calc = 8'h00;
    end
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy            <= 1'b0;
        out_scale_rows  <= 16'h0000;
        out_scale_cols  <= 16'h0000;
        out_in_range    <= 1'b0;
        out_scale_value <= 8'h00;
    end else if (input_accepted) begin
        busy            <= 1'b1;
        out_scale_rows  <= scale_rows_calc;
        out_scale_cols  <= scale_cols_calc;
        out_in_range    <= in_range_calc;
        out_scale_value <= scale_value_calc;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule