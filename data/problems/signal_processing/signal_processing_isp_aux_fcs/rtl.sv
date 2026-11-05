module signal_processing_isp_aux_fcs (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_img_r,
    input  logic [7:0]  in_img_g,
    input  logic [7:0]  in_img_b,
    input  logic signed [15:0] in_edgemap,
    input  logic [7:0]  in_fcs_edge_lo,
    input  logic [7:0]  in_fcs_edge_hi,
    input  logic signed [15:0] in_gain,
    input  logic signed [15:0] in_intercept,
    input  logic signed [15:0] in_slope,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_img_r,
    output logic [7:0]  out_img_g,
    output logic [7:0]  out_img_b
);

logic busy;
wire output_consumed;
wire input_accepted;

assign output_consumed = out_valid & out_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;

function automatic logic [15:0] abs16_u(input logic signed [15:0] v);
    logic [15:0] neg_v;
    begin
        if (v < 0) begin
            neg_v   = -v;
            abs16_u = neg_v;
        end else begin
            abs16_u = v[15:0];
        end
    end
endfunction

function automatic logic [7:0] channel_calc(
    input logic signed [31:0] uvgain,
    input logic [7:0] c
);
    logic signed [39:0] product;
    logic signed [39:0] biased;
    begin
        product = uvgain * $signed({1'b0, c});
        biased = (product >>> 8) + 40'sd128;

        if (biased < 0) begin
            channel_calc = 8'd0;
        end else if (biased > 40'sd255) begin
            channel_calc = 8'd255;
        end else begin
            channel_calc = biased[7:0];
        end
    end
endfunction

always_ff @(posedge clk or posedge rst) begin
    logic [15:0] edge_abs;
    logic signed [31:0] slope_ext;
    logic signed [31:0] edgemap_ext;
    logic signed [31:0] intercept_ext;
    logic signed [31:0] gain_ext;
    logic signed [31:0] mult_term;
    logic signed [31:0] uvgain_calc;

    if (rst) begin
        busy      <= 1'b0;
        out_img_r <= 8'd0;
        out_img_g <= 8'd0;
        out_img_b <= 8'd0;
    end else begin
        if (input_accepted) begin
            edge_abs      = abs16_u(in_edgemap);
            slope_ext     = in_slope;
            edgemap_ext   = in_edgemap;
            intercept_ext = in_intercept;
            gain_ext      = in_gain;
            mult_term     = slope_ext * edgemap_ext;
            uvgain_calc   = 32'sd0;

            if (edge_abs <= {8'd0, in_fcs_edge_lo}) begin
                uvgain_calc = gain_ext;
            end else if (edge_abs < {8'd0, in_fcs_edge_hi}) begin
                uvgain_calc = intercept_ext - mult_term;
            end else begin
                uvgain_calc = 32'sd0;
            end

            out_img_r <= channel_calc(uvgain_calc, in_img_r);
            out_img_g <= channel_calc(uvgain_calc, in_img_g);
            out_img_b <= channel_calc(uvgain_calc, in_img_b);
            busy      <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end
end

assign input_accepted = in_valid & in_ready;

endmodule
