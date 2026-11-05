module WinogradF43Full (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_d00,
    input  logic [15:0]  in_d01,
    input  logic [15:0]  in_d02,
    input  logic [15:0]  in_d03,
    input  logic [15:0]  in_d04,
    input  logic [15:0]  in_d05,
    input  logic [15:0]  in_d10,
    input  logic [15:0]  in_d11,
    input  logic [15:0]  in_d12,
    input  logic [15:0]  in_d13,
    input  logic [15:0]  in_d14,
    input  logic [15:0]  in_d15,
    input  logic [15:0]  in_d20,
    input  logic [15:0]  in_d21,
    input  logic [15:0]  in_d22,
    input  logic [15:0]  in_d23,
    input  logic [15:0]  in_d24,
    input  logic [15:0]  in_d25,
    input  logic [15:0]  in_d30,
    input  logic [15:0]  in_d31,
    input  logic [15:0]  in_d32,
    input  logic [15:0]  in_d33,
    input  logic [15:0]  in_d34,
    input  logic [15:0]  in_d35,
    input  logic [15:0]  in_d40,
    input  logic [15:0]  in_d41,
    input  logic [15:0]  in_d42,
    input  logic [15:0]  in_d43,
    input  logic [15:0]  in_d44,
    input  logic [15:0]  in_d45,
    input  logic [15:0]  in_d50,
    input  logic [15:0]  in_d51,
    input  logic [15:0]  in_d52,
    input  logic [15:0]  in_d53,
    input  logic [15:0]  in_d54,
    input  logic [15:0]  in_d55,

    input  logic [15:0]  in_g00,
    input  logic [15:0]  in_g01,
    input  logic [15:0]  in_g02,
    input  logic [15:0]  in_g10,
    input  logic [15:0]  in_g11,
    input  logic [15:0]  in_g12,
    input  logic [15:0]  in_g20,
    input  logic [15:0]  in_g21,
    input  logic [15:0]  in_g22,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_y00,
    output logic [31:0]  out_y01,
    output logic [31:0]  out_y02,
    output logic [31:0]  out_y03,
    output logic [31:0]  out_y10,
    output logic [31:0]  out_y11,
    output logic [31:0]  out_y12,
    output logic [31:0]  out_y13,
    output logic [31:0]  out_y20,
    output logic [31:0]  out_y21,
    output logic [31:0]  out_y22,
    output logic [31:0]  out_y23,
    output logic [31:0]  out_y30,
    output logic [31:0]  out_y31,
    output logic [31:0]  out_y32,
    output logic [31:0]  out_y33
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [15:0] s16(input logic [15:0] x);
    begin
        s16 = $signed(x);
    end
endfunction

always_ff @(posedge clk or posedge rst) begin
    logic signed [15:0] d00, d01, d02, d03, d04, d05;
    logic signed [15:0] d10, d11, d12, d13, d14, d15;
    logic signed [15:0] d20, d21, d22, d23, d24, d25;
    logic signed [15:0] d30, d31, d32, d33, d34, d35;
    logic signed [15:0] d40, d41, d42, d43, d44, d45;
    logic signed [15:0] d50, d51, d52, d53, d54, d55;

    logic signed [15:0] g00, g01, g02;
    logic signed [15:0] g10, g11, g12;
    logic signed [15:0] g20, g21, g22;

    logic signed [35:0] y00_s, y01_s, y02_s, y03_s;
    logic signed [35:0] y10_s, y11_s, y12_s, y13_s;
    logic signed [35:0] y20_s, y21_s, y22_s, y23_s;
    logic signed [35:0] y30_s, y31_s, y32_s, y33_s;

    if (rst) begin
        busy    <= 1'b0;
        out_y00 <= 32'd0;
        out_y01 <= 32'd0;
        out_y02 <= 32'd0;
        out_y03 <= 32'd0;
        out_y10 <= 32'd0;
        out_y11 <= 32'd0;
        out_y12 <= 32'd0;
        out_y13 <= 32'd0;
        out_y20 <= 32'd0;
        out_y21 <= 32'd0;
        out_y22 <= 32'd0;
        out_y23 <= 32'd0;
        out_y30 <= 32'd0;
        out_y31 <= 32'd0;
        out_y32 <= 32'd0;
        out_y33 <= 32'd0;
    end else if (input_accepted) begin
        d00 = s16(in_d00); d01 = s16(in_d01); d02 = s16(in_d02); d03 = s16(in_d03); d04 = s16(in_d04); d05 = s16(in_d05);
        d10 = s16(in_d10); d11 = s16(in_d11); d12 = s16(in_d12); d13 = s16(in_d13); d14 = s16(in_d14); d15 = s16(in_d15);
        d20 = s16(in_d20); d21 = s16(in_d21); d22 = s16(in_d22); d23 = s16(in_d23); d24 = s16(in_d24); d25 = s16(in_d25);
        d30 = s16(in_d30); d31 = s16(in_d31); d32 = s16(in_d32); d33 = s16(in_d33); d34 = s16(in_d34); d35 = s16(in_d35);
        d40 = s16(in_d40); d41 = s16(in_d41); d42 = s16(in_d42); d43 = s16(in_d43); d44 = s16(in_d44); d45 = s16(in_d45);
        d50 = s16(in_d50); d51 = s16(in_d51); d52 = s16(in_d52); d53 = s16(in_d53); d54 = s16(in_d54); d55 = s16(in_d55);

        g00 = s16(in_g00); g01 = s16(in_g01); g02 = s16(in_g02);
        g10 = s16(in_g10); g11 = s16(in_g11); g12 = s16(in_g12);
        g20 = s16(in_g20); g21 = s16(in_g21); g22 = s16(in_g22);

        y00_s = d00 * g00 + d01 * g01 + d02 * g02 + d10 * g10 + d11 * g11 + d12 * g12 + d20 * g20 + d21 * g21 + d22 * g22;
        y01_s = d01 * g00 + d02 * g01 + d03 * g02 + d11 * g10 + d12 * g11 + d13 * g12 + d21 * g20 + d22 * g21 + d23 * g22;
        y02_s = d02 * g00 + d03 * g01 + d04 * g02 + d12 * g10 + d13 * g11 + d14 * g12 + d22 * g20 + d23 * g21 + d24 * g22;
        y03_s = d03 * g00 + d04 * g01 + d05 * g02 + d13 * g10 + d14 * g11 + d15 * g12 + d23 * g20 + d24 * g21 + d25 * g22;

        y10_s = d10 * g00 + d11 * g01 + d12 * g02 + d20 * g10 + d21 * g11 + d22 * g12 + d30 * g20 + d31 * g21 + d32 * g22;
        y11_s = d11 * g00 + d12 * g01 + d13 * g02 + d21 * g10 + d22 * g11 + d23 * g12 + d31 * g20 + d32 * g21 + d33 * g22;
        y12_s = d12 * g00 + d13 * g01 + d14 * g02 + d22 * g10 + d23 * g11 + d24 * g12 + d32 * g20 + d33 * g21 + d34 * g22;
        y13_s = d13 * g00 + d14 * g01 + d15 * g02 + d23 * g10 + d24 * g11 + d25 * g12 + d33 * g20 + d34 * g21 + d35 * g22;

        y20_s = d20 * g00 + d21 * g01 + d22 * g02 + d30 * g10 + d31 * g11 + d32 * g12 + d40 * g20 + d41 * g21 + d42 * g22;
        y21_s = d21 * g00 + d22 * g01 + d23 * g02 + d31 * g10 + d32 * g11 + d33 * g12 + d41 * g20 + d42 * g21 + d43 * g22;
        y22_s = d22 * g00 + d23 * g01 + d24 * g02 + d32 * g10 + d33 * g11 + d34 * g12 + d42 * g20 + d43 * g21 + d44 * g22;
        y23_s = d23 * g00 + d24 * g01 + d25 * g02 + d33 * g10 + d34 * g11 + d35 * g12 + d43 * g20 + d44 * g21 + d45 * g22;

        y30_s = d30 * g00 + d31 * g01 + d32 * g02 + d40 * g10 + d41 * g11 + d42 * g12 + d50 * g20 + d51 * g21 + d52 * g22;
        y31_s = d31 * g00 + d32 * g01 + d33 * g02 + d41 * g10 + d42 * g11 + d43 * g12 + d51 * g20 + d52 * g21 + d53 * g22;
        y32_s = d32 * g00 + d33 * g01 + d34 * g02 + d42 * g10 + d43 * g11 + d44 * g12 + d52 * g20 + d53 * g21 + d54 * g22;
        y33_s = d33 * g00 + d34 * g01 + d35 * g02 + d43 * g10 + d44 * g11 + d45 * g12 + d53 * g20 + d54 * g21 + d55 * g22;

        out_y00 <= y00_s[31:0];
        out_y01 <= y01_s[31:0];
        out_y02 <= y02_s[31:0];
        out_y03 <= y03_s[31:0];
        out_y10 <= y10_s[31:0];
        out_y11 <= y11_s[31:0];
        out_y12 <= y12_s[31:0];
        out_y13 <= y13_s[31:0];
        out_y20 <= y20_s[31:0];
        out_y21 <= y21_s[31:0];
        out_y22 <= y22_s[31:0];
        out_y23 <= y23_s[31:0];
        out_y30 <= y30_s[31:0];
        out_y31 <= y31_s[31:0];
        out_y32 <= y32_s[31:0];
        out_y33 <= y33_s[31:0];

        busy <= 1'b1;
    end else if (output_consumed) begin
        busy    <= 1'b0;
        out_y00 <= 32'd0;
        out_y01 <= 32'd0;
        out_y02 <= 32'd0;
        out_y03 <= 32'd0;
        out_y10 <= 32'd0;
        out_y11 <= 32'd0;
        out_y12 <= 32'd0;
        out_y13 <= 32'd0;
        out_y20 <= 32'd0;
        out_y21 <= 32'd0;
        out_y22 <= 32'd0;
        out_y23 <= 32'd0;
        out_y30 <= 32'd0;
        out_y31 <= 32'd0;
        out_y32 <= 32'd0;
        out_y33 <= 32'd0;
    end
end

endmodule