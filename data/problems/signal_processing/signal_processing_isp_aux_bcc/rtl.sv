module signal_processing_isp_aux_bcc (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_img,
    input  logic [7:0]  in_brightness,
    input  logic [7:0]  in_contrast,
    input  logic [7:0]  in_clip,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_img
);

    logic busy;

    logic signed [8:0]  brightness_s;
    logic signed [8:0]  contrast_s;
    logic signed [8:0]  img_u_s;
    logic signed [9:0]  temp_s;
    logic signed [9:0]  delta_s;
    logic signed [17:0] scaled_s;
    logic signed [18:0] bcc_s;
    logic [7:0]         out_img_next;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        brightness_s = {in_brightness[7], in_brightness};
        contrast_s   = {in_contrast[7], in_contrast};
        img_u_s      = {1'b0, in_img};

        temp_s       = img_u_s + brightness_s;
        delta_s      = img_u_s - 10'sd127;
        scaled_s     = delta_s * contrast_s;
        bcc_s        = temp_s + scaled_s;

        if (bcc_s < 19'sd0) begin
            out_img_next = 8'd0;
        end else if (bcc_s > {11'd0, in_clip}) begin
            out_img_next = in_clip;
        end else begin
            out_img_next = bcc_s[7:0];
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy    <= 1'b0;
            out_img <= 8'd0;
        end else if (input_accepted) begin
            busy    <= 1'b1;
            out_img <= out_img_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule