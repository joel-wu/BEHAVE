module cryptography_sym_nonce_extend_xsalsa20_hsalsa_extend (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_input,
    input  logic [255:0] in_key,
    input  logic [127:0] in_const,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_subkey
);

logic busy;
wire  input_accepted;
wire  output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [31:0] rotl32(input logic [31:0] v, input logic [4:0] s);
    logic [31:0] left_part;
    logic [31:0] right_part;
begin
    left_part  = v << s;
    right_part = v >> (5'd32 - s);
    rotl32     = left_part | right_part;
end
endfunction

function automatic logic [127:0] quarterround(input logic [31:0] a_in,
                                               input logic [31:0] b_in,
                                               input logic [31:0] c_in,
                                               input logic [31:0] d_in);
    logic [31:0] a_tmp;
    logic [31:0] b_tmp;
    logic [31:0] c_tmp;
    logic [31:0] d_tmp;
    logic [31:0] sum_tmp;
begin
    a_tmp = a_in;
    b_tmp = b_in;
    c_tmp = c_in;
    d_tmp = d_in;

    sum_tmp = a_tmp + d_tmp;
    b_tmp   = b_tmp ^ rotl32(sum_tmp, 5'd7);

    sum_tmp = b_tmp + a_tmp;
    c_tmp   = c_tmp ^ rotl32(sum_tmp, 5'd9);

    sum_tmp = c_tmp + b_tmp;
    d_tmp   = d_tmp ^ rotl32(sum_tmp, 5'd13);

    sum_tmp = d_tmp + c_tmp;
    a_tmp   = a_tmp ^ rotl32(sum_tmp, 5'd18);

    quarterround = {a_tmp, b_tmp, c_tmp, d_tmp};
end
endfunction

logic [31:0] x0;
logic [31:0] x1;
logic [31:0] x2;
logic [31:0] x3;
logic [31:0] x4;
logic [31:0] x5;
logic [31:0] x6;
logic [31:0] x7;
logic [31:0] x8;
logic [31:0] x9;
logic [31:0] x10;
logic [31:0] x11;
logic [31:0] x12;
logic [31:0] x13;
logic [31:0] x14;
logic [31:0] x15;

logic [31:0] t0;
logic [31:0] t1;
logic [31:0] t2;
logic [31:0] t3;
logic [31:0] t4;
logic [31:0] t5;
logic [31:0] t6;
logic [31:0] t7;
logic [31:0] t8;
logic [31:0] t9;
logic [31:0] t10;
logic [31:0] t11;
logic [31:0] t12;
logic [31:0] t13;
logic [31:0] t14;
logic [31:0] t15;

logic [127:0] qr0;
logic [127:0] qr1;
logic [127:0] qr2;
logic [127:0] qr3;
logic [127:0] qr4;
logic [127:0] qr5;
logic [127:0] qr6;
logic [127:0] qr7;

integer i;
always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_subkey <= 256'h0000000000000000000000000000000000000000000000000000000000000000;
    end else if (input_accepted) begin
        x0  = in_const[31:0];
        x1  = in_key[31:0];
        x2  = in_key[63:32];
        x3  = in_key[95:64];
        x4  = in_key[127:96];
        x5  = in_const[63:32];
        x6  = in_input[31:0];
        x7  = in_input[63:32];
        x8  = in_input[95:64];
        x9  = in_input[127:96];
        x10 = in_const[95:64];
        x11 = in_key[159:128];
        x12 = in_key[191:160];
        x13 = in_key[223:192];
        x14 = in_key[255:224];
        x15 = in_const[127:96];

        for (i = 0; i < 10; i = i + 1) begin
            qr0 = quarterround(x0, x4, x8, x12);
            qr1 = quarterround(x5, x9, x13, x1);
            qr2 = quarterround(x10, x14, x2, x6);
            qr3 = quarterround(x15, x3, x7, x11);

            t0  = qr0[127:96];
            t4  = qr0[95:64];
            t8  = qr0[63:32];
            t12 = qr0[31:0];

            t5  = qr1[127:96];
            t9  = qr1[95:64];
            t13 = qr1[63:32];
            t1  = qr1[31:0];

            t10 = qr2[127:96];
            t14 = qr2[95:64];
            t2  = qr2[63:32];
            t6  = qr2[31:0];

            t15 = qr3[127:96];
            t3  = qr3[95:64];
            t7  = qr3[63:32];
            t11 = qr3[31:0];

            qr4 = quarterround(t0, t1, t2, t3);
            qr5 = quarterround(t5, t6, t7, t4);
            qr6 = quarterround(t10, t11, t8, t9);
            qr7 = quarterround(t15, t12, t13, t14);

            x0  = qr4[127:96];
            x1  = qr4[95:64];
            x2  = qr4[63:32];
            x3  = qr4[31:0];

            x5  = qr5[127:96];
            x6  = qr5[95:64];
            x7  = qr5[63:32];
            x4  = qr5[31:0];

            x10 = qr6[127:96];
            x11 = qr6[95:64];
            x8  = qr6[63:32];
            x9  = qr6[31:0];

            x15 = qr7[127:96];
            x12 = qr7[95:64];
            x13 = qr7[63:32];
            x14 = qr7[31:0];
        end

        out_subkey <= {x9, x8, x7, x6, x15, x10, x5, x0};
        busy       <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule