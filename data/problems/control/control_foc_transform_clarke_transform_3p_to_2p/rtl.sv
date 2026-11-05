module control_foc_transform_clarke_transform_3p_to_2p (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_full_clarke,
    input  logic [15:0]  in_ia,
    input  logic [15:0]  in_ib,
    input  logic [15:0]  in_ic,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_i_alpha,
    output logic [15:0]  out_i_beta
);

    localparam logic signed [15:0] TWO_THIRDS_Q15    = 16'sd21845;
    localparam logic signed [15:0] NEG_ONE_THIRD_Q15 = -16'sd10923;
    localparam logic signed [15:0] ONE_BY_SQRT3_Q15  = 16'sd18919;
    localparam logic signed [16:0] TWO_BY_SQRT3_Q15  = 17'sd37837;

    logic busy;

    logic signed [15:0] ia_s;
    logic signed [15:0] ib_s;
    logic signed [15:0] ic_s;

    logic signed [31:0] p_alpha0;
    logic signed [31:0] p_alpha1;
    logic signed [31:0] p_alpha2;
    logic signed [32:0] sum_alpha_wide;
    logic signed [32:0] alpha_shifted;

    logic signed [31:0] p_beta0;
    logic signed [31:0] p_beta1;
    logic signed [32:0] sum_beta_wide;
    logic signed [32:0] beta_shifted;

    logic signed [15:0] alpha_calc;
    logic signed [15:0] beta_calc;

    wire input_accepted;
    wire output_consumed;

    function automatic logic signed [15:0] sat_s16(input logic signed [32:0] value);
        begin
            if (value > 33'sd32767)
                sat_s16 = 16'sd32767;
            else if (value < -33'sd32768)
                sat_s16 = -16'sd32768;
            else
                sat_s16 = value[15:0];
        end
    endfunction

    assign ia_s = $signed(in_ia);
    assign ib_s = $signed(in_ib);
    assign ic_s = $signed(in_ic);

    always_comb begin
        p_alpha0      = 32'sd0;
        p_alpha1      = 32'sd0;
        p_alpha2      = 32'sd0;
        sum_alpha_wide = 33'sd0;
        alpha_shifted = 33'sd0;

        p_beta0       = 32'sd0;
        p_beta1       = 32'sd0;
        sum_beta_wide  = 33'sd0;
        beta_shifted  = 33'sd0;

        alpha_calc    = 16'sd0;
        beta_calc     = 16'sd0;

        if (in_full_clarke) begin
            p_alpha0       = ia_s * TWO_THIRDS_Q15;
            p_alpha1       = ib_s * NEG_ONE_THIRD_Q15;
            p_alpha2       = ic_s * NEG_ONE_THIRD_Q15;
            sum_alpha_wide = $signed({p_alpha0[31], p_alpha0}) +
                             $signed({p_alpha1[31], p_alpha1}) +
                             $signed({p_alpha2[31], p_alpha2});
            alpha_shifted  = sum_alpha_wide >>> 15;
            alpha_calc     = sat_s16(alpha_shifted);

            p_beta0        = ib_s * ONE_BY_SQRT3_Q15;
            p_beta1        = ic_s * (-ONE_BY_SQRT3_Q15);
            sum_beta_wide  = $signed({p_beta0[31], p_beta0}) +
                             $signed({p_beta1[31], p_beta1});
            beta_shifted   = sum_beta_wide >>> 15;
            beta_calc      = sat_s16(beta_shifted);
        end else begin
            alpha_calc     = sat_s16($signed({{17{ia_s[15]}}, ia_s}));

            p_beta0        = ia_s * ONE_BY_SQRT3_Q15;
            p_beta1        = ib_s * $signed(TWO_BY_SQRT3_Q15);
            sum_beta_wide  = $signed({p_beta0[31], p_beta0}) +
                             $signed({p_beta1[31], p_beta1});
            beta_shifted   = sum_beta_wide >>> 15;
            beta_calc      = sat_s16(beta_shifted);
        end
    end

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy        <= 1'b0;
            out_i_alpha <= 16'h0000;
            out_i_beta  <= 16'h0000;
        end else if (input_accepted) begin
            busy        <= 1'b1;
            out_i_alpha <= alpha_calc[15:0];
            out_i_beta  <= beta_calc[15:0];
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule