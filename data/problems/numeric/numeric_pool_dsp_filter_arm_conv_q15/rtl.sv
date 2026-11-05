module q15_convolution8 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_a0,
    input  logic [15:0]  in_a1,
    input  logic [15:0]  in_a2,
    input  logic [15:0]  in_a3,
    input  logic [15:0]  in_a4,
    input  logic [15:0]  in_a5,
    input  logic [15:0]  in_a6,
    input  logic [15:0]  in_a7,
    input  logic [15:0]  in_b0,
    input  logic [15:0]  in_b1,
    input  logic [15:0]  in_b2,
    input  logic [15:0]  in_b3,
    input  logic [15:0]  in_b4,
    input  logic [15:0]  in_b5,
    input  logic [15:0]  in_b6,
    input  logic [15:0]  in_b7,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_y0,
    output logic [15:0]  out_y1,
    output logic [15:0]  out_y2,
    output logic [15:0]  out_y3,
    output logic [15:0]  out_y4,
    output logic [15:0]  out_y5,
    output logic [15:0]  out_y6,
    output logic [15:0]  out_y7,
    output logic [15:0]  out_y8,
    output logic [15:0]  out_y9,
    output logic [15:0]  out_y10,
    output logic [15:0]  out_y11,
    output logic [15:0]  out_y12,
    output logic [15:0]  out_y13,
    output logic [15:0]  out_y14
);

logic busy;
wire  output_consumed;
wire  input_accepted;

logic signed [15:0] a0_s, a1_s, a2_s, a3_s, a4_s, a5_s, a6_s, a7_s;
logic signed [15:0] b0_s, b1_s, b2_s, b3_s, b4_s, b5_s, b6_s, b7_s;

logic signed [31:0] p00, p01, p02, p03, p04, p05, p06, p07;
logic signed [31:0] p10, p11, p12, p13, p14, p15, p16, p17;
logic signed [31:0] p20, p21, p22, p23, p24, p25, p26, p27;
logic signed [31:0] p30, p31, p32, p33, p34, p35, p36, p37;
logic signed [31:0] p40, p41, p42, p43, p44, p45, p46, p47;
logic signed [31:0] p50, p51, p52, p53, p54, p55, p56, p57;
logic signed [31:0] p60, p61, p62, p63, p64, p65, p66, p67;
logic signed [31:0] p70, p71, p72, p73, p74, p75, p76, p77;

logic signed [34:0] s0_c, s1_c, s2_c, s3_c, s4_c, s5_c, s6_c, s7_c;
logic signed [34:0] s8_c, s9_c, s10_c, s11_c, s12_c, s13_c, s14_c;

logic [15:0] y0_c, y1_c, y2_c, y3_c, y4_c, y5_c, y6_c, y7_c;
logic [15:0] y8_c, y9_c, y10_c, y11_c, y12_c, y13_c, y14_c;

function automatic logic [15:0] sat_q15_from_q30_sum(input logic signed [34:0] value);
    logic signed [34:0] shifted;
    begin
        shifted = value >>> 15;
        if (shifted > 35'sd32767)
            sat_q15_from_q30_sum = 16'h7fff;
        else if (shifted < -35'sd32768)
            sat_q15_from_q30_sum = 16'h8000;
        else
            sat_q15_from_q30_sum = shifted[15:0];
    end
endfunction

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    a0_s = $signed(in_a0);
    a1_s = $signed(in_a1);
    a2_s = $signed(in_a2);
    a3_s = $signed(in_a3);
    a4_s = $signed(in_a4);
    a5_s = $signed(in_a5);
    a6_s = $signed(in_a6);
    a7_s = $signed(in_a7);

    b0_s = $signed(in_b0);
    b1_s = $signed(in_b1);
    b2_s = $signed(in_b2);
    b3_s = $signed(in_b3);
    b4_s = $signed(in_b4);
    b5_s = $signed(in_b5);
    b6_s = $signed(in_b6);
    b7_s = $signed(in_b7);

    p00 = a0_s * b0_s; p01 = a0_s * b1_s; p02 = a0_s * b2_s; p03 = a0_s * b3_s;
    p04 = a0_s * b4_s; p05 = a0_s * b5_s; p06 = a0_s * b6_s; p07 = a0_s * b7_s;
    p10 = a1_s * b0_s; p11 = a1_s * b1_s; p12 = a1_s * b2_s; p13 = a1_s * b3_s;
    p14 = a1_s * b4_s; p15 = a1_s * b5_s; p16 = a1_s * b6_s; p17 = a1_s * b7_s;
    p20 = a2_s * b0_s; p21 = a2_s * b1_s; p22 = a2_s * b2_s; p23 = a2_s * b3_s;
    p24 = a2_s * b4_s; p25 = a2_s * b5_s; p26 = a2_s * b6_s; p27 = a2_s * b7_s;
    p30 = a3_s * b0_s; p31 = a3_s * b1_s; p32 = a3_s * b2_s; p33 = a3_s * b3_s;
    p34 = a3_s * b4_s; p35 = a3_s * b5_s; p36 = a3_s * b6_s; p37 = a3_s * b7_s;
    p40 = a4_s * b0_s; p41 = a4_s * b1_s; p42 = a4_s * b2_s; p43 = a4_s * b3_s;
    p44 = a4_s * b4_s; p45 = a4_s * b5_s; p46 = a4_s * b6_s; p47 = a4_s * b7_s;
    p50 = a5_s * b0_s; p51 = a5_s * b1_s; p52 = a5_s * b2_s; p53 = a5_s * b3_s;
    p54 = a5_s * b4_s; p55 = a5_s * b5_s; p56 = a5_s * b6_s; p57 = a5_s * b7_s;
    p60 = a6_s * b0_s; p61 = a6_s * b1_s; p62 = a6_s * b2_s; p63 = a6_s * b3_s;
    p64 = a6_s * b4_s; p65 = a6_s * b5_s; p66 = a6_s * b6_s; p67 = a6_s * b7_s;
    p70 = a7_s * b0_s; p71 = a7_s * b1_s; p72 = a7_s * b2_s; p73 = a7_s * b3_s;
    p74 = a7_s * b4_s; p75 = a7_s * b5_s; p76 = a7_s * b6_s; p77 = a7_s * b7_s;

    s0_c  = $signed({{3{p00[31]}}, p00});
    s1_c  = $signed({{3{p01[31]}}, p01}) + $signed({{3{p10[31]}}, p10});
    s2_c  = $signed({{3{p02[31]}}, p02}) + $signed({{3{p11[31]}}, p11}) + $signed({{3{p20[31]}}, p20});
    s3_c  = $signed({{3{p03[31]}}, p03}) + $signed({{3{p12[31]}}, p12}) + $signed({{3{p21[31]}}, p21}) + $signed({{3{p30[31]}}, p30});
    s4_c  = $signed({{3{p04[31]}}, p04}) + $signed({{3{p13[31]}}, p13}) + $signed({{3{p22[31]}}, p22}) + $signed({{3{p31[31]}}, p31}) + $signed({{3{p40[31]}}, p40});
    s5_c  = $signed({{3{p05[31]}}, p05}) + $signed({{3{p14[31]}}, p14}) + $signed({{3{p23[31]}}, p23}) + $signed({{3{p32[31]}}, p32}) + $signed({{3{p41[31]}}, p41}) + $signed({{3{p50[31]}}, p50});
    s6_c  = $signed({{3{p06[31]}}, p06}) + $signed({{3{p15[31]}}, p15}) + $signed({{3{p24[31]}}, p24}) + $signed({{3{p33[31]}}, p33}) + $signed({{3{p42[31]}}, p42}) + $signed({{3{p51[31]}}, p51}) + $signed({{3{p60[31]}}, p60});
    s7_c  = $signed({{3{p07[31]}}, p07}) + $signed({{3{p16[31]}}, p16}) + $signed({{3{p25[31]}}, p25}) + $signed({{3{p34[31]}}, p34}) + $signed({{3{p43[31]}}, p43}) + $signed({{3{p52[31]}}, p52}) + $signed({{3{p61[31]}}, p61}) + $signed({{3{p70[31]}}, p70});
    s8_c  = $signed({{3{p17[31]}}, p17}) + $signed({{3{p26[31]}}, p26}) + $signed({{3{p35[31]}}, p35}) + $signed({{3{p44[31]}}, p44}) + $signed({{3{p53[31]}}, p53}) + $signed({{3{p62[31]}}, p62}) + $signed({{3{p71[31]}}, p71});
    s9_c  = $signed({{3{p27[31]}}, p27}) + $signed({{3{p36[31]}}, p36}) + $signed({{3{p45[31]}}, p45}) + $signed({{3{p54[31]}}, p54}) + $signed({{3{p63[31]}}, p63}) + $signed({{3{p72[31]}}, p72});
    s10_c = $signed({{3{p37[31]}}, p37}) + $signed({{3{p46[31]}}, p46}) + $signed({{3{p55[31]}}, p55}) + $signed({{3{p64[31]}}, p64}) + $signed({{3{p73[31]}}, p73});
    s11_c = $signed({{3{p47[31]}}, p47}) + $signed({{3{p56[31]}}, p56}) + $signed({{3{p65[31]}}, p65}) + $signed({{3{p74[31]}}, p74});
    s12_c = $signed({{3{p57[31]}}, p57}) + $signed({{3{p66[31]}}, p66}) + $signed({{3{p75[31]}}, p75});
    s13_c = $signed({{3{p67[31]}}, p67}) + $signed({{3{p76[31]}}, p76});
    s14_c = $signed({{3{p77[31]}}, p77});

    y0_c  = sat_q15_from_q30_sum(s0_c);
    y1_c  = sat_q15_from_q30_sum(s1_c);
    y2_c  = sat_q15_from_q30_sum(s2_c);
    y3_c  = sat_q15_from_q30_sum(s3_c);
    y4_c  = sat_q15_from_q30_sum(s4_c);
    y5_c  = sat_q15_from_q30_sum(s5_c);
    y6_c  = sat_q15_from_q30_sum(s6_c);
    y7_c  = sat_q15_from_q30_sum(s7_c);
    y8_c  = sat_q15_from_q30_sum(s8_c);
    y9_c  = sat_q15_from_q30_sum(s9_c);
    y10_c = sat_q15_from_q30_sum(s10_c);
    y11_c = sat_q15_from_q30_sum(s11_c);
    y12_c = sat_q15_from_q30_sum(s12_c);
    y13_c = sat_q15_from_q30_sum(s13_c);
    y14_c = sat_q15_from_q30_sum(s14_c);
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy    <= 1'b0;
        out_y0  <= 16'h0000;
        out_y1  <= 16'h0000;
        out_y2  <= 16'h0000;
        out_y3  <= 16'h0000;
        out_y4  <= 16'h0000;
        out_y5  <= 16'h0000;
        out_y6  <= 16'h0000;
        out_y7  <= 16'h0000;
        out_y8  <= 16'h0000;
        out_y9  <= 16'h0000;
        out_y10 <= 16'h0000;
        out_y11 <= 16'h0000;
        out_y12 <= 16'h0000;
        out_y13 <= 16'h0000;
        out_y14 <= 16'h0000;
    end else if (input_accepted) begin
        busy    <= 1'b1;
        out_y0  <= y0_c;
        out_y1  <= y1_c;
        out_y2  <= y2_c;
        out_y3  <= y3_c;
        out_y4  <= y4_c;
        out_y5  <= y5_c;
        out_y6  <= y6_c;
        out_y7  <= y7_c;
        out_y8  <= y8_c;
        out_y9  <= y9_c;
        out_y10 <= y10_c;
        out_y11 <= y11_c;
        out_y12 <= y12_c;
        out_y13 <= y13_c;
        out_y14 <= y14_c;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule