module signal_processing_isp_bayer_digital_gain (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_pixel,
    input  logic        in_is_auto,
    input  logic signed [1:0] in_ae_feedback,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_pixel,
    output logic [2:0]  out_current_gain
);

    localparam logic [2:0] CURRENT_GAIN_RESET = 3'd0;
    localparam logic [9:0] MAX_PIXEL          = 10'd1023;

    logic       busy;
    logic [2:0] current_gain;

    logic [2:0] selected_gain_index_comb;
    logic [4:0] gain_value_comb;
    logic [20:0] product_comb;
    logic [15:0] clipped_pixel_comb;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = (~busy) | output_consumed;
    assign out_valid = busy;

    always_comb begin
        selected_gain_index_comb = current_gain;

        if (in_is_auto) begin
            if (in_ae_feedback < 0) begin
                if (current_gain < 3'd4) begin
                    selected_gain_index_comb = current_gain + 3'd1;
                end else begin
                    selected_gain_index_comb = 3'd4;
                end
            end else if (in_ae_feedback > 0) begin
                if (current_gain > 3'd0) begin
                    selected_gain_index_comb = current_gain - 3'd1;
                end else begin
                    selected_gain_index_comb = 3'd0;
                end
            end else begin
                selected_gain_index_comb = current_gain;
            end
        end else begin
            selected_gain_index_comb = current_gain;
        end
    end

    always_comb begin
        gain_value_comb = 5'd1;
        unique case (selected_gain_index_comb)
            3'd0: gain_value_comb = 5'd1;
            3'd1: gain_value_comb = 5'd2;
            3'd2: gain_value_comb = 5'd4;
            3'd3: gain_value_comb = 5'd8;
            3'd4: gain_value_comb = 5'd16;
        endcase
    end

    assign product_comb = in_pixel * gain_value_comb;

    always_comb begin
        if (product_comb > {11'd0, MAX_PIXEL}) begin
            clipped_pixel_comb = 16'd1023;
        end else begin
            clipped_pixel_comb = product_comb[15:0];
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy             <= 1'b0;
            current_gain     <= CURRENT_GAIN_RESET;
            out_pixel        <= 16'd0;
            out_current_gain <= 3'd0;
        end else if (input_accepted) begin
            busy             <= 1'b1;
            current_gain     <= selected_gain_index_comb;
            out_pixel        <= clipped_pixel_comb;
            out_current_gain <= selected_gain_index_comb;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule