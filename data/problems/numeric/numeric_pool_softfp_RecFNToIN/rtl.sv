module recfn_to_int_i32 (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [32:0] in_in,
    input  logic [2:0]  in_roundingMode,
    input  logic        in_signedOut,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_out,
    output logic [2:0]  out_intExceptionFlags
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    logic        sign;
    logic [8:0]  expRec;
    logic [22:0] fract;
    logic [2:0]  expTop;

    logic        isNaN;
    logic        isInf;
    logic        isZero;
    logic       magGeOne;
    logic [7:0] posExp;
    logic       magJustBelowOne;

    logic [23:0] shiftedSigBase;
    logic [4:0]  shiftAmt;
    logic [55:0] shiftedSig;
    logic        sticky;
    logic [34:0] alignedSig;
    logic [31:0] unroundedInt;

    logic common_inexact;
    logic roundIncr_near_even;
    logic roundIncr_near_maxMag;

    logic roundingMode_near_even;
    logic roundingMode_minMag;
    logic roundingMode_min;
    logic roundingMode_max;
    logic roundingMode_near_maxMag;
    logic roundingMode_odd;

    logic roundIncr;
    logic [31:0] complUnroundedInt;
    logic [31:0] roundedCore;
    logic [31:0] roundedInt;

    logic magGeOne_atOverflowEdge;
    logic roundCarryBut2;
    logic common_overflow;

    logic invalidExc;
    logic overflow;
    logic inexact;

    logic excSign;
    logic [31:0] excOut;

    logic [31:0] calc_out_out;
    logic [2:0]  calc_out_intExceptionFlags;

    always_comb begin
        sign   = in_in[32];
        expRec = in_in[31:23];
        fract  = in_in[22:0];
        expTop = expRec[8:6];

        isNaN = (expTop == 3'b111);
        isInf = (expTop == 3'b110);
        isZero = (expTop == 3'b000);
        magGeOne = expRec[8];
        posExp = expRec[7:0];
        magJustBelowOne = (~magGeOne) && (posExp == 8'hFF);

        shiftedSigBase = {magGeOne, fract};
        shiftAmt = magGeOne ? expRec[4:0] : 5'd0;
        shiftedSig = {32'd0, shiftedSigBase} << shiftAmt;
        sticky = (shiftedSig[21:0] != 22'd0);
        alignedSig = ({1'b0, (shiftedSig >> 22)} << 1) | {{34{1'b0}}, sticky};
        unroundedInt = alignedSig[33:2];

        common_inexact = (magGeOne && (alignedSig[1:0] != 2'b00)) || ((~magGeOne) && (~isZero));
        roundIncr_near_even =
            (magGeOne && (((alignedSig[2:1] == 2'b11) || (alignedSig[1:0] == 2'b11)))) ||
            (magJustBelowOne && (alignedSig[1:0] != 2'b00));
        roundIncr_near_maxMag =
            (magGeOne && alignedSig[1]) || magJustBelowOne;

        roundingMode_near_even   = (in_roundingMode == 3'b000);
        roundingMode_minMag      = (in_roundingMode == 3'b001);
        roundingMode_min         = (in_roundingMode == 3'b010);
        roundingMode_max         = (in_roundingMode == 3'b011);
        roundingMode_near_maxMag = (in_roundingMode == 3'b100);
        roundingMode_odd         = (in_roundingMode == 3'b110);

        roundIncr =
            (roundingMode_near_even   && roundIncr_near_even)   ||
            (roundingMode_near_maxMag && roundIncr_near_maxMag) ||
            ((roundingMode_min || roundingMode_odd) && sign && common_inexact) ||
            (roundingMode_max && (~sign) && common_inexact);

        complUnroundedInt = sign ? ~unroundedInt : unroundedInt;
        roundedCore = (roundIncr ^ sign) ? (complUnroundedInt + 32'd1) : complUnroundedInt;
        roundedInt = (roundingMode_odd && common_inexact) ? (roundedCore | 32'd1) : roundedCore;

        magGeOne_atOverflowEdge = (posExp == 8'd31);
        roundCarryBut2 = (&unroundedInt[29:0]) && roundIncr;

        if (magGeOne) begin
            if (posExp >= 8'd32) begin
                common_overflow = 1'b1;
            end else if (in_signedOut) begin
                if (sign) begin
                    common_overflow = magGeOne_atOverflowEdge && ((unroundedInt[30:0] != 31'd0) || roundIncr);
                end else begin
                    common_overflow = magGeOne_atOverflowEdge || ((posExp == 8'd30) && roundCarryBut2);
                end
            end else begin
                common_overflow = sign || (magGeOne_atOverflowEdge && unroundedInt[30] && roundCarryBut2);
            end
        end else begin
            common_overflow = (~in_signedOut) && sign && roundIncr;
        end

        invalidExc = isNaN || isInf;
        overflow = (~invalidExc) && common_overflow;
        inexact = (~invalidExc) && (~common_overflow) && common_inexact;

        excSign = (~isNaN) && sign;
        excOut = ((in_signedOut == excSign) ? 32'h80000000 : 32'h00000000) |
                 ((~excSign) ? 32'h7FFFFFFF : 32'h00000000);

        calc_out_out = (invalidExc || common_overflow) ? excOut : roundedInt;
        calc_out_intExceptionFlags = {invalidExc, overflow, inexact};
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            out_out <= 32'h00000000;
            out_intExceptionFlags <= 3'b000;
        end else if (input_accepted) begin
            busy <= 1'b1;
            out_out <= calc_out_out;
            out_intExceptionFlags <= calc_out_intExceptionFlags;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
