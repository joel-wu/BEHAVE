module keccak_permutation_1600 (
    input  logic          clk,
    input  logic          rst,

    input  logic          in_valid,
    output logic          in_ready,
    input  logic [1599:0] in_state,
    input  logic [7:0]    in_rounds,

    output logic          out_valid,
    input  logic          out_ready,
    output logic [1599:0] out_state,
    output logic out_error
);

    logic busy;
    wire  input_accepted;
    wire  output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [63:0] rol64(input logic [63:0] x, input integer s);
        logic [63:0] left_part;
        logic [63:0] right_part;
        begin
            left_part  = x << s;
            right_part = x >> (64 - s);
            rol64 = left_part | right_part;
        end
    endfunction

    function automatic logic [63:0] round_constant(input logic [4:0] idx);
        begin
            round_constant = 64'h0000000000000000;
            unique case (idx)
                5'd0:  round_constant = 64'h0000000000000001;
                5'd1:  round_constant = 64'h0000000000008082;
                5'd2:  round_constant = 64'h800000000000808A;
                5'd3:  round_constant = 64'h8000000080008000;
                5'd4:  round_constant = 64'h000000000000808B;
                5'd5:  round_constant = 64'h0000000080000001;
                5'd6:  round_constant = 64'h8000000080008081;
                5'd7:  round_constant = 64'h8000000000008009;
                5'd8:  round_constant = 64'h000000000000008A;
                5'd9:  round_constant = 64'h0000000000000088;
                5'd10: round_constant = 64'h0000000080008009;
                5'd11: round_constant = 64'h000000008000000A;
                5'd12: round_constant = 64'h000000008000808B;
                5'd13: round_constant = 64'h800000000000008B;
                5'd14: round_constant = 64'h8000000000008089;
                5'd15: round_constant = 64'h8000000000008003;
                5'd16: round_constant = 64'h8000000000008002;
                5'd17: round_constant = 64'h8000000000000080;
                5'd18: round_constant = 64'h000000000000800A;
                5'd19: round_constant = 64'h800000008000000A;
                5'd20: round_constant = 64'h8000000080008081;
                5'd21: round_constant = 64'h8000000000008080;
                5'd22: round_constant = 64'h0000000080000001;
                5'd23: round_constant = 64'h8000000080008008;
            endcase
        end
    endfunction

    logic [1599:0] computed_out;

    always_comb begin
        logic [63:0] a0;
        logic [63:0] a1;
        logic [63:0] a2;
        logic [63:0] a3;
        logic [63:0] a4;
        logic [63:0] a5;
        logic [63:0] a6;
        logic [63:0] a7;
        logic [63:0] a8;
        logic [63:0] a9;
        logic [63:0] a10;
        logic [63:0] a11;
        logic [63:0] a12;
        logic [63:0] a13;
        logic [63:0] a14;
        logic [63:0] a15;
        logic [63:0] a16;
        logic [63:0] a17;
        logic [63:0] a18;
        logic [63:0] a19;
        logic [63:0] a20;
        logic [63:0] a21;
        logic [63:0] a22;
        logic [63:0] a23;
        logic [63:0] a24;

        logic [63:0] b0;
        logic [63:0] b1;
        logic [63:0] b2;
        logic [63:0] b3;
        logic [63:0] b4;
        logic [63:0] b5;
        logic [63:0] b6;
        logic [63:0] b7;
        logic [63:0] b8;
        logic [63:0] b9;
        logic [63:0] b10;
        logic [63:0] b11;
        logic [63:0] b12;
        logic [63:0] b13;
        logic [63:0] b14;
        logic [63:0] b15;
        logic [63:0] b16;
        logic [63:0] b17;
        logic [63:0] b18;
        logic [63:0] b19;
        logic [63:0] b20;
        logic [63:0] b21;
        logic [63:0] b22;
        logic [63:0] b23;
        logic [63:0] b24;

        logic [63:0] c0;
        logic [63:0] c1;
        logic [63:0] c2;
        logic [63:0] c3;
        logic [63:0] c4;
        logic [63:0] d;
        logic [4:0]  start_round;
        integer i;

        a0  = in_state[63:0];
        a1  = in_state[127:64];
        a2  = in_state[191:128];
        a3  = in_state[255:192];
        a4  = in_state[319:256];
        a5  = in_state[383:320];
        a6  = in_state[447:384];
        a7  = in_state[511:448];
        a8  = in_state[575:512];
        a9  = in_state[639:576];
        a10 = in_state[703:640];
        a11 = in_state[767:704];
        a12 = in_state[831:768];
        a13 = in_state[895:832];
        a14 = in_state[959:896];
        a15 = in_state[1023:960];
        a16 = in_state[1087:1024];
        a17 = in_state[1151:1088];
        a18 = in_state[1215:1152];
        a19 = in_state[1279:1216];
        a20 = in_state[1343:1280];
        a21 = in_state[1407:1344];
        a22 = in_state[1471:1408];
        a23 = in_state[1535:1472];
        a24 = in_state[1599:1536];

        if (in_rounds == 8'd24) begin
            start_round = 5'd0;
        end else begin
            start_round = 5'd12;
        end

        for (i = 0; i < 24; i = i + 1) begin
            if (i >= start_round) begin
                c0 = a0 ^ a5 ^ a10 ^ a15 ^ a20;
                c1 = a1 ^ a6 ^ a11 ^ a16 ^ a21;
                c2 = a2 ^ a7 ^ a12 ^ a17 ^ a22;
                c3 = a3 ^ a8 ^ a13 ^ a18 ^ a23;
                c4 = a4 ^ a9 ^ a14 ^ a19 ^ a24;

                d   = c4 ^ rol64(c1, 1);
                b0  = d ^ a0;
                b16 = rol64(d ^ a5, 36);
                b7  = rol64(d ^ a10, 3);
                b23 = rol64(d ^ a15, 41);
                b14 = rol64(d ^ a20, 18);

                d   = c0 ^ rol64(c2, 1);
                b10 = rol64(d ^ a1, 1);
                b1  = rol64(d ^ a6, 44);
                b17 = rol64(d ^ a11, 10);
                b8  = rol64(d ^ a16, 45);
                b24 = rol64(d ^ a21, 2);

                d   = c1 ^ rol64(c3, 1);
                b20 = rol64(d ^ a2, 62);
                b11 = rol64(d ^ a7, 6);
                b2  = rol64(d ^ a12, 43);
                b18 = rol64(d ^ a17, 15);
                b9  = rol64(d ^ a22, 61);

                d   = c2 ^ rol64(c4, 1);
                b5  = rol64(d ^ a3, 28);
                b21 = rol64(d ^ a8, 55);
                b12 = rol64(d ^ a13, 25);
                b3  = rol64(d ^ a18, 21);
                b19 = rol64(d ^ a23, 56);

                d   = c3 ^ rol64(c0, 1);
                b15 = rol64(d ^ a4, 27);
                b6  = rol64(d ^ a9, 20);
                b22 = rol64(d ^ a14, 39);
                b13 = rol64(d ^ a19, 8);
                b4  = rol64(d ^ a24, 14);

                a0  = b0  ^ ((~b1)  & b2)  ^ round_constant(i[4:0]);
                a1  = b1  ^ ((~b2)  & b3);
                a2  = b2  ^ ((~b3)  & b4);
                a3  = b3  ^ ((~b4)  & b0);
                a4  = b4  ^ ((~b0)  & b1);

                a5  = b5  ^ ((~b6)  & b7);
                a6  = b6  ^ ((~b7)  & b8);
                a7  = b7  ^ ((~b8)  & b9);
                a8  = b8  ^ ((~b9)  & b5);
                a9  = b9  ^ ((~b5)  & b6);

                a10 = b10 ^ ((~b11) & b12);
                a11 = b11 ^ ((~b12) & b13);
                a12 = b12 ^ ((~b13) & b14);
                a13 = b13 ^ ((~b14) & b10);
                a14 = b14 ^ ((~b10) & b11);

                a15 = b15 ^ ((~b16) & b17);
                a16 = b16 ^ ((~b17) & b18);
                a17 = b17 ^ ((~b18) & b19);
                a18 = b18 ^ ((~b19) & b15);
                a19 = b19 ^ ((~b15) & b16);

                a20 = b20 ^ ((~b21) & b22);
                a21 = b21 ^ ((~b22) & b23);
                a22 = b22 ^ ((~b23) & b24);
                a23 = b23 ^ ((~b24) & b20);
                a24 = b24 ^ ((~b20) & b21);
            end
        end

        computed_out = 1600'h0;
        computed_out[63:0]       = a0;
        computed_out[127:64]     = a1;
        computed_out[191:128]    = a2;
        computed_out[255:192]    = a3;
        computed_out[319:256]    = a4;
        computed_out[383:320]    = a5;
        computed_out[447:384]    = a6;
        computed_out[511:448]    = a7;
        computed_out[575:512]    = a8;
        computed_out[639:576]    = a9;
        computed_out[703:640]    = a10;
        computed_out[767:704]    = a11;
        computed_out[831:768]    = a12;
        computed_out[895:832]    = a13;
        computed_out[959:896]    = a14;
        computed_out[1023:960]   = a15;
        computed_out[1087:1024]  = a16;
        computed_out[1151:1088]  = a17;
        computed_out[1215:1152]  = a18;
        computed_out[1279:1216]  = a19;
        computed_out[1343:1280]  = a20;
        computed_out[1407:1344]  = a21;
        computed_out[1471:1408]  = a22;
        computed_out[1535:1472]  = a23;
        computed_out[1599:1536]  = a24;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_state <= 1600'h0;
            out_error <= 1'b0;
        end else if (input_accepted) begin
            busy      <= 1'b1;
            out_state <= (in_rounds == 12 || in_rounds == 24) ? computed_out : 1600'd0;
            out_error <= !(in_rounds == 12 || in_rounds == 24);
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
