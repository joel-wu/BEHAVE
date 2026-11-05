module cryptography_sym_chacha_salsa20_quarterround (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_in,
    input  logic [255:0] in_k,
    input  logic [127:0] in_c,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [511:0] out_out
);

    logic busy;
    wire  input_accepted;
    wire  output_consumed;

    function automatic logic [31:0] rotl32(input logic [31:0] v, input logic [4:0] s);
        logic [31:0] left_part;
        logic [31:0] right_part;
        begin
            left_part  = v << s;
            right_part = v >> (32 - int'(s));
            rotl32     = left_part | right_part;
        end
    endfunction

    function automatic logic [511:0] salsa20_core(
        input logic [127:0] in_in_f,
        input logic [255:0] in_k_f,
        input logic [127:0] in_c_f
    );
        logic [31:0] in0, in1, in2, in3;
        logic [31:0] k0, k1, k2, k3, k4, k5, k6, k7;
        logic [31:0] c0, c1, c2, c3;
        logic [31:0] j0, j1, j2, j3, j4, j5, j6, j7;
        logic [31:0] j8, j9, j10, j11, j12, j13, j14, j15;
        logic [31:0] x0, x1, x2, x3, x4, x5, x6, x7;
        logic [31:0] x8, x9, x10, x11, x12, x13, x14, x15;
        logic [31:0] y0, y1, y2, y3, y4, y5, y6, y7;
        logic [31:0] y8, y9, y10, y11, y12, y13, y14, y15;
        integer r;
        begin
            salsa20_core = 512'h0;

            in0 = in_in_f[31:0];
            in1 = in_in_f[63:32];
            in2 = in_in_f[95:64];
            in3 = in_in_f[127:96];

            k0 = in_k_f[31:0];
            k1 = in_k_f[63:32];
            k2 = in_k_f[95:64];
            k3 = in_k_f[127:96];
            k4 = in_k_f[159:128];
            k5 = in_k_f[191:160];
            k6 = in_k_f[223:192];
            k7 = in_k_f[255:224];

            c0 = in_c_f[31:0];
            c1 = in_c_f[63:32];
            c2 = in_c_f[95:64];
            c3 = in_c_f[127:96];

            j0  = c0;  x0  = c0;
            j1  = k0;  x1  = k0;
            j2  = k1;  x2  = k1;
            j3  = k2;  x3  = k2;
            j4  = k3;  x4  = k3;
            j5  = c1;  x5  = c1;
            j6  = in0; x6  = in0;
            j7  = in1; x7  = in1;
            j8  = in2; x8  = in2;
            j9  = in3; x9  = in3;
            j10 = c2;  x10 = c2;
            j11 = k4;  x11 = k4;
            j12 = k5;  x12 = k5;
            j13 = k6;  x13 = k6;
            j14 = k7;  x14 = k7;
            j15 = c3;  x15 = c3;

            for (r = 0; r < 10; r = r + 1) begin
                x4  = x4  ^ rotl32(x0  + x12, 5'd7);
                x8  = x8  ^ rotl32(x4  + x0,  5'd9);
                x12 = x12 ^ rotl32(x8  + x4,  5'd13);
                x0  = x0  ^ rotl32(x12 + x8,  5'd18);

                x9  = x9  ^ rotl32(x5  + x1,  5'd7);
                x13 = x13 ^ rotl32(x9  + x5,  5'd9);
                x1  = x1  ^ rotl32(x13 + x9,  5'd13);
                x5  = x5  ^ rotl32(x1  + x13, 5'd18);

                x14 = x14 ^ rotl32(x10 + x6,  5'd7);
                x2  = x2  ^ rotl32(x14 + x10, 5'd9);
                x6  = x6  ^ rotl32(x2  + x14, 5'd13);
                x10 = x10 ^ rotl32(x6  + x2,  5'd18);

                x3  = x3  ^ rotl32(x15 + x11, 5'd7);
                x7  = x7  ^ rotl32(x3  + x15, 5'd9);
                x11 = x11 ^ rotl32(x7  + x3,  5'd13);
                x15 = x15 ^ rotl32(x11 + x7,  5'd18);

                x1  = x1  ^ rotl32(x0  + x3,  5'd7);
                x2  = x2  ^ rotl32(x1  + x0,  5'd9);
                x3  = x3  ^ rotl32(x2  + x1,  5'd13);
                x0  = x0  ^ rotl32(x3  + x2,  5'd18);

                x6  = x6  ^ rotl32(x5  + x4,  5'd7);
                x7  = x7  ^ rotl32(x6  + x5,  5'd9);
                x4  = x4  ^ rotl32(x7  + x6,  5'd13);
                x5  = x5  ^ rotl32(x4  + x7,  5'd18);

                x11 = x11 ^ rotl32(x10 + x9,  5'd7);
                x8  = x8  ^ rotl32(x11 + x10, 5'd9);
                x9  = x9  ^ rotl32(x8  + x11, 5'd13);
                x10 = x10 ^ rotl32(x9  + x8,  5'd18);

                x12 = x12 ^ rotl32(x15 + x14, 5'd7);
                x13 = x13 ^ rotl32(x12 + x15, 5'd9);
                x14 = x14 ^ rotl32(x13 + x12, 5'd13);
                x15 = x15 ^ rotl32(x14 + x13, 5'd18);
            end

            y0  = x0  + j0;
            y1  = x1  + j1;
            y2  = x2  + j2;
            y3  = x3  + j3;
            y4  = x4  + j4;
            y5  = x5  + j5;
            y6  = x6  + j6;
            y7  = x7  + j7;
            y8  = x8  + j8;
            y9  = x9  + j9;
            y10 = x10 + j10;
            y11 = x11 + j11;
            y12 = x12 + j12;
            y13 = x13 + j13;
            y14 = x14 + j14;
            y15 = x15 + j15;

            salsa20_core = {
                y15, y14, y13, y12,
                y11, y10, y9,  y8,
                y7,  y6,  y5,  y4,
                y3,  y2,  y1,  y0
            };
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy    <= 1'b0;
            out_out <= 512'h0;
        end else if (input_accepted) begin
            out_out <= salsa20_core(in_in, in_k, in_c);
            busy    <= 1'b1;
        end else if (output_consumed) begin
            busy    <= 1'b0;
        end
    end

endmodule
