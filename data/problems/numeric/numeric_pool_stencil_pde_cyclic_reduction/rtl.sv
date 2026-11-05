module cyclic_reduction_stage (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a_cw_m1,
    input  logic [63:0]  in_a_cc_m1,
    input  logic [63:0]  in_a_ce_m1,
    input  logic [63:0]  in_a_cw_0,
    input  logic [63:0]  in_a_cc_0,
    input  logic [63:0]  in_a_ce_0,
    input  logic [63:0]  in_a_cw_p1,
    input  logic [63:0]  in_a_cc_p1,
    input  logic [63:0]  in_a_ce_p1,
    input  logic         in_periodic_collapse,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_ac_cw,
    output logic [63:0]  out_ac_cc,
    output logic [63:0]  out_ac_ce
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic signed [63:0] trunc_div64(
        input logic signed [127:0] n,
        input logic signed [127:0] d
    );
        logic sign_neg;
        logic signed [127:0] an;
        logic signed [127:0] ad;
        logic signed [127:0] q;
        begin
            trunc_div64 = '0;
            sign_neg = n[127] ^ d[127];
            an = n[127] ? -n : n;
            ad = d[127] ? -d : d;
            q = an / ad;
            if (sign_neg) begin
                trunc_div64 = -q[63:0];
            end else begin
                trunc_div64 = q[63:0];
            end
        end
    endfunction

    function automatic logic signed [63:0] qmul64(
        input logic signed [63:0] a,
        input logic signed [63:0] b
    );
        logic signed [127:0] prod;
        logic signed [127:0] denom;
        begin
            prod = a * b;
            denom = 128'sd4294967296;
            qmul64 = trunc_div64(prod, denom);
        end
    endfunction

    function automatic logic signed [63:0] qdiv64(
        input logic signed [63:0] a,
        input logic signed [63:0] b
    );
        logic signed [127:0] numer;
        logic signed [127:0] denom;
        begin
            numer = {{64{a[63]}}, a} <<< 32;
            denom = {{64{b[63]}}, b};
            qdiv64 = trunc_div64(numer, denom);
        end
    endfunction

    logic signed [63:0] accm1;
    logic signed [63:0] accp1;
    logic signed [63:0] mul_w;
    logic signed [63:0] mul_c_left;
    logic signed [63:0] mul_c_right;
    logic signed [63:0] mul_e;
    logic signed [63:0] term_w;
    logic signed [63:0] term_c_left;
    logic signed [63:0] term_c_right;
    logic signed [63:0] term_e;
    logic signed [63:0] ac_cw_base;
    logic signed [63:0] ac_cc_base;
    logic signed [63:0] ac_ce_base;
    logic signed [63:0] collapsed_cc;

    localparam logic [63:0] ONE_Q32_32 = 64'h00000001_00000000;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy      <= 1'b0;
            out_ac_cw <= 64'd0;
            out_ac_cc <= 64'd0;
            out_ac_ce <= 64'd0;
        end else if (input_accepted) begin
            accm1 = ($signed(in_a_cc_m1) != 64'sd0) ? $signed(in_a_cc_m1) : $signed(ONE_Q32_32);
            accp1 = ($signed(in_a_cc_p1) != 64'sd0) ? $signed(in_a_cc_p1) : $signed(ONE_Q32_32);

            mul_w       = qmul64($signed(in_a_cw_0), $signed(in_a_cw_m1));
            mul_c_left  = qmul64($signed(in_a_cw_0), $signed(in_a_ce_m1));
            mul_c_right = qmul64($signed(in_a_ce_0), $signed(in_a_cw_p1));
            mul_e       = qmul64($signed(in_a_ce_0), $signed(in_a_ce_p1));

            term_w       = qdiv64(mul_w, accm1);
            term_c_left  = qdiv64(mul_c_left, accm1);
            term_c_right = qdiv64(mul_c_right, accp1);
            term_e       = qdiv64(mul_e, accp1);

            ac_cw_base = -term_w;
            ac_cc_base = $signed(in_a_cc_0) - term_c_left - term_c_right;
            ac_ce_base = -term_e;

            collapsed_cc = ac_cc_base + ac_cw_base + ac_ce_base;

            if (in_periodic_collapse) begin
                out_ac_cw <= 64'd0;
                out_ac_cc <= collapsed_cc;
                out_ac_ce <= 64'd0;
            end else begin
                out_ac_cw <= ac_cw_base;
                out_ac_cc <= ac_cc_base;
                out_ac_ce <= ac_ce_base;
            end

            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule