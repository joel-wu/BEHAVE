module signal_processing_comm_fec_fec_hamming128 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_encode,
    input  logic [11:0] in_symbol,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [11:0] out_symbol
);

    logic        busy;
    logic [11:0] result_next;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    function automatic logic parity8(input logic [7:0] x);
        begin
            parity8 = x[0] ^ x[1] ^ x[2] ^ x[3] ^ x[4] ^ x[5] ^ x[6] ^ x[7];
        end
    endfunction

    function automatic logic parity12(input logic [11:0] x);
        begin
            parity12 = x[0] ^ x[1] ^ x[2] ^ x[3] ^ x[4] ^ x[5] ^ x[6] ^ x[7] ^ x[8] ^ x[9] ^ x[10] ^ x[11];
        end
    endfunction

    always_comb begin
        logic [7:0]  d_enc;
        logic [11:0] c_dec;
        logic        p1;
        logic        p2;
        logic        p4;
        logic        p8;
        logic        s1;
        logic        s2;
        logic        s4;
        logic        s8;
        logic [3:0]  z;
        logic [7:0]  d_dec;

        result_next = 12'h000;

        d_enc = in_symbol[7:0];
        c_dec = in_symbol;
        p1 = 1'b0;
        p2 = 1'b0;
        p4 = 1'b0;
        p8 = 1'b0;
        s1 = 1'b0;
        s2 = 1'b0;
        s4 = 1'b0;
        s8 = 1'b0;
        z  = 4'h0;
        d_dec = 8'h00;

        if (in_encode) begin
            p1 = parity8(d_enc & 8'hDA);
            p2 = parity8(d_enc & 8'hB6);
            p4 = parity8(d_enc & 8'h71);
            p8 = parity8(d_enc & 8'h0F);
            result_next = (({4'b0000, d_enc} & 12'h00F) << 0) |
                          (({4'b0000, d_enc} & 12'h070) << 1) |
                          (({4'b0000, d_enc} & 12'h080) << 2) |
                          ({11'b0, p1} << 11) |
                          ({11'b0, p2} << 10) |
                          ({11'b0, p4} << 8)  |
                          ({11'b0, p8} << 4);
        end else begin
            s1 = parity12(c_dec & 12'hAAA);
            s2 = parity12(c_dec & 12'h666);
            s4 = parity12(c_dec & 12'h1E1);
            s8 = parity12(c_dec & 12'h01F);
            z  = {s8, s4, s2, s1};

            if ((z != 4'h0) && (z <= 4'd12)) begin
                c_dec = c_dec ^ (12'h001 << (12 - z));
            end

            d_dec = ((c_dec & 12'h00F) >> 0) |
                    ((c_dec & 12'h0E0) >> 1) |
                    ((c_dec & 12'h200) >> 2);

            result_next = {4'h0, d_dec};
        end
    end

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy       <= 1'b0;
            out_symbol <= 12'h000;
        end else if (input_accepted) begin
            busy       <= 1'b1;
            out_symbol <= result_next;
        end else if (output_consumed) begin
            busy       <= 1'b0;
        end
    end

endmodule