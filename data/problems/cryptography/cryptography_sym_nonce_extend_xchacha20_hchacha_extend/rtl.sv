module cryptography_sym_nonce_extend_xchacha20_hchacha_extend (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_nonce,
    input  logic [255:0] in_key,
    input  logic [127:0] in_const,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_subkey
);

    logic         busy;
    logic [255:0] computed_subkey;

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

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [31:0] rotl16(input logic [31:0] v);
        begin
            rotl16 = {v[15:0], v[31:16]};
        end
    endfunction

    function automatic logic [31:0] rotl12(input logic [31:0] v);
        begin
            rotl12 = {v[19:0], v[31:20]};
        end
    endfunction

    function automatic logic [31:0] rotl8(input logic [31:0] v);
        begin
            rotl8 = {v[23:0], v[31:24]};
        end
    endfunction

    function automatic logic [31:0] rotl7(input logic [31:0] v);
        begin
            rotl7 = {v[24:0], v[31:25]};
        end
    endfunction

    task automatic quarter_round(
        inout logic [31:0] a,
        inout logic [31:0] b,
        inout logic [31:0] c,
        inout logic [31:0] d
    );
        begin
            a = a + b;
            d = rotl16(d ^ a);
            c = c + d;
            b = rotl12(b ^ c);
            a = a + b;
            d = rotl8(d ^ a);
            c = c + d;
            b = rotl7(b ^ c);
        end
    endtask

    integer round_idx;

    always_comb begin
        x0  = in_const[31:0];
        x1  = in_const[63:32];
        x2  = in_const[95:64];
        x3  = in_const[127:96];

        x4  = in_key[31:0];
        x5  = in_key[63:32];
        x6  = in_key[95:64];
        x7  = in_key[127:96];
        x8  = in_key[159:128];
        x9  = in_key[191:160];
        x10 = in_key[223:192];
        x11 = in_key[255:224];

        x12 = in_nonce[31:0];
        x13 = in_nonce[63:32];
        x14 = in_nonce[95:64];
        x15 = in_nonce[127:96];

        for (round_idx = 0; round_idx < 10; round_idx = round_idx + 1) begin
            quarter_round(x0, x4, x8,  x12);
            quarter_round(x1, x5, x9,  x13);
            quarter_round(x2, x6, x10, x14);
            quarter_round(x3, x7, x11, x15);

            quarter_round(x0, x5, x10, x15);
            quarter_round(x1, x6, x11, x12);
            quarter_round(x2, x7, x8,  x13);
            quarter_round(x3, x4, x9,  x14);
        end

        computed_subkey = {x15, x14, x13, x12, x3, x2, x1, x0};
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy       <= 1'b0;
            out_subkey <= 256'h0000000000000000000000000000000000000000000000000000000000000000;
        end else if (input_accepted) begin
            busy       <= 1'b1;
            out_subkey <= computed_subkey;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule