module compression_jpeg_transform_jpeg_idct_1d_8point_int (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_coef0,
    input  logic [15:0]  in_coef1,
    input  logic [15:0]  in_coef2,
    input  logic [15:0]  in_coef3,
    input  logic [15:0]  in_coef4,
    input  logic [15:0]  in_coef5,
    input  logic [15:0]  in_coef6,
    input  logic [15:0]  in_coef7,
    input  logic [15:0]  in_quant0,
    input  logic [15:0]  in_quant1,
    input  logic [15:0]  in_quant2,
    input  logic [15:0]  in_quant3,
    input  logic [15:0]  in_quant4,
    input  logic [15:0]  in_quant5,
    input  logic [15:0]  in_quant6,
    input  logic [15:0]  in_quant7,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_s0,
    output logic [15:0]  out_s1,
    output logic [15:0]  out_s2,
    output logic [15:0]  out_s3,
    output logic [15:0]  out_s4,
    output logic [15:0]  out_s5,
    output logic [15:0]  out_s6,
    output logic [15:0]  out_s7
);

    localparam logic signed [15:0] CONST_BITS = 16'sd13;
    localparam logic signed [15:0] PASS1_BITS = 16'sd2;

    localparam logic signed [15:0] FIX_0_298631336 = 16'sd2446;
    localparam logic signed [15:0] FIX_0_390180644 = 16'sd3196;
    localparam logic signed [15:0] FIX_0_541196100 = 16'sd4433;
    localparam logic signed [15:0] FIX_0_765366865 = 16'sd6270;
    localparam logic signed [15:0] FIX_0_899976223 = 16'sd7373;
    localparam logic signed [15:0] FIX_1_175875602 = 16'sd9633;
    localparam logic signed [15:0] FIX_1_501321110 = 16'sd12299;
    localparam logic signed [15:0] FIX_1_847759065 = 16'sd15137;
    localparam logic signed [15:0] FIX_1_961570560 = 16'sd16069;
    localparam logic signed [15:0] FIX_2_053119869 = 16'sd16819;
    localparam logic signed [15:0] FIX_2_562915447 = 16'sd20995;
    localparam logic signed [15:0] FIX_3_072711026 = 16'sd25172;

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic [15:0] out_s0_next;
    logic [15:0] out_s1_next;
    logic [15:0] out_s2_next;
    logic [15:0] out_s3_next;
    logic [15:0] out_s4_next;
    logic [15:0] out_s5_next;
    logic [15:0] out_s6_next;
    logic [15:0] out_s7_next;

    function automatic logic signed [63:0] descale64(
        input logic signed [63:0] x,
        input integer n
    );
        logic signed [63:0] bias;
        begin
            descale64 = 64'sd0;
            bias = 64'sd0;
            if (n == 0) begin
                descale64 = x;
            end else begin
                bias = 64'sd1 <<< (n - 1);
                descale64 = (x + bias) >>> n;
            end
        end
    endfunction

    assign output_consumed = out_valid & out_ready;
    assign input_accepted  = in_valid & in_ready;

    assign in_ready  = (~busy) | output_consumed;
    assign out_valid = busy;

    always_comb begin
        logic signed [15:0] coef0;
        logic signed [15:0] coef1;
        logic signed [15:0] coef2;
        logic signed [15:0] coef3;
        logic signed [15:0] coef4;
        logic signed [15:0] coef5;
        logic signed [15:0] coef6;
        logic signed [15:0] coef7;

        logic [15:0] quant0;
        logic [15:0] quant1;
        logic [15:0] quant2;
        logic [15:0] quant3;
        logic [15:0] quant4;
        logic [15:0] quant5;
        logic [15:0] quant6;
        logic [15:0] quant7;

        logic signed [31:0] dq0;
        logic signed [31:0] dq1;
        logic signed [31:0] dq2;
        logic signed [31:0] dq3;
        logic signed [31:0] dq4;
        logic signed [31:0] dq5;
        logic signed [31:0] dq6;
        logic signed [31:0] dq7;

        logic signed [63:0] z1;
        logic signed [63:0] z2;
        logic signed [63:0] z3;
        logic signed [63:0] z4;
        logic signed [63:0] z5;
        logic signed [63:0] tmp0;
        logic signed [63:0] tmp1;
        logic signed [63:0] tmp2;
        logic signed [63:0] tmp3;
        logic signed [63:0] tmp10;
        logic signed [63:0] tmp11;
        logic signed [63:0] tmp12;
        logic signed [63:0] tmp13;
        logic signed [63:0] dcval;

        logic signed [63:0] s0_v;
        logic signed [63:0] s1_v;
        logic signed [63:0] s2_v;
        logic signed [63:0] s3_v;
        logic signed [63:0] s4_v;
        logic signed [63:0] s5_v;
        logic signed [63:0] s6_v;
        logic signed [63:0] s7_v;

        coef0 = $signed(in_coef0);
        coef1 = $signed(in_coef1);
        coef2 = $signed(in_coef2);
        coef3 = $signed(in_coef3);
        coef4 = $signed(in_coef4);
        coef5 = $signed(in_coef5);
        coef6 = $signed(in_coef6);
        coef7 = $signed(in_coef7);

        quant0 = in_quant0;
        quant1 = in_quant1;
        quant2 = in_quant2;
        quant3 = in_quant3;
        quant4 = in_quant4;
        quant5 = in_quant5;
        quant6 = in_quant6;
        quant7 = in_quant7;

        dq0 = coef0 * $signed({1'b0, quant0});
        dq1 = coef1 * $signed({1'b0, quant1});
        dq2 = coef2 * $signed({1'b0, quant2});
        dq3 = coef3 * $signed({1'b0, quant3});
        dq4 = coef4 * $signed({1'b0, quant4});
        dq5 = coef5 * $signed({1'b0, quant5});
        dq6 = coef6 * $signed({1'b0, quant6});
        dq7 = coef7 * $signed({1'b0, quant7});

        z1 = 64'sd0;
        z2 = 64'sd0;
        z3 = 64'sd0;
        z4 = 64'sd0;
        z5 = 64'sd0;
        tmp0 = 64'sd0;
        tmp1 = 64'sd0;
        tmp2 = 64'sd0;
        tmp3 = 64'sd0;
        tmp10 = 64'sd0;
        tmp11 = 64'sd0;
        tmp12 = 64'sd0;
        tmp13 = 64'sd0;
        dcval = 64'sd0;
        s0_v = 64'sd0;
        s1_v = 64'sd0;
        s2_v = 64'sd0;
        s3_v = 64'sd0;
        s4_v = 64'sd0;
        s5_v = 64'sd0;
        s6_v = 64'sd0;
        s7_v = 64'sd0;

        if ((coef1 == 16'sd0) && (coef2 == 16'sd0) && (coef3 == 16'sd0) &&
            (coef4 == 16'sd0) && (coef5 == 16'sd0) && (coef6 == 16'sd0) &&
            (coef7 == 16'sd0)) begin
            dcval = $signed(dq0) <<< PASS1_BITS;
            s0_v = dcval;
            s1_v = dcval;
            s2_v = dcval;
            s3_v = dcval;
            s4_v = dcval;
            s5_v = dcval;
            s6_v = dcval;
            s7_v = dcval;
        end else begin
            z2 = dq2;
            z3 = dq6;

            z1 = (z2 + z3) * FIX_0_541196100;
            tmp2 = z1 + z3 * (-FIX_1_847759065);
            tmp3 = z1 + z2 * FIX_0_765366865;

            z2 = dq0;
            z3 = dq4;

            tmp0 = (z2 + z3) <<< CONST_BITS;
            tmp1 = (z2 - z3) <<< CONST_BITS;

            tmp10 = tmp0 + tmp3;
            tmp13 = tmp0 - tmp3;
            tmp11 = tmp1 + tmp2;
            tmp12 = tmp1 - tmp2;

            tmp0 = dq7;
            tmp1 = dq5;
            tmp2 = dq3;
            tmp3 = dq1;

            z1 = tmp0 + tmp3;
            z2 = tmp1 + tmp2;
            z3 = tmp0 + tmp2;
            z4 = tmp1 + tmp3;
            z5 = (z3 + z4) * FIX_1_175875602;

            tmp0 = tmp0 * FIX_0_298631336;
            tmp1 = tmp1 * FIX_2_053119869;
            tmp2 = tmp2 * FIX_3_072711026;
            tmp3 = tmp3 * FIX_1_501321110;
            z1 = z1 * (-FIX_0_899976223);
            z2 = z2 * (-FIX_2_562915447);
            z3 = z3 * (-FIX_1_961570560);
            z4 = z4 * (-FIX_0_390180644);

            z3 = z3 + z5;
            z4 = z4 + z5;

            tmp0 = tmp0 + z1 + z3;
            tmp1 = tmp1 + z2 + z4;
            tmp2 = tmp2 + z2 + z3;
            tmp3 = tmp3 + z1 + z4;

            s0_v = descale64(tmp10 + tmp3, 11);
            s7_v = descale64(tmp10 - tmp3, 11);
            s1_v = descale64(tmp11 + tmp2, 11);
            s6_v = descale64(tmp11 - tmp2, 11);
            s2_v = descale64(tmp12 + tmp1, 11);
            s5_v = descale64(tmp12 - tmp1, 11);
            s3_v = descale64(tmp13 + tmp0, 11);
            s4_v = descale64(tmp13 - tmp0, 11);
        end

        out_s0_next = s0_v[15:0];
        out_s1_next = s1_v[15:0];
        out_s2_next = s2_v[15:0];
        out_s3_next = s3_v[15:0];
        out_s4_next = s4_v[15:0];
        out_s5_next = s5_v[15:0];
        out_s6_next = s6_v[15:0];
        out_s7_next = s7_v[15:0];
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy   <= 1'b0;
            out_s0 <= 16'h0000;
            out_s1 <= 16'h0000;
            out_s2 <= 16'h0000;
            out_s3 <= 16'h0000;
            out_s4 <= 16'h0000;
            out_s5 <= 16'h0000;
            out_s6 <= 16'h0000;
            out_s7 <= 16'h0000;
        end else if (input_accepted) begin
            busy   <= 1'b1;
            out_s0 <= out_s0_next;
            out_s1 <= out_s1_next;
            out_s2 <= out_s2_next;
            out_s3 <= out_s3_next;
            out_s4 <= out_s4_next;
            out_s5 <= out_s5_next;
            out_s6 <= out_s6_next;
            out_s7 <= out_s7_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule