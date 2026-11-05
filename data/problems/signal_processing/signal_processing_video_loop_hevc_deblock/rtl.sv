module hevc_deblock_boundary_strength (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_block_strength_pre,
    input  logic [0:0]  in_p_intra,
    input  logic [0:0]  in_q_intra,
    input  logic [0:0]  in_p_cbf_luma,
    input  logic [0:0]  in_q_cbf_luma,
    input  logic [0:0]  in_slice_p_is_inter_p,
    input  logic [0:0]  in_slice_q_is_inter_p,

    input  logic [7:0]  in_ref_p0,
    input  logic [7:0]  in_ref_q0,
    input  logic [7:0]  in_ref_p1,
    input  logic [7:0]  in_ref_q1,

    input  logic signed [15:0] in_mv_p0_x,
    input  logic signed [15:0] in_mv_p0_y,
    input  logic signed [15:0] in_mv_q0_x,
    input  logic signed [15:0] in_mv_q0_y,
    input  logic signed [15:0] in_mv_p1_x,
    input  logic signed [15:0] in_mv_p1_y,
    input  logic signed [15:0] in_mv_q1_x,
    input  logic signed [15:0] in_mv_q1_y,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [1:0]  out_bs
);

    logic busy;
    logic [1:0] bs_next;

    logic signed [15:0] mv_p0_x_eff, mv_p0_y_eff;
    logic signed [15:0] mv_q0_x_eff, mv_q0_y_eff;
    logic signed [15:0] mv_p1_x_eff, mv_p1_y_eff;
    logic signed [15:0] mv_q1_x_eff, mv_q1_y_eff;

    logic signed [16:0] d00x, d00y, d11x, d11y;
    logic signed [16:0] d10x, d10y, d01x, d01y;

    logic same_order, cross_order;
    logic cond_a, cond_b;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic mv_far17(input logic signed [16:0] diff_x, input logic signed [16:0] diff_y);
        begin
            if ((diff_x <= -17'sd4) || (diff_x >= 17'sd4) ||
                (diff_y <= -17'sd4) || (diff_y >= 17'sd4))
                mv_far17 = 1'b1;
            else
                mv_far17 = 1'b0;
        end
    endfunction

    always_comb begin
        mv_p0_x_eff = (in_ref_p0 == 8'hFF) ? 16'sd0 : in_mv_p0_x;
        mv_p0_y_eff = (in_ref_p0 == 8'hFF) ? 16'sd0 : in_mv_p0_y;
        mv_q0_x_eff = (in_ref_q0 == 8'hFF) ? 16'sd0 : in_mv_q0_x;
        mv_q0_y_eff = (in_ref_q0 == 8'hFF) ? 16'sd0 : in_mv_q0_y;
        mv_p1_x_eff = (in_ref_p1 == 8'hFF) ? 16'sd0 : in_mv_p1_x;
        mv_p1_y_eff = (in_ref_p1 == 8'hFF) ? 16'sd0 : in_mv_p1_y;
        mv_q1_x_eff = (in_ref_q1 == 8'hFF) ? 16'sd0 : in_mv_q1_x;
        mv_q1_y_eff = (in_ref_q1 == 8'hFF) ? 16'sd0 : in_mv_q1_y;

        d00x = mv_q0_x_eff - mv_p0_x_eff;
        d00y = mv_q0_y_eff - mv_p0_y_eff;
        d11x = mv_q1_x_eff - mv_p1_x_eff;
        d11y = mv_q1_y_eff - mv_p1_y_eff;
        d10x = mv_q1_x_eff - mv_p0_x_eff;
        d10y = mv_q1_y_eff - mv_p0_y_eff;
        d01x = mv_q0_x_eff - mv_p1_x_eff;
        d01y = mv_q0_y_eff - mv_p1_y_eff;

        same_order = (in_ref_p0 == in_ref_q0) && (in_ref_p1 == in_ref_q1);
        cross_order = (in_ref_p0 == in_ref_q1) && (in_ref_p1 == in_ref_q0);

        cond_a = mv_far17(d00x, d00y) || mv_far17(d11x, d11y);
        cond_b = mv_far17(d10x, d10y) || mv_far17(d01x, d01y);

        bs_next = 2'b00;

        if (in_p_intra[0] || in_q_intra[0]) begin
            bs_next = 2'd2;
        end else if (in_block_strength_pre > 2'd1 && (in_p_cbf_luma[0] || in_q_cbf_luma[0])) begin
            bs_next = 2'd1;
        end else if (in_slice_p_is_inter_p[0] && in_slice_q_is_inter_p[0]) begin
            if ((in_ref_p0 != in_ref_q0) || mv_far17(d00x, d00y))
                bs_next = 2'd1;
            else
                bs_next = 2'd0;
        end else begin
            if (!(same_order || cross_order)) begin
                bs_next = 2'd1;
            end else if (in_ref_p0 != in_ref_p1) begin
                if (in_ref_p0 == in_ref_q0) begin
                    if (mv_far17(d00x, d00y) || mv_far17(d11x, d11y))
                        bs_next = 2'd1;
                    else
                        bs_next = 2'd0;
                end else begin
                    if (mv_far17(d10x, d10y) || mv_far17(d01x, d01y))
                        bs_next = 2'd1;
                    else
                        bs_next = 2'd0;
                end
            end else begin
                if (cond_a && cond_b)
                    bs_next = 2'd1;
                else
                    bs_next = 2'd0;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy   <= 1'b0;
            out_bs <= 2'b00;
        end else if (input_accepted) begin
            busy   <= 1'b1;
            out_bs <= bs_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
