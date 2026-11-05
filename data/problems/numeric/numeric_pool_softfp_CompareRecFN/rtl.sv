module recoded_fp_compare #(
    parameter int EXP_WIDTH = 8,
    parameter int SIG_WIDTH = 24
) (
    input  logic                             clk,
    input  logic                             rst,

    input  logic                             in_valid,
    output logic                             in_ready,
    input  logic [EXP_WIDTH+SIG_WIDTH:0]     in_a,
    input  logic [EXP_WIDTH+SIG_WIDTH:0]     in_b,
    input  logic                             in_signaling,

    output logic                             out_valid,
    input  logic                             out_ready,
    output logic                             out_lt,
    output logic                             out_eq,
    output logic                             out_gt,
    output logic [4:0]                       out_exceptionFlags
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic calc_lt;
    logic calc_eq;
    logic calc_gt;
    logic [4:0] calc_exceptionFlags;

    logic a_sign, b_sign;
    logic [EXP_WIDTH:0] a_expRec, b_expRec;
    logic [SIG_WIDTH-2:0] a_fract, b_fract;
    logic [2:0] a_expCode, b_expCode;
    logic a_isZero, b_isZero;
    logic a_isInf, b_isInf;
    logic a_isNaN, b_isNaN;
    logic signed [EXP_WIDTH+1:0] a_sExp, b_sExp;
    logic [SIG_WIDTH-2:0] a_sig, b_sig;
    logic a_isSigNaN, b_isSigNaN;

    logic ordered;
    logic bothInfs;
    logic bothZeros;
    logic eqExps;
    logic common_ltMags;
    logic common_eqMags;
    logic ordered_lt;
    logic ordered_eq;
    logic invalid;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        a_sign   = in_a[EXP_WIDTH+SIG_WIDTH];
        b_sign   = in_b[EXP_WIDTH+SIG_WIDTH];

        a_expRec = in_a[EXP_WIDTH+SIG_WIDTH-1 -: (EXP_WIDTH+1)];
        b_expRec = in_b[EXP_WIDTH+SIG_WIDTH-1 -: (EXP_WIDTH+1)];

        a_fract  = in_a[SIG_WIDTH-2:0];
        b_fract  = in_b[SIG_WIDTH-2:0];

        a_expCode = a_expRec[EXP_WIDTH:EXP_WIDTH-2];
        b_expCode = b_expRec[EXP_WIDTH:EXP_WIDTH-2];

        a_isZero = (a_expCode == 3'b000);
        b_isZero = (b_expCode == 3'b000);

        a_isInf  = (a_expCode == 3'b110);
        b_isInf  = (b_expCode == 3'b110);

        a_isNaN  = (a_expCode == 3'b111);
        b_isNaN  = (b_expCode == 3'b111);

        a_sExp = $signed({1'b0, a_expRec});
        b_sExp = $signed({1'b0, b_expRec});

        a_sig = a_fract;
        b_sig = b_fract;

        a_isSigNaN = a_isNaN & ~a_fract[SIG_WIDTH-2];
        b_isSigNaN = b_isNaN & ~b_fract[SIG_WIDTH-2];

        ordered       = ~a_isNaN & ~b_isNaN;
        bothInfs      = a_isInf & b_isInf;
        bothZeros     = a_isZero & b_isZero;
        eqExps        = (a_sExp == b_sExp);
        common_ltMags = (a_sExp < b_sExp) | (eqExps & (a_sig < b_sig));
        common_eqMags = eqExps & (a_sig == b_sig);

        ordered_lt = ~bothZeros & (
                        (a_sign & ~b_sign) |
                        (~bothInfs & (
                            (a_sign & ~common_ltMags & ~common_eqMags) |
                            (~b_sign & common_ltMags)
                        ))
                     );

        ordered_eq = bothZeros | (
                        (a_sign == b_sign) & (bothInfs | common_eqMags)
                     );

        invalid = a_isSigNaN | b_isSigNaN | (in_signaling & ~ordered);

        calc_lt = ordered & ordered_lt;
        calc_eq = ordered & ordered_eq;
        calc_gt = ordered & ~ordered_lt & ~ordered_eq;
        calc_exceptionFlags = {invalid, 4'b0000};
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy               <= 1'b0;
            out_lt             <= 1'b0;
            out_eq             <= 1'b0;
            out_gt             <= 1'b0;
            out_exceptionFlags <= 5'b00000;
        end else if (input_accepted) begin
            busy               <= 1'b1;
            out_lt             <= calc_lt;
            out_eq             <= calc_eq;
            out_gt             <= calc_gt;
            out_exceptionFlags <= calc_exceptionFlags;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
