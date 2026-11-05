module signal_processing_isp_bayer_white_balance (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_pixel,
    input  logic [15:0] in_row,
    input  logic [15:0] in_col,
    input  logic [1:0]  in_bayer_pattern,
    input  logic [4:0]  in_bit_depth,
    input  logic [15:0] in_r_gain_u16p8,
    input  logic [15:0] in_b_gain_u16p8,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_pixel
);

logic        busy;
logic [15:0] out_pixel_next;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    logic row_parity;
    logic col_parity;
    logic [31:0] max_value;
    logic use_r_gain;
    logic use_b_gain;
    logic [31:0] product_full;
    logic [31:0] scaled_value;

    row_parity    = in_row[0];
    col_parity    = in_col[0];
    max_value     = 32'd0;
    use_r_gain    = 1'b0;
    use_b_gain    = 1'b0;
    product_full  = 32'd0;
    scaled_value  = 32'd0;
    out_pixel_next = 16'd0;

    if (in_bit_depth >= 5'd16) begin
        max_value = 32'd65535;
    end else begin
        max_value = (32'd1 << in_bit_depth) - 32'd1;
    end

    unique case (in_bayer_pattern)
        2'd0: begin
            if ((row_parity == 1'b0) && (col_parity == 1'b0)) begin
                use_r_gain = 1'b1;
            end else if ((row_parity == 1'b1) && (col_parity == 1'b1)) begin
                use_b_gain = 1'b1;
            end
        end
        2'd1: begin
            if ((row_parity == 1'b0) && (col_parity == 1'b0)) begin
                use_b_gain = 1'b1;
            end else if ((row_parity == 1'b1) && (col_parity == 1'b1)) begin
                use_r_gain = 1'b1;
            end
        end
        2'd2: begin
            if ((row_parity == 1'b0) && (col_parity == 1'b1)) begin
                use_r_gain = 1'b1;
            end else if ((row_parity == 1'b1) && (col_parity == 1'b0)) begin
                use_b_gain = 1'b1;
            end
        end
        2'd3: begin
            if ((row_parity == 1'b0) && (col_parity == 1'b1)) begin
                use_b_gain = 1'b1;
            end else if ((row_parity == 1'b1) && (col_parity == 1'b0)) begin
                use_r_gain = 1'b1;
            end
        end
    endcase

    if (use_r_gain) begin
        product_full = in_pixel * in_r_gain_u16p8;
        scaled_value = {8'd0, product_full[31:8]};
    end else if (use_b_gain) begin
        product_full = in_pixel * in_b_gain_u16p8;
        scaled_value = {8'd0, product_full[31:8]};
    end else begin
        scaled_value = {16'd0, in_pixel};
    end

    if (scaled_value > max_value) begin
        out_pixel_next = max_value[15:0];
    end else begin
        out_pixel_next = scaled_value[15:0];
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy      <= 1'b0;
        out_pixel <= 16'd0;
    end else if (input_accepted) begin
        busy      <= 1'b1;
        out_pixel <= out_pixel_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
