module signal_processing_comm_fec_fec_hamming1511 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_encode,
    input  logic [14:0] in_symbol,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [14:0] out_symbol
);

    logic        busy;
    logic [14:0] out_symbol_next;

    wire input_accepted;
    wire output_consumed;

    function automatic logic parity16(input logic [15:0] x);
        begin
            parity16 = ^x;
        end
    endfunction

    always_comb begin
        logic [10:0] data11;
        logic [14:0] enc15;
        logic        p1;
        logic        p2;
        logic        p4;
        logic        p8;
        logic        s1;
        logic        s2;
        logic        s4;
        logic        s8;
        logic [3:0]  z;
        logic [14:0] flip_mask;

        out_symbol_next = 15'b0;
        data11          = in_symbol[10:0];
        enc15           = 15'b0;
        p1              = 1'b0;
        p2              = 1'b0;
        p4              = 1'b0;
        p8              = 1'b0;
        s1              = 1'b0;
        s2              = 1'b0;
        s4              = 1'b0;
        s8              = 1'b0;
        z               = 4'b0;
        flip_mask       = 15'b0;

        if (in_encode) begin
            p1 = parity16({5'b0, (data11 & 11'h6D5)});
            p2 = parity16({5'b0, (data11 & 11'h5B3)});
            p4 = parity16({5'b0, (data11 & 11'h38F)});
            p8 = parity16({5'b0, (data11 & 11'h07F)});

            enc15 = (({4'b0, (data11 & 11'h07F)}) << 0)
                  | (({4'b0, (data11 & 11'h380)}) << 1)
                  | (({4'b0, (data11 & 11'h400)}) << 2)
                  | ({14'b0, p1} << 14)
                  | ({14'b0, p2} << 13)
                  | ({14'b0, p4} << 11)
                  | ({14'b0, p8} << 7);

            out_symbol_next = enc15;
        end else begin
            enc15 = in_symbol & 15'h7FFF;

            s1 = parity16({1'b0, (enc15 & 15'h5555)});
            s2 = parity16({1'b0, (enc15 & 15'h3333)});
            s4 = parity16({1'b0, (enc15 & 15'h0F0F)});
            s8 = parity16({1'b0, (enc15 & 15'h00FF)});

            z = {s8, s4, s2, s1};

            if (z != 4'b0) begin
                flip_mask = (15'b1 << (15 - z));
                enc15 = enc15 ^ flip_mask;
            end

            out_symbol_next = {4'b0, enc15[12], enc15[10:8], enc15[6:0]};
        end
    end

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy       <= 1'b0;
            out_symbol <= 15'b0;
        end else if (input_accepted) begin
            busy       <= 1'b1;
            out_symbol <= out_symbol_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
