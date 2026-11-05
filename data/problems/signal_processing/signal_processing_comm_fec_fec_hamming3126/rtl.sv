module Hamming3126Codec (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_encode,
    input  logic [30:0] in_symbol,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [30:0] out_symbol
);

    logic busy;
    logic [30:0] out_symbol_next;

    wire input_accepted;
    wire output_consumed;

    function automatic logic parity32(input logic [31:0] x);
        logic [31:0] t;
        begin
            t = x;
            t = t ^ (t >> 16);
            t = t ^ (t >> 8);
            t = t ^ (t >> 4);
            t = t ^ (t >> 2);
            t = t ^ (t >> 1);
            parity32 = t[0];
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        logic [25:0] sym_dec;
        logic        p1;
        logic        p2;
        logic        p4;
        logic        p8;
        logic        p16;
        logic        s1;
        logic        s2;
        logic        s4;
        logic        s8;
        logic        s16;
        logic [30:0] encoded_word;
        logic [30:0] sym_enc_tmp;
        logic [30:0] decoded_word;
        logic [4:0]  z;

        sym_dec         = 26'h0000000;
        p1              = 1'b0;
        p2              = 1'b0;
        p4              = 1'b0;
        p8              = 1'b0;
        p16             = 1'b0;
        s1              = 1'b0;
        s2              = 1'b0;
        s4              = 1'b0;
        s8              = 1'b0;
        s16             = 1'b0;
        encoded_word    = 31'h00000000;
        sym_enc_tmp     = 31'h00000000;
        decoded_word    = 31'h00000000;
        z               = 5'h00;
        out_symbol_next = 31'h00000000;

        if (in_encode) begin
            sym_dec = in_symbol[25:0];

            p1  = parity32({6'b0, (sym_dec & 26'h36AD555)});
            p2  = parity32({6'b0, (sym_dec & 26'h2D9B333)});
            p4  = parity32({6'b0, (sym_dec & 26'h1C78F0F)});
            p8  = parity32({6'b0, (sym_dec & 26'h03F80FF)});
            p16 = parity32({6'b0, (sym_dec & 26'h0007FFF)});

            encoded_word =
                (({5'b0, sym_dec} & 31'h00007FFF) << 0) |
                (({5'b0, sym_dec} & 31'h003F8000) << 1) |
                (({5'b0, sym_dec} & 31'h01C00000) << 2) |
                (({5'b0, sym_dec} & 31'h02000000) << 3) |
                ({30'b0, p1}  << 30) |
                ({30'b0, p2}  << 29) |
                ({30'b0, p4}  << 27) |
                ({30'b0, p8}  << 23) |
                ({30'b0, p16} << 15);

            out_symbol_next = encoded_word;
        end else begin
            s1  = parity32({1'b0, (in_symbol & 31'h55555555)});
            s2  = parity32({1'b0, (in_symbol & 31'h33333333)});
            s4  = parity32({1'b0, (in_symbol & 31'h0F0F0F0F)});
            s8  = parity32({1'b0, (in_symbol & 31'h00FF00FF)});
            s16 = parity32({16'b0, in_symbol[15:0]});

            z = {s16, s8, s4, s2, s1};

            sym_enc_tmp = in_symbol;
            if (z != 5'd0) begin
                sym_enc_tmp = in_symbol ^ (31'h00000001 << (31 - z));
            end

            decoded_word =
                ((sym_enc_tmp & 31'h00007FFF) >> 0) |
                ((sym_enc_tmp & 31'h007F0000) >> 1) |
                ((sym_enc_tmp & 31'h07000000) >> 2) |
                ((sym_enc_tmp & 31'h10000000) >> 3);

            out_symbol_next = {5'b0, decoded_word[25:0]};
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy       <= 1'b0;
            out_symbol <= 31'h00000000;
        end else if (input_accepted) begin
            busy       <= 1'b1;
            out_symbol <= out_symbol_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
