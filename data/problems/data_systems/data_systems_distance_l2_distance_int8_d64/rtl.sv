module l2_distance_uint8_d64 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_a0,
    input  logic [7:0]   in_a1,
    input  logic [7:0]   in_a2,
    input  logic [7:0]   in_a3,
    input  logic [7:0]   in_a4,
    input  logic [7:0]   in_a5,
    input  logic [7:0]   in_a6,
    input  logic [7:0]   in_a7,
    input  logic [7:0]   in_a8,
    input  logic [7:0]   in_a9,
    input  logic [7:0]   in_a10,
    input  logic [7:0]   in_a11,
    input  logic [7:0]   in_a12,
    input  logic [7:0]   in_a13,
    input  logic [7:0]   in_a14,
    input  logic [7:0]   in_a15,
    input  logic [7:0]   in_a16,
    input  logic [7:0]   in_a17,
    input  logic [7:0]   in_a18,
    input  logic [7:0]   in_a19,
    input  logic [7:0]   in_a20,
    input  logic [7:0]   in_a21,
    input  logic [7:0]   in_a22,
    input  logic [7:0]   in_a23,
    input  logic [7:0]   in_a24,
    input  logic [7:0]   in_a25,
    input  logic [7:0]   in_a26,
    input  logic [7:0]   in_a27,
    input  logic [7:0]   in_a28,
    input  logic [7:0]   in_a29,
    input  logic [7:0]   in_a30,
    input  logic [7:0]   in_a31,
    input  logic [7:0]   in_a32,
    input  logic [7:0]   in_a33,
    input  logic [7:0]   in_a34,
    input  logic [7:0]   in_a35,
    input  logic [7:0]   in_a36,
    input  logic [7:0]   in_a37,
    input  logic [7:0]   in_a38,
    input  logic [7:0]   in_a39,
    input  logic [7:0]   in_a40,
    input  logic [7:0]   in_a41,
    input  logic [7:0]   in_a42,
    input  logic [7:0]   in_a43,
    input  logic [7:0]   in_a44,
    input  logic [7:0]   in_a45,
    input  logic [7:0]   in_a46,
    input  logic [7:0]   in_a47,
    input  logic [7:0]   in_a48,
    input  logic [7:0]   in_a49,
    input  logic [7:0]   in_a50,
    input  logic [7:0]   in_a51,
    input  logic [7:0]   in_a52,
    input  logic [7:0]   in_a53,
    input  logic [7:0]   in_a54,
    input  logic [7:0]   in_a55,
    input  logic [7:0]   in_a56,
    input  logic [7:0]   in_a57,
    input  logic [7:0]   in_a58,
    input  logic [7:0]   in_a59,
    input  logic [7:0]   in_a60,
    input  logic [7:0]   in_a61,
    input  logic [7:0]   in_a62,
    input  logic [7:0]   in_a63,
    input  logic [7:0]   in_b0,
    input  logic [7:0]   in_b1,
    input  logic [7:0]   in_b2,
    input  logic [7:0]   in_b3,
    input  logic [7:0]   in_b4,
    input  logic [7:0]   in_b5,
    input  logic [7:0]   in_b6,
    input  logic [7:0]   in_b7,
    input  logic [7:0]   in_b8,
    input  logic [7:0]   in_b9,
    input  logic [7:0]   in_b10,
    input  logic [7:0]   in_b11,
    input  logic [7:0]   in_b12,
    input  logic [7:0]   in_b13,
    input  logic [7:0]   in_b14,
    input  logic [7:0]   in_b15,
    input  logic [7:0]   in_b16,
    input  logic [7:0]   in_b17,
    input  logic [7:0]   in_b18,
    input  logic [7:0]   in_b19,
    input  logic [7:0]   in_b20,
    input  logic [7:0]   in_b21,
    input  logic [7:0]   in_b22,
    input  logic [7:0]   in_b23,
    input  logic [7:0]   in_b24,
    input  logic [7:0]   in_b25,
    input  logic [7:0]   in_b26,
    input  logic [7:0]   in_b27,
    input  logic [7:0]   in_b28,
    input  logic [7:0]   in_b29,
    input  logic [7:0]   in_b30,
    input  logic [7:0]   in_b31,
    input  logic [7:0]   in_b32,
    input  logic [7:0]   in_b33,
    input  logic [7:0]   in_b34,
    input  logic [7:0]   in_b35,
    input  logic [7:0]   in_b36,
    input  logic [7:0]   in_b37,
    input  logic [7:0]   in_b38,
    input  logic [7:0]   in_b39,
    input  logic [7:0]   in_b40,
    input  logic [7:0]   in_b41,
    input  logic [7:0]   in_b42,
    input  logic [7:0]   in_b43,
    input  logic [7:0]   in_b44,
    input  logic [7:0]   in_b45,
    input  logic [7:0]   in_b46,
    input  logic [7:0]   in_b47,
    input  logic [7:0]   in_b48,
    input  logic [7:0]   in_b49,
    input  logic [7:0]   in_b50,
    input  logic [7:0]   in_b51,
    input  logic [7:0]   in_b52,
    input  logic [7:0]   in_b53,
    input  logic [7:0]   in_b54,
    input  logic [7:0]   in_b55,
    input  logic [7:0]   in_b56,
    input  logic [7:0]   in_b57,
    input  logic [7:0]   in_b58,
    input  logic [7:0]   in_b59,
    input  logic [7:0]   in_b60,
    input  logic [7:0]   in_b61,
    input  logic [7:0]   in_b62,
    input  logic [7:0]   in_b63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [21:0]  out_distance
);

logic busy;
wire output_consumed;
wire input_accepted;

assign output_consumed = out_valid & out_ready;
assign in_ready = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [21:0] sqdiff8(input logic [7:0] a, input logic [7:0] b);
    logic signed [8:0] diff;
    logic signed [17:0] prod;
    begin
        diff = $signed({1'b0, a}) - $signed({1'b0, b});
        prod = diff * diff;
        sqdiff8 = {4'b0000, prod};
    end
endfunction

logic [21:0] distance_next;

always_comb begin
    distance_next = 22'd0
        + sqdiff8(in_a0,  in_b0)  + sqdiff8(in_a1,  in_b1)
        + sqdiff8(in_a2,  in_b2)  + sqdiff8(in_a3,  in_b3)
        + sqdiff8(in_a4,  in_b4)  + sqdiff8(in_a5,  in_b5)
        + sqdiff8(in_a6,  in_b6)  + sqdiff8(in_a7,  in_b7)
        + sqdiff8(in_a8,  in_b8)  + sqdiff8(in_a9,  in_b9)
        + sqdiff8(in_a10, in_b10) + sqdiff8(in_a11, in_b11)
        + sqdiff8(in_a12, in_b12) + sqdiff8(in_a13, in_b13)
        + sqdiff8(in_a14, in_b14) + sqdiff8(in_a15, in_b15)
        + sqdiff8(in_a16, in_b16) + sqdiff8(in_a17, in_b17)
        + sqdiff8(in_a18, in_b18) + sqdiff8(in_a19, in_b19)
        + sqdiff8(in_a20, in_b20) + sqdiff8(in_a21, in_b21)
        + sqdiff8(in_a22, in_b22) + sqdiff8(in_a23, in_b23)
        + sqdiff8(in_a24, in_b24) + sqdiff8(in_a25, in_b25)
        + sqdiff8(in_a26, in_b26) + sqdiff8(in_a27, in_b27)
        + sqdiff8(in_a28, in_b28) + sqdiff8(in_a29, in_b29)
        + sqdiff8(in_a30, in_b30) + sqdiff8(in_a31, in_b31)
        + sqdiff8(in_a32, in_b32) + sqdiff8(in_a33, in_b33)
        + sqdiff8(in_a34, in_b34) + sqdiff8(in_a35, in_b35)
        + sqdiff8(in_a36, in_b36) + sqdiff8(in_a37, in_b37)
        + sqdiff8(in_a38, in_b38) + sqdiff8(in_a39, in_b39)
        + sqdiff8(in_a40, in_b40) + sqdiff8(in_a41, in_b41)
        + sqdiff8(in_a42, in_b42) + sqdiff8(in_a43, in_b43)
        + sqdiff8(in_a44, in_b44) + sqdiff8(in_a45, in_b45)
        + sqdiff8(in_a46, in_b46) + sqdiff8(in_a47, in_b47)
        + sqdiff8(in_a48, in_b48) + sqdiff8(in_a49, in_b49)
        + sqdiff8(in_a50, in_b50) + sqdiff8(in_a51, in_b51)
        + sqdiff8(in_a52, in_b52) + sqdiff8(in_a53, in_b53)
        + sqdiff8(in_a54, in_b54) + sqdiff8(in_a55, in_b55)
        + sqdiff8(in_a56, in_b56) + sqdiff8(in_a57, in_b57)
        + sqdiff8(in_a58, in_b58) + sqdiff8(in_a59, in_b59)
        + sqdiff8(in_a60, in_b60) + sqdiff8(in_a61, in_b61)
        + sqdiff8(in_a62, in_b62) + sqdiff8(in_a63, in_b63);
end

assign input_accepted = in_valid & in_ready;

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        out_distance <= 22'b0;
    end else if (input_accepted) begin
        busy <= 1'b1;
        out_distance <= distance_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
